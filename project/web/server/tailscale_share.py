"""Tailscale public sharing — single owner for the console public link.

Responsibility: expose the local console (127.0.0.1:7777) on a permanent
public URL that anyone can open, using Tailscale Funnel:
  https://<machine>.<tailnet>.ts.net  (stable while machine + tailnet names stay)

Why Funnel and not Serve: `tailscale serve` is tailnet-only (your devices),
`tailscale funnel` is the public internet link the user asked for.

Cross-platform: Windows + Linux, stdlib only (subprocess + json + webbrowser).
No secrets, no hardcoded tokens, bounded retries (max 3), bounded timeouts.

Lifecycle (explicit, owned here):
  ensure_public(port) -> create/login/funnel -> save Data/database/tailscale.json
  status()            -> read-only snapshot (never boots anything)
  stop()              -> `tailscale funnel --https=443 off` (best-effort)
"""

from __future__ import annotations

import json
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path

TAILNET_INSTALL_URL = "https://tailscale.com/download"
LOGIN_URL_RE = re.compile(r"https://login\.tailscale\.com/[^\s'\"]+")
CMD_TIMEOUT = 30
LOGIN_WAIT_SEC = 180
LOGIN_MAX_ATTEMPTS = 3
FUNNEL_PORT = 443

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _data_file() -> Path:
    try:
        sys.path.insert(0, str(PROJECT_ROOT))
        from core.paths import data_db_file

        return data_db_file("tailscale.json")
    except Exception:
        fallback = PROJECT_ROOT.parents[0] / "X5Agent" / "Data" / "database" / "tailscale.json"
        fallback.parent.mkdir(parents=True, exist_ok=True)
        return fallback


def find_tailscale() -> str | None:
    """Locate the tailscale binary (PATH + Windows default location)."""
    found = shutil.which("tailscale")
    if found:
        return found
    if sys.platform.startswith("win"):
        for candidate in (
            Path(r"C:\Program Files\Tailscale\tailscale.exe"),
            Path(r"C:\Program Files (x86)\Tailscale\tailscale.exe"),
        ):
            if candidate.exists():
                return str(candidate)
    return None


def _run(args: list[str], timeout: int = CMD_TIMEOUT) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout)


def is_installed() -> tuple[bool, str | None]:
    path = find_tailscale()
    return (path is not None, path)


def login_status(bin_path: str) -> dict:
    """Read-only status: logged in? + tailnet DNS name. Never boots anything."""
    try:
        proc = _run([bin_path, "status", "--json"], timeout=15)
        if proc is None or proc.returncode != 0:
            return {"logged_in": False, "dns": None}
        data = json.loads(proc.stdout or "{}")
        backend = str(data.get("BackendState") or "")
        self_node = data.get("Self") or {}
        dns = str(self_node.get("DNSName") or "").rstrip(".")
        logged_in = backend == "Running" and bool(dns)
        return {"logged_in": logged_in, "dns": dns or None}
    except Exception:
        return {"logged_in": False, "dns": None}


def funnel_status(bin_path: str) -> dict:
    """Read-only funnel/serve snapshot (never enables anything)."""
    try:
        proc = _run([bin_path, "funnel", "status", "--json"], timeout=15)
        if proc is not None and proc.returncode == 0 and (proc.stdout or "").strip():
            try:
                return {"raw": json.loads(proc.stdout)}
            except Exception:
                return {"raw": (proc.stdout or "")[:500]}
    except Exception:
        pass
    return {"raw": None}


def status(port: int = 7777) -> dict:
    """Public-share snapshot for UI/API: installed/logged_in/dns/public_url."""
    installed, bin_path = is_installed()
    out: dict = {"installed": installed, "logged_in": False, "dns": None,
                 "public_url": None, "local": f"http://127.0.0.1:{port}/"}
    if not installed or not bin_path:
        out["install_url"] = TAILNET_INSTALL_URL
        try:
            saved = json.loads(_data_file().read_text(encoding="utf-8"))
            if isinstance(saved, dict) and saved.get("public_url"):
                out["public_url"] = str(saved["public_url"])[:200]
        except Exception:
            pass
        return out
    info = login_status(bin_path)
    out.update(info)
    try:
        saved = json.loads(_data_file().read_text(encoding="utf-8"))
        if isinstance(saved, dict) and saved.get("public_url"):
            out["public_url"] = str(saved["public_url"])[:200]
    except Exception:
        pass
    if not out["public_url"] and info.get("dns"):
        out["public_url"] = f"https://{info['dns']}"
    return out


def _extract_login_url(text: str) -> str | None:
    match = LOGIN_URL_RE.search(text or "")
    return match.group(0) if match else None


class _Spinner:
    """Live progress ticker for one blocking call (stdlib only).

    `with _Spinner("enabling funnel"):` ticks a spinner plus elapsed seconds
    on the same terminal line until the block ends, then clears the line (the
    caller prints its own OK/WARN line after). Daemon thread, always joined
    in __exit__ - no orphaned threads. On non-terminal output it prints the
    message once and stays silent (clean logs).
    """
    def __init__(self, message: str):
        self._message = message
        self._stop = threading.Event()
        self._thread = None

    def _live(self) -> bool:
        try:
            return sys.stdout.isatty()
        except Exception:
            return False

    def __enter__(self):
        if self._live():
            self._thread = threading.Thread(target=self._tick, daemon=True)
            self._thread.start()
        else:
            print(f"  {self._message} ...")
        return self

    def __exit__(self, *exc):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
            self._thread = None
            try:
                sys.stdout.write("\r" + " " * 72 + "\r")
                sys.stdout.flush()
            except Exception:
                pass
        return False

    def _tick(self):
        frames = "|/-\\"
        start = time.time()
        i = 0
        while not self._stop.wait(0.5):
            try:
                elapsed = int(time.time() - start)
                sys.stdout.write(
                    f"\r  {self._message} {frames[i % 4]} {elapsed}s")
                sys.stdout.flush()
            except Exception:
                break
            i += 1


def _open_url_best_effort(url: str) -> bool:
    """Try to open a URL in a browser. Best-effort, never raises.

    A headless server has no browser to open (the common VPS case), so
    callers always print the link for the user's own computer/phone too.
    """
    try:
        if webbrowser.open(url):
            return True
    except Exception:
        pass
    try:
        import os as _os

        if _os.environ.get("DISPLAY") and shutil.which("xdg-open"):
            proc = subprocess.run(
                ["xdg-open", url], capture_output=True, timeout=15)
            return proc.returncode == 0
    except Exception:
        pass
    return False


def _announce_login_url(url: str, attempt: int) -> None:
    """Print one registration link in a clean action box (a fresh link per try)."""
    if attempt > 1:
        print(f"  link expired - issuing a fresh one "
              f"(attempt {attempt}/{LOGIN_MAX_ATTEMPTS}) ...")
    print("  " + "=" * 66)
    print("  >>> OPEN THIS LINK TO REGISTER TAILSCALE:")
    print(f"  >>> {url}")
    print("  " + "=" * 66)
    _open_url_best_effort(url)


def _login_once(bin_path: str, attempt: int) -> bool:
    """One `tailscale login` run: show its link, wait up to LOGIN_WAIT_SEC.

    Returns True once `tailscale status` reports Running or when the login
    process exits (bounded either way). The process is always terminated on
    exit so a later attempt can mint a fresh link. Never raises.

    The child's output is drained by a daemon thread into a queue so the wait
    loop never blocks on a silent process: a plain `for line in stdout` hangs
    forever when `tailscale login` stops printing after the link.
    """
    try:
        proc = subprocess.Popen(
            [bin_path, "login"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1,
        )
    except OSError as e:
        print(f"  ERROR: cannot start tailscale login ({e})", file=sys.stderr)
        return False

    lines: queue.Queue = queue.Queue()
    end = object()

    def _pump() -> None:
        try:
            assert proc.stdout is not None
            for line in proc.stdout:
                lines.put(line)
        except Exception:
            pass
        finally:
            lines.put(end)

    threading.Thread(target=_pump, daemon=True).start()

    opened = False
    start = time.time()
    last_beat = start
    last_check = start
    try:
        while True:
            try:
                item = lines.get(timeout=0.5)
            except queue.Empty:
                item = None
            if item is end:
                return bool(login_status(bin_path).get("logged_in"))
            if isinstance(item, str):
                text = item.strip()
                # The URL (and its boilerplate) is shown once in the box below,
                # so only other output (errors, notes) is echoed.
                if text and not _extract_login_url(text) \
                        and "to authenticate" not in text.lower():
                    print(f"  tailscale: {text[:200]}")
                url = _extract_login_url(text)
                if url and not opened:
                    opened = True
                    _announce_login_url(url, attempt)
            now = time.time()
            if now - last_check >= 3:
                last_check = now
                if login_status(bin_path).get("logged_in"):
                    return True
            if now - last_beat >= 20:
                last_beat = now
                left = max(0, int(LOGIN_WAIT_SEC - (now - start)))
                print(f"  waiting for you to finish in the browser ... "
                      f"({left // 60}:{left % 60:02d} left)")
            if now - start > LOGIN_WAIT_SEC:
                return False
    except Exception:
        return False
    finally:
        try:
            if proc.poll() is None:
                proc.terminate()
        except Exception:
            pass


def login_interactive(bin_path: str) -> bool:
    """Register Tailscale, auto-renewing the one-time link when it expires.

    A registration link is single-use and times out; when it does (the wait
    window elapses or the login process exits without auth) a fresh link is
    minted automatically, up to LOGIN_MAX_ATTEMPTS times, so the user never
    has to re-run the installer just because they were slow to open it.
    Returns True when `tailscale status --json` reports Running. Never raises.
    """
    print("  Tailscale registration (one time, ~30 seconds).")
    print("  Open the link below on your own computer or phone; this continues "
          "by itself. If it expires, a fresh link is issued automatically.")
    for attempt in range(1, LOGIN_MAX_ATTEMPTS + 1):
        if _login_once(bin_path, attempt) \
                or login_status(bin_path).get("logged_in"):
            print("  OK: Tailscale logged in")
            return True
    print("  WARN: login not completed (run `tailscale login` manually)")
    return False


FUNNEL_APPROVAL_RE = re.compile(r"https://login\.tailscale\.com/f/funnel[^\s'\"]*")
LIVE_ROUTE_PATH = "/live/"
LIVE_WS_PATH = "/websockify"
LIVE_VIEWER_PORT = 6080
VIEWER_FILE = "x5-live.html"

# The x5browser library draws a small "efficiency dot" (green = active,
# orange = idle) inside its viewer page. The console renders the viewer in
# borderless templates (floating preview, control cards) where the overlay
# reads as clutter, so every deployed copy gets it hidden (details below).
# Nothing else in the page is touched — the beacon/SSE logic keeps running.
_DOT_HIDE_CSS = (
    "\n        /* X5Agent: hide the efficiency-dot overlay "
    "(borderless templates). */\n"
    "        #efficiency { display: none !important; }\n"
)
_DOT_HIDE_DIV = '<div id="efficiency" title="browser active"></div>'
_DOT_HIDE_DIV_NEW = ('<div id="efficiency" title="browser active" '
                     'style="display:none !important"></div>')


def strip_viewer_dot(path: str) -> bool:
    """Hide the efficiency-dot overlay in one deployed viewer copy.

    Idempotent (safe to re-run after library upgrades): adds a display:none
    rule for #efficiency when the file has one, and hides the div itself.
    Returns True when the file exists and carries no visible dot afterwards.
    Never raises.
    """
    try:
        from pathlib import Path as _Path

        target = _Path(str(path))
        if not target.is_file():
            return False
        text = target.read_text(encoding="utf-8")
        changed = False
        if _DOT_HIDE_CSS not in text and "#efficiency" in text:
            marker = "\n    </style>"
            if marker in text:
                text = text.replace(marker, _DOT_HIDE_CSS + marker, 1)
                changed = True
        if _DOT_HIDE_DIV in text:
            text = text.replace(_DOT_HIDE_DIV, _DOT_HIDE_DIV_NEW)
            changed = True
        if changed:
            target.write_text(text, encoding="utf-8")
        compact = text.replace(" ", "")
        return ('id="efficiency"' not in text
                or "display:none" in compact)
    except Exception:
        return False


def strip_deployed_viewer_dots() -> int:
    """Hide the dot in every deployed viewer copy. Returns files fixed.

    Copies live in two places: the library package (source of future
    deploys) and the served noVNC web root. Both are patched so the overlay
    never comes back after a reinstall/redeploy. Never raises.
    """
    fixed = 0
    try:
        from pathlib import Path as _Path

        candidates = [_Path("/usr/share/novnc") / VIEWER_FILE]
        try:
            import x5browser.live.web as _web  # type: ignore

            candidates.append(_Path(str(_web.__path__[0])) / VIEWER_FILE)
        except Exception:
            pass
        try:
            import x5browser as _xb  # type: ignore

            candidates.append(_Path(str(_xb.__path__[0])) / "live" / "web" / VIEWER_FILE)
        except Exception:
            pass
        seen: set = set()
        for cand in candidates:
            key = str(cand)
            if key in seen:
                continue
            seen.add(key)
            try:
                if strip_viewer_dot(key):
                    fixed += 1
            except Exception:
                continue
    except Exception:
        pass
    return fixed


def _print_funnel_approval(link: str | None) -> None:
    """Action box: funnel is disabled tailnet-wide, only an admin approval
    unlocks it (no code path can). Points at the exact approval link when
    Tailscale printed one, else the admin DNS page."""
    print("  " + "=" * 66)
    print("  >>> FUNNEL NEEDS ONE ADMIN APPROVAL (public link stays off until then):")
    if link:
        print(f"  >>> approve here: {link}")
        try:
            if webbrowser.open(link):
                print("  (opened in a browser automatically)")
                return
        except Exception:
            pass
        print("  (no browser on this server - open the link above on YOUR computer)")
    else:
        print("  >>> admin console: https://login.tailscale.com/admin/dns")
        print("  >>> enable HTTPS certificates + approve Funnel, then re-run")
    print("  " + "=" * 66)


def _serve_paths() -> set[str]:
    """Path prefixes currently routed, or empty set. Never raises."""
    try:
        installed, _ = is_installed()
        if not installed:
            return set()
        proc = _run(["tailscale", "serve", "status", "--json"], timeout=15)
        data = json.loads(proc.stdout or "{}")
        web = data.get("Web") if isinstance(data, dict) else None
        if not isinstance(web, dict):
            return set()
        out: set[str] = set()
        for cfg in web.values():
            handlers = cfg.get("Handlers") if isinstance(cfg, dict) else None
            if isinstance(handlers, dict):
                out.update(str(h) for h in handlers)
        return out
    except Exception:
        return set()


def live_route_active() -> bool:
    """True when the /live/ viewer route already exists in serve config."""
    try:
        return LIVE_ROUTE_PATH in _serve_paths()
    except Exception:
        return False


def ensure_live_route() -> bool:
    """Expose the live viewer through the funnel (idempotent, best-effort).

    Two routes: /live/ (page + assets, WS when ?path= is given) and
    /websockify (the viewer's default WS path, so even a bare page URL
    streams). The library mints viewer links on 127.0.0.1:6080, unreachable
    from a remote browser.
    Skips quietly when Tailscale is absent or the viewer is down (a later
    call after browser boot retries it). Never raises.
    """
    try:
        paths = _serve_paths()
        if LIVE_ROUTE_PATH in paths and LIVE_WS_PATH in paths:
            try:
                strip_deployed_viewer_dots()
            except Exception:
                pass
            return True
        installed, bin_path = is_installed()
        if not installed or not bin_path:
            return False
        import socket as _socket

        try:
            probe = _socket.create_connection(
                ("127.0.0.1", LIVE_VIEWER_PORT), timeout=3.0)
            try:
                probe.close()
            except Exception:
                pass
        except Exception:
            return False  # viewer down: nothing to route yet
        for route in (LIVE_ROUTE_PATH, LIVE_WS_PATH):
            if route in _serve_paths():
                continue
            proc = _run([bin_path, "funnel", "--bg", "--set-path", route,
                         f"http://127.0.0.1:{LIVE_VIEWER_PORT}"], timeout=60)
            if proc is None or proc.returncode != 0:
                print(f"  WARN: live route {route} not added "
                      f"(exit {getattr(proc, 'returncode', '?')})")
        if live_route_active():
            print("  OK: live viewer routed at <tailnet>/live/ (+ WS path)")
            try:
                strip_deployed_viewer_dots()
            except Exception:
                pass
            return True
        return False
    except Exception:
        return False


def enable_funnel(bin_path: str, port: int = 7777) -> str | None:
    """Enable Funnel on the console port, return the public URL (or None).

    Tries the modern syntax first, falls back to older variants.
    Funnel needs HTTPS + MagicDNS + funnel allowed in the admin ACL; when the
    admin blocks it, falls back to tailnet-only `serve` and returns its URL.
    When the tailnet never enabled Funnel, Tailscale prints an approval link:
    it is shown immediately and we STOP (retrying is pointless until a human
    approves). One attempt per variant with progress prints, so a slow
    first-time certificate provisioning never looks hung.
    """
    target = f"http://127.0.0.1:{port}"
    print("  enabling funnel (first time may provision certificates, ~1 min)...")
    variants = [
        [bin_path, "funnel", "--bg", target],
        [bin_path, "funnel", "--bg", str(port)],
        [bin_path, "funnel", str(port)],
    ]
    chosen = None
    with _Spinner("enabling funnel"):
        for args, timeout in zip(variants, (45, 30, 30)):
            try:
                proc = _run(args, timeout=timeout)
            except (subprocess.TimeoutExpired, OSError) as e:
                print(f"  funnel variant timed out ({' '.join(args[1:])}: "
                      f"{str(e)[:80]}), trying next ...")
                continue
            out = ""
            if proc is not None:
                out = str(getattr(proc, "stdout", "") or "") + str(getattr(proc, "stderr", "") or "")
            approval = FUNNEL_APPROVAL_RE.search(out)
            if approval:
                _print_funnel_approval(approval.group(0))
                return None
            if proc is not None and proc.returncode == 0:
                chosen = args
                break
            print(f"  funnel variant failed ({' '.join(args[1:])}), trying next ...")
    if chosen is not None:
        print(f"  OK: funnel on ({' '.join(chosen[1:])})")
    else:
        # Funnel blocked (ACL/plan) -> tailnet-only serve, still useful.
        print("  funnel refused - falling back to tailnet-only serve ...")
        with _Spinner("enabling tailnet serve"):
            try:
                _run([bin_path, "serve", "--bg", target], timeout=60)
            except (subprocess.TimeoutExpired, OSError) as e:
                print(f"  WARN: serve timed out ({str(e)[:80]})")
        print("  WARN: funnel refused (check admin: HTTPS + funnel in ACL) — using tailnet-only serve")
    info = login_status(bin_path)
    dns = info.get("dns")
    public_url = f"https://{dns}" if dns else None
    try:
        record = {"public_url": public_url, "dns": dns, "port": port,
                  "mode": "funnel",
                  "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
        _data_file().write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass
    return public_url


def ensure_public(port: int = 7777, open_browser: bool = True) -> dict:
    """Full flow: install check -> login (URL shown + opened) -> funnel on.

    Never raises; returns status() snapshot. Safe to call from install or server.
    """
    installed, bin_path = is_installed()
    if not installed or not bin_path:
        print("  Tailscale is not installed (needed for the public link).")
        print(f"  1) install: {TAILNET_INSTALL_URL}  (Windows .exe / Linux package, 1 minute)")
        print("  2) re-run this step after installing — the local console works meanwhile.")
        try:
            if open_browser:
                webbrowser.open(TAILNET_INSTALL_URL)
        except Exception:
            pass
        return status(port)
    info = login_status(bin_path)
    if not info.get("logged_in"):
        if not login_interactive(bin_path):
            return status(port)
    public_url = enable_funnel(bin_path, port)
    if public_url:
        print(f"  >>> PUBLIC console (anyone can open): {public_url}")
        try:
            if open_browser:
                webbrowser.open(public_url)
        except Exception:
            pass
        # Viewer route for remote live view (skips quietly when the
        # browser is not up yet - retried after browser boot).
        ensure_live_route()
    else:
        print("  WARN: could not determine the public URL — open admin: https://login.tailscale.com/admin/machines")
    return status(port)


def stop() -> None:
    """Best-effort funnel off (cleanup path, never raises)."""
    try:
        installed, bin_path = is_installed()
        if installed and bin_path:
            _run([bin_path, "funnel", f"--https={FUNNEL_PORT}", "off"], timeout=15)
    except Exception:
        pass
