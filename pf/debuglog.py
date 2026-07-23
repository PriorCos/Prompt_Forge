"""Lightweight debug log: in-memory ring buffer + a log file, gated by level.

Levels (the "robust vs not" control):
- off     : nothing recorded.
- basic   : one line per notable step (generation start/done, errors, actions).
- verbose : basic + full request/response payloads saved as their own files,
            plus fine-grained steps (settings changes, first token, etc).

Thread-safe: the API client logs from a worker thread while the UI reads the
buffer from the main thread, so mutations are guarded by a lock. The UI polls
`lines` on a timer rather than being called back across threads.
"""

import json
import sys
import threading
import traceback
from datetime import datetime
from pathlib import Path

LOG_DIR = Path(__file__).resolve().parent.parent / 'logs'
LOG_FILE = LOG_DIR / 'prompt_forge.log'

LEVELS = {'off': 0, 'basic': 1, 'verbose': 2}
_MAX_LINES = 1000

_lock = threading.Lock()
_state = {'level': 'off'}
lines: list[str] = []


def set_level(level: str) -> None:
    _state['level'] = level if level in LEVELS else 'off'


def get_level() -> str:
    return _state['level']


def enabled(need: str = 'basic') -> bool:
    return LEVELS[_state['level']] >= LEVELS[need]


def _fmt(data) -> str:
    if isinstance(data, str):
        return data
    try:
        return json.dumps(data, indent=2, ensure_ascii=False)
    except (TypeError, ValueError):
        return repr(data)


def log(msg: str, level: str = 'basic', data=None) -> None:
    """Record a line. `data` (dict/str) is only written when verbose."""
    if not enabled(level):
        return
    ts = datetime.now().strftime('%H:%M:%S')
    line = f'{ts} [{level}] {msg}'
    body = _fmt(data) if (data is not None and enabled('verbose')) else None
    with _lock:
        lines.append(line)
        if body is not None:
            lines.extend('    ' + b for b in body.splitlines())
        if len(lines) > _MAX_LINES:
            del lines[:len(lines) - _MAX_LINES]
        try:
            LOG_DIR.mkdir(exist_ok=True)
            with LOG_FILE.open('a', encoding='utf-8') as f:
                f.write(line + '\n')
                if body is not None:
                    f.write('\n'.join('    ' + b for b in body.splitlines()) + '\n')
        except OSError:
            pass


def exc(label: str, level: str = 'basic') -> None:
    """Log the exception currently being handled: message at `level`, full
    traceback at verbose. Call from inside an except block."""
    e = sys.exc_info()[1]
    log(f'{label}: {type(e).__name__}: {e}', level)
    if enabled('verbose'):
        log(f'{label} traceback', 'verbose', traceback.format_exc().rstrip())


def save_blob(name: str, content) -> Path | None:
    """Write a request/response payload to its own timestamped file (verbose only)."""
    if not enabled('verbose'):
        return None
    with _lock:
        try:
            LOG_DIR.mkdir(exist_ok=True)
            stamp = datetime.now().strftime('%Y%m%d_%H%M%S_%f')
            path = LOG_DIR / f'{stamp}_{name}'
            path.write_text(_fmt(content), encoding='utf-8')
            return path
        except OSError:
            return None


def clear() -> None:
    with _lock:
        lines.clear()
        try:
            LOG_FILE.unlink()
        except OSError:
            pass
