"""
OpenRouter provider - OpenAI-compatible chat completions.
Responsible for calling https://openrouter.ai/api/v1/chat/completions with tool calling support.
No hardcoded secrets: reads OPENROUTER_API_KEY from env or passed arg.
Bounded retries: 3, timeouts, validation.
"""
from __future__ import annotations

import os
import time
import json
import requests
from typing import Any

DEFAULT_MODEL = "deepseek/deepseek-chat"
REQUESTED_MODEL = "deepseek/deepseek-v4.1-flash"
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
MAX_RETRIES = 3
BACKOFF = [1, 2, 4]
TIMEOUT_SECONDS = 60


def _get_api_key(explicit: str | None = None) -> str:
    if explicit and isinstance(explicit, str) and explicit.strip():
        key = explicit.strip()
    else:
        key = ""
        for env_key in ["OPENROUTER_API_KEY", "OPENROUTER_KEY", "OR_API_KEY"]:
            v = os.environ.get(env_key)
            if v and v.strip():
                key = v.strip()
                break
        if not key:
            raise ValueError("OpenRouter API key missing. Set OPENROUTER_API_KEY env or pass api_key argument.")
    # HTTP headers are latin-1 only: a corrupted/non-ASCII key fails at send
    # time with "'latin-1' codec can't encode". Fail here with a clear message
    # instead (never log the key itself).
    try:
        key.encode("ascii")
    except UnicodeEncodeError:
        raise ValueError("OpenRouter API key is corrupted (non-ASCII chars). Re-save the key in Models.") from None
    return key


def _validate_messages(messages: Any) -> list[dict]:
    if not isinstance(messages, list) or not messages:
        raise ValueError("messages must be non-empty list")
    for m in messages:
        if not isinstance(m, dict):
            raise TypeError("each message must be dict")
        if "role" not in m or "content" not in m:
            # tool messages have tool_call_id instead of content check relax
            if m.get("role") == "tool" and "tool_call_id" in m:
                continue
            raise ValueError(f"message missing role/content: {m}")
    return messages


def _parse_usage_blob(raw_usage: Any) -> dict:
    try:
        pt = int(raw_usage.get("prompt_tokens") or 0)
    except Exception:
        pt = 0
    try:
        ct = int(raw_usage.get("completion_tokens") or 0)
    except Exception:
        ct = 0
    try:
        tt = int(raw_usage.get("total_tokens") or (pt + ct))
    except Exception:
        tt = pt + ct
    return {"prompt_tokens": max(0, pt), "completion_tokens": max(0, ct), "total_tokens": max(0, tt)}


def _chat_stream(resp: Any, on_token: Any, mdl: str) -> dict:
    """Consume one SSE stream: forward content deltas live, rebuild tool calls.

    Raises when nothing arrived yet (caller retries cleanly); a mid-stream
    break keeps the partial text (already shown live) instead of replaying.
    """
    content_parts: list[str] = []
    tc_acc: dict[int, dict] = {}
    finish_reason = None
    usage: dict = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    got_any = False
    try:
        # Decode bytes as UTF-8 ourselves: requests guesses ISO-8859-1 for
        # text/event-stream (no charset), which mojibakes Arabic/emoji.
        for raw in resp.iter_lines(decode_unicode=False):
            if not raw:
                continue
            try:
                line = raw.decode("utf-8")
            except Exception:
                line = raw.decode("utf-8", errors="replace")
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            try:
                chunk = json.loads(data)
            except Exception:
                continue
            got_any = True
            if isinstance(chunk.get("usage"), dict):
                usage = _parse_usage_blob(chunk.get("usage"))
                continue
            choices = chunk.get("choices") or []
            if not choices:
                continue
            ch = choices[0] if isinstance(choices[0], dict) else {}
            if ch.get("finish_reason"):
                finish_reason = ch.get("finish_reason")
            delta = ch.get("delta") if isinstance(ch.get("delta"), dict) else {}
            piece = delta.get("content")
            if isinstance(piece, str) and piece:
                content_parts.append(piece)
                try:
                    on_token(piece)
                except Exception:
                    pass
            for tc in delta.get("tool_calls") or []:
                if not isinstance(tc, dict):
                    continue
                try:
                    idx = int(tc.get("index", 0))
                except Exception:
                    idx = 0
                acc = tc_acc.setdefault(idx, {"id": "", "name": "", "args": ""})
                if tc.get("id"):
                    acc["id"] = str(tc.get("id"))
                fn = tc.get("function") if isinstance(tc.get("function"), dict) else {}
                if isinstance(fn.get("name"), str) and fn.get("name") and not acc["name"]:
                    acc["name"] = fn.get("name")
                if isinstance(fn.get("arguments"), str):
                    acc["args"] += fn.get("arguments")
    except Exception:
        if not got_any:
            raise
    tool_calls_norm: list[dict] = []
    for idx in sorted(tc_acc):
        acc = tc_acc[idx]
        args: dict = {}
        args_raw = acc["args"]
        if args_raw.strip():
            try:
                args = json.loads(args_raw)
            except Exception:
                args = {"_raw": args_raw}
        if acc["name"]:
            tool_calls_norm.append({
                "id": acc["id"] or f"call_stream_{idx}",
                "type": "function",
                "function": {"name": acc["name"], "arguments": args, "arguments_raw": args_raw},
            })
    try:
        from core.lm.usage import log_usage
        log_usage(mdl, usage["prompt_tokens"], usage["completion_tokens"])
    except Exception:
        pass
    return {
        "content": "".join(content_parts) or None,
        "tool_calls": tool_calls_norm,
        "raw": {"streamed": True, "model": mdl},
        "model_used": mdl,
        "finish_reason": finish_reason,
        "usage": usage,
    }


def chat(
    messages: list[dict],
    tools: list[dict] | None = None,
    model: str | None = None,
    api_key: str | None = None,
    temperature: float = 0.3,
    max_tokens: int = 2048,
    on_token=None,
) -> dict:
    """
    Call OpenRouter chat completions.
    Returns dict with keys: content, tool_calls (list), raw, model_used
    Tool_calls format: [{"id":..., "type":"function", "function":{"name":..., "arguments": {...}}}]
    When on_token (callable) is given, the request uses SSE streaming and
    on_token(delta_text) fires live for every content chunk, so the UI can
    write behind the model. Tool calls are still reconstructed from deltas.
    Retries happen only before the first byte: a partial stream never replays,
    so live text can't duplicate.
    """
    msgs = _validate_messages(messages)
    key = _get_api_key(api_key)
    # model fallback: try requested, fallback to default if invalid
    mdl = model.strip() if model and isinstance(model, str) and model.strip() else REQUESTED_MODEL
    # Validate temperature
    if not isinstance(temperature, (int, float)) or not (0 <= temperature <= 2):
        raise ValueError("temperature must be 0..2")
    payload: dict[str, Any] = {
        "model": mdl,
        "messages": msgs,
        "temperature": float(temperature),
        "max_tokens": int(max_tokens),
    }
    if tools:
        if not isinstance(tools, list):
            raise TypeError("tools must be list")
        payload["tools"] = tools
        payload["tool_choice"] = "auto"
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com/x5agent",
        "X-Title": "X5Agent",
    }
    stream_cb = on_token if callable(on_token) else None
    if stream_cb is not None:
        payload["stream"] = True
        payload["stream_options"] = {"include_usage": True}
    last_err: Exception | None = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.post(OPENROUTER_URL, headers=headers, json=payload,
                                 timeout=TIMEOUT_SECONDS, stream=stream_cb is not None)
            # handle rate limit / auth errors with retry only for transient
            if resp.status_code == 429 or 500 <= resp.status_code < 600:
                # transient
                if attempt == MAX_RETRIES:
                    raise RuntimeError(f"OpenRouter HTTP {resp.status_code}: {resp.text[:500]}")
                time.sleep(BACKOFF[min(attempt -1, len(BACKOFF)-1)])
                continue
            if resp.status_code == 401:
                raise PermissionError(f"OpenRouter auth failed (401): check API key. Body: {resp.text[:300]}")
            if resp.status_code == 404 and mdl == REQUESTED_MODEL:
                # fallback model
                payload["model"] = DEFAULT_MODEL
                mdl = DEFAULT_MODEL
                if attempt < MAX_RETRIES:
                    time.sleep(1)
                    continue
                # will try again next iteration? Actually we already set fallback, retry
                continue
            resp.raise_for_status()
            if stream_cb is not None:
                try:
                    return _chat_stream(resp, stream_cb, mdl)
                finally:
                    try:
                        resp.close()
                    except Exception:
                        pass
            data = resp.json()
            # validate response shape
            if "choices" not in data or not data["choices"]:
                raise ValueError(f"Invalid OpenRouter response: {str(data)[:500]}")
            choice = data["choices"][0]
            message = choice.get("message", {})
            content = message.get("content")
            tool_calls_raw = message.get("tool_calls")
            tool_calls_norm: list[dict] = []
            if isinstance(tool_calls_raw, list):
                for tc in tool_calls_raw:
                    # OpenAI format: {id, type, function:{name, arguments}}
                    # Some providers return arguments as string JSON
                    fn = tc.get("function", {})
                    name = fn.get("name")
                    args_raw = fn.get("arguments")
                    args: dict = {}
                    if isinstance(args_raw, str):
                        try:
                            args = json.loads(args_raw) if args_raw.strip() else {}
                        except Exception:
                            # keep as string wrapper
                            args = {"_raw": args_raw}
                    elif isinstance(args_raw, dict):
                        args = args_raw
                    if name:
                        tool_calls_norm.append({
                            "id": tc.get("id") or f"call_{len(tool_calls_norm)}",
                            "type": tc.get("type") or "function",
                            "function": {"name": name, "arguments": args, "arguments_raw": args_raw},
                        })
            # Measured usage for real cost accounting (OpenRouter returns usage on every call).
            raw_usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
            try:
                pt = int(raw_usage.get("prompt_tokens") or 0)
            except Exception:
                pt = 0
            try:
                ct = int(raw_usage.get("completion_tokens") or 0)
            except Exception:
                ct = 0
            try:
                tt = int(raw_usage.get("total_tokens") or (pt + ct))
            except Exception:
                tt = pt + ct
            usage = {"prompt_tokens": max(0, pt), "completion_tokens": max(0, ct), "total_tokens": max(0, tt)}
            # Best-effort accounting: must never break the chat call.
            try:
                from core.lm.usage import log_usage
                log_usage(mdl, usage["prompt_tokens"], usage["completion_tokens"])
            except Exception:
                pass
            return {
                "content": content,
                "tool_calls": tool_calls_norm,
                "raw": data,
                "model_used": mdl,
                "finish_reason": choice.get("finish_reason"),
                "usage": usage,
            }
        except (requests.Timeout, requests.ConnectionError, requests.HTTPError) as e:
            last_err = e
            # only retry transient
            is_transient = isinstance(e, (requests.Timeout, requests.ConnectionError)) or "429" in str(e) or "500" in str(e)
            if not is_transient or attempt == MAX_RETRIES:
                if attempt == MAX_RETRIES:
                    break
            time.sleep(BACKOFF[min(attempt -1, len(BACKOFF)-1)])
            continue
        except Exception as e:
            last_err = e
            if attempt == MAX_RETRIES:
                break
            # don't retry on auth / validation errors
            if "auth" in str(e).lower() or "API key" in str(e):
                break
            time.sleep(BACKOFF[min(attempt -1, len(BACKOFF)-1)])
            continue
    raise RuntimeError(f"OpenRouter chat failed after {MAX_RETRIES} retries: {last_err}") from last_err