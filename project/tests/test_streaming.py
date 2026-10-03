import sys
from pathlib import Path

project_root = Path(__file__).resolve().parents[1]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from core.lm.providers.openrouter.provider import _chat_stream

AR = "أهلاً بك! 👋"


def _frame(obj):
    import json as _json
    return ("data: " + _json.dumps(obj, ensure_ascii=False)).encode("utf-8")


CHUNKS = [
    _frame({"choices": [{"delta": {"content": AR}}]}),
    b"",
    _frame({"choices": [{"delta": {"tool_calls": [
        {"index": 0, "id": "c1", "type": "function",
         "function": {"name": "terminal", "arguments": '{"comm'}}]}}]}),
    _frame({"choices": [{"delta": {"tool_calls": [
        {"index": 0, "function": {"arguments": 'and":"ls"}'}}]}}]}),
    _frame({"choices": [{"finish_reason": "tool_calls"}]}),
    _frame({"usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}}),
    b"data: [DONE]",
]


class FakeResp:
    def iter_lines(self, decode_unicode=False):
        assert decode_unicode is False, "must read raw bytes (requests mis-decodes SSE charset)"
        for c in CHUNKS:
            yield c


seen = []
out = _chat_stream(FakeResp(), seen.append, "m")
assert "".join(seen) == AR, seen
assert out["content"] == AR, out
assert out["tool_calls"][0]["function"]["name"] == "terminal", out
assert out["tool_calls"][0]["function"]["arguments"] == {"command": "ls"}, out
assert out["usage"] == {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}, out
assert out["finish_reason"] == "tool_calls", out
print("STREAM DECODING OK (no mojibake, tool deltas rebuilt)")
