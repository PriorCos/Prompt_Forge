"""NovelAI text-generation client.

Endpoint CONFIRMED (2026-07-22) from NovelAI's own OpenAPI spec at
https://text.novelai.net/docs/doc.json ("Omegalaser API"):

    POST https://text.novelai.net/oa/v1/chat/completions   (OpenAI-compatible)
    GET  https://text.novelai.net/oa/v1/models             (list model ids, free)

Auth: `Authorization: Bearer <persistent token>` (account settings ->
"Get Persistent API Token"; expires roughly monthly, 401 = replace it).

The request schema is OpenAI chat-completions plus NovelAI extensions, the
notable ones being `enable_thinking` (GLM is a reasoning-capable model; we
keep it off so output stays pure prompt text) and `generation_prefix`
(assistant prefill - useful later for steering how a response opens).
"""

import json
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

import httpx

from pf import debuglog
from pf.paths import base_dir

BASE = 'https://text.novelai.net'
CHAT_URL = f'{BASE}/oa/v1/chat/completions'
MODELS_URL = f'{BASE}/oa/v1/models'

# The chat endpoint terminates turns properly, unlike the in-app completions
# endpoint the script uses. Only the explicit sentinel stays: the extra stop
# strings ('\n\n\n', 'Idea:', '```') the script needs can fire mid-reasoning
# here and kill the response before any content is produced.
DEFAULT_STOP = ['<END>']

# Raw JSON of the most recent chat response, for diagnosing bad generations.
LAST_RESPONSE_PATH = base_dir() / 'last_response.json'


class NAIError(Exception):
    pass


class TokenExpired(NAIError):
    pass


@dataclass
class GenParams:
    model: str = 'glm-4-6'
    max_tokens: int = 900
    temperature: float = 0.75
    top_p: float = 0.95
    top_k: int = 0
    min_p: float = 0.0
    frequency_penalty: float = 0.4
    presence_penalty: float = 0.3
    seed: int = -1
    unified_linear: float = 0.0
    unified_quadratic: float = 0.0
    unified_cubic: float = 0.0
    unified_increase_linear_with_entropy: bool = False
    enable_thinking: bool = False
    generation_prefix: str = ''


def make_params(model: str, max_tokens: int, p: dict) -> GenParams:
    """Build GenParams from a model, a token ceiling, and a preset param dict."""
    return GenParams(
        model=model, max_tokens=max_tokens,
        temperature=float(p.get('temperature', 0.75)),
        top_p=float(p.get('top_p', 0.95)),
        top_k=int(p.get('top_k', 0)),
        min_p=float(p.get('min_p', 0.0)),
        frequency_penalty=float(p.get('frequency_penalty', 0.4)),
        presence_penalty=float(p.get('presence_penalty', 0.3)),
        seed=int(p.get('seed', -1)),
        unified_linear=float(p.get('unified_linear', 0.0)),
        unified_quadratic=float(p.get('unified_quadratic', 0.0)),
        unified_cubic=float(p.get('unified_cubic', 0.0)),
        unified_increase_linear_with_entropy=bool(
            p.get('unified_increase_linear_with_entropy', False)),
    )


@dataclass
class GenResult:
    text: str
    seconds: float = 0.0
    usage: dict = field(default_factory=dict)
    model: str = ''
    stopped: bool = False  # True when the user cancelled mid-stream


class NAIClient:
    def __init__(self, token: str, endpoint: str = '', timeout: float = 120.0):
        self.token = token.strip()
        self.chat_url = endpoint.strip() or CHAT_URL
        self.timeout = timeout

    def _headers(self) -> dict:
        if not self.token:
            raise TokenExpired('No API token configured - paste one in Settings.')
        return {
            'Authorization': f'Bearer {self.token}',
            'Content-Type': 'application/json',
        }

    def _raise_for_status(self, resp: httpx.Response, context: str) -> None:
        if resp.status_code == 401:
            raise TokenExpired('401 Unauthorized - persistent token missing or expired.')
        if resp.status_code == 403:
            raise NAIError(f'403 Forbidden - model not allowed for your tier. {resp.text[:200]}')
        if resp.status_code == 429:
            raise NAIError('429 Rate limited - slow down and retry shortly.')
        if resp.status_code >= 400:
            raise NAIError(f'{context} -> HTTP {resp.status_code}: {resp.text[:400]}')

    def _payload(self, messages: list[dict], params: GenParams, stream: bool) -> dict:
        payload: dict = {
            'model': params.model,
            'messages': messages,
            'max_tokens': params.max_tokens,
            'temperature': params.temperature,
            'top_p': params.top_p,
            'frequency_penalty': params.frequency_penalty,
            'presence_penalty': params.presence_penalty,
            'stop': DEFAULT_STOP,
            'stream': stream,
            'enable_thinking': params.enable_thinking,
        }
        # Only send the "off-by-default" knobs when actually set, so leaving
        # them at their disabled value doesn't override a server default.
        if params.top_k > 0:
            payload['top_k'] = params.top_k
        if params.min_p > 0:
            payload['min_p'] = params.min_p
        if params.seed >= 0:
            payload['seed'] = params.seed
        for key in ('unified_linear', 'unified_quadratic', 'unified_cubic'):
            value = getattr(params, key)
            if value:
                payload[key] = value
        if params.unified_increase_linear_with_entropy:
            payload['unified_increase_linear_with_entropy'] = True
        if params.generation_prefix:
            payload['generation_prefix'] = params.generation_prefix
        return payload

    @staticmethod
    def _text_from_choice(c0: dict) -> str:
        """Pull generated text out of one streaming choice.

        Streaming yields choices[0].delta.content; the token-native path fills
        choices[0].text (and can leave token_ids while text is empty - the
        gotcha that forced streaming in the first place). Thinking models keep
        scratch work in reasoning_content, which we ignore here (cleanup strips
        it if it leaks into content)."""
        delta = c0.get('delta')
        if isinstance(delta, dict) and isinstance(delta.get('content'), str):
            return delta['content']
        if isinstance(c0.get('text'), str):
            return c0['text']
        return ''

    def chat(self, messages: list[dict], params: GenParams,
             on_delta: Optional[Callable[[str], None]] = None,
             cancel: Optional[threading.Event] = None) -> GenResult:
        """Generate via streaming SSE - the path NovelAI's own frontend uses.

        The non-streaming path returns raw token_ids with empty text on this
        endpoint; streaming delivers decoded text deltas.

        on_delta is called with each text chunk as it arrives (worker thread).
        Setting the cancel event aborts the stream; whatever text arrived so
        far is returned with stopped=True.
        """
        payload = self._payload(messages, params, stream=True)
        parts: list[str] = []
        last_event: dict = {}
        stopped = False
        events = 0
        decode_errors = 0
        token_only_seen = False
        start = time.monotonic()

        debuglog.log(f'POST {self.chat_url} model={params.model} '
                     f'max_tokens={params.max_tokens} temp={params.temperature}', 'basic')
        debuglog.log('request: '
                     + f'{len(messages)} messages ['
                     + ', '.join(f'{m.get("role")}:{len(m.get("content", ""))}c' for m in messages)
                     + f'] top_p={params.top_p} freq_pen={params.frequency_penalty} '
                     + f'pres_pen={params.presence_penalty} thinking={params.enable_thinking}',
                     'verbose')
        debuglog.save_blob('request.json', payload)

        with httpx.stream('POST', self.chat_url, headers=self._headers(),
                          json=payload, timeout=self.timeout) as resp:
            debuglog.log(f'HTTP {resp.status_code}', 'basic')
            debuglog.log('response headers', 'verbose',
                         {k: v for k, v in resp.headers.items()
                          if k.lower() not in ('set-cookie',)})
            if resp.status_code >= 400:
                resp.read()
                self._raise_for_status(resp, self.chat_url)
            for line in resp.iter_lines():
                if cancel is not None and cancel.is_set():
                    stopped = True
                    debuglog.log('stream cancelled by user', 'verbose')
                    break
                if not line or not line.startswith('data:'):
                    continue
                data = line[len('data:'):].strip()
                if data == '[DONE]':
                    break
                try:
                    event = json.loads(data)
                except json.JSONDecodeError:
                    decode_errors += 1
                    continue
                events += 1
                last_event = event
                choices = event.get('choices')
                if isinstance(choices, list) and choices:
                    c0 = choices[0]
                    chunk = self._text_from_choice(c0)
                    if chunk:
                        parts.append(chunk)
                        if on_delta is not None:
                            on_delta(chunk)
                    elif c0.get('token_ids') and not c0.get('text'):
                        token_only_seen = True

        elapsed = time.monotonic() - start
        finish = ''
        usage = {}
        if isinstance(last_event.get('choices'), list) and last_event['choices']:
            finish = last_event['choices'][0].get('finish_reason', '')
        if isinstance(last_event.get('usage'), dict):
            usage = last_event['usage']
        debuglog.log(f'stream: {events} events, {decode_errors} decode errors, '
                     f'finish_reason={finish!r}, usage={usage}', 'verbose')
        if token_only_seen:
            debuglog.log('WARNING: saw token_ids with empty text - server sent the '
                         'token-native form; streaming should still have text', 'verbose')

        try:
            LAST_RESPONSE_PATH.write_text(json.dumps(last_event, indent=1), encoding='utf-8')
        except OSError:
            debuglog.exc('write last_response.json', 'verbose')

        text = ''.join(parts)
        if text.strip():
            debuglog.log(f'stream done: {len(text)} chars, {elapsed:.1f}s, '
                         f'stopped={stopped}', 'basic')
            debuglog.save_blob('response.txt', text)
            return GenResult(text=text, seconds=elapsed, usage=usage,
                             model=str(last_event.get('model') or params.model), stopped=stopped)

        if stopped:
            raise NAIError('Stopped before any text arrived.')
        raise NAIError(
            'Model returned no text over the stream'
            f' (finish_reason={finish!r}). Last event saved to last_response.json.'
        )

    def models(self) -> list[str]:
        """List model ids the account can use. Free - no tokens spent."""
        resp = httpx.get(MODELS_URL, headers=self._headers(), timeout=30.0)
        self._raise_for_status(resp, MODELS_URL)
        data = resp.json()
        items = data.get('data', data if isinstance(data, list) else [])
        ids = []
        for m in items:
            if isinstance(m, dict) and m.get('id'):
                ids.append(str(m['id']))
            elif isinstance(m, str):
                ids.append(m)
        return ids

    def probe(self, model: str = 'glm-4-6') -> list[dict]:
        """Two checks: token validity + model list (free), then a tiny chat ping."""
        results: list[dict] = []

        entry: dict = {'check': 'models', 'url': MODELS_URL}
        try:
            ids = self.models()
            entry['ok'] = True
            entry['models'] = ids
            entry['note'] = ('requested model present' if model in ids
                             else f'!! {model} NOT in list - check the string')
        except Exception as e:  # noqa: BLE001 - report, don't crash the probe
            entry['ok'] = False
            entry['error'] = str(e)
        results.append(entry)

        entry = {'check': 'chat ping', 'url': self.chat_url, 'model': model}
        try:
            result = self.chat(
                [{'role': 'system', 'content': 'Reply with exactly: pong <END>'},
                 {'role': 'user', 'content': 'ping'}],
                GenParams(model=model, max_tokens=16),
            )
            entry['ok'] = True
            entry['text'] = result.text[:100]
        except Exception as e:  # noqa: BLE001 - report, don't crash the probe
            entry['ok'] = False
            entry['error'] = str(e)
        results.append(entry)
        return results
