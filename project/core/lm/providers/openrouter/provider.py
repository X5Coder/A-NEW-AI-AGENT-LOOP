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
        return explicit.strip()
    for env_key in ["OPENROUTER_API_KEY", "OPENROUTER_KEY", "OR_API_KEY"]:
        v = os.environ.get(env_key)
        if v and v.strip():
            return v.strip()
    raise ValueError("OpenRouter API key missing. Set OPENROUTER_API_KEY env or pass api_key argument.")


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


def chat(
    messages: list[dict],
    tools: list[dict] | None = None,
    model: str | None = None,
    api_key: str | None = None,
    temperature: float = 0.3,
    max_tokens: int = 2048,
) -> dict:
    """
    Call OpenRouter chat completions.
    Returns dict with keys: content, tool_calls (list), raw, model_used
    Tool_calls format: [{"id":..., "type":"function", "function":{"name":..., "arguments": {...}}}]
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
        "HTTP-Referer": "https://github.com/a-new-agent-loop",
        "X-Title": "A New Agent Loop",
    }
    last_err: Exception | None = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.post(OPENROUTER_URL, headers=headers, json=payload, timeout=TIMEOUT_SECONDS)
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
            return {
                "content": content,
                "tool_calls": tool_calls_norm,
                "raw": data,
                "model_used": mdl,
                "finish_reason": choice.get("finish_reason"),
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
