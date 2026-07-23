"""Output post-processing for the image prompt the model returns.

`family` is the active prompt's output family: 'tags' (NAI Diffusion - collapse
to a single comma-separated line) or 'natural' (Krea - one flowing paragraph).

Note: the chat endpoint this app uses terminates turns cleanly, so the block
dedup and <END> stripping below are defensive insurance, not the everyday
behavior they were when this logic ran against the in-app *completions*
endpoint (which ran to max_tokens and emitted repeated copies).
"""

import re

from pf import debuglog


def cleanup(raw: str, family: str) -> str:
    t = str(raw or '')
    original_len = len(t)

    # GLM is reasoning-capable; drop any <think> scratch work that leaks into
    # the content. An unclosed <think> means all of it is reasoning - keep only
    # what follows the final close tag.
    before = len(t)
    t = re.sub(r'<think>.*?</think>', '', t, flags=re.S)
    if '</think>' in t:
        t = t.rsplit('</think>', 1)[1]
    t = t.replace('<think>', '')
    if len(t) != before:
        debuglog.log(f'cleanup: stripped think content ({before - len(t)} chars)', 'verbose')

    # The model signals completion with <END>; strip it and anything after.
    cut = t.find('<END>')
    if cut != -1:
        debuglog.log(f'cleanup: <END> found at {cut}, trimming tail', 'verbose')
        t = t[:cut]
    t = re.sub(r'</?END>', '', t)

    # strip code fences the model may add despite instructions
    t = re.sub(r'```[a-zA-Z]*', '', t).replace('```', '')
    t = t.strip()

    blocks = re.split(r'\n\s*\n', t)
    kept: list[str] = []
    seen: set[str] = set()
    dupes = 0
    for b in blocks:
        b = b.strip()
        if not b:
            continue
        key = b.lower()
        if key in seen:
            dupes += 1
            continue
        seen.add(key)
        kept.append(b)
    if dupes:
        debuglog.log(f'cleanup: dropped {dupes} duplicate block(s)', 'verbose')

    main = kept[0] if kept else ''
    undesired = ''
    for b in kept[1:]:
        if b.upper().startswith('UNDESIRED'):
            undesired = b
            break

    if family == 'tags':
        main = re.sub(r'\s*\n\s*', ' ', main)          # tags on one line
        main = re.sub(r',\s*,', ',', main)             # collapse empty tags
        main = re.sub(r'\s+', ' ', main)
        main = re.sub(r',\s*$', '', main).strip()
    else:
        main = re.sub(r'\s*\n\s*', ' ', main)
        main = re.sub(r'\s+', ' ', main).strip()

    debuglog.log(f'cleanup: {family} {original_len} raw -> {len(main)} chars'
                 + (' + undesired' if undesired else ''), 'verbose')
    return f'{main}\n\n{undesired}' if undesired else main


def split_undesired(text: str) -> tuple[str, str]:
    """Split a cleaned output into (main prompt, undesired tag line).

    The undesired part is returned WITHOUT the 'UNDESIRED:' label so it can
    be pasted straight into NovelAI's Undesired Content field. Empty string
    when there is none.
    """
    text = str(text or '').strip()
    blocks = text.split('\n\n')
    for i, b in enumerate(blocks):
        if b.strip().upper().startswith('UNDESIRED'):
            main = '\n\n'.join(blocks[:i] + blocks[i + 1:]).strip()
            und = re.sub(r'^\s*UNDESIRED\s*:?\s*', '', b.strip(), flags=re.I)
            return main, und
    return text, ''


def join_undesired(main: str, undesired: str) -> str:
    """Inverse of split_undesired - rebuild the combined output text."""
    main = str(main or '').strip()
    undesired = str(undesired or '').strip()
    return f'{main}\n\nUNDESIRED: {undesired}' if undesired else main
