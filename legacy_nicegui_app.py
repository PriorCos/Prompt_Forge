"""Prompt Forge - standalone edition.

Two-pane native-window GUI: compose on the left, result + iteration on the
right, settings in a slide-out drawer. Idea -> NAI Diffusion tags or Krea 2
natural-language prompt via GLM-4.6 / Xialong, with refine, variation,
add-detail (enrich), one-shot notes, live streaming, and history.

Settings are declared in SETTINGS_SPEC and rendered generically - to add a
setting: add a default in pf/settings.py, an entry in the spec, and read
cfg['your_key'] wherever it matters.

Run:  python app.py
"""

import os
import threading

from nicegui import run, ui

from pf import debuglog, history, models, prompts, settings
from pf.cleanup import cleanup, join_undesired, split_undesired
from pf.nai_client import GenParams, NAIClient, TokenExpired
from pf.prompts import enrich_message, refine_message, variation_message

prompts.ensure_files()
models.ensure_file()

SUBMIT_KEYS = {
    'enter': ('keydown.enter.exact.prevent', 'Enter sends - Shift+Enter for a new line'),
    'shift-enter': ('keydown.enter.shift.prevent', 'Shift+Enter sends - Enter for a new line'),
    'ctrl-enter': ('keydown.enter.ctrl.prevent', 'Ctrl+Enter sends - Enter for a new line'),
}

cfg = settings.load()
debuglog.set_level(cfg['log_level'])
state = {
    'notes': '',
    'main': '',        # editable main prompt shown in the output box
    'undesired': '',   # undesired tag line (without label), '' if none
}
last_output = cfg.get('last_output', '')
state['main'], state['undesired'] = split_undesired(last_output)
running = False
cancel_event = threading.Event()
stream_parts: list[str] = []
expansion_open: dict[str, bool] = {}   # remembers which settings sections are open


def client() -> NAIClient:
    return NAIClient(cfg['token'], cfg['endpoint'])


def persist() -> None:
    cfg['last_output'] = last_output
    settings.save(cfg)


def sync_last_output_from_editor() -> None:
    """Manual edits in the output box become the text Refine iterates on."""
    global last_output
    if running:
        return
    last_output = join_undesired(state['main'], state['undesired'])
    update_badge()


def active_family() -> str:
    return prompts.family_of(cfg['prompt'])


def update_badge() -> None:
    main = state['main'].strip()
    if not main:
        badge.text = ''
        return
    if active_family() == 'tags':
        n = len([t for t in main.replace('|', ',').split(',') if t.strip()])
        target = int(cfg['tag_target'])
        badge.text = f'{n} tags · target {target}'
    else:
        n = len(main.split())
        target = int(cfg['krea_words'])
        badge.text = f'{n} words · target {target}'
    off_target = n > target * 1.3 or n < target * 0.4
    badge.classes(replace='text-xs px-2 py-0.5 rounded self-center '
                  + ('bg-amber-200 text-amber-900' if off_target
                     else 'bg-green-200 text-green-900'))


# ---------------------------------------------------------------------------
# Generation
# ---------------------------------------------------------------------------

def on_delta(chunk: str) -> None:
    if not stream_parts:
        debuglog.log('first token received', 'verbose')
    stream_parts.append(chunk)  # worker thread: buffer only, UI reads via timer


def poll_stream() -> None:
    if running and stream_parts:
        output_box.value = ''.join(stream_parts)


async def do_run(kind: str) -> None:
    global running, last_output
    if running:
        status.text = 'Already running.'
        return
    idea = (cfg.get('idea') or '').strip()
    if not idea:
        status.text = 'Write an idea first.'
        return
    if not cfg['token']:
        status.text = 'No API token - open Settings (menu icon) and paste one.'
        return
    if kind != 'fresh' and not last_output:
        status.text = 'Nothing to iterate on yet - Generate first.'
        return

    running = True
    cancel_event.clear()
    stream_parts.clear()
    family = active_family()
    cleanup_mode = 'nai' if family == 'tags' else 'krea'
    notes = state['notes'].strip()
    output_box.value = ''
    status.text = {'variation': 'Rerolling...', 'refine': 'Refining...',
                   'enrich': 'Adding detail...'}.get(kind, 'Building...')
    stop_button.visible = True

    debuglog.log(f'{kind}: prompt={cfg["prompt"]} family={family} model={cfg["model"]} '
                 f'appends={selected_appends(family)}', 'basic')

    system = prompts.build_system(cfg['prompt'], cfg['active_appends'],
                                  {'tag_target': int(cfg['tag_target']),
                                   'word_target': int(cfg['krea_words'])})
    max_tokens = int(cfg['max_tokens_natural'] if family == 'natural'
                     else cfg['max_tokens_tags'])
    debuglog.log('system prompt built', 'verbose', system)

    messages = [
        {'role': 'system', 'content': system},
        {'role': 'user', 'content': 'Idea:\n' + idea},
    ]
    if kind != 'fresh':
        messages.append({'role': 'assistant', 'content': last_output})
        followup = {'variation': variation_message,
                    'enrich': enrich_message}.get(kind, refine_message)
        messages.append({'role': 'user', 'content': followup(notes)})

    params = GenParams(
        model=cfg['model'],
        max_tokens=max_tokens,
        temperature={'variation': 1.0, 'enrich': 0.9}.get(kind, 0.75),
    )

    try:
        result = await run.io_bound(client().chat, messages, params, on_delta, cancel_event)
        text = cleanup(result.text, cleanup_mode)
        debuglog.log(f'cleanup: {len(result.text)} raw -> {len(text)} chars', 'verbose')
        if not text:
            debuglog.log('empty response after cleanup', 'basic')
            status.text = 'Empty response, try again.'
            return
        last_output = text
        state['main'], state['undesired'] = split_undesired(text)
        output_box.value = state['main']
        update_badge()
        tokens = result.usage.get('completion_tokens')
        stats = f'{result.model} · {result.seconds:.1f}s' \
                + (f' · {tokens} tok' if tokens else '')
        done = 'Stopped early - partial result.' if result.stopped else \
            ('Tags ready.' if family == 'tags' else 'Natural language prompt ready.')
        status.text = f'{done}  ({stats})'
        debuglog.log(f'done: {stats}', 'basic')
        # Notes are one-shot: applied by a refine/variation/enrich, then cleared.
        if kind != 'fresh' and notes:
            state['notes'] = ''
        persist()
        try:
            history.append({'prompt': cfg['prompt'], 'mode': cleanup_mode,
                            'model': params.model, 'kind': kind,
                            'idea': idea, 'notes': notes, 'output': text})
        except OSError:
            pass
    except TokenExpired:
        debuglog.log('token rejected (401)', 'basic')
        status.text = 'Token rejected (401). Grab a fresh persistent token in NovelAI account settings.'
    except Exception as e:  # noqa: BLE001 - surface everything, never die silently
        debuglog.log(f'error: {type(e).__name__}: {e}', 'basic')
        status.text = f'Failed: {e}'
    finally:
        running = False
        stop_button.visible = False


def stop_generation() -> None:
    cancel_event.set()
    debuglog.log('stop requested', 'basic')
    status.text = 'Stopping...'


def flash_copied(button: ui.button) -> None:
    button.props('icon=check')
    ui.notify('Copied', type='positive', timeout=1200)
    ui.timer(1.2, lambda: button.props('icon=content_copy'), once=True)


async def copy_main() -> None:
    if not state['main'].strip():
        status.text = 'Nothing to copy yet.'
        return
    ui.clipboard.write(state['main'].strip())
    flash_copied(copy_main_btn)


async def copy_undesired() -> None:
    if state['undesired'].strip():
        ui.clipboard.write(state['undesired'].strip())
        flash_copied(copy_und_btn)


# ---------------------------------------------------------------------------
# History
# ---------------------------------------------------------------------------

def load_history_entry(entry: dict) -> None:
    global last_output
    cfg['idea'] = entry.get('idea', '')
    last_output = entry.get('output', '')
    state['main'], state['undesired'] = split_undesired(last_output)
    output_box.value = state['main']
    prompt_id = entry.get('prompt') or ('krea' if entry.get('mode') == 'krea' else 'nai')
    if prompt_id in prompts.system_options():
        set_prompt(prompt_id)  # updates badge + refreshes the prompt section
    else:
        update_badge()
    persist()
    history_dialog.close()
    status.text = 'Loaded from history - Refine/Variation/Add detail continue from it.'


def open_history() -> None:
    history_list.clear()
    entries = history.load()
    with history_list:
        if not entries:
            ui.label('Nothing here yet - every generation is saved automatically.') \
                .classes('opacity-60')
        for entry in entries:
            with ui.row().classes('w-full items-center gap-2 border-b py-1 no-wrap'):
                ui.label(entry['ts'][5:16].replace('T', ' ')).classes('text-xs opacity-60 shrink-0')
                ui.label('NL' if entry.get('mode') == 'krea' else 'tags') \
                    .classes('text-xs shrink-0 px-1 rounded bg-blue-100 text-blue-900') \
                    .tooltip(entry.get('prompt', ''))
                ui.label((entry.get('idea') or '')[:60]).classes('text-sm grow truncate')
                ui.button('Load', on_click=lambda e=entry: load_history_entry(e)) \
                    .props('dense flat size=sm')
                ui.button(icon='delete',
                          on_click=lambda e=entry: (history.delete(e.get('id', '')), open_history())) \
                    .props('dense flat size=sm color=negative')
    history_dialog.open()


async def probe_endpoints() -> None:
    if not cfg['token']:
        status.text = 'No API token - paste one above first.'
        return
    status.text = 'Probing endpoints...'
    debuglog.log(f'probe endpoints, model={cfg["model"]}', 'basic')
    results = await run.io_bound(client().probe, cfg['model'])
    probe_log.clear()
    for r in results:
        probe_log.push(str(r))
    debuglog.log('probe results', 'verbose', results)
    status.text = 'Probe done - see the log in Settings.'


# ---------------------------------------------------------------------------
# Settings drawer: declarative spec + generic renderer
# ---------------------------------------------------------------------------

dark = ui.dark_mode()


def apply_theme() -> None:
    {'light': dark.disable, 'dark': dark.enable}.get(cfg['theme'], dark.auto)()


def refresh_inputs() -> None:
    idea_area.refresh()
    notes_area.refresh()


# Each item: key (cfg key), type, label, and per-type extras. `help` renders
# as a caption under the control; `on_change` runs after the value persists.
SETTINGS_SPEC = [
    ('Generation', 'auto_awesome', True, [
        dict(key='model', type='select', label='Model', options=models.options(),
             help_fn=models.description),
        dict(key='tag_target', type='slider', label='Tag target', min=10, max=60, step=1,
             suffix=' tags', on_change=update_badge,
             help='Soft goal for NAI tag mode - the model may go over when needed.'),
        dict(key='krea_words', type='slider', label='Word target', min=50, max=300, step=10,
             suffix=' words', on_change=update_badge,
             help='Soft goal for natural language mode.'),
    ]),
    ('Limits', 'speed', False, [
        dict(key='max_tokens_tags', type='number', label='Max tokens - tag mode',
             min=150, max=2000, help='Hard response ceiling. Headroom, not a target.'),
        dict(key='max_tokens_natural', type='number', label='Max tokens - natural language',
             min=150, max=2000, help='Hard response ceiling for natural language mode.'),
    ]),
    ('Input', 'keyboard', False, [
        dict(key='submit_key', type='select', label='Send generation with',
             options={'enter': 'Enter (Shift+Enter = newline)',
                      'shift-enter': 'Shift+Enter',
                      'ctrl-enter': 'Ctrl+Enter'},
             on_change=refresh_inputs,
             help='Applies to the idea box (Generate) and the notes box (Refine).'),
    ]),
    ('Appearance', 'palette', False, [
        dict(key='theme', type='select', label='Theme',
             options={'auto': 'Auto (follow system)', 'light': 'Light', 'dark': 'Dark'},
             on_change=apply_theme),
    ]),
    ('Connection', 'cloud', False, [
        dict(key='token', type='password', label='Persistent API token',
             help='NovelAI account settings -> Get Persistent API Token. Expires roughly monthly.'),
        dict(key='endpoint', type='text', label='Endpoint override',
             help='Leave empty for the confirmed default endpoint.'),
    ]),
    ('Debug', 'bug_report', False, [
        dict(key='log_level', type='select', label='Logging',
             options={'off': 'Off', 'basic': 'Basic (steps + errors)',
                      'verbose': 'Verbose (robust: full request/response)'},
             on_change=lambda: on_log_level_changed(),
             help='Basic logs each step; Verbose also saves full request/response '
                  'payloads to the logs/ folder. Shows a live log at the bottom of the window.'),
    ]),
]


def on_log_level_changed() -> None:
    debuglog.set_level(cfg['log_level'])
    debuglog.log(f'logging set to {cfg["log_level"]}', 'basic')


def render_setting(item: dict) -> None:
    key, kind = item['key'], item['type']

    def changed(e) -> None:
        value = e.value
        if kind in ('slider', 'number'):
            value = int(value if value is not None else settings.DEFAULTS[key])
        if kind == 'text':
            value = str(value or '').strip()
        cfg[key] = value
        persist()
        debuglog.log(f'setting {key} = '
                     + ('(hidden)' if key == 'token' else repr(value)), 'verbose')
        if item.get('on_change'):
            item['on_change']()

    if kind == 'select':
        ui.select(item['options'], value=cfg[key], label=item['label'],
                  on_change=changed).props('outlined dense options-dense').classes('w-full')
    elif kind == 'slider':
        ui.label().bind_text_from(
            cfg, key,
            lambda v, i=item: f"{i['label']}: {int(v)}{i.get('suffix', '')}"
        ).classes('text-sm mt-1')
        ui.slider(min=item['min'], max=item['max'], step=item['step'],
                  value=int(cfg[key]), on_change=changed)
    elif kind == 'number':
        ui.number(item['label'], value=int(cfg[key]), min=item['min'], max=item['max'],
                  on_change=changed).props('outlined dense').classes('w-full')
    elif kind == 'switch':
        ui.switch(item['label'], value=bool(cfg[key]), on_change=changed)
    elif kind in ('text', 'password'):
        ui.input(item['label'], value=str(cfg[key]), password=(kind == 'password'),
                 password_toggle_button=(kind == 'password'),
                 on_change=changed).props('outlined dense').classes('w-full')

    if item.get('help_fn'):
        # Caption reacts to the current value (e.g. the selected model's blurb);
        # an empty string renders as no caption.
        ui.label().bind_text_from(cfg, key, item['help_fn']) \
            .classes('text-xs opacity-60 leading-tight')
    elif item.get('help'):
        ui.label(item['help']).classes('text-xs opacity-60 leading-tight')


def restore_defaults() -> None:
    keep = {k: cfg[k] for k in ('token', 'endpoint', 'idea', 'last_output', 'prompt')}
    cfg.update(settings.DEFAULTS)
    cfg.update(keep)
    persist()
    apply_theme()
    refresh_inputs()
    update_badge()
    settings_body.refresh()
    ui.notify('Settings restored to defaults (token and idea kept).', type='info')


def selected_appends(family: str) -> list[str]:
    """Active append ids that apply to this family, in the user's chosen order."""
    ids = {ap['id'] for ap in prompts.append_items(family)}
    return [a for a in cfg['active_appends'] if a in ids]


def add_append(append_id: str) -> None:
    if append_id and append_id not in cfg['active_appends']:
        cfg['active_appends'] = cfg['active_appends'] + [append_id]
        persist()
        prompts_section_body.refresh()


def remove_append(append_id: str) -> None:
    cfg['active_appends'] = [a for a in cfg['active_appends'] if a != append_id]
    persist()
    prompts_section_body.refresh()


def move_append(family: str, append_id: str, delta: int) -> None:
    sel = selected_appends(family)
    i = sel.index(append_id)
    j = i + delta
    if 0 <= j < len(sel):
        sel[i], sel[j] = sel[j], sel[i]
        others = [a for a in cfg['active_appends'] if a not in sel]
        cfg['active_appends'] = sel + others
        persist()
        prompts_section_body.refresh()


@ui.refreshable
def prompts_section_body() -> None:
    """Contents of the Prompts settings section. Refreshing only this leaves the
    surrounding expansion open (the collapse bug was full-drawer refreshes)."""
    opts = prompts.system_options()
    if cfg['prompt'] not in opts:
        cfg['prompt'] = next(iter(opts))
    ui.select(opts, value=cfg['prompt'], label='Prompt (generation mode)',
              on_change=lambda e: set_prompt(e.value)) \
        .props('outlined dense').classes('w-full')
    family = active_family()
    ui.label('Diffusion tags output' if family == 'tags' else 'Natural language output') \
        .classes('text-xs opacity-60')

    all_appends = prompts.append_items(family)
    name_of = {ap['id']: ap['name'] for ap in all_appends}
    sel = selected_appends(family)

    ui.label('Append prompts added to this prompt, top to bottom:') \
        .classes('text-sm font-semibold mt-3')
    with ui.column().classes('w-full gap-1 max-h-56 overflow-auto border rounded p-1'):
        if not sel:
            ui.label('None selected.').classes('text-xs opacity-60 p-1')
        for idx, aid in enumerate(sel):
            with ui.row().classes('w-full items-center gap-1 no-wrap'):
                ui.label(f'{idx + 1}.').classes('text-xs opacity-50 shrink-0')
                ui.label(name_of.get(aid, aid)).classes('text-sm grow truncate')
                ui.button(icon='keyboard_arrow_up',
                          on_click=lambda a=aid: move_append(family, a, -1)) \
                    .props('dense flat size=sm').set_enabled(idx > 0)
                ui.button(icon='keyboard_arrow_down',
                          on_click=lambda a=aid: move_append(family, a, 1)) \
                    .props('dense flat size=sm').set_enabled(idx < len(sel) - 1)
                ui.button(icon='close', on_click=lambda a=aid: remove_append(a)) \
                    .props('dense flat size=sm color=negative')

    available = {ap['id']: ap['name'] for ap in all_appends if ap['id'] not in sel}
    if available:
        ui.select(available, label='Add an append prompt', value=None,
                  on_change=lambda e: add_append(e.value)) \
            .props('outlined dense options-dense').classes('w-full')
    elif not all_appends:
        ui.label('No append prompts exist for this output type yet - '
                 'create one in the library below.').classes('text-xs opacity-60')

    ui.button('Manage prompt library', icon='edit_note',
              on_click=open_prompt_manager).props('outline').classes('mt-2')


def render_prompts_section() -> None:
    title = 'Prompts'
    with ui.expansion(title, icon='description', value=expansion_open.get(title, True),
                      on_value_change=lambda e: expansion_open.__setitem__(title, e.value)) \
            .classes('w-full'):
        prompts_section_body()


@ui.refreshable
def settings_body() -> None:
    global probe_log
    for title, icon, start_open, items in SETTINGS_SPEC:
        with ui.expansion(title, icon=icon, value=expansion_open.get(title, start_open),
                          on_value_change=lambda e, t=title: expansion_open.__setitem__(t, e.value)) \
                .classes('w-full'):
            with ui.column().classes('w-full gap-1'):
                for item in items:
                    render_setting(item)
                if title == 'Connection':
                    ui.button('Probe endpoints', on_click=probe_endpoints).props('outline')
                    probe_log = ui.log(max_lines=12).classes('w-full h-32')
        if title == 'Generation':
            render_prompts_section()
    ui.separator().classes('my-2')
    ui.button('Restore defaults', icon='settings_backup_restore',
              on_click=lambda: confirm_restore.open()).props('flat color=negative')


# ---------------------------------------------------------------------------
# Compose / iterate inputs (rebuilt when the submit key changes)
# ---------------------------------------------------------------------------

@ui.refreshable
def idea_area() -> None:
    event, hint = SUBMIT_KEYS.get(cfg['submit_key'], SUBMIT_KEYS['enter'])
    box = ui.textarea(placeholder='she stops at the top of the stairwell, '
                                  'hand still on the rail, listening') \
        .props('outlined input-class=h-64').classes('w-full')
    box.bind_value(cfg, 'idea')
    box.on(event, lambda: do_run('fresh'))
    ui.label(f'{hint} · generates from the idea').classes('text-xs opacity-50')


@ui.refreshable
def notes_area() -> None:
    event, hint = SUBMIT_KEYS.get(cfg['submit_key'], SUBMIT_KEYS['enter'])
    box = ui.textarea(placeholder='colder light, pull back to a wide shot / the background') \
        .props('outlined autogrow').classes('w-full')
    box.bind_value(state, 'notes')
    box.on(event, lambda: do_run('refine'))
    ui.label(f'{hint} · refines with the notes').classes('text-xs opacity-50')


def set_prompt(prompt_id: str) -> None:
    cfg['prompt'] = prompt_id
    persist()
    update_badge()
    prompts_section_body.refresh()  # applicable appends depend on the prompt's family


# ---------------------------------------------------------------------------
# Prompt library manager
# ---------------------------------------------------------------------------

editor_state = {'kind': 'system', 'id': None}


def _norm_family(kind: str, fam: str) -> str | None:
    if kind == 'system':
        return fam if fam in ('tags', 'natural') else 'natural'
    return fam if fam in ('tags', 'natural') else None


def open_prompt_manager() -> None:
    manager_list.refresh()
    prompt_manager_dialog.open()


def export_prompt(kind: str, entry_id: str) -> None:
    ui.clipboard.write(prompts.entry_text(kind, entry_id))
    ui.notify('Prompt text copied to clipboard.', type='positive')


def delete_prompt(kind: str, entry_id: str) -> None:
    if not prompts.delete_entry(kind, entry_id):
        ui.notify('Built-in prompts cannot be deleted - use Reset.', type='warning')
        return
    if kind == 'appends' and entry_id in cfg['active_appends']:
        cfg['active_appends'] = [a for a in cfg['active_appends'] if a != entry_id]
    persist()
    manager_list.refresh()
    prompts_section_body.refresh()  # rebuilds the prompt dropdown + append list
    update_badge()
    ui.notify('Deleted.', type='info')


def reset_prompt(kind: str, entry_id: str) -> None:
    prompts.reset_builtin(kind, entry_id)
    ui.notify('Reset to the shipped default.', type='info')


def open_editor(kind: str, entry_id: str | None) -> None:
    editor_state['kind'] = kind
    editor_state['id'] = entry_id
    entry = prompts.find_entry(kind, entry_id) if entry_id else None
    editor_title.text = ('Edit ' if entry_id else 'New ') \
        + ('full prompt' if kind == 'system' else 'append prompt')
    editor_name.value = entry['name'] if entry else ''
    editor_family.value = (entry.get('family') or 'any') if entry \
        else ('tags' if kind == 'system' else 'any')
    editor_text.value = prompts.entry_text(kind, entry_id) if entry_id else ''
    editor_hint.text = ('Use $tag_target / $word_target for the length goal, and $appends '
                        'where append prompts should be inserted.') if kind == 'system' \
        else 'This text is added to the end of the instructions when the append is active.'
    editor_dialog.open()


def save_editor() -> None:
    kind = editor_state['kind']
    name = editor_name.value.strip()
    if not name:
        ui.notify('Give the prompt a name.', type='warning')
        return
    fam = _norm_family(kind, editor_family.value)
    if editor_state['id']:
        prompts.update_entry(kind, editor_state['id'], name=name,
                             text=editor_text.value, family=fam)
    else:
        prompts.create_entry(kind, name, editor_text.value, family=fam or 'natural')
    editor_dialog.close()
    manager_list.refresh()
    prompts_section_body.refresh()
    ui.notify('Saved.', type='positive')


# ---------------------------------------------------------------------------
# Debug log panel
# ---------------------------------------------------------------------------

log_seen = {'n': 0}


def poll_log() -> None:
    buf = debuglog.lines
    if len(buf) > log_seen['n']:
        for line in buf[log_seen['n']:]:
            log_panel.push(line)
        log_seen['n'] = len(buf)


def clear_log() -> None:
    debuglog.clear()
    log_seen['n'] = 0
    log_panel.clear()
    debuglog.log('log cleared', 'basic')


def open_logs_folder() -> None:
    debuglog.LOG_DIR.mkdir(exist_ok=True)
    try:
        os.startfile(str(debuglog.LOG_DIR))  # Windows; opens Explorer
    except (OSError, AttributeError) as e:
        ui.notify(f'Could not open folder: {e}', type='warning')


# ---------------------------------------------------------------------------
# Page
# ---------------------------------------------------------------------------

with ui.left_drawer(value=False, fixed=True).props('overlay bordered width=340') \
        .classes('p-4') as drawer:
    ui.label('Settings').classes('text-lg font-semibold')
    settings_body()

with ui.header().classes('items-center px-4 py-2'):
    ui.button(icon='menu', on_click=drawer.toggle).props('flat round color=white')
    ui.label('Prompt Forge').classes('text-lg font-semibold')
    ui.space()
    ui.button('History', icon='history', on_click=open_history).props('flat color=white')

with ui.row().classes('w-full no-wrap gap-6 p-4 items-stretch'):

    # left: compose
    with ui.column().classes('w-[360px] shrink-0 gap-2'):
        ui.label('Idea').classes('text-base font-semibold')
        ui.label('Rough or detailed - a moment or an action.').classes('text-sm opacity-70')
        idea_area()
        ui.label().bind_text_from(
            cfg, 'prompt',
            lambda p: 'Mode: ' + prompts.system_options().get(p, p)
            + '  ·  change in Settings → Prompts') \
            .classes('text-xs opacity-60')
        ui.button('Generate', on_click=lambda: do_run('fresh')) \
            .props('unelevated size=lg').classes('w-full')

    # right: result + iteration
    with ui.column().classes('grow gap-2 min-w-0'):
        with ui.row().classes('w-full items-center gap-2'):
            ui.label('Result').classes('text-base font-semibold')
            badge = ui.label('').classes('text-xs px-2 py-0.5 rounded self-center')
            ui.space()
            stop_button = ui.button('Stop', icon='stop', on_click=stop_generation) \
                .props('flat color=negative')
            stop_button.visible = False
            copy_main_btn = ui.button('Copy', icon='content_copy', on_click=copy_main) \
                .props('outline')
        status = ui.label('Ready.').classes('text-sm opacity-70')
        output_box = ui.textarea(value=state['main']) \
            .props('outlined autogrow input-class="font-mono text-sm min-h-[16rem]"') \
            .classes('w-full')
        output_box.bind_value(state, 'main')
        output_box.on_value_change(lambda e: sync_last_output_from_editor())

        with ui.row().classes('w-full items-center gap-2') as undesired_row:
            ui.label('Undesired:').classes('text-sm font-semibold shrink-0')
            ui.label('').classes('text-sm font-mono grow truncate') \
                .bind_text_from(state, 'undesired')
            copy_und_btn = ui.button(icon='content_copy', on_click=copy_undesired) \
                .props('dense flat size=sm')
        undesired_row.bind_visibility_from(state, 'undesired', backward=bool)

        ui.label('Notes - what to change (Refine) or what to detail (Add detail).') \
            .classes('text-sm opacity-70 mt-2')
        notes_area()
        with ui.row().classes('w-full gap-2'):
            ui.button('Refine', on_click=lambda: do_run('refine')).props('outline')
            ui.button('Variation', on_click=lambda: do_run('variation')).props('outline')
            ui.button('Add detail', on_click=lambda: do_run('enrich')).props('outline') \
                .tooltip('Invent hyper-specific new detail about whatever the notes box names '
                         '(e.g. "the background", "her armor"). Unlike Refine, it may add new things.')

with ui.column().classes('w-full px-4 pb-4 gap-1') as debug_area:
    with ui.row().classes('w-full items-center gap-2'):
        ui.label('Debug log').classes('text-sm font-semibold')
        ui.label().bind_text_from(cfg, 'log_level', lambda v: f'({v})') \
            .classes('text-xs opacity-60')
        ui.space()
        ui.button('Open logs folder', icon='folder_open', on_click=open_logs_folder) \
            .props('flat dense size=sm')
        ui.button('Clear', icon='delete_sweep', on_click=clear_log).props('flat dense size=sm')
    log_panel = ui.log(max_lines=500).classes('w-full h-48 font-mono text-xs')
debug_area.bind_visibility_from(cfg, 'log_level', backward=lambda v: v != 'off')

with ui.dialog() as history_dialog, ui.card().classes('w-[640px] max-w-full'):
    ui.label('History').classes('text-lg font-semibold')
    history_list = ui.column().classes('w-full max-h-[60vh] overflow-auto')

with ui.dialog() as confirm_restore, ui.card():
    ui.label('Restore all settings to defaults?').classes('font-semibold')
    ui.label('Your token, endpoint, idea and current output are kept.') \
        .classes('text-sm opacity-70')
    with ui.row().classes('w-full justify-end gap-2'):
        ui.button('Cancel', on_click=confirm_restore.close).props('flat')
        ui.button('Restore', on_click=lambda: (confirm_restore.close(), restore_defaults())) \
            .props('color=negative')


@ui.refreshable
def manager_list() -> None:
    cat = prompts.load_catalog()
    for kind, heading in (('system', 'Full prompts (generation modes)'),
                          ('appends', 'Append prompts')):
        ui.label(heading).classes('text-sm font-semibold mt-3')
        for e in cat[kind]:
            with ui.row().classes('w-full items-center gap-2 border-b py-1 no-wrap'):
                ui.label(e['name']).classes('text-sm grow truncate')
                ui.label(e.get('family') or 'any').classes('text-xs opacity-60 shrink-0')
                if e.get('builtin'):
                    ui.label('built-in').classes('text-xs opacity-50 shrink-0')
                ui.button(icon='edit', on_click=lambda k=kind, i=e['id']: open_editor(k, i)) \
                    .props('dense flat size=sm').tooltip('Edit')
                ui.button(icon='content_copy',
                          on_click=lambda k=kind, i=e['id']: export_prompt(k, i)) \
                    .props('dense flat size=sm').tooltip('Copy text (export)')
                if e.get('builtin'):
                    ui.button(icon='restore', on_click=lambda k=kind, i=e['id']: reset_prompt(k, i)) \
                        .props('dense flat size=sm').tooltip('Reset to default')
                else:
                    ui.button(icon='delete', on_click=lambda k=kind, i=e['id']: delete_prompt(k, i)) \
                        .props('dense flat size=sm color=negative').tooltip('Delete')
    with ui.row().classes('w-full gap-2 mt-3'):
        ui.button('New full prompt', icon='add',
                  on_click=lambda: open_editor('system', None)).props('outline')
        ui.button('New append prompt', icon='add',
                  on_click=lambda: open_editor('appends', None)).props('outline')


with ui.dialog() as prompt_manager_dialog, ui.card().classes('w-[680px] max-w-full'):
    ui.label('Prompt library').classes('text-lg font-semibold')
    ui.label('Full prompts are the instructions sent in place of the built-ins. '
             'Append prompts are added at the end (like emphasis / undesired). '
             'Copy exports a prompt; paste into a new one to import.') \
        .classes('text-xs opacity-60')
    with ui.column().classes('w-full max-h-[60vh] overflow-auto'):
        manager_list()
    with ui.row().classes('w-full justify-end'):
        ui.button('Close', on_click=prompt_manager_dialog.close).props('flat')

with ui.dialog() as editor_dialog, ui.card().classes('w-[680px] max-w-full'):
    editor_title = ui.label('Edit prompt').classes('text-lg font-semibold')
    editor_name = ui.input('Name').props('outlined dense').classes('w-full')
    editor_family = ui.select({'tags': 'Tags (one-line Danbooru output)',
                               'natural': 'Natural language (paragraph)',
                               'any': 'Any output (append prompts only)'},
                              label='Output family').props('outlined dense').classes('w-full')
    editor_hint = ui.label('').classes('text-xs opacity-60')
    editor_text = ui.textarea('Prompt text') \
        .props('outlined input-class="font-mono text-xs h-72"').classes('w-full')
    with ui.row().classes('w-full justify-end gap-2'):
        ui.button('Cancel', on_click=editor_dialog.close).props('flat')
        ui.button('Save', on_click=save_editor).props('unelevated')

ui.timer(0.15, poll_stream)
ui.timer(0.3, poll_log)
apply_theme()
update_badge()
debuglog.log('app started', 'basic')

ui.run(native=True, window_size=(1100, 760), title='Prompt Forge', reload=False)
