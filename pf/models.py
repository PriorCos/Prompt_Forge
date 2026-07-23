"""Model registry, loaded from an editable models.json next to the app.

Each entry: id -> {"name": display name, "description": caption text}. The
model dropdown and the caption under it read from here, so adding a model
(later, your own) is a data edit - no code change. A model whose description
is empty simply shows no caption.
"""

import json

from pf.paths import base_dir

MODELS_PATH = base_dir() / 'models.json'

DEFAULT_MODELS = {
    'glm-4-6': {
        'name': 'GLM-4.6',
        'description': 'Zhipu GLM-4.6 as served by NovelAI. Reasoning-capable; '
                       'the app keeps thinking off so the output is pure prompt text.',
    },
    'xialong-v1': {
        'name': 'Xialong',
        'description': "NovelAI's storytelling finetune of GLM-4.6, Opus tier. "
                       'Looser and more creative - good when the base model feels stiff.',
    },
}


def ensure_file() -> None:
    if not MODELS_PATH.exists():
        MODELS_PATH.write_text(json.dumps(DEFAULT_MODELS, indent=2), encoding='utf-8')


def load() -> dict:
    """Return {id: {name, description}}, falling back to defaults on any problem."""
    try:
        data = json.loads(MODELS_PATH.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError):
        return dict(DEFAULT_MODELS)
    if not isinstance(data, dict):
        return dict(DEFAULT_MODELS)
    out = {}
    for mid, entry in data.items():
        if isinstance(entry, dict):
            out[str(mid)] = {
                'name': str(entry.get('name', mid)),
                'description': str(entry.get('description', '')),
            }
    return out or dict(DEFAULT_MODELS)


def options() -> dict:
    """{id: display name} for a dropdown."""
    return {mid: m['name'] for mid, m in load().items()}


def description(model_id: str) -> str:
    return load().get(model_id, {}).get('description', '')
