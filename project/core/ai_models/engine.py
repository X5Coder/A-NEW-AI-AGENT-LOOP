"""
Universal Model Connection Engine + AIP/1 router (protocol v1).

Single generic core: validate -> substitute -> build request -> send -> parse.
No provider-specific code anywhere.

Routing: every public function (chat/analyze/generate_image/synthesize/
transcribe) first checks _is_aip_record() — the SAME check the agent loop
uses. AIP/1 records go through the AIP/1 interpreter (compile-once, cached
plan, single-shot JSON); legacy connection-JSON records keep the legacy
template path below (which also keeps binary-body + submit-then-poll flows
that AIP/1 explicitly refuses). Callers never branch themselves.

Standard library only (urllib). chat()/test_connection() raise EngineError
with structured, redacted, copyable diagnostics.
"""
from __future__ import annotations

import io
import json
import logging
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable

log = logging.getLogger("ai_models")

PROTOCOL_VERSION = 1
SUPPORTED_KINDS = ("text", "vision", "image", "tts", "stt")
MAX_RESPONSE_BYTES = 5 * 1024 * 1024
MAX_AUDIO_BYTES = 25 * 1024 * 1024
MAX_BODY_PREVIEW = 1500
# Variables per kind. "text" is the base set; every kind also knows
# model/api_key. Union is accepted by validation (unknown names still error).
KIND_VARIABLES = {
    "text": ("model", "message", "messages", "system", "api_key"),
    "vision": ("model", "prompt", "image_base64", "image_url", "image_mime",
               "messages", "system", "api_key"),
    "image": ("model", "prompt", "system", "api_key"),
    "tts": ("model", "text", "system", "api_key"),
    "stt": ("model", "audio_base64", "audio_mime", "system", "api_key"),
}
KNOWN_VARS: tuple = tuple(sorted({v for vs in KIND_VARIABLES.values() for v in vs} | {"job_id"}))
OUTPUT_TYPES = ("text", "image", "audio")
_VAR_RE = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")

TEST_MESSAGE = "Reply with the single word: OK"


class EngineError(Exception):
    """Structured, UI-safe failure. Never contains secrets."""

    def __init__(self, category: str, message: str, technical: str = "", copyable: str = ""):
        super().__init__(message)
        self.category = category
        self.message = message
        self.technical = technical
        self.copyable = copyable


def is_aip_record(model_record: dict) -> bool:
    """True when the record carries an AIP/1 parser (new system).

    SAME check the agent loop uses to pick its path, so every caller of
    this engine automatically follows the loop. Import of the interpreter
    stays lazy (function-level) to avoid a top-level import cycle:
    aip._send_once reuses engine._send.
    """
    try:
        rec = model_record or {}
        pdef = rec.get("parser_definition") or rec.get("config_json") or {}
        return isinstance(pdef, dict) and pdef.get("language") == "AIP/1"
    except Exception:
        return False


def _aip_messages(system: str, user_content: Any) -> list[dict]:
    """Canonical messages: optional system first, then one user message."""
    messages: list[dict] = []
    if isinstance(system, str) and system.strip():
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": user_content})
    return messages


def _aip_text_out(res: Any) -> str:
    """Extract text from an AIP result. Empty when the service said nothing."""
    if isinstance(res, dict):
        text = res.get("text") or ""
        if isinstance(text, str):
            return text
    return ""


def _aip_media_out(res: Any) -> tuple[str, str]:
    """Extract (media_data, source) from an AIP result. Empty data when absent."""
    media = res.get("media") if isinstance(res, dict) else None
    if isinstance(media, dict):
        data = media.get("data") or ""
        source = media.get("source") or "base64"
        if isinstance(data, str) and isinstance(source, str):
            return data, source
    return "", "base64"


# --------------------------------------------------------------------------
# Path syntax: a.b, [0], [*], ["my.key"]
# --------------------------------------------------------------------------

_PATH_TOKEN = re.compile(r"""
    (?P<dotkey>[A-Za-z0-9_\-]+)
  | \["(?P<qkey>(?:[^"\\]|\\.)*)"\]
  | \['(?P<qkey2>(?:[^'\\]|\\.)*)'\]
  | \[(?P<idx>\d+|\*)\]
""", re.VERBOSE)


def _parse_path(path: str) -> list:
    """Parse a path string into tokens. Raises ValueError on syntax error."""
    if not isinstance(path, str) or not path.strip():
        raise ValueError("path must be a non-empty string")
    tokens: list = []
    i, n = 0, len(path)
    expect_key = True
    while i < n:
        ch = path[i]
        if ch == ".":
            if expect_key:
                raise ValueError(f"bad path '{path}': unexpected '.'")
            i += 1
            expect_key = True
            continue
        if ch.isspace():
            i += 1
            continue
        m = _PATH_TOKEN.match(path, i)
        if not m or m.start() != i:
            raise ValueError(f"bad path '{path}': unexpected '{path[i:i+8]}'")
        if m.group("dotkey") is not None:
            if not expect_key:
                raise ValueError(f"bad path '{path}': missing '.'")
            tokens.append(("key", m.group("dotkey")))
            expect_key = False
        elif m.group("qkey") is not None:
            if not expect_key:
                raise ValueError(f"bad path '{path}': missing '.'")
            tokens.append(("key", m.group("qkey").replace('\\"', '"').replace("\\\\", "\\")))
            expect_key = False
        elif m.group("qkey2") is not None:
            if not expect_key:
                raise ValueError(f"bad path '{path}': missing '.'")
            tokens.append(("key", m.group("qkey2")))
            expect_key = False
        else:
            tokens.append(("idx", m.group("idx")))
            expect_key = False
        i = m.end()
    if expect_key and tokens:
        raise ValueError(f"bad path '{path}': trailing '.'")
    return tokens


def _available_keys(node: Any) -> list:
    if isinstance(node, dict):
        return sorted(str(k) for k in list(node.keys())[:20])
    if isinstance(node, list):
        return [f"[{i}]" for i in range(min(len(node), 20))]
    return [type(node).__name__]


def resolve_path(data: Any, path: str) -> Any:
    """Resolve path against parsed JSON. Raises EngineError(path_not_found/...)."""
    try:
        tokens = _parse_path(path)
    except ValueError as e:
        raise EngineError("invalid_config", f"Bad path: {e}", technical=str(e),
                          copyable=f"Path error: {e}\nPath: {path}")
    return _resolve_tokens(data, tokens, path)


def _scalar_text(value: Any, path: str) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float, bool)):
        return str(value)
    if value is None:
        raise EngineError(
            "path_not_found",
            f"Reply has no text at '{path}' (it is empty).",
            technical=f"path={path} resolved to null",
            copyable=f"Path not found: {path}\nIt exists but is empty (null).",
        )
    raise EngineError(
        "bad_response",
        f"Path '{path}' points to a {type(value).__name__}, not text. Point it at the text field inside.",
        technical=f"path={path} resolved type={type(value).__name__}",
        copyable=(f"Bad response: path '{path}' found a {type(value).__name__}.\n"
                  f"Point the path at the text field inside it."),
    )


def _resolve_tokens(data: Any, tokens: list, path: str) -> Any:
    node = data
    i = 0
    while i < len(tokens):
        t = tokens[i]
        if t[0] == "key":
            if not isinstance(node, dict):
                raise EngineError(
                    "path_not_found",
                    f"Nothing at '{path}': expected an object with key '{t[1]}' but found {type(node).__name__}.",
                    technical=f"path={path} broke at key '{t[1]}', node type={type(node).__name__}",
                    copyable=(f"Path not found: {path}\nBroke at key '{t[1]}' "
                              f"(found {type(node).__name__} instead of an object)."),
                )
            if t[1] not in node:
                raise EngineError(
                    "path_not_found",
                    f"Reply has no '{path}'. Available keys here: {', '.join(_available_keys(node)) or '(none)'}.",
                    technical=f"path={path} missing key '{t[1]}'",
                    copyable=(f"Path not found: {path}\nMissing key: '{t[1]}'\n"
                              f"Available keys: {', '.join(_available_keys(node)) or '(none)'}"),
                )
            node = node[t[1]]
        else:
            if t[1] == "*":
                if not isinstance(node, list):
                    raise EngineError(
                        "path_not_found",
                        f"Nothing at '{path}': '[*]' needs a list but found {type(node).__name__}.",
                        technical=f"path={path} [*] on {type(node).__name__}",
                        copyable=f"Path not found: {path}\n'[*]' needs a list, found {type(node).__name__}.",
                    )
                rest = tokens[i + 1:]
                parts: list[str] = []
                for item in node:
                    try:
                        val = _resolve_tokens(item, rest, path) if rest else item
                    except EngineError as e:
                        if e.category == "path_not_found":
                            continue  # item without the field: skipped like metadata chunks
                        raise
                    if val is None:
                        continue
                    parts.append(_scalar_text(val, path))
                return "".join(parts)
            idx = int(t[1])
            if not isinstance(node, list):
                raise EngineError(
                    "path_not_found",
                    f"Nothing at '{path}': expected a list at '[{idx}]' but found {type(node).__name__}.",
                    technical=f"path={path} [{idx}] on {type(node).__name__}",
                    copyable=f"Path not found: {path}\nExpected a list at [{idx}], found {type(node).__name__}.",
                )
            if idx >= len(node):
                raise EngineError(
                    "path_not_found",
                    f"Reply has no '{path}'. The list has {len(node)} item(s): {', '.join(_available_keys(node)) or '(empty)'}.",
                    technical=f"path={path} index {idx} out of range (len {len(node)})",
                    copyable=(f"Path not found: {path}\nIndex [{idx}] out of range, "
                              f"list has {len(node)} item(s)."),
                )
            node = node[idx]
        i += 1
    return _scalar_text(node, path)


# --------------------------------------------------------------------------
# Validation (never throws on bad user input)
# --------------------------------------------------------------------------

def validate(config: Any) -> list[str]:
    """Return human-readable problems (empty = OK). Never raises."""
    try:
        return _validate_inner(config)
    except Exception as e:  # defensive: validation itself must never crash
        return [f"Invalid Connection JSON: {e}"]


def _validate_inner(config: Any) -> list[str]:
    problems: list[str] = []
    if not isinstance(config, dict):
        return ["Connection JSON must be an object (paste the JSON your assistant gave you)."]
    kind = config.get("kind")
    if config.get("version") != PROTOCOL_VERSION:
        problems.append(
            f"Unsupported version '{config.get('version')}'. Supported version is 1 only."
        )
    if kind not in SUPPORTED_KINDS:
        problems.append(
            f"Unsupported kind '{kind}'. Supported kinds: {', '.join(SUPPORTED_KINDS)}."
        )
    req = config.get("request", {})
    if not isinstance(req, dict):
        problems.append("request must be an object.")
        req = {}
    method = req.get("method", "POST")
    if not isinstance(method, str) or not method.strip():
        problems.append("request.method must be a non-empty string (for example POST).")
    for field in ("path",):
        if field in req and not isinstance(req[field], str):
            problems.append(f"request.{field} must be a string.")
    for field in ("query", "headers", "body"):
        if field in req and req[field] is not None and not isinstance(req[field], (dict, str)):
            if field == "body" and isinstance(req.get(field), list):
                continue
            problems.append(f"request.{field} must be an object.")
    bt = req.get("body_type", "json")
    if bt not in ("json", "text", "form", "binary"):
        problems.append("request.body_type must be json, text, form, or binary.")
    if bt == "text" and "body" in req and not isinstance(req["body"], str):
        problems.append("With body_type text, request.body must be a string.")
    if bt == "form" and "body" in req and not isinstance(req["body"], dict):
        problems.append("With body_type form, request.body must be an object.")
    if bt == "binary":
        body = req.get("body", "")
        if not isinstance(body, str) or not _VAR_RE.fullmatch(body.strip()):
            problems.append(
                "With body_type binary, request.body must be exactly one variable "
                "(for example \"{{audio_base64}}\") whose bytes are sent raw.")
    mm = req.get("messages_map")
    if mm is not None:
        if not isinstance(mm, dict):
            problems.append("request.messages_map must be an object with role_map and item.")
        else:
            if "item" in mm and not isinstance(mm["item"], dict):
                problems.append("request.messages_map.item must be an object template.")
            if "role_map" in mm and not isinstance(mm["role_map"], dict):
                problems.append("request.messages_map.role_map must be an object.")
    to = req.get("timeout_seconds", 60)
    if not isinstance(to, (int, float)) or isinstance(to, bool) or not (1 <= float(to) <= 600):
        problems.append("request.timeout_seconds must be a number between 1 and 600.")
    resp = config.get("response", {})
    if not isinstance(resp, dict):
        problems.append("response must be an object.")
        resp = {}
    if resp.get("mode", "json") not in ("json", "sse", "ndjson", "text"):
        problems.append("response.mode must be json, sse, ndjson, or text.")
    for field in ("output", "stream"):
        if field in resp and not isinstance(resp[field], dict):
            problems.append(f"response.{field} must be an object.")
    # Unknown variables scan (body/path/query/headers, not applied to substituted values)
    text = json.dumps({
        "path": req.get("path", ""),
        "query": req.get("query", {}),
        "headers": req.get("headers", {}),
        "body": req.get("body", {}),
    }, ensure_ascii=False)
    for name in sorted(set(_VAR_RE.findall(text))):
        if name not in KNOWN_VARS:
            problems.append(
                f"Unknown variable '{{{{{name}}}}}' in the request. "
                f"Known variables: {', '.join('{{' + v + '}}' for v in KNOWN_VARS)}."
            )
    out_type = (resp.get("output") or {}).get("type", "text")
    if out_type not in OUTPUT_TYPES:
        problems.append("response.output.type must be text, image, or audio.")
    mode = resp.get("mode", "json")
    if out_type in ("image", "audio") and mode not in ("json", "text"):
        problems.append("Image/audio outputs need response.mode json or text.")
    poll = resp.get("poll")
    if poll is not None:
        if not isinstance(poll, dict):
            problems.append("response.poll must be an object.")
        else:
            for f in ("id_path", "status_path", "result_path"):
                if f in poll and not isinstance(poll[f], str):
                    problems.append(f"response.poll.{f} must be a string.")
            if "done_value" not in poll:
                problems.append("response.poll.done_value is required when polling (for example \"succeeded\").")
            for f in ("interval_seconds", "max_wait_seconds"):
                v = poll.get(f, 5 if f == "interval_seconds" else 300)
                if not isinstance(v, (int, float)) or isinstance(v, bool) or v <= 0:
                    problems.append(f"response.poll.{f} must be a positive number.")
            if "method" in poll and not isinstance(poll["method"], str):
                problems.append("response.poll.method must be a string.")
    # Path syntax check
    poll_d = poll if isinstance(poll, dict) else {}
    for label, p in (("response.output.path", (resp.get("output") or {}).get("path", "")),
                     ("response.stream.chunk_path", (resp.get("stream") or {}).get("chunk_path", "")),
                     ("response.error_path", resp.get("error_path", "")),
                     ("response.poll.id_path", poll_d.get("id_path", "")),
                     ("response.poll.status_path", poll_d.get("status_path", "")),
                     ("response.poll.result_path", poll_d.get("result_path", ""))):
        if p:
            if not isinstance(p, str):
                problems.append(f"{label} must be a string.")
            else:
                try:
                    _parse_path(p)
                except ValueError as e:
                    problems.append(f"{label}: {e}")
    return problems


# --------------------------------------------------------------------------
# Variable substitution (on the parsed structure, never string-concat + parse)
# --------------------------------------------------------------------------

def _text_form(value: Any) -> str:
    if isinstance(value, str):
        return value
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    return json.dumps(value, ensure_ascii=False)


def _substitute_string(s: str, variables: dict) -> Any:
    m = _VAR_RE.fullmatch(s)
    if m:
        name = m.group(1)
        if name not in variables:
            raise EngineError("invalid_config", f"Unknown variable '{{{{{name}}}}}'.",
                              technical=f"unknown variable {name}",
                              copyable=f"Unknown variable '{{{{{name}}}}}'.")
        return variables[name]  # real typed value (list stays a list)
    def _repl(mo: re.Match) -> str:
        name = mo.group(1)
        if name not in variables:
            raise EngineError("invalid_config", f"Unknown variable '{{{{{name}}}}}'.",
                              technical=f"unknown variable {name}",
                              copyable=f"Unknown variable '{{{{{name}}}}}'.")
        return _text_form(variables[name])  # text form inside longer strings
    return _VAR_RE.sub(_repl, s)


def _substitute(node: Any, variables: dict) -> Any:
    if isinstance(node, str):
        return _substitute_string(node, variables)
    if isinstance(node, dict):
        return {k: _substitute(v, variables) for k, v in node.items()}
    if isinstance(node, list):
        return [_substitute(v, variables) for v in node]
    return node


def build_messages(history: list[dict], messages_map: Any) -> list:
    """Build {{messages}} via optional messages_map (role_map + item template)."""
    if not messages_map:
        return [{"role": m.get("role", "user"), "content": m.get("content", "")} for m in history]
    role_map = messages_map.get("role_map") or {}
    item = messages_map.get("item")

    def _render(node: Any, role: str, content: Any) -> Any:
        if isinstance(node, str):
            return node.replace("{{role}}", role).replace("{{content}}", _text_form(content))
        if isinstance(node, dict):
            return {k: _render(v, role, content) for k, v in node.items()}
        if isinstance(node, list):
            return [_render(v, role, content) for v in node]
        return node

    out = []
    for m in history:
        role = str(role_map.get(m.get("role", "user"), m.get("role", "user")))
        content = m.get("content", "")
        if not isinstance(item, dict):
            out.append({"role": role, "content": content})
            continue
        out.append(_render(item, role, content))
    return out


# --------------------------------------------------------------------------
# Request building
# --------------------------------------------------------------------------

def join_url(base: str, path: str) -> str:
    base = (base or "").strip()
    path = (path or "").strip()
    if not path:
        return base
    return base.rstrip("/") + "/" + path.lstrip("/")


def _redact_value(name: str, value: str, secrets: list[str]) -> str:
    for s in secrets:
        if s and isinstance(value, str) and s in value:
            value = value.replace(s, "••••")
    if name.lower() in ("authorization", "x-api-key", "api-key", "api_key") and value:
        return "••••"
    return value


def redact_summary(url: str, headers: dict, secrets: list[str]) -> str:
    try:
        parsed = urllib.parse.urlsplit(url)
        q = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
        q2 = [(k, "••••" if any(s and s == v for s in secrets) or k.lower() in ("key", "api_key", "apikey", "token") else v)
              for k, v in q]
        url = urllib.parse.urlunsplit(
            (parsed.scheme, parsed.netloc, parsed.path, urllib.parse.urlencode(q2), parsed.fragment))
    except Exception:
        pass
    hn = sorted(str(k) for k in (headers or {}).keys())
    return f"URL={url} headers=[{', '.join(hn)}]"


def build_http_request(model_record: dict, history: list[dict], system: str = "") -> tuple:
    """Returns (url, method, headers, body_bytes, content_type, secrets)."""
    cfg = model_record.get("config_json") or {}
    req = cfg.get("request", {}) if isinstance(cfg.get("request"), dict) else {}
    server_url = str(model_record.get("server_url") or "").strip()
    if not server_url:
        raise EngineError("invalid_config", "Server URL is empty. Paste the service address first.",
                          technical="empty server_url",
                          copyable="Configuration error: Server URL is empty.")
    parsed = urllib.parse.urlsplit(server_url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise EngineError("invalid_config", "Server URL must start with http:// or https://.",
                          technical=f"bad server_url scheme={parsed.scheme}",
                          copyable=f"Configuration error: Server URL must start with http:// or https://.\nGot: {server_url[:120]}")
    model_name = str(model_record.get("model_name") or "")
    api_key = str(model_record.get("api_key") or "")
    message = ""
    for m in reversed(history):
        if m.get("role") == "user":
            message = str(m.get("content") or "")
            break
    variables = {
        "model": model_name,
        "message": message,
        "messages": build_messages(history, req.get("messages_map")),
        "system": system or "",
        "api_key": api_key,
    }
    return _build_from_vars(model_record, req, variables)


def _build_from_vars(model_record: dict, req: dict, variables: dict) -> tuple:
    """Shared core used by every kind: substitute path/query/headers/body.
    Returns (url, method, headers, body_bytes, body_type, timeout, secrets)."""
    server_url = str(model_record.get("server_url") or "").strip()
    if not server_url:
        raise EngineError("invalid_config", "Server URL is empty. Paste the service address first.",
                          technical="empty server_url",
                          copyable="Configuration error: Server URL is empty.")
    parsed = urllib.parse.urlsplit(server_url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise EngineError("invalid_config", "Server URL must start with http:// or https://.",
                          technical=f"bad server_url scheme={parsed.scheme}",
                          copyable=f"Configuration error: Server URL must start with http:// or https://.\nGot: {server_url[:120]}")
    api_key = str(model_record.get("api_key") or "")
    secrets = [api_key] if api_key else []
    raw_path = _substitute(req.get("path", ""), variables)
    url = join_url(server_url, _text_form(raw_path))
    # Merge query (keep query already in the Server URL)
    raw_query = _substitute(req.get("query", {}) or {}, variables)
    if not isinstance(raw_query, dict):
        raise EngineError("invalid_config", "request.query must be an object.",
                          technical="query not object after substitution",
                          copyable="Configuration error: request.query must be an object.")
    try:
        parts = urllib.parse.urlsplit(url)
        existing = urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
        extra = [(str(k), _text_form(v)) for k, v in raw_query.items()]
        url = urllib.parse.urlunsplit(
            (parts.scheme, parts.netloc, parts.path,
             urllib.parse.urlencode(existing + extra), parts.fragment))
    except EngineError:
        raise
    except Exception as e:
        raise EngineError("invalid_config", f"Bad query: {e}", technical=str(e),
                          copyable=f"Configuration error in request.query: {e}")
    raw_headers = _substitute(req.get("headers", {}) or {}, variables)
    if not isinstance(raw_headers, dict):
        raise EngineError("invalid_config", "request.headers must be an object.",
                          technical="headers not object",
                          copyable="Configuration error: request.headers must be an object.")
    headers = {str(k): _text_form(v) for k, v in raw_headers.items()}
    body_type = req.get("body_type", "json")
    if body_type == "json" and not any(k.lower() == "content-type" for k in headers):
        headers["Content-Type"] = "application/json"
    raw_body = _substitute(req.get("body", {}) if "body" in req else {}, variables)
    if body_type == "json":
        body_bytes = json.dumps(raw_body, ensure_ascii=False).encode("utf-8")
    elif body_type == "text":
        if not isinstance(raw_body, str):
            raise EngineError("invalid_config", "With body_type text, request.body must be a string.",
                              technical="text body not string",
                              copyable="Configuration error: with body_type text, request.body must be a string.")
        body_bytes = raw_body.encode("utf-8")
    elif body_type == "form":
        if not isinstance(raw_body, dict):
            raise EngineError("invalid_config", "With body_type form, request.body must be an object.",
                              technical="form body not object",
                              copyable="Configuration error: with body_type form, request.body must be an object.")
        body_bytes = urllib.parse.urlencode(
            {str(k): _text_form(v) for k, v in raw_body.items()}).encode("utf-8")
        if not any(k.lower() == "content-type" for k in headers):
            headers["Content-Type"] = "application/x-www-form-urlencoded"
    elif body_type == "binary":
        # Exactly one variable (validated): its base64 text is decoded to raw
        # bytes (audio for STT). A data-URL prefix is stripped when present.
        if not isinstance(raw_body, str):
            raise EngineError("invalid_config",
                              "With body_type binary, request.body must be exactly one variable.",
                              technical="binary body not string",
                              copyable="Configuration error: with body_type binary, request.body must be exactly one variable.")
        import base64 as _b64
        blob = raw_body.strip()
        if "," in blob and blob.startswith("data:"):
            blob = blob.split(",", 1)[1]
        try:
            body_bytes = _b64.b64decode(blob)
        except Exception:
            raise EngineError("invalid_config", "The audio data is not valid base64.",
                              technical="binary body base64 decode failed",
                              copyable="Configuration error: the audio data is not valid base64.")
        if len(body_bytes) > MAX_AUDIO_BYTES:
            raise EngineError("invalid_config", "Audio too large (max 25 MB).",
                              technical=f"binary body {len(body_bytes)} bytes",
                              copyable="Audio too large (max 25 MB).")
    else:
        raise EngineError("invalid_config", f"Unknown body_type '{body_type}'.",
                          technical=f"body_type={body_type}",
                          copyable=f"Configuration error: unknown body_type '{body_type}'.")
    method = str(req.get("method", "POST") or "POST").upper()
    timeout = req.get("timeout_seconds", 60)
    try:
        timeout = float(timeout)
    except Exception:
        timeout = 60.0
    return url, method, headers, body_bytes, body_type, timeout, secrets


# --------------------------------------------------------------------------
# HTTP send (no silent cross-host redirects, capped size, timeout, cancel)
# --------------------------------------------------------------------------

class _NoCrossHostRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        try:
            old = urllib.parse.urlsplit(req.full_url)
            new = urllib.parse.urlsplit(urllib.parse.urljoin(req.full_url, newurl))
            if old.netloc.lower() != new.netloc.lower():
                raise EngineError(
                    "bad_response",
                    "The service tried to redirect to a different address. Stopped for safety.",
                    technical=f"cross-host redirect {old.netloc} -> {new.netloc}",
                    copyable=(f"Blocked redirect to a different host: {old.netloc} -> {new.netloc}.\n"
                              f"Fix the Server URL in AI Models."),
                )
        except EngineError:
            raise
        except Exception:
            pass
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _send(url: str, method: str, headers: dict, body: bytes, timeout: float,
          cancel_event=None) -> tuple[int, bytes]:
    if cancel_event is not None and getattr(cancel_event, "is_set", lambda: False)():
        raise EngineError("timeout", "Cancelled.", technical="cancelled before send",
                          copyable="Cancelled before sending.")
    opener = urllib.request.build_opener(_NoCrossHostRedirect)
    data = body if method not in ("GET", "HEAD") else None
    creq = urllib.request.Request(url, data=data, method=method)
    for k, v in headers.items():
        try:
            creq.add_header(k, v)
        except Exception:
            continue
    try:
        with opener.open(creq, timeout=timeout) as resp:
            status = int(getattr(resp, "status", 200) or 200)
            chunks: list[bytes] = []
            total = 0
            while True:
                if cancel_event is not None and getattr(cancel_event, "is_set", lambda: False)():
                    raise EngineError("timeout", "Cancelled.", technical="cancelled mid-read",
                                      copyable="Cancelled while reading the reply.")
                piece = resp.read(65536)
                if not piece:
                    break
                total += len(piece)
                if total > MAX_RESPONSE_BYTES:
                    raise EngineError(
                        "bad_response", "The service reply was too large (over 5 MB).",
                        technical=f"response exceeded {MAX_RESPONSE_BYTES} bytes",
                        copyable="Bad response: the service reply exceeded 5 MB and was stopped.")
                chunks.append(piece)
            return status, b"".join(chunks)
    except urllib.error.HTTPError as e:
        try:
            raw = e.read(MAX_RESPONSE_BYTES) or b""
        except Exception:
            raw = b""
        raise _http_status_error(int(getattr(e, "code", 0) or 0), raw)
    except EngineError:
        raise
    except TimeoutError:
        raise EngineError("timeout", "The service took too long. Try again, or raise the timeout in the Connection JSON.",
                          technical="timeout", copyable="Timeout: the service took too long with no reply.")
    except (ConnectionRefusedError, ConnectionResetError):
        raise EngineError("network", _unreachable_hint(url),
                          technical="connection refused/reset",
                          copyable=f"Network error: cannot reach {url.split('/')[2] if '/' in url else url}.")
    except OSError as e:
        msg = str(e).lower()
        if "timed out" in msg or "timeout" in msg:
            raise EngineError("timeout", "The service took too long. Try again, or raise the timeout in the Connection JSON.",
                              technical=str(e)[:200], copyable=f"Timeout: {e}")
        raise EngineError("network", _unreachable_hint(url),
                          technical=f"OSError: {e}"[:300],
                          copyable=f"Network error: {e}\nURL host: {urllib.parse.urlsplit(url).netloc}")


def _unreachable_hint(url: str) -> str:
    try:
        host = urllib.parse.urlsplit(url).netloc or url
    except Exception:
        host = url
    extra = ""
    if "localhost" in host or "127." in host or host.startswith("192.168.") or host.startswith("10."):
        extra = " If the model runs on your own machine, make sure it is running and reachable from this device."
    return f"Cannot reach {host}. Check the Server URL and your connection.{extra}"


def _http_status_error(status: int, raw: bytes) -> EngineError:
    excerpt = raw[:MAX_BODY_PREVIEW].decode("utf-8", errors="replace")
    if status in (401, 403):
        return EngineError("auth", "The service refused the key (401/403). Check the API key field.",
                           technical=f"HTTP {status} {excerpt[:300]}",
                           copyable=f"Auth refused (HTTP {status}). Check the API key.\nExcerpt: {excerpt[:500]}")
    if status == 404:
        return EngineError("not_found", "Address or model not found (404). Check the Server URL and the model name.",
                           technical=f"HTTP 404 {excerpt[:300]}",
                           copyable=f"Not found (HTTP 404). Check the Server URL and model name.\nExcerpt: {excerpt[:500]}")
    if status == 429:
        return EngineError("rate_limit", "Rate limit or no credit left (429). Wait a little or top up the account.",
                           technical=f"HTTP 429 {excerpt[:300]}",
                           copyable=f"Rate limited (HTTP 429).\nExcerpt: {excerpt[:500]}")
    if 500 <= status < 600:
        return EngineError("server", f"The service itself failed (HTTP {status}). Try again later.",
                           technical=f"HTTP {status} {excerpt[:300]}",
                           copyable=f"Service error (HTTP {status}).\nExcerpt: {excerpt[:500]}")
    return EngineError("server", f"The service returned HTTP {status}.",
                       technical=f"HTTP {status} {excerpt[:300]}",
                       copyable=f"HTTP {status}.\nExcerpt: {excerpt[:500]}")


# --------------------------------------------------------------------------
# Response parsing
# --------------------------------------------------------------------------

def _extract_error_hint(raw: bytes, error_path: str) -> str:
    if not error_path:
        return ""
    try:
        data = json.loads(raw.decode("utf-8"))
        val = resolve_path(data, error_path)
        return str(val)[:500]
    except Exception:
        return ""


def parse_json_mode(raw: bytes, cfg_resp: dict) -> str:
    try:
        text = raw.decode("utf-8")
    except Exception:
        raise EngineError("bad_response", "The reply was not readable text.",
                          technical="response not utf-8",
                          copyable="Bad response: the reply was not readable UTF-8 text.")
    try:
        data = json.loads(text)
    except Exception:
        raise EngineError("bad_response",
                          "Expected a JSON reply but got something else. Check response.mode in the Connection JSON.",
                          technical=f"json parse failed, body[:300]={text[:300]}",
                          copyable=(f"Bad response: expected JSON but got:\n{text[:500]}\n"
                                    f"Check response.mode in the Connection JSON."))
    path = (cfg_resp.get("output") or {}).get("path", "")
    if not path:
        raise EngineError("invalid_config",
                          "response.output.path is empty. Tell your assistant which field holds the reply text.",
                          technical="empty output.path in json mode",
                          copyable="Configuration error: response.output.path is empty.")
    return resolve_path(data, path)


def _iter_sse_events(stream: io.BufferedReader | io.RawIOBase, cancel_event=None):
    """Yield joined data payloads, handling arbitrary byte splits + multibyte chars."""
    buf = b""
    data_lines: list[str] = []
    decoder = io.TextIOWrapper(io.BytesIO(), encoding="utf-8", errors="replace", newline="\n")

    def _feed(chunk: bytes):
        nonlocal buf
        buf += chunk

    def _lines():
        nonlocal buf
        while b"\n" in buf:
            line, buf = buf.split(b"\n", 1)
            yield line.decode("utf-8", errors="replace")

    import http.client as _http
    while True:
        if cancel_event is not None and getattr(cancel_event, "is_set", lambda: False)():
            raise EngineError("timeout", "Cancelled.", technical="cancelled mid-stream",
                              copyable="Cancelled while reading the stream.")
        piece = stream.read(65536)
        if not piece:
            break
        _feed(piece if isinstance(piece, bytes) else bytes(piece))
        for raw_line in _lines():
            line = raw_line.rstrip("\r")
            if not line:
                if data_lines:
                    yield "\n".join(data_lines)
                    data_lines = []
                continue
            if line.startswith(":"):
                continue
            if line.startswith("data:"):
                payload = line[5:]
                if payload.startswith(" "):
                    payload = payload[1:]
                data_lines.append(payload)
                continue
            # event:/id:/retry: ignored
            continue
    if data_lines:
        yield "\n".join(data_lines)


def parse_stream_mode(raw: bytes, cfg_resp: dict, on_chunk: Callable | None = None,
                      cancel_event=None) -> str:
    stream_cfg = cfg_resp.get("stream") or {}
    chunk_path = str(stream_cfg.get("chunk_path") or "")
    done_marker = str(stream_cfg.get("done_marker") or "")
    if not chunk_path:
        raise EngineError("invalid_config",
                          "response.stream.chunk_path is empty. Tell your assistant which field holds each text piece.",
                          technical="empty chunk_path",
                          copyable="Configuration error: response.stream.chunk_path is empty.")
    mode = cfg_resp.get("mode", "sse")
    pieces: list[str] = []
    skipped_non_json = 0
    first_lines: list[str] = []

    def _handle_payload(payload: str):
        nonlocal skipped_non_json
        if done_marker and payload == done_marker:
            return "done"
        try:
            obj = json.loads(payload)
        except Exception:
            skipped_non_json += 1
            if len(first_lines) < 5:
                first_lines.append(payload[:300])
            return None
        try:
            piece = resolve_path(obj, chunk_path)
        except EngineError as e:
            if e.category == "path_not_found":
                return None  # metadata chunk: silently skipped
            raise
        return piece

    if mode == "ndjson":
        try:
            text = raw.decode("utf-8")
        except Exception:
            raise EngineError("bad_response", "The reply was not readable text.",
                              technical="ndjson not utf-8",
                              copyable="Bad response: the stream was not readable UTF-8 text.")
        for line in text.splitlines():
            if cancel_event is not None and getattr(cancel_event, "is_set", lambda: False)():
                raise EngineError("timeout", "Cancelled.", technical="cancelled mid-stream",
                                  copyable="Cancelled while reading the stream.")
            s = line.strip()
            if not s:
                continue
            r = _handle_payload(s)
            if r == "done":
                break
            if isinstance(r, str) and r:
                pieces.append(r)
                try:
                    if on_chunk is not None:
                        on_chunk(r)
                except Exception:
                    pass
    else:  # sse
        for payload in _iter_sse_events(io.BytesIO(raw), cancel_event):
            r = _handle_payload(payload)
            if r == "done":
                break
            if isinstance(r, str) and r:
                pieces.append(r)
                try:
                    if on_chunk is not None:
                        on_chunk(r)
                except Exception:
                    pass
    if not pieces:
        raise EngineError(
            "stream_empty",
            "The service replied, but nothing matched chunk_path. The path is probably wrong.",
            technical=f"stream yielded 0 pieces (skipped_non_json={skipped_non_json})",
            copyable=("Stream empty: nothing matched response.stream.chunk_path.\n"
                      f"First lines received:\n" + ("\n".join(first_lines[:5]) or "(no text lines)") +
                      "\nFix chunk_path to point at the text piece inside each chunk."),
        )
    return "".join(pieces)


def parse_response(raw: bytes, cfg_resp: dict, on_chunk=None, cancel_event=None) -> str:
    mode = (cfg_resp.get("mode", "json") or "json")
    if mode == "json":
        return parse_json_mode(raw, cfg_resp)
    if mode in ("sse", "ndjson"):
        return parse_stream_mode(raw, cfg_resp, on_chunk, cancel_event)
    if mode == "text":
        try:
            return raw.decode("utf-8").strip()
        except Exception:
            raise EngineError("bad_response", "The reply was not readable text.",
                              technical="text mode not utf-8",
                              copyable="Bad response: the reply was not readable UTF-8 text.")
    raise EngineError("invalid_config", f"Unknown response.mode '{mode}'.",
                      technical=f"mode={mode}",
                      copyable=f"Configuration error: unknown response.mode '{mode}'.")


# --------------------------------------------------------------------------
# Public API: one generic core, dispatched on kind (text/vision/image/tts/stt)
# --------------------------------------------------------------------------

KIND_TITLES = {
    "text": "AI Text Model",
    "vision": "AI Vision Model",
    "image": "AI Image Model",
    "tts": "AI Text to Speech",
    "stt": "AI Speech to Text",
}


def _check_kind(cfg: dict, expect: str | None = None) -> None:
    if cfg.get("version") != PROTOCOL_VERSION:
        raise EngineError("invalid_config",
                          f"Unsupported version '{cfg.get('version')}'. Supported version is 1 only.",
                          technical=f"version={cfg.get('version')}",
                          copyable=f"Unsupported version '{cfg.get('version')}'. Use version 1.")
    if cfg.get("kind") not in SUPPORTED_KINDS:
        raise EngineError("invalid_config",
                          f"Unsupported kind '{cfg.get('kind')}'. Supported: {', '.join(SUPPORTED_KINDS)}.",
                          technical=f"kind={cfg.get('kind')}",
                          copyable=f"Unsupported kind '{cfg.get('kind')}'.")
    if expect is not None and cfg.get("kind") != expect:
        title = KIND_TITLES.get(expect, expect)
        raise EngineError("invalid_config",
                          f"This connection is for '{cfg.get('kind')}'. Use the {title} section for this call.",
                          technical=f"kind={cfg.get('kind')} expected={expect}",
                          copyable=(f"Wrong section: this Connection JSON has kind '{cfg.get('kind')}'.\n"
                                    f"Configure it under {title} instead."))


def _diagnose_http_error(status: int, raw: bytes, cfg: dict, secrets: list) -> EngineError:
    base = _http_status_error(status, raw)
    hint = _extract_error_hint(raw, str((cfg.get("response") or {}).get("error_path") or ""))
    if hint:
        base.message = f"{base.message} Service says: {hint[:300]}"
        base.copyable = f"{base.copyable}\nService error detail: {hint[:500]}"
    return base


def _validated_cfg(model_record: dict, expect: str) -> dict:
    cfg = (model_record or {}).get("config_json") or {}
    _check_kind(cfg, expect)
    problems = validate(cfg)
    if problems:
        raise EngineError("invalid_config", problems[0], technical="; ".join(problems)[:500],
                          copyable="Configuration problems:\n- " + "\n- ".join(problems))
    return cfg


def _shape_of(body: bytes, body_type: str) -> str:
    if body_type == "json":
        try:
            obj = json.loads(body.decode("utf-8", "replace"))
            if isinstance(obj, dict):
                keys = sorted(str(k) for k in obj.keys())[:8]
                # Values never shown (may hold base64 blobs); variables already substituted.
                return f"method-body json keys={keys}"
            return "method-body json list"
        except Exception:
            return "method-body json?"
    if body_type == "binary":
        return f"method-body binary {len(body)} bytes"
    return f"method-body {body_type}"


def chat(model_record: dict, messages: list[dict], options: dict | None = None) -> str:
    """Full reply text. options: {system, on_chunk, cancel_event}."""
    options = options or {}
    system = str(options.get("system") or "")
    on_chunk = options.get("on_chunk")
    cancel_event = options.get("cancel_event")
    if is_aip_record(model_record):
        # New system: SAME AIP/1 path as the loop (compile-once, cached plan).
        if cancel_event is not None and getattr(cancel_event, "is_set", lambda: False)():
            raise EngineError("timeout", "Cancelled.", technical="cancelled",
                              copyable="Cancelled.")
        from core.ai_models import aip as _aip
        built = list(messages or [])
        if system:
            built = [{"role": "system", "content": system}] + built
        res = _aip.execute(model_record, {"version": 1, "messages": built}, "text")
        reply = _aip_text_out(res)
        if on_chunk is not None and reply:
            try:
                on_chunk(reply)
            except Exception:
                pass
        if not reply.strip():
            raise EngineError("bad_response", "The service returned an empty reply.",
                              technical="empty text",
                              copyable="Bad response: expected text.")
        return reply
    cfg = _validated_cfg(model_record, "text")
    url, method, headers, body, _bt, timeout, secrets = build_http_request(
        model_record, messages or [], system)
    if cancel_event is not None and getattr(cancel_event, "is_set", lambda: False)():
        raise EngineError("timeout", "Cancelled.", technical="cancelled",
                          copyable="Cancelled.")
    t0 = time.time()
    shape = f"method={method} {_shape_of(body, _bt)}"
    try:
        status, raw = _send(url, method, headers, body, timeout, cancel_event)
    except urllib.error.HTTPError as e:  # belt-and-suspenders (_send already maps these)
        raise _diagnose_http_error(int(getattr(e, "code", 0) or 0), b"", cfg, secrets)
    except EngineError as e:
        if not e.copyable or "What was attempted" not in e.copyable:
            e.copyable = (f"{e.copyable}\n---\nWhat was attempted: {method} "
                           f"{redact_summary(url, headers, secrets)}\n{shape}")
        log.info("ai_model call failed category=%s status=? latency=? %s",
                 e.category, redact_summary(url, headers, secrets))
        raise
    latency = int((time.time() - t0) * 1000)
    if status >= 400:
        err = _diagnose_http_error(status, raw, cfg, secrets)
        err.copyable = (f"{err.copyable}\n---\nWhat was attempted: {method} "
                        f"{redact_summary(url, headers, secrets)}\n{shape}\n"
                        f"Status: {status}\nLatency: {latency} ms")
        log.info("ai_model call failed category=%s status=%s latency=%sms %s",
                 err.category, status, latency, redact_summary(url, headers, secrets))
        raise err
    try:
        reply = _resolve_output(raw, cfg.get("response") or {}, cfg,
                                model_record, url, headers, secrets,
                                on_chunk=on_chunk, cancel_event=cancel_event)
    except EngineError as e:
        excerpt = raw[:MAX_BODY_PREVIEW].decode("utf-8", errors="replace")
        for s in secrets:
            if s:
                excerpt = excerpt.replace(s, "••••")
        e.copyable = (f"{e.copyable}\n---\nWhat was attempted: {method} "
                      f"{redact_summary(url, headers, secrets)}\n{shape}\n"
                      f"Status: {status}\nResponse excerpt:\n{excerpt[:1500]}")
        log.info("ai_model parse failed category=%s status=%s latency=%sms %s",
                 e.category, status, latency, redact_summary(url, headers, secrets))
        raise
    log.info("ai_model call ok status=%s latency=%sms chars=%s %s",
             status, latency, len(reply), redact_summary(url, headers, secrets))
    return reply


def _media_source(value: str) -> str:
    v = (value or "").strip()
    if v.startswith("data:"):
        return "data_url"
    low = v.lower()
    if low.startswith("http://") or low.startswith("https://"):
        return "url"
    return "base64"


def parse_media_output(raw: bytes, cfg_resp: dict) -> dict:
    """Extract an image/audio output: {"media": str, "source": url|base64|data_url}."""
    out_type = (cfg_resp.get("output") or {}).get("type", "text")
    mode = cfg_resp.get("mode", "json")
    if mode == "text":
        try:
            value = raw.decode("utf-8").strip()
        except Exception:
            raise EngineError("bad_response", "The reply was not readable text.",
                              technical="media text mode not utf-8",
                              copyable="Bad response: the reply was not readable UTF-8 text.")
    else:
        try:
            data = json.loads(raw.decode("utf-8"))
        except Exception:
            raise EngineError("bad_response",
                              "Expected a JSON reply but got something else.",
                              technical=f"media json parse failed, body[:200]={raw[:200]!r}",
                              copyable="Bad response: expected JSON but got something else.")
        path = (cfg_resp.get("output") or {}).get("path", "")
        if not path:
            raise EngineError("invalid_config",
                              "response.output.path is empty. Point it at the file URL or base64 field.",
                              technical="empty output.path for media",
                              copyable="Configuration error: response.output.path is empty.")
        value = resolve_path(data, path)
    if not isinstance(value, str) or not value.strip():
        raise EngineError("bad_response", "The service returned no file (empty value).",
                          technical="media value empty",
                          copyable="Bad response: the service returned an empty file value.")
    value = value.strip()
    if len(value) > MAX_RESPONSE_BYTES:
        raise EngineError("bad_response", "The service reply was too large (over 5 MB).",
                          technical="media value exceeds cap",
                          copyable="Bad response: the media value exceeded 5 MB.")
    return {"media": value, "source": _media_source(value), "type": out_type}


def _resolve_output(raw: bytes, cfg_resp: dict, cfg: dict, model_record: dict,
                    url: str, headers: dict, secrets: list,
                    on_chunk=None, cancel_event=None):
    """Submit-response resolution shared by every kind: instant output, or the
    generic submit-then-poll flow when response.poll is configured."""
    poll = cfg_resp.get("poll")
    if not isinstance(poll, dict):
        out_type = (cfg_resp.get("output") or {}).get("type", "text")
        if out_type == "text":
            return parse_response(raw, cfg_resp, on_chunk, cancel_event)
        return parse_media_output(raw, cfg_resp)
    return _poll_to_output(raw, cfg_resp, poll, cfg, model_record, url, headers,
                           secrets, on_chunk=on_chunk, cancel_event=cancel_event)


def _poll_to_output(submit_raw: bytes, cfg_resp: dict, poll: dict, cfg: dict,
                    model_record: dict, submit_url: str, submit_headers: dict,
                    secrets: list, on_chunk=None, cancel_event=None):
    """Generic async flow: submit returns a job id -> poll status -> read result."""
    try:
        submit_data = json.loads(submit_raw.decode("utf-8"))
    except Exception:
        raise EngineError("bad_response",
                          "The submit reply was not JSON, so no job id could be read.",
                          technical=f"poll submit body[:200]={submit_raw[:200]!r}",
                          copyable="Bad response: the first reply was not JSON.")
    id_path = str(poll.get("id_path") or "")
    job_id = resolve_path(submit_data, id_path) if id_path else ""
    job_id = str(job_id or "").strip()
    if not job_id:
        # No job id and no id_path: maybe the result is already inline.
        result_path = str(poll.get("result_path") or "")
        if result_path:
            try:
                return _scalar_text(resolve_path(submit_data, result_path), result_path)
            except EngineError:
                pass
        raise EngineError("bad_response", "The service gave no job to wait for.",
                          technical="poll: empty job id",
                          copyable="Bad response: no job id found. Check response.poll.id_path.")
    interval = max(1.0, float(poll.get("interval_seconds", 5)))
    max_wait = min(3600.0, max(1.0, float(poll.get("max_wait_seconds", 300))))
    status_path = str(poll.get("status_path") or "")
    done_value = poll.get("done_value")
    fail_value = poll.get("fail_value")
    result_path = str(poll.get("result_path") or "")
    result_type = str(poll.get("result_type", (cfg_resp.get("output") or {}).get("type", "text")))
    deadline = time.time() + max_wait
    last_status = ""
    while True:
        if cancel_event is not None and getattr(cancel_event, "is_set", lambda: False)():
            raise EngineError("timeout", "Cancelled while waiting for the job.",
                              technical="poll cancelled",
                              copyable="Cancelled while waiting for the job.")
        if time.time() > deadline:
            raise EngineError("timeout",
                              "The job took too long. Raise response.poll.max_wait_seconds or try again.",
                              technical=f"poll timeout last_status={last_status[:100]}",
                              copyable="Timeout while waiting for the job.")
        time.sleep(interval)
        req = dict(cfg.get("request", {}) or {})
        if poll.get("path"):
            req["path"] = poll["path"]
        if poll.get("query"):
            req["query"] = poll["query"]
        req["method"] = poll.get("method", "GET")
        req["body_type"] = "json"
        req["body"] = {}
        base_vars = {"model": str(model_record.get("model_name") or ""),
                     "api_key": str(model_record.get("api_key") or ""),
                     "job_id": job_id, "system": ""}
        url2, method2, headers2, body2, _b, timeout2, _s = _build_from_vars(
            model_record, req, base_vars)
        status2, raw2 = _send(url2, method2, headers2, body2, timeout2, cancel_event)
        if status2 >= 400:
            raise _diagnose_http_error(status2, raw2, cfg, secrets)
        try:
            pdata = json.loads(raw2.decode("utf-8"))
        except Exception:
            continue  # transient non-JSON poll reply: keep waiting
        try:
            cur = resolve_path(pdata, status_path) if status_path else ""
        except EngineError:
            continue
        last_status = str(cur)
        if fail_value is not None and str(cur) == str(fail_value):
            hint = _extract_error_hint(raw2, str(cfg_resp.get("error_path") or ""))
            raise EngineError("server", f"The job failed ({cur})." + (f" Service says: {hint[:200]}" if hint else ""),
                              technical=f"poll failed status={cur}",
                              copyable=f"The job failed with status '{cur}'.")
        if str(cur) != str(done_value):
            continue
        if not result_path:
            return last_status
        value = resolve_path(pdata, result_path)
        if result_type in ("image", "audio"):
            if not isinstance(value, str) or not value.strip():
                raise EngineError("bad_response", "The finished job returned no file.",
                                  technical="poll result empty",
                                  copyable="Bad response: the finished job returned no file.")
            return {"media": value.strip(), "source": _media_source(value.strip()), "type": result_type}
        return _scalar_text(value, result_path)


def test_connection(model_record: dict) -> dict:
    """Structured Test result (never raises). Kind-aware fixed probes."""
    t0 = time.time()
    try:
        cfg = (model_record or {}).get("config_json") or {}
        kind = cfg.get("kind")
        _check_kind(cfg)
        problems = validate(cfg)
        if problems:
            raise EngineError("invalid_config", problems[0],
                              technical="; ".join(problems)[:500],
                              copyable="Configuration problems:\n- " + "\n- ".join(problems))
        preview = _run_kind_test(model_record, kind, cfg)
        latency = int((time.time() - t0) * 1000)
        log.info("ai_model test ok kind=%s latency=%sms", kind, latency)
        return {"ok": True, "latency_ms": latency,
                "reply_preview": str(preview)[:200] or "OK", "error": None}
    except EngineError as e:
        latency = int((time.time() - t0) * 1000)
        log.info("ai_model test failed category=%s latency=%sms", e.category, latency)
        return {"ok": False, "latency_ms": latency, "reply_preview": None,
                "error": {"category": e.category, "message": e.message,
                          "technical": e.technical, "copyable": e.copyable}}
    except Exception as e:
        latency = int((time.time() - t0) * 1000)
        msg = f"Unexpected error: {e}"[:300]
        log.info("ai_model test failed category=server latency=%sms", latency)
        return {"ok": False, "latency_ms": latency, "reply_preview": None,
                "error": {"category": "server", "message": msg,
                          "technical": msg, "copyable": msg}}


# --------------------------------------------------------------------------
# Kind calls: vision / image / tts / stt (same core, kind-specific variables)
# --------------------------------------------------------------------------

def _post_with_vars(model_record: dict, cfg: dict, variables: dict,
                    options: dict | None = None):
    """Shared submit: build from vars -> send -> map HTTP errors. Returns ctx."""
    options = options or {}
    cancel_event = options.get("cancel_event")
    req = cfg.get("request", {}) if isinstance(cfg.get("request"), dict) else {}
    url, method, headers, body, body_type, timeout, secrets = _build_from_vars(
        model_record, req, variables)
    if cancel_event is not None and getattr(cancel_event, "is_set", lambda: False)():
        raise EngineError("timeout", "Cancelled.", technical="cancelled",
                          copyable="Cancelled.")
    t0 = time.time()
    shape = f"method={method} {_shape_of(body, body_type)}"
    try:
        status, raw = _send(url, method, headers, body, timeout, cancel_event)
    except urllib.error.HTTPError as e:
        raise _diagnose_http_error(int(getattr(e, "code", 0) or 0), b"", cfg, secrets)
    except EngineError as e:
        if not e.copyable or "What was attempted" not in e.copyable:
            e.copyable = (f"{e.copyable}\n---\nWhat was attempted: {method} "
                           f"{redact_summary(url, headers, secrets)}\n{shape}")
        log.info("ai_model call failed category=%s %s", e.category,
                 redact_summary(url, headers, secrets))
        raise
    latency = int((time.time() - t0) * 1000)
    if status >= 400:
        err = _diagnose_http_error(status, raw, cfg, secrets)
        err.copyable = (f"{err.copyable}\n---\nWhat was attempted: {method} "
                        f"{redact_summary(url, headers, secrets)}\n{shape}\n"
                        f"Status: {status}\nLatency: {latency} ms")
        log.info("ai_model call failed category=%s status=%s latency=%sms %s",
                 err.category, status, latency, redact_summary(url, headers, secrets))
        raise err
    return {"raw": raw, "status": status, "latency_ms": latency, "url": url,
            "method": method, "headers": headers, "shape": shape, "secrets": secrets}


def _finish_text_out(ctx: dict, cfg: dict, options: dict | None = None) -> str:
    options = options or {}
    try:
        reply = _resolve_output(ctx["raw"], cfg.get("response") or {}, cfg,
                                options.get("_record") or {}, ctx["url"], ctx["headers"],
                                ctx["secrets"], on_chunk=options.get("on_chunk"),
                                cancel_event=options.get("cancel_event"))
    except EngineError as e:
        excerpt = ctx["raw"][:MAX_BODY_PREVIEW].decode("utf-8", errors="replace")
        for s in ctx["secrets"]:
            if s:
                excerpt = excerpt.replace(s, "••••")
        e.copyable = (f"{e.copyable}\n---\nWhat was attempted: {ctx['method']} "
                      f"{redact_summary(ctx['url'], ctx['headers'], ctx['secrets'])}\n{ctx['shape']}\n"
                      f"Status: {ctx['status']}\nResponse excerpt:\n{excerpt[:1500]}")
        raise
    log.info("ai_model call ok kind=%s status=%s latency=%sms",
             (cfg.get("kind") or "?"), ctx["status"], ctx["latency_ms"])
    if not isinstance(reply, str):
        raise EngineError("bad_response", "The service did not return text.",
                          technical=f"unexpected output {type(reply).__name__}",
                          copyable="Bad response: expected text.")
    return reply


def _finish_media_out(ctx: dict, cfg: dict, options: dict | None = None) -> dict:
    options = options or {}
    try:
        out = _resolve_output(ctx["raw"], cfg.get("response") or {}, cfg,
                              options.get("_record") or {}, ctx["url"], ctx["headers"],
                              ctx["secrets"], cancel_event=options.get("cancel_event"))
    except EngineError as e:
        excerpt = ctx["raw"][:MAX_BODY_PREVIEW].decode("utf-8", errors="replace")
        for s in ctx["secrets"]:
            if s:
                excerpt = excerpt.replace(s, "••••")
        e.copyable = (f"{e.copyable}\n---\nWhat was attempted: {ctx['method']} "
                      f"{redact_summary(ctx['url'], ctx['headers'], ctx['secrets'])}\n{ctx['shape']}\n"
                      f"Status: {ctx['status']}\nResponse excerpt:\n{excerpt[:1500]}")
        raise
    log.info("ai_model call ok kind=%s status=%s latency=%sms",
             (cfg.get("kind") or "?"), ctx["status"], ctx["latency_ms"])
    if not isinstance(out, dict) or "media" not in out:
        raise EngineError("bad_response", "The service did not return a file.",
                          technical=f"unexpected output {type(out).__name__}",
                          copyable="Bad response: expected a file (URL or base64).")
    return out


def _base_vars(model_record: dict) -> dict:
    return {"model": str(model_record.get("model_name") or ""),
            "api_key": str(model_record.get("api_key") or ""),
            "system": ""}


def analyze(model_record: dict, inputs: dict, options: dict | None = None) -> str:
    """Vision: describe/answer about an image. inputs: {prompt, image_base64?, image_url?, image_mime?}."""
    options = dict(options or {})
    inputs = inputs or {}
    system = str(options.get("system") or "")
    if is_aip_record(model_record):
        # New system: image travels as a canonical typed part (url or data-URL).
        from core.ai_models import aip as _aip
        prompt = str(inputs.get("prompt") or "")
        parts: list[dict] = []
        if prompt:
            parts.append({"type": "text", "text": prompt})
        image_url = str(inputs.get("image_url") or "")
        image_b64 = str(inputs.get("image_base64") or "")
        mime = str(inputs.get("image_mime") or "image/png")
        if image_url:
            parts.append({"type": "image", "url": image_url, "mime": mime})
        elif image_b64:
            parts.append({"type": "image", "data": f"data:{mime};base64,{image_b64}", "mime": mime})
        res = _aip.execute(model_record, {
            "version": 1,
            "messages": _aip_messages(system, parts),
            "modalities": {"input": ["text", "image"], "output": ["text"]},
        }, "vision")
        reply = _aip_text_out(res)
        if not reply.strip():
            raise EngineError("bad_response", "The service did not return text.",
                              technical="empty text",
                              copyable="Bad response: expected text.")
        return reply
    cfg = _validated_cfg(model_record, "vision")
    variables = _base_vars(model_record)
    variables.update({
        "prompt": str(inputs.get("prompt") or ""),
        "image_base64": str(inputs.get("image_base64") or ""),
        "image_url": str(inputs.get("image_url") or ""),
        "image_mime": str(inputs.get("image_mime") or "image/png"),
        "messages": [],
        "system": str(options.get("system") or ""),
    })
    options["_record"] = model_record
    ctx = _post_with_vars(model_record, cfg, variables, options)
    return _finish_text_out(ctx, cfg, options)


def generate_image(model_record: dict, inputs: dict, options: dict | None = None) -> dict:
    """Image generation: {prompt} -> {"media", "source", "type": "image"} (polls when configured)."""
    options = dict(options or {})
    inputs = inputs or {}
    system = str(options.get("system") or "")
    if is_aip_record(model_record):
        # New system: single-shot JSON only (AIP/1 refuses async poll flows).
        from core.ai_models import aip as _aip
        res = _aip.execute(model_record, {
            "version": 1,
            "messages": _aip_messages(system, str(inputs.get("prompt") or "")),
            "modalities": {"input": ["text"], "output": ["image"]},
        }, "image")
        data, source = _aip_media_out(res)
        if not data:
            raise EngineError("bad_response", "The service did not return a file.",
                              technical="missing media",
                              copyable="Bad response: expected a file (URL or base64).")
        return {"media": data, "source": source, "type": "image"}
    cfg = _validated_cfg(model_record, "image")
    variables = _base_vars(model_record)
    variables.update({"prompt": str(inputs.get("prompt") or ""),
                      "system": str(options.get("system") or "")})
    options["_record"] = model_record
    ctx = _post_with_vars(model_record, cfg, variables, options)
    return _finish_media_out(ctx, cfg, options)


def synthesize(model_record: dict, inputs: dict, options: dict | None = None) -> dict:
    """Text-to-speech: {text} -> {"media", "source", "type": "audio"} (polls when configured)."""
    options = dict(options or {})
    inputs = inputs or {}
    system = str(options.get("system") or "")
    if is_aip_record(model_record):
        # New system: single-shot JSON only (AIP/1 refuses async poll flows).
        from core.ai_models import aip as _aip
        res = _aip.execute(model_record, {
            "version": 1,
            "messages": _aip_messages(system, str(inputs.get("text") or "")),
            "modalities": {"input": ["text"], "output": ["audio"]},
        }, "tts")
        data, source = _aip_media_out(res)
        if not data:
            raise EngineError("bad_response", "The service did not return a file.",
                              technical="missing media",
                              copyable="Bad response: expected a file (URL or base64).")
        return {"media": data, "source": source, "type": "audio"}
    cfg = _validated_cfg(model_record, "tts")
    variables = _base_vars(model_record)
    variables.update({"text": str(inputs.get("text") or ""),
                      "system": str(options.get("system") or "")})
    options["_record"] = model_record
    ctx = _post_with_vars(model_record, cfg, variables, options)
    return _finish_media_out(ctx, cfg, options)


def transcribe(model_record: dict, inputs: dict, options: dict | None = None) -> str:
    """Speech-to-text: {audio_bytes, mime?} -> text."""
    options = dict(options or {})
    inputs = inputs or {}
    blob = inputs.get("audio_bytes")
    if not isinstance(blob, (bytes, bytearray)) or not blob:
        raise EngineError("invalid_config", "No audio was provided for transcription.",
                          technical="audio_bytes missing",
                          copyable="No audio reached the speech-to-text model.")
    if len(blob) > MAX_AUDIO_BYTES:
        raise EngineError("invalid_config", "Audio too large (max 25 MB).",
                          technical=f"audio {len(blob)} bytes",
                          copyable="Audio too large (max 25 MB).")
    import base64 as _b64
    raw_b64 = _b64.b64encode(bytes(blob)).decode("ascii")
    mime = str(inputs.get("mime") or "audio/wav")
    if is_aip_record(model_record):
        # New system: audio travels as a canonical typed part (data-URL).
        from core.ai_models import aip as _aip
        system = str(options.get("system") or "")
        res = _aip.execute(model_record, {
            "version": 1,
            "messages": _aip_messages(system, [
                {"type": "audio", "data": f"data:{mime};base64,{raw_b64}", "mime": mime},
            ]),
            "modalities": {"input": ["audio"], "output": ["text"]},
        }, "stt")
        reply = _aip_text_out(res)
        if not reply.strip():
            raise EngineError("bad_response", "The service did not return text.",
                              technical="empty text",
                              copyable="Bad response: expected text.")
        return reply
    cfg = _validated_cfg(model_record, "stt")
    variables = _base_vars(model_record)
    variables.update({"audio_base64": raw_b64,
                      "audio_mime": mime,
                      "system": str(options.get("system") or "")})
    options["_record"] = model_record
    ctx = _post_with_vars(model_record, cfg, variables, options)
    return _finish_text_out(ctx, cfg, options)


def _tiny_png_b64() -> str:
    import base64 as _b64
    # 1x1 transparent PNG (fixed test asset, no network).
    return _b64.b64encode(bytes.fromhex(
        "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
        "0000000d49444154789c626001000000ffff03000006000557bfabd40000000049454e44ae426082")).decode("ascii")


def _silence_wav() -> bytes:
    import io as _io
    import struct as _st
    import wave as _wv
    buf = _io.BytesIO()
    with _wv.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(8000)
        w.writeframes(_st.pack("<400h", *([0] * 400)))
    return buf.getvalue()


def _run_kind_test(model_record: dict, kind: str, cfg: dict):
    """Fixed tiny probe per kind. Returns preview text (media kinds: short summary)."""
    if kind == "text":
        return chat(model_record, [{"role": "user", "content": TEST_MESSAGE}], {})
    if kind == "vision":
        return analyze(model_record, {"prompt": "Reply with the single word: OK",
                                      "image_base64": _tiny_png_b64(),
                                      "image_mime": "image/png"}, {})
    if kind == "image":
        out = generate_image(model_record, {"prompt": "a tiny red square icon"}, {})
        return f"Image OK ({out.get('source', '?')})"
    if kind == "tts":
        out = synthesize(model_record, {"text": "OK"}, {})
        return f"Audio OK ({out.get('source', '?')})"
    if kind == "stt":
        return transcribe(model_record, {"audio_bytes": _silence_wav(), "mime": "audio/wav"}, {})
    raise EngineError("invalid_config", f"Unsupported kind '{kind}'.",
                      technical=f"kind={kind}",
                      copyable=f"Unsupported kind '{kind}'.")
