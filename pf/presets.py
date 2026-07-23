"""Sampling presets: named bundles of generation parameters.

Model-agnostic and freely swappable - keep several ('Balanced', 'Creative',
your own) and pick the active one; any preset works with any model. Stored in
presets.json (same pattern as models.json / the prompt catalog).

PARAMS is the single source of truth for which knobs exist, their types,
ranges and defaults - the payload builder and the settings UI both read it, so
adding a parameter is one entry here.
"""

import json
import re
from pathlib import Path

PRESETS_PATH = Path(__file__).resolve().parent.parent / 'presets.json'

# key, label, type, and UI metadata. `omit_when` marks the "off" value that
# should NOT be sent to the API (so a 0 doesn't clobber a server default).
PARAMS = [
    dict(key='temperature', label='Temperature', type='float',
         min=0.0, max=2.0, step=0.05, default=0.75),
    dict(key='top_p', label='Top-p', type='float',
         min=0.0, max=1.0, step=0.01, default=0.95),
    dict(key='top_k', label='Top-k', type='int',
         min=0, max=200, step=1, default=0, omit_when=0, hint='0 disables top-k'),
    dict(key='min_p', label='Min-p', type='float',
         min=0.0, max=1.0, step=0.01, default=0.0, omit_when=0.0, hint='0 disables min-p'),
    dict(key='frequency_penalty', label='Frequency penalty', type='float',
         min=-2.0, max=2.0, step=0.05, default=0.4),
    dict(key='presence_penalty', label='Presence penalty', type='float',
         min=-2.0, max=2.0, step=0.05, default=0.3),
    dict(key='seed', label='Seed', type='int',
         min=-1, max=2_147_483_647, step=1, default=-1, omit_when=-1,
         hint='-1 picks a random seed each time'),
    dict(key='unified_linear', label='Unified: linear', type='float',
         min=0.0, max=1.0, step=0.01, default=0.0, omit_when=0.0, advanced=True),
    dict(key='unified_quadratic', label='Unified: quadratic', type='float',
         min=0.0, max=1.0, step=0.01, default=0.0, omit_when=0.0, advanced=True),
    dict(key='unified_cubic', label='Unified: cubic', type='float',
         min=0.0, max=1.0, step=0.01, default=0.0, omit_when=0.0, advanced=True),
    dict(key='unified_increase_linear_with_entropy',
         label='Unified: increase linear with entropy', type='bool',
         default=False, omit_when=False, advanced=True),
]
PARAM_KEYS = [p['key'] for p in PARAMS]


def default_params() -> dict:
    return {p['key']: p['default'] for p in PARAMS}


def _preset(name, builtin=False, **overrides):
    params = default_params()
    params.update(overrides)
    return {'name': name, 'builtin': builtin, 'params': params}


DEFAULT_PRESETS = {
    'balanced': _preset('Balanced', builtin=True),  # today's defaults
    'creative': _preset('Creative', builtin=True,
                        temperature=1.05, top_p=0.98, frequency_penalty=0.3),
    'precise': _preset('Precise', builtin=True,
                       temperature=0.5, top_p=0.9, frequency_penalty=0.5, presence_penalty=0.4),
}


def ensure_file() -> None:
    if not PRESETS_PATH.exists():
        PRESETS_PATH.write_text(json.dumps(DEFAULT_PRESETS, indent=2), encoding='utf-8')
        return
    data = _read()
    changed = False
    for pid, row in DEFAULT_PRESETS.items():  # re-add any missing built-in
        if pid not in data:
            data[pid] = row
            changed = True
    if changed:
        PRESETS_PATH.write_text(json.dumps(data, indent=2), encoding='utf-8')


def _read() -> dict:
    try:
        data = json.loads(PRESETS_PATH.read_text(encoding='utf-8'))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def load() -> dict:
    data = _read()
    return data or dict(DEFAULT_PRESETS)


def _save(data: dict) -> None:
    PRESETS_PATH.write_text(json.dumps(data, indent=2), encoding='utf-8')


def options() -> dict:
    return {pid: row.get('name', pid) for pid, row in load().items()}


def get_params(preset_id: str) -> dict:
    """Params for a preset, with any missing keys filled from defaults."""
    row = load().get(preset_id)
    params = default_params()
    if row and isinstance(row.get('params'), dict):
        for k in PARAM_KEYS:
            if k in row['params']:
                params[k] = row['params'][k]
    return params


def is_builtin(preset_id: str) -> bool:
    return bool(load().get(preset_id, {}).get('builtin'))


def _slug(name: str, existing: set[str]) -> str:
    base = re.sub(r'[^a-z0-9]+', '_', name.lower()).strip('_') or 'preset'
    slug, n = base, 2
    while slug in existing:
        slug, n = f'{base}_{n}', n + 1
    return slug


def create(name: str, params: dict) -> str:
    data = load()
    pid = _slug(name, set(data))
    clean = default_params()
    clean.update({k: params[k] for k in PARAM_KEYS if k in params})
    data[pid] = {'name': name.strip() or pid, 'builtin': False, 'params': clean}
    _save(data)
    return pid


def update(preset_id: str, name: str | None = None, params: dict | None = None) -> None:
    data = load()
    row = data.get(preset_id)
    if not row or row.get('builtin'):
        return
    if name is not None:
        row['name'] = name.strip() or preset_id
    if params is not None:
        merged = default_params()
        merged.update({k: params[k] for k in PARAM_KEYS if k in params})
        row['params'] = merged
    _save(data)


def delete(preset_id: str) -> bool:
    data = load()
    row = data.get(preset_id)
    if not row or row.get('builtin'):
        return False
    del data[preset_id]
    _save(data)
    return True
