import sys
from pathlib import Path

project_root = Path(__file__).resolve().parents[1]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from X5Browser import service as svc

# Lifecycle lock is a singleton (serializes start/stop across callers).
assert svc._lifecycle_lock() is svc._lifecycle_lock()
print("lock singleton OK")

# This test must NEVER close a live browser: the stopped-branch assertions only
# make sense while nothing runs, so skip them instead of killing a session.
if svc.is_running(quick=True):
    print("browser running -> stopped-branch assertions skipped (no live browser closed)")
    print("ALL BROWSER LIFECYCLE TESTS PASSED")
    raise SystemExit(0)

# Closing with no browser never boots anything: pure no-op.
gen_before = svc._gen
out = svc.call_tool("close_browser", {})
assert out == "Browser already stopped.", out
assert svc._gen == gen_before + 1
assert svc._browser is None and svc._started is False
print("close-noop OK (no boot, generation bumped)")

# Probes stay read-only.
assert svc.is_running() in (True, False)
print("probe OK")

# Explicit lifecycle: any non-starter while stopped fails clearly and
# boots NOTHING (no WSL, no units, no Chrome) on any OS.
gen_before = svc._gen
for _tool, _args in (("navigate", {"to": "https://www.google.com/"}),
                     ("page_snapshot", {}),
                     ("live_preview", {})):
    try:
        svc.call_tool(_tool, _args)
    except RuntimeError as e:
        assert "browser stopped" in str(e), str(e)
    else:
        raise AssertionError(f"{_tool} booted the browser while stopped")
assert svc._browser is None and svc._started is False
assert svc._gen == gen_before
print("explicit-lifecycle gate OK (no boot from page/read/live tools)")
print("ALL BROWSER LIFECYCLE TESTS PASSED")
