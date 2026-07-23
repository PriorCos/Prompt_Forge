# Prompt Forge — standalone edition

True native desktop app (Python + **PySide6/Qt** — no local web server):
idea → NAI Diffusion V4.5 tags or Krea 2 natural-language prompt via GLM-4.6 /
Xialong, with refine, variation, add-detail, one-shot notes, prompt catalog,
history, and debug logging. Styled by `pf/qss.py` — flat modern theme, one
violet accent, light/dark/auto following Windows dark mode.

The previous NiceGUI implementation is kept as `legacy_nicegui_app.py`
(needs `pip install nicegui[native]` to run); `app.py` is the Qt app.
The UI layer changed; every `pf/` module is shared by both unchanged.

Window chrome: the app is **frameless** — the top row is a custom title bar
(☰ settings, title, mode chip, History, minimize, maximize/restore, close).
Drag the window by that row; double-click it or use the maximize button to
maximize/restore (taskbar-aware via `WM_GETMINMAXINFO`). Resize from **any
edge or corner** — on Windows, `nativeEvent` handles `WM_NCHITTEST` so the OS
does real native resizing with proper cursors; the bottom-right `QSizeGrip` is
kept as a visible affordance (and the fallback on non-Windows). The settings
panel **overlays** the main page (anchored below the header) rather than
pushing content, so the ☰ button never moves. It **slides in** from the left
with a fading scrim behind it; close with **Esc** or by clicking the scrim.

**Animations** (all code-driven — Qt QSS has no CSS transitions), governed by
Settings → Motion: "Disable animations" (master off; stored as `animations`,
default on) and "Reduce motion" (keeps gentle fades, drops spatial motion).
Helpers `anim_on()` / `motion_on()` / `fade()` enforce this everywhere; every
duration passes through `dur()` (× `ANIM_SCALE`, one speed knob). Effects:
settings slide + scrim fade, dropdown chevron spin, accordion expand/collapse
(animated maxHeight — clamped to 0 before showing to avoid a flash),
maximize/restore (geometry animation to/from the work area; the **OS maximized
state is the single source of truth** so Win+Up, edge-snap, double-click and the
button all stay in sync via `changeEvent`), minimize (fade-out then
`showMinimized`), output
fade-in on completion, status cross-fade, dialog + launch fade-in, theme pulse,
copy pulse. Deferred: per-button scale, badge colour tween, animated append
reorder, streaming caret. Window size/position/maximized
state are remembered in `config.json` (`window` key) and restored on launch
(off-screen positions are ignored). Streaming output **auto-scrolls** to follow
new text unless you've scrolled up. All four action buttons disable while a
generation runs.

The in-app `.naiscript` ladder lives in `../prompt_forge/` and remains the
reference for prompt wording — the two are kept in lockstep.

## Setup

Use **Python 3.12** — 3.14 is too new, several common wheels (pillow, regex)
don't build on it, which also blocks the reference SDK.

```
py -3.12 -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python app.py
```

Then, in the app's **Settings** panel, paste your token: NovelAI → account
settings → **Get Persistent API Token**. Tokens expire roughly monthly; a 401
in the status line means paste a fresh one. The token is stored in plain text
in `config.json` next to the app — keep this folder private.

## Building a standalone .exe

Bundles Python + PySide6 + httpx + the app into one double-clickable
executable (no Python install needed to run it):

```
.venv\Scripts\pip install pyinstaller
.venv\Scripts\pyinstaller --noconfirm --onefile --windowed --name PromptForge --exclude-module nicegui --exclude-module tkinter app.py
```

Output: `dist\PromptForge.exe` (~large; PySide6 is bundled). It is **portable** -
`config.json`, `logs/`, `history.jsonl` and the prompt catalog are written next
to the .exe (see `pf/paths.py`, which uses the exe folder when frozen). Keep the
.exe in a user-writable folder (Desktop, Downloads) rather than Program Files so
it can write those files. `build/`, `dist/` and `*.spec` are gitignored.

## Endpoint — CONFIRMED (2026-07-22)

Found via NovelAI's own OpenAPI spec (https://text.novelai.net/docs/doc.json,
"Omegalaser API"). The chat API is OpenAI-compatible under an `/oa` prefix:

- `POST https://text.novelai.net/oa/v1/chat/completions` — generation
- `GET  https://text.novelai.net/oa/v1/models` — model list, free; used by
  the **Probe endpoints** button to verify the token and the `glm-4-6` /
  `xialong-v1` strings without spending tokens
- Auth: `Authorization: Bearer <persistent token>`; both endpoints answer
  401 (not 404) without a valid token — verified from this machine
- Schema is OpenAI chat-completions plus NovelAI extensions, notably
  `enable_thinking` (kept **off** so output stays pure prompt text) and
  `generation_prefix` (assistant prefill — future steering lever)

### The empty-text gotcha (why we stream)

NovelAI's text backend is **token-native**: the OpenAPI spec's `use_string`
flag toggles between returning *"a string containing the detokenized text"*
and *"the packed representation of the tokens."* The non-streaming
`/oa/v1/chat/completions` path returns the token form — `choices[0].text` is
`""` while `choices[0].token_ids` holds the real (GLM-tokenizer) output, which
is why an early build got null results. The client therefore uses **streaming**
(`stream: true`, SSE), the same path NovelAI's own frontend uses, which
delivers decoded text deltas. The raw last SSE event is still saved to
`last_response.json` for diagnosis.

Note: the Aedial `novelai-api` SDK does **not** cover this endpoint — it
targets the classic `/ai/generate` API (Kayra/Clio/Erato) and has no GLM,
`/oa/`, or chat-completions support. Its tokenizers are for those older
models, so it cannot decode GLM `token_ids` either.

## Layout

- `app.py` — two-pane native window. Left: idea + a read-only mode indicator
  + Generate. Right: streamed result (editable — manual edits feed straight
  into what Refine iterates on), UNDESIRED split out with its own copy button,
  tag/word count badge vs target, one-shot notes + Refine / Variation /
  **Add detail** (enrich). Settings drawer (menu icon): declarative,
  registry-driven accordion sections. The **Prompts** section holds the active
  prompt (generation-mode) dropdown, an ordered append-prompt picker (add via
  dropdown, reorder with up/down, remove) whose order is the insertion order,
  and the prompt-library manager. Its inner content is a separate
  `@ui.refreshable` (`prompts_section_body`) so add/move/remove refresh just
  that block and the expansion stays open; expansions also remember open state
  across a full drawer refresh (`expansion_open`). Other sections: dropdowns
  for model / submit key / theme, sliders for targets, restore-defaults.
  History auto-saves to `history.jsonl`. Config migrates automatically
  (`use_xialong`→`model`, `dark`→`theme`, `mode`→`prompt`,
  emphasis/undesired switches→`active_appends`); legacy keys dropped on save.
- `prompts/` — the **prompt catalog** (what we SEND to the model). `catalog.json`
  is the manifest (name / file / kind / family). `system/*.txt` are full
  prompts, selectable as generation modes; `appends/*.txt` are fragments added
  to the end of the instructions (emphasis, undesired, and any you make);
  `messages/*.txt` are the refine/variation/enrich templates. Each system
  prompt declares a `family`: `tags` (one-line, NAI cleanup, tag target) or
  `natural` (paragraph, Krea cleanup, word target). `$tag_target`/`$word_target`
  fill the length goal; `$appends` marks where appends go. Re-read every
  generation. Manage in-app via Settings → Prompts → Manage prompt library
  (create / edit / delete / reset built-ins / copy-to-export).
- `models.json` — model registry ({id: {name, description}}). The model
  dropdown and its caption read from here; add your own model as a data edit.
- `presets.json` — **sampling presets**: named, swappable bundles of generation
  parameters (temperature, top-p, top-k, min-p, frequency/presence penalty,
  seed, and NovelAI's unified-sampler params). Model-agnostic. Edit inline in
  Settings → Sampling (built-ins locked; Duplicate to customize; New/Delete).
  `pf/presets.py`'s `PARAMS` list is the single source of truth for which knobs
  exist; off-by-default knobs (top-k/min-p/seed/unified) are only sent when set.
  Variation nudges the preset's temperature up slightly for divergence.
- `pf/prompts.py` — catalog + builder + CRUD helpers + message builders.
- `pf/models.py` — model registry loader.
- `pf/cleanup.py` — output post-processing (takes `family`: 'tags'/'natural')
  + UNDESIRED split/join. Dedup/`<END>` stripping is defensive insurance now
  (the chat endpoint terminates cleanly, unlike the old completions endpoint).
- `pf/nai_client.py` — streaming httpx client (SSE deltas, cancel event,
  usage stats), models list, probe.
- `pf/history.py` — JSONL history (append/load/delete by unique id).
- `pf/debuglog.py` — leveled debug log (`off`/`basic`/`verbose`), thread-safe
  ring buffer + `logs/prompt_forge.log`; verbose saves full request/response
  payloads as timestamped files in `logs/`. Enable in Settings → Debug; a live
  log panel shows at the bottom of the window, with Clear and Open-logs-folder.
  Verbose instruments nearly everything: request shape (roles/lengths, params),
  response headers, per-stream event/decode-error counts, finish_reason, usage,
  cleanup steps (think/END/dedup/undesired), history/prompt-library writes,
  settings-panel and mode changes, geometry, and full request/response blobs.
  `debuglog.exc()` logs message-at-basic + traceback-at-verbose from except
  blocks; a global `sys.excepthook` and Qt message handler capture anything
  uncaught. The API token is never logged.
- `pf/settings.py` — `config.json` persistence.
- `tests/` — offline tests: `python -m unittest discover tests`

## Parity with the script (v0.2.10)

Same: prompts, cleanup, one-shot notes semantics, soft length targets,
temperature 0.75 / variation 1.0, penalties, `<END>` sentinel + stop list,
GLM/Xialong toggle. Different: no token budget (that was a sandbox limit),
Enter-to-refine works, notes clear reliably, config lives in `config.json`.

## Roadmap (not in V1 by choice)

- Output safeguards pass (quality-tag scrub, per-tag dedupe, refusal detector,
  truncation detection) — mirror into the script when done
- NAI image generation (standard sizes are free on Opus) for idea → image
- Prompt history browser
