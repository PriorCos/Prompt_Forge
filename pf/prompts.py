"""Prompt catalog: the instructions we send to the chat model.

Terminology (the user's): a "prompt" is what we SEND to GLM/Xialong; the
"image prompt" is what comes back. This module manages the sent side.

Two kinds, tracked in prompts/catalog.json:

- **system** prompts - a full instruction set, selectable as a generation
  mode, used in place of another. Each declares a `family`: 'tags' (one-line
  Danbooru output, NAI cleanup) or 'natural' (a paragraph, Krea cleanup).
  The family drives cleanup, length target and max-tokens.
- **append** prompts - fragments added to the END of the active system
  prompt (like the old emphasis / undesired options), selectable and
  stackable. An append may set a `family` to restrict where it applies.

Files live under prompts/system, prompts/appends and prompts/messages. The
catalog is the source of truth for what exists; text lives in the files, so
users can import a prompt by dropping a .txt in and adding a catalog row (or
via the in-app manager). Files are re-read on every generation - edit and
regenerate, no restart. Placeholders use $name (string.Template); a system
prompt marks where appends go with $appends (else they are added at the end).
"""

import json
import re
from pathlib import Path  # noqa: F401 - used in type annotations
from string import Template

from pf.paths import base_dir

PROMPTS_DIR = base_dir() / 'prompts'


def _catalog_file() -> Path:
    # Derived at call time so patching PROMPTS_DIR (tests) also moves the catalog.
    return PROMPTS_DIR / 'catalog.json'

DEFAULT_CATALOG = {
    'system': [
        {'id': 'nai', 'name': 'NAI Diffusion tags', 'file': 'system/nai.txt',
         'family': 'tags', 'builtin': True},
        {'id': 'krea', 'name': 'Natural language', 'file': 'system/krea.txt',
         'family': 'natural', 'builtin': True},
    ],
    'appends': [
        {'id': 'nai_emphasis', 'name': 'Numeric emphasis', 'file': 'appends/nai_emphasis.txt',
         'family': 'tags', 'builtin': True},
        {'id': 'nai_undesired', 'name': 'Suggest undesired content',
         'file': 'appends/nai_undesired.txt', 'family': 'tags', 'builtin': True},
    ],
}

DEFAULT_FILES: dict[str, str] = {}

DEFAULT_FILES['system/nai.txt'] = """\
You are a prompt engineer for NovelAI Diffusion V4.5, which is trained on Danbooru tags.
Convert the idea into a NovelAI image prompt.

TAG VOCABULARY
- Use real Danbooru tags. Lowercase, spaces not underscores. If a concept has no tag, express it with the closest combination of real tags rather than inventing a phrase.
- Danbooru follows "tag what you see". Do not tag anything out of frame.
- NovelAI renamed some tags: write "peace sign" not v, "double peace" not double v, "bar eyes" not |_|, "neutral face" not :|, "square bikini" not eyepatch bikini, "character image" not tachi-e.
- The tag "location" means an unspecified indoor or outdoor setting.
- For a scene with no people at all (landscape, still life, animal portrait) you may open with the tag "background dataset" to get a photographic treatment.
- The tag "year 2014" and similar shift the art style toward that year. Use only if the idea implies a period style.

ORDER
- Open with subject count tags, then named characters, then series, then everything else in any order. Example opening: 1girl, 1boy, character name, series name, ...
- Past that opening, tag position is not weighted on V4.5, so group related tags together for readability rather than competing for early placement.

WHAT TO BE DETAILED ABOUT
- Be specific about pose and body position, expression and gaze direction, framing (cowboy shot, upper body, close-up, full body), camera angle (from above, from below, from side, dutch angle), lighting, and setting.
- Be specific about clothing only where the idea implies it. Do not invent outfits, colours or props the idea does not support.
- Preserve every subject, action, colour and spatial relationship stated in the idea. Add nothing the idea does not imply.
- Aim for roughly $tag_target tags. Use fewer if the idea is simple, and go over when the idea genuinely needs it. A short accurate prompt beats a padded one.

DO NOT INCLUDE
- Quality or aesthetic tags such as best quality, amazing quality, masterpiece, very aesthetic, absurdres. NovelAI appends those itself via its Add Quality Tags toggle.

MULTIPLE CHARACTERS
- If the scene has two or more people, split the prompt with the | character:
  base prompt | first character | second character
- The base prompt holds the subject count tags (2girls, 1boy 1girl, 3others), the setting, lighting, composition and style. Each character prompt describes only that character.
- Start each character prompt with a bare girl, boy or other, with NO number.
- Characters are placed top to bottom, left to right in the order listed, so list the leftmost character first.
- For interactions use action tag prefixes: source# on the character performing it, target# on the character receiving it, mutual# when both do it. Example: source#hug on one character, target#hug on the other.
- A short natural language sentence may end a character prompt to disambiguate, for example "She is pointing at the other girl."
- With a single character, do not use | at all. Never use | for anything else.

TEXT IN THE IMAGE
- If the idea calls for visible text, add the tag text and then a line of the form Text: the exact words.
$appends
OUTPUT FORMAT
- Output the prompt exactly once. Do not repeat it.
- No preamble, no explanation, no code fences, no bullets, and no labels other than those specified above.
- When the prompt is complete, output <END> and stop. Write nothing after it.
"""

DEFAULT_FILES['system/krea.txt'] = """\
You are an expert prompt engineer for modern text-to-image models that read natural language,
such as Krea 2. Write for that class of model.
Expand the idea into one highly effective image generation prompt.

THINK FIRST, SILENTLY
- What is the subject and the mood?
- Which medium, style and lighting best serve it? Consider two or three and pick one.
- What composition, framing and grounded detail will help the model?
Do not show any of this reasoning. Output only the finished prompt.

RULES
1. Faithfulness first. Preserve every subject, action, colour and spatial relationship in the idea. Do not add objects, props, characters or animals the idea does not clearly imply.
2. Do not over-specify. Do not invent highly specific clothing, colours, materials or scene details the idea does not support. Vagueness in the idea is not an invitation to fill it in.
3. If the idea already carries a lot of detail, polish and finalise it rather than expanding it. Preserve the wording and direction the idea already has.
4. If the idea names a medium such as photo of, painting of, 3D render of, honour it exactly. Never switch medium.
5. Group each subject with its own attributes and actions so the model can bind them correctly. Use grounded phrasing for pose, interaction and spatial layout: which side of the frame, what is foreground, what is background.
6. For visible text, state the exact words and wrap them in double quotes.
7. Treat people with dignity. Assume clothing covers intimate anatomy.

WHAT TO COVER, ROUGHLY IN THIS ORDER
- Medium and aesthetic up front, for example a 35mm film photograph, a stylized digital painting, 1990s vintage anime cel animation, a minimalist flat-colour illustration.
- Subject with its concrete attributes, then action.
- Spatial layout: foreground, background, left, right, what dominates the frame.
- Lighting: source, direction, quality, colour temperature.
- Camera and framing: shot distance, angle, lens, depth of field.
- Colour palette and texture or finish, for example muted earthy palette, visible brushstrokes, film grain, grainy paper texture.

FORM
- One cohesive paragraph. No bullets, no JSON, no markdown, no line breaks.
- Either flowing sentences or comma separated descriptive phrases is fine. Both work well with this model. Choose whichever suits the idea.
- Aim for roughly $word_target words. Use considerably fewer when the idea is simple, and go over when the idea genuinely needs it. This is a goal, not a hard limit.
- Describe what is present, never what is absent.
$appends
Output only the prompt. No preamble, no explanation, no code fences.
When the prompt is complete, output <END> and stop. Write nothing after it.
"""

DEFAULT_FILES['appends/nai_emphasis.txt'] = """\
EMPHASIS
- Numeric emphasis: 1.4::tag :: strengthens, 0.6::tag :: weakens, -1::tag :: inverts or removes a concept.
- Close every weighted section with a bare :: . Use at most three, only where a tag genuinely needs it."""

DEFAULT_FILES['appends/nai_undesired.txt'] = """\
UNDESIRED CONTENT
- After the prompt, output one blank line, then a line starting with UNDESIRED: followed by tags for the Undesired Content field. List only what is genuinely at risk in this specific scene, not a generic boilerplate list."""

DEFAULT_FILES['messages/refine.txt'] = """\
Revise the prompt with these notes:
$notes

Return the full revised prompt.
"""

DEFAULT_FILES['messages/variation.txt'] = """\
Give a different take on the same idea. Change framing, lighting or mood, keep the subject and intent.$notes_section
"""

DEFAULT_FILES['messages/enrich.txt'] = """\
Take the current prompt and add rich, concrete detail about: $target.

Unlike a normal revision, you are encouraged to invent specific new details freely. Be hyper-specific about $target - textures, materials, colours, shapes, wear and history, small believable particulars - as long as nothing contradicts the idea or the rest of the prompt. Keep everything else unchanged. It is fine to exceed the length target while doing this.

Return the full updated prompt.
"""


# ---------------------------------------------------------------------------
# Files + catalog
# ---------------------------------------------------------------------------

def ensure_files() -> None:
    """Create the folder tree, default files and catalog if missing.

    Also re-adds any missing built-in catalog rows to an existing catalog, so
    upgrades that introduce a new built-in prompt still surface it.
    """
    for sub in ('system', 'appends', 'messages'):
        (PROMPTS_DIR / sub).mkdir(parents=True, exist_ok=True)
    for rel, text in DEFAULT_FILES.items():
        path = PROMPTS_DIR / rel
        if not path.exists():
            path.write_text(text, encoding='utf-8')

    catalog = _read_catalog_raw()
    if catalog is None:
        _catalog_file().write_text(json.dumps(DEFAULT_CATALOG, indent=2), encoding='utf-8')
        return
    changed = False
    for section in ('system', 'appends'):
        have = {e.get('id') for e in catalog.get(section, [])}
        for row in DEFAULT_CATALOG[section]:
            if row['id'] not in have:
                catalog.setdefault(section, []).append(dict(row))
                changed = True
    if changed:
        _catalog_file().write_text(json.dumps(catalog, indent=2), encoding='utf-8')


def _read_catalog_raw() -> dict | None:
    try:
        data = json.loads(_catalog_file().read_text(encoding='utf-8'))
        return data if isinstance(data, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def load_catalog() -> dict:
    data = _read_catalog_raw() or {}
    system = [e for e in data.get('system', []) if isinstance(e, dict) and e.get('id')]
    appends = [e for e in data.get('appends', []) if isinstance(e, dict) and e.get('id')]
    if not system:
        system = [dict(e) for e in DEFAULT_CATALOG['system']]
    return {'system': system, 'appends': appends or []}


def save_catalog(catalog: dict) -> None:
    _catalog_file().write_text(json.dumps(catalog, indent=2), encoding='utf-8')


def _read(rel: str) -> str:
    try:
        return (PROMPTS_DIR / rel).read_text(encoding='utf-8')
    except OSError:
        return DEFAULT_FILES.get(rel, '')


# ---------------------------------------------------------------------------
# Lookups
# ---------------------------------------------------------------------------

def system_options() -> dict:
    """{id: name} of full prompts, for a dropdown."""
    return {e['id']: e.get('name', e['id']) for e in load_catalog()['system']}


def system_entry(prompt_id: str) -> dict:
    cat = load_catalog()
    for e in cat['system']:
        if e['id'] == prompt_id:
            return e
    return cat['system'][0]


def family_of(prompt_id: str) -> str:
    return system_entry(prompt_id).get('family', 'tags')


def append_items(family: str = '') -> list[dict]:
    """Appends applicable to a family (or all appends when family is empty)."""
    out = []
    for e in load_catalog()['appends']:
        fam = e.get('family')
        if not family or not fam or fam == family:
            out.append(e)
    return out


# ---------------------------------------------------------------------------
# Managing prompts (create / edit / delete / export)
# ---------------------------------------------------------------------------

def _slug(name: str, existing: set[str]) -> str:
    base = re.sub(r'[^a-z0-9]+', '_', name.lower()).strip('_') or 'prompt'
    slug, n = base, 2
    while slug in existing:
        slug, n = f'{base}_{n}', n + 1
    return slug


def find_entry(kind: str, entry_id: str) -> dict | None:
    for e in load_catalog()[kind]:
        if e['id'] == entry_id:
            return e
    return None


def entry_text(kind: str, entry_id: str) -> str:
    e = find_entry(kind, entry_id)
    return _read(e['file']) if e else ''


def create_entry(kind: str, name: str, text: str, family: str = 'natural') -> str:
    """kind is 'system' or 'appends'. Returns the new id."""
    cat = load_catalog()
    entry_id = _slug(name, {e['id'] for e in cat[kind]})
    sub = 'system' if kind == 'system' else 'appends'
    rel = f'{sub}/{entry_id}.txt'
    (PROMPTS_DIR / rel).write_text(text, encoding='utf-8')
    row = {'id': entry_id, 'name': name.strip() or entry_id, 'file': rel}
    if kind == 'system':
        row['family'] = family if family in ('tags', 'natural') else 'natural'
    else:
        row['family'] = family if family in ('tags', 'natural') else None
    cat[kind].append(row)
    save_catalog(cat)
    return entry_id


def update_entry(kind: str, entry_id: str, name: str | None = None,
                 text: str | None = None, family: str | None = None) -> None:
    cat = load_catalog()
    for e in cat[kind]:
        if e['id'] == entry_id:
            if name is not None:
                e['name'] = name.strip() or e['id']
            if family is not None:
                e['family'] = family
            if text is not None:
                (PROMPTS_DIR / e['file']).write_text(text, encoding='utf-8')
            save_catalog(cat)
            return


def delete_entry(kind: str, entry_id: str) -> bool:
    """Delete a custom prompt (file + catalog row). Built-ins are protected."""
    cat = load_catalog()
    for e in cat[kind]:
        if e['id'] == entry_id:
            if e.get('builtin'):
                return False
            try:
                (PROMPTS_DIR / e['file']).unlink()
            except OSError:
                pass
            cat[kind] = [x for x in cat[kind] if x['id'] != entry_id]
            save_catalog(cat)
            return True
    return False


def reset_builtin(kind: str, entry_id: str) -> bool:
    """Restore a built-in prompt's text to its shipped default."""
    e = find_entry(kind, entry_id)
    if not e or not e.get('builtin') or e['file'] not in DEFAULT_FILES:
        return False
    (PROMPTS_DIR / e['file']).write_text(DEFAULT_FILES[e['file']], encoding='utf-8')
    return True


# ---------------------------------------------------------------------------
# Building the sent prompt
# ---------------------------------------------------------------------------

def build_system(prompt_id: str, active_append_ids: list[str], values: dict) -> str:
    """Render the selected full prompt with the active appends inserted.

    Appends replace the $appends placeholder in the prompt; if the prompt has
    none, they are added at the end. Only appends matching the prompt's family
    (or family-agnostic ones) are used.
    """
    cat = load_catalog()
    entry = system_entry(prompt_id)
    family = entry.get('family', 'tags')
    body = _read(entry['file'])

    # Insert appends in the order the user selected them, skipping any that no
    # longer exist or do not apply to this prompt's family.
    by_id = {ap['id']: ap for ap in cat['appends']}
    texts = []
    for aid in (active_append_ids or []):
        ap = by_id.get(aid)
        if ap is None:
            continue
        fam = ap.get('family')
        if fam and fam != family:
            continue
        texts.append(Template(_read(ap['file'])).safe_substitute(**values).strip())
    block = ('\n' + '\n\n'.join(texts) + '\n') if texts else ''

    rendered = Template(body).safe_substitute(appends=block, **values)
    if block and '$appends' not in body and '${appends}' not in body:
        rendered = rendered.rstrip() + '\n\n' + block.strip() + '\n'
    return rendered


# ---------------------------------------------------------------------------
# Iteration message templates
# ---------------------------------------------------------------------------

def refine_message(notes: str) -> str:
    return Template(_read('messages/refine.txt')).safe_substitute(
        notes=notes.strip() or '(no notes, tighten it and make it more specific)')


def variation_message(notes: str) -> str:
    section = '\n\nAlso apply:\n' + notes.strip() if notes.strip() else ''
    return Template(_read('messages/variation.txt')).safe_substitute(notes_section=section)


def enrich_message(target: str) -> str:
    return Template(_read('messages/enrich.txt')).safe_substitute(
        target=target.strip() or 'whatever part of the prompt is currently thinnest')
