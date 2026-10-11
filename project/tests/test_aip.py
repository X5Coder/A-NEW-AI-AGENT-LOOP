"""AIP/1 interpreter tests (mock HTTP only, no real keys/network)."""
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

project_root = Path(__file__).resolve().parents[1]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from core.ai_models import aip as A

SECRET = "sk-test-AIP-SECRET"


def _rec(server_url, parser=None, api_url="/v1/chat/completions"):
    return {"model_name": "gpt-x", "server_url": server_url, "api_url": api_url,
            "api_key": SECRET, "config_json": parser or A.openai_text_parser(),
            "parser_definition": parser or A.openai_text_parser(), "kind": "text"}


def test_ops_literal_ref_coalesce():
    ctx = {"input": {"a": None, "b": "hi"}, "model": {"model_name": "m"}}
    assert A.evaluate({"literal": 5}, ctx) == 5
    assert A.evaluate({"ref": "input.b"}, ctx) == "hi"
    assert A.evaluate({"coalesce": [{"ref": "input.a"}, {"literal": "fb"}]}, ctx) == "fb"


def test_ops_map_if_omit():
    ctx = {"input": {"items": [{"n": "a"}, {"n": None}]}, "model": {}}
    out = A.evaluate({"map": {"over": {"ref": "input.items"},
                               "item": {"select": "$item.n"}}}, ctx)
    assert out == ["a", None]
    assert A.evaluate({"if": {"cond": {"equals": [{"literal": 1}, {"literal": 1}]},
                               "then": {"literal": "y"}, "else": {"literal": "n"}}}, ctx) == "y"
    assert A.evaluate({"omit_if_null": {"literal": None}}, ctx) is A.OMIT


def test_ops_parse_json_rejects_bad_args():
    ctx = {"response": {"c": "not-json{"}}
    try:
        A.evaluate({"parse_json": {"select": "response.c"}}, ctx)
        raise AssertionError("should have raised")
    except A.AipError as e:
        assert e.category == "response_decoding_error"


def test_missing_vs_null():
    # Missing path -> None (absent). Explicit null -> None but via different route.
    # Both must NOT be confused with false/0/"".
    ctx = {"response": {"a": None, "b": False, "c": 0, "d": ""}}
    assert A.evaluate({"ref": "response.missing_deep.x"}, ctx) is None
    assert A.evaluate({"ref": "response.a"}, ctx) is None
    assert A.evaluate({"ref": "response.b"}, ctx) is False
    assert A.evaluate({"ref": "response.c"}, ctx) == 0
    assert A.evaluate({"ref": "response.d"}, ctx) == ""


def test_validate_parser_rejects_code():
    bad = {"language": "AIP/1", "version": 1, "request": {"method": "POST"},
           "response": {}, "auth": {"type": "none"},
           "request_eval": "eval(__import__('os').system('x'))",
           "body": {"literal": "{{secrets.key}}"}}
    probs = A.validate_parser(bad)
    assert probs, "must reject malicious parser"


def test_url_policy_blocks():
    for bad_url in ("javascript:alert(1)", "file:///etc/passwd",
                    "http://169.254.169.254/latest", "ftp://x/y"):
        try:
            A.check_url(bad_url)
            raise AssertionError(f"should block {bad_url}")
        except A.AipError:
            pass


def test_openai_body_build_no_secret_in_parser():
    rec = _rec("https://api.openai.com")
    url, method, headers, body = A.build_request(rec, {
        "version": 1, "messages": [{"role": "user", "content": "hi"}]})
    assert url == "https://api.openai.com/v1/chat/completions"
    assert headers.get("Authorization") == f"Bearer {SECRET}"
    payload = json.loads(body.decode())
    assert payload["model"] == "gpt-x"
    assert payload["messages"] == [{"role": "user", "content": "hi"}]
    raw_parser = json.dumps(rec["parser_definition"])
    assert SECRET not in raw_parser


def test_normalize_openai_text_and_tools():
    parser = A.openai_text_parser()
    prov = json.dumps({"id": "r1", "choices": [{"message": {
        "content": "hello",
        "tool_calls": [{"id": "c1", "function": {
            "name": "get_weather",
            "arguments": "{\"city\": \"Cairo\"}"}}]},
        "finish_reason": "tool_calls"}], "usage": {"total_tokens": 5}})
    out = A.normalize(prov, parser)
    assert out["text"] == "hello"
    assert out["tool_calls"][0]["name"] == "get_weather"
    assert out["tool_calls"][0]["arguments"] == {"city": "Cairo"}
    assert out["finish_reason"] == "tool_calls"
    assert out["response_id"] == "r1"


def test_normalize_skips_bad_tool_args():
    parser = A.openai_text_parser()
    prov = json.dumps({"choices": [{"message": {
        "content": "t",
        "tool_calls": [{"id": "c9", "function": {
            "name": "bad_tool", "arguments": "{oops"}}]}}]})
    out = A.normalize(prov, parser)
    assert "tool_calls" not in out
    assert out["warnings"], "must warn on malformed args"


def test_normalize_image_media():
    parser = {"language": "AIP/1", "version": 1, "request": {"method": "POST"},
              "response": {"media": {"select": "response.data[0].b64_json"},
                           "usage": {"select": "response.usage"}},
              "auth": {"type": "none"}}
    assert A.validate_parser(parser) == []
    prov = json.dumps({"created": 1713833628, "data": [{"b64_json": "aGVsbG8="}],
                       "usage": {"total_tokens": 100}})
    out = A.normalize(prov, parser)
    assert out["media"] == {"data": "aGVsbG8=", "source": "base64"}
    assert out["usage"] == {"total_tokens": 100}
    assert "text" not in out  # never fabricated


def test_parts_validation():
    good = {"version": 1, "messages": [
        {"role": "user", "content": [
            {"type": "text", "text": "what is this?"},
            {"type": "image", "url": "https://x/y.png", "mime": "image/png"},
            {"type": "audio", "b64": "AAA=", "mime": "audio/wav"},
            {"type": "video", "url": "https://x/v.mp4"}]}]}
    assert A.validate_canonical(good) == []
    bad = {"version": 1, "messages": [
        {"role": "user", "content": [{"type": "pdf", "data": "x"}]}]}
    assert A.validate_canonical(bad), "unknown part type must be rejected"
    nodata = {"version": 1, "messages": [
        {"role": "user", "content": [{"type": "image"}]}]}
    assert A.validate_canonical(nodata), "image without data must be rejected"


def test_collect_media_nested():
    ctx = {"input": {"messages": [
        {"role": "user", "content": [
            {"type": "text", "text": "hi"},
            {"type": "image", "url": "https://x/1.png"},
            {"type": "audio", "url": "https://x/a.mp3"}]}]}}
    assert A.evaluate({"collect_media": {"parts": {"ref": "input.messages"},
                                          "kind": "image"}}, ctx) == ["https://x/1.png"]
    assert A.evaluate({"collect_media": {"parts": {"ref": "input.messages"},
                                          "kind": "text"}}, ctx) == ["hi"]


def test_modalities_gate():
    parser = A.openai_vision_parser()
    assert A.validate_parser(parser) == []
    blocked = {"version": 1, "messages": [{"role": "user", "content": "x"}],
               "modalities": {"input": ["text", "audio"]}}
    assert A.check_capabilities(parser, blocked), "audio input must be refused"
    ok = {"version": 1, "messages": [{"role": "user", "content": "x"}],
          "modalities": {"input": ["text", "image"], "output": ["text"]}}
    assert A.check_capabilities(parser, ok) == []


def test_vision_body_build():
    rec = {"model_name": "gpt-x", "server_url": "https://api.openai.com",
           "api_url": "/v1/chat/completions", "api_key": SECRET,
           "config_json": A.openai_vision_parser(),
           "parser_definition": A.openai_vision_parser(), "kind": "text"}
    req = {"version": 1, "messages": [
        {"role": "user", "content": [
            {"type": "text", "text": "describe"},
            {"type": "image", "url": "https://x/1.png"}]}],
        "modalities": {"input": ["text", "image"]}}
    url, method, headers, body = A.build_request(rec, req)
    payload = json.loads(body.decode())
    assert payload["messages"][0]["content"] == [
        {"type": "text", "text": "describe"},
        {"type": "image_url", "image_url": {"url": "https://x/1.png"}}]


def test_async_workflow_refused_explicitly():
    p = A.openai_text_parser()
    p["capabilities"] = dict(p.get("capabilities") or {}, async_workflow=True)
    rec = {"model_name": "m", "server_url": "https://x", "api_url": "/v",
           "api_key": "", "config_json": p, "parser_definition": p, "kind": "text"}
    try:
        A.execute(rec, {"version": 1, "messages": [{"role": "user", "content": "hi"}]})
        raise AssertionError("async must be refused, not pretended")
    except A.AipError as e:
        assert e.category == "unsupported_capability"


def _private_parser():
    return {
        "language": "AIP/1", "version": 1,
        "capabilities": {"tools": True, "streaming": False,
                          "input_modalities": ["text"], "output_modalities": ["text"]},
        "auth": {"type": "none"},
        "request": {"method": "POST",
                    "headers": {"literal": {"Content-Type": "application/json"}},
                    "body": {"object": {"messages": {"ref": "input.messages"}}}},
        "response": {
            "text": {"select": "response.reply.text"},
            "tool_calls": {"map": {
                "over": {"coalesce": [{"select": "response.actions"}, {"literal": []}]},
                "item": {"object": {
                    "id": {"select": "$item.id"},
                    "name": {"select": "$item.tool"},
                    "arguments": {"select": "$item.args"},
                    "status": {"if": {
                        "cond": {"equals": [{"select": "$item.needs_confirmation"},
                                            {"literal": True}]},
                        "then": {"literal": "needs_approval"},
                        "else": {"literal": "pending"}}},
                    "metadata": {"object": {
                        "depends_on": {"select": "$item.depends_on"}}}}}}},
            "provider_metadata": {"object": {
                "speech": {"select": "response.reply.speech"},
                "emotion": {"select": "response.reply.emotion"},
                "language": {"select": "response.language"},
                "memory": {"select": "response.memory"},
                "followup": {"select": "response.followup"}}},
        },
        "errors": {"path": "response.error"},
    }


def test_dotted_tool_names_allowed():
    req = {"version": 1, "messages": [{"role": "user", "content": "x"}],
           "tools": [{"name": "browser.open", "description": "d",
                      "parameters_schema": {"type": "object"}}]}
    assert A.validate_canonical(req) == []


def test_private_server_shape():
    parser = _private_parser()
    assert A.validate_parser(parser) == []
    prov = json.dumps({
        "version": "1.0", "status": "ok", "language": "ar",
        "reply": {"text": "done", "speech": "done", "emotion": "neutral"},
        "actions": [
            {"id": "a1", "tool": "browser.open", "args": {"url": "https://example.com"},
             "needs_confirmation": False},
            {"id": "a2", "tool": "shell.run", "args": {"cmd": "ls -la"},
             "needs_confirmation": True, "depends_on": ["a1"]}],
        "vision": {"needed": False, "target": None},
        "memory": {"save": [{"key": "preferred_voice", "value": "jarvis"}]},
        "followup": {"question": None, "expects_reply": False},
        "error": None})
    out = A.normalize(prov, parser)
    assert out["text"] == "done"
    assert [c["name"] for c in out["tool_calls"]] == ["browser.open", "shell.run"]
    assert [c["status"] for c in out["tool_calls"]] == ["pending", "needs_approval"]
    assert out["tool_calls"][1]["metadata"] == {"depends_on": ["a1"]}
    assert out["provider_metadata"]["emotion"] == "neutral"
    assert out["provider_metadata"]["language"] == "ar"
    assert "usage" not in out  # absent stays absent, never fabricated


def test_private_server_error_path():
    out = None
    try:
        A.normalize(json.dumps({"reply": {"text": "x"}, "error": "boom"}), _private_parser())
    except A.AipError as e:
        out = e
    assert out is not None and "boom" in out.message


def test_auto_openai_text():
    out = A.normalize_auto(json.dumps(
        {"choices": [{"message": {"content": "hi"}}], "usage": {"total_tokens": 3}}))
    assert out["text"] == "hi"
    assert out["usage"] == {"total_tokens": 3}
    assert any("Auto-detected" in w for w in out["warnings"])


def test_auto_private_shape():
    prov = json.dumps({
        "reply": {"text": "done", "speech": "done"},
        "actions": [{"id": "a1", "tool": "browser.open",
                     "args": {"url": "https://example.com"}}],
        "error": None})
    out = A.normalize_auto(prov)
    assert out["text"] == "done"
    assert out["tool_calls"][0]["name"] == "browser.open"


def test_auto_image_and_error():
    out = A.normalize_auto(json.dumps({"data": [{"b64_json": "aGVsbG8="}]}))
    assert out["media"]["source"] == "base64"
    try:
        A.normalize_auto(json.dumps({"reply": {"text": "x"}, "error": "bad key"}))
        raise AssertionError("error must fail")
    except A.AipError as e:
        assert "bad key" in e.message


def test_auto_unknown_shape_fails_clearly():
    try:
        A.normalize_auto(json.dumps({"foo": 1, "bar": [1, 2]}))
        raise AssertionError("unknown shape must fail")
    except A.AipError as e:
        assert "foo" in e.message


def test_auto_flag_via_parser():
    parser = {"language": "AIP/1", "version": 1,
              "request": {"method": "POST"},
              "response": {"auto": {"literal": True}},
              "auth": {"type": "none"}}
    assert A.validate_parser(parser) == []
    out = A.normalize(json.dumps({"text": "auto ok"}), parser)
    assert out["text"] == "auto ok"


def test_auto_nameless_text_and_media():
    prov = json.dumps({"zzz_antwort": "hello world answer", "qq": 5,
                       "www_file": "https://cdn.x/f.mp4"})
    out = A.normalize_auto(prov)
    assert out["text"] == "hello world answer"
    assert any("Low-confidence text" in w for w in out["warnings"])
    assert out["media"] == {"data": "https://cdn.x/f.mp4", "source": "url"}


def test_auto_names_are_not_tool_calls():
    # A name without arguments is data, never a tool call.
    out = A.normalize_auto(json.dumps({"users": [{"name": "Ali", "age": 30}],
                                       "text": "hi"}))
    assert "tool_calls" not in out
    assert out["text"] == "hi"


def _auth_rec(auth, api_key="sk-K"):
    p = {"language": "AIP/1", "version": 1,
         "capabilities": {},
         "auth": auth,
         "request": {"method": "POST",
                     "headers": {"literal": {}},
                     "body": {"object": {"messages": {"ref": "input.messages"}}}},
         "response": {"text": {"literal": "x"}}}
    assert A.validate_parser(p) == []
    return {"model_name": "m", "server_url": "https://srv.test",
            "api_url": "/go", "api_key": api_key,
            "config_json": p, "parser_definition": p, "kind": "text"}


def _canon():
    return {"version": 1, "messages": [{"role": "user", "content": "hi"}]}


def test_auth_token_placements():
    url, _, headers, body = A.build_request(
        _auth_rec({"type": "bearer"}), _canon())
    assert headers["Authorization"] == "Bearer sk-K"

    url, _, headers, _ = A.build_request(
        _auth_rec({"type": "header", "header": "X-Token"}), _canon())
    assert headers["X-Token"] == "sk-K"

    url, _, _, _ = A.build_request(
        _auth_rec({"type": "query", "param": "token"}), _canon())
    assert "token=sk-K" in url

    _, _, _, body = A.build_request(
        _auth_rec({"type": "body_token", "field": "key"}), _canon())
    assert json.loads(body.decode())["key"] == "sk-K"

    _, _, headers, body = A.build_request(_auth_rec({"type": "none"}, ""), _canon())
    assert "Authorization" not in headers
    assert "sk-K" not in json.dumps({"h": headers, "b": body.decode()})

    try:
        A.build_request(_auth_rec({"type": "bearer"}, ""), _canon())
        raise AssertionError("missing key must fail")
    except A.AipError as e:
        assert e.category == "authentication_failure"


def test_compile_once_fast_path():
    parser = A.openai_text_parser()
    rec = {"model_name": "m", "server_url": "https://srv.test", "api_url": "/go",
           "api_key": "k", "config_json": parser, "parser_definition": parser,
           "kind": "text"}
    A._COMPILED.clear()
    c1 = A.get_compiled(rec, "text")
    c2 = A.get_compiled(rec, "text")
    assert c1 is c2  # interpreter ran once; second call is the cached plan
    canon = {"version": 1, "messages": [{"role": "user", "content": "hi"}]}
    assert A._build_from_compiled(c2, canon) == A.build_request(rec, canon)
    rec2 = dict(rec, api_url="/v2")
    assert A.get_compiled(rec2, "text") is not c1  # config changed -> recompile
    try:
        A.execute_compiled(c2, {"version": 1, "messages": []})
        raise AssertionError("bad input must still be validated on fast path")
    except A.AipError as e:
        assert e.category == "invalid_request"
    try:
        A.compile_model({"model_name": "m", "server_url": "https://x",
                         "config_json": {"language": "nope"}, "kind": "text"})
        raise AssertionError("bad parser must fail at compile time")
    except A.AipError as e:
        assert e.category == "parser_validation_error"


def test_models_prompts_teach_aip1():
    from core.ai_models.models_prompts import get_prompt, read_all
    p = project_root / "core" / "ai_models" / "models_prompts.md"
    assert p.exists(), "missing models_prompts.md"
    sections = read_all()
    for kind in ("text", "vision", "image", "tts", "stt"):
        text = sections.get(kind) or get_prompt(kind)
        assert text and text.strip(), f"missing prompt section for {kind}"
        assert '"language": "AIP/1"' in text, f"{kind} prompt must teach AIP/1"
        assert "{{api_key}}" not in text, f"{kind} prompt must not use legacy variables"
        assert "Field 5" in text, f"{kind} prompt must describe all 5 dialog fields"


class _Mock(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n) if n else b""
        assert self.headers.get("Authorization") == f"Bearer {SECRET}"
        assert json.loads(body.decode())["model"] == "gpt-x"
        reply = {"id": "r9", "choices": [{"message": {"content": "OK"},
                                          "finish_reason": "stop"}]}
        raw = json.dumps(reply).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


def test_execute_end_to_end_mock():
    srv = HTTPServer(("127.0.0.1", 0), _Mock)
    port = srv.server_address[1]
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        rec = _rec(f"http://127.0.0.1:{port}")
        out = A.execute(rec, {"version": 1, "messages": [
            {"role": "user", "content": "Reply with the single word: OK"}]})
        assert out["text"] == "OK"
        assert out["response_id"] == "r9"
    finally:
        srv.shutdown()


def test_canonical_keeps_tool_wiring():
    msgs = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "",
         "tool_calls": [{"id": "c1", "type": "function",
                         "function": {"name": "tool_search", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "c1", "name": "tool_search",
         "content": '{"tools": []}', "internal_note": "must not leak"},
    ]
    out = A.canonical_from_loop(msgs, [])
    assert out["messages"][1]["tool_calls"][0]["function"]["name"] == "tool_search"
    assert out["messages"][2]["tool_call_id"] == "c1"
    assert "internal_note" not in out["messages"][2]
    assert out["messages"][2]["content"] == '{"tools": []}'
