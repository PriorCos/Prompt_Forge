"""Local settings persisted to config.json next to the app.

The persistent API token is stored here in plain text on your own machine.
Keep the folder out of anything shared or synced publicly.
"""

import json

from pf.paths import base_dir

CONFIG_PATH = base_dir() / 'config.json'

DEFAULTS = {
    'token': '',
    'endpoint': '',          # confirmed endpoint URL override; empty = default
    'model': 'glm-4-6',      # id from models.json
    'prompt': 'nai',         # id of the active full prompt (catalog)
    'preset': 'balanced',    # id of the active sampling preset (presets.json)
    'active_appends': [],     # ids of active append prompts (catalog)
    'tag_target': 30,
    'krea_words': 130,
    'max_tokens_tags': 900,
    'max_tokens_natural': 1100,
    'idea': '',
    'last_output': '',
    'submit_key': 'enter',   # 'enter' | 'shift-enter' | 'ctrl-enter'
    'theme': 'auto',         # 'auto' | 'light' | 'dark'
    'log_level': 'off',      # 'off' | 'basic' | 'verbose'
    'window': {},            # {x, y, w, h, maximized} - restored on launch
    'animations': True,      # master on/off for all UI animation
    'reduce_motion': False,  # keep gentle fades, drop spatial motion (slide/spin/expand)
}


def _migrate(data: dict) -> dict:
    """Upgrade legacy config keys in place; unknown keys are dropped on save."""
    if 'model' not in data and 'use_xialong' in data:
        data['model'] = 'xialong-v1' if data['use_xialong'] else 'glm-4-6'
    if 'theme' not in data and 'dark' in data:
        data['theme'] = 'dark' if data['dark'] else 'light'
    if 'prompt' not in data and 'mode' in data:
        data['prompt'] = 'krea' if data['mode'] == 'krea' else 'nai'
    if 'active_appends' not in data:
        appends = []
        if data.get('use_emphasis'):
            appends.append('nai_emphasis')
        if data.get('suggest_undesired'):
            appends.append('nai_undesired')
        data['active_appends'] = appends
    return data


def load() -> dict:
    cfg = dict(DEFAULTS)
    try:
        if CONFIG_PATH.exists():
            data = json.loads(CONFIG_PATH.read_text(encoding='utf-8'))
            if isinstance(data, dict):
                data = _migrate(data)
                cfg.update({k: data[k] for k in DEFAULTS if k in data})
    except (OSError, json.JSONDecodeError):
        pass
    return cfg


def save(cfg: dict) -> None:
    known = {k: cfg.get(k, DEFAULTS[k]) for k in DEFAULTS}
    CONFIG_PATH.write_text(json.dumps(known, indent=2), encoding='utf-8')
