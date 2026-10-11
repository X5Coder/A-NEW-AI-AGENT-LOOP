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


def login_interactive(bin_path: str) -> bool:
    """Run `tailscale login`, print + open the registration URL, wait for login.

    Returns True when `tailscale status --json` reports Running.
    Never raises; bounded wait (LOGIN_WAIT_SEC).
    """
    print("  Tailscale registration (one time, ~30 seconds).")
    print("  Waiting up to 3 minutes - finish in your browser and this "
          "continues by itself ...")
    try:
        proc = subprocess.Popen(
            [bin_path, "login"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1,
        )
    except OSError as e:
        print(f"  ERROR: cannot start tailscale login ({e})", file=sys.stderr)
        return False
    opened = False
    start = time.time()
    last_beat = start
    try:
        assert proc.stdout is not None
        for line in proc.stdout:
            line = line.strip()
            if line:
                print(f"  tailscale: {line[:200]}")
            url = _extract_login_url(line)
            if url and not opened:
                opened = True
                print("  " + "=" * 66)
                print("  >>> REGISTER HERE (open on YOUR computer or phone):")
                print(f"  >>> {url}")
                print("  " + "=" * 66)
                if _open_url_best_effort(url):
                    print("  (opened in a browser automatically)")
                else:
                    print("  (no browser on this server - copy the link above "
                          "into your own browser)")
            if login_status(bin_path).get("logged_in"):
                break
            now = time.time()
            if now - last_beat >= 20:
                last_beat = now
                left = max(0, int(LOGIN_WAIT_SEC - (now - start)))
                print(f"  still waiting for the browser login ... "
                      f"({left // 60}:{left % 60:02d} left)")
            if now - start > LOGIN_WAIT_SEC:
                break
    except Exception:
        pass
    finally:
        try:
            if proc.poll() is None:
                proc.terminate()
        except Exception:
            pass
    ok = bool(login_status(bin_path).get("logged_in"))
    print("  OK: Tailscale logged in" if ok else "  WARN: login not completed (run `tailscale login` manually)")
    return ok


FUNNEL_APPROVAL_RE = re.compile(r"https://login\.tailscale\.com/f/funnel[^\s'\"]*")


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
