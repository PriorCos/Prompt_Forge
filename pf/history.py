"""Generation history: one JSON object per line in history.jsonl.

Every successful generation is appended automatically. Append-only writes
keep it corruption-resistant; a bad line is skipped on read, never fatal.
"""

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

HISTORY_PATH = Path(__file__).resolve().parent.parent / 'history.jsonl'


def append(entry: dict) -> None:
    entry = dict(entry)
    entry['ts'] = datetime.now(timezone.utc).astimezone().isoformat(timespec='seconds')
    entry['id'] = uuid.uuid4().hex[:12]  # ts alone is not unique within a second
    with HISTORY_PATH.open('a', encoding='utf-8') as f:
        f.write(json.dumps(entry, ensure_ascii=False) + '\n')


def load(limit: int = 200) -> list[dict]:
    """Return entries, newest first."""
    if not HISTORY_PATH.exists():
        return []
    entries = []
    for line in HISTORY_PATH.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            e = json.loads(line)
            if isinstance(e, dict) and e.get('ts'):
                entries.append(e)
        except json.JSONDecodeError:
            continue
    return list(reversed(entries))[:limit]


def delete(entry_id: str) -> None:
    """Remove the entry with the given unique id."""
    entries = [e for e in reversed(load(limit=100000)) if e.get('id') != entry_id]
    with HISTORY_PATH.open('w', encoding='utf-8') as f:
        for e in entries:
            f.write(json.dumps(e, ensure_ascii=False) + '\n')
