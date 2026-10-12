"""X5Agent single install/update command.

Flow (exactly, nothing else):
  1. python install.py [--run] [--yes] [--email E --password P] [--public|--no-public]
  2. environment check (python, CPU/GPU, disk, dependencies auto-installed
     via `pip install -e .` when missing; below 3.12 warns:
     x5browser 1.5.2 runs on 3.10+, 3.12 recommended for full browser support)
  3. project state check (missing / complete / has live data; latest version
     read from raw updates.json on GitHub — no releases needed)
  4. source-only update (git pull --ff-only: tracked code only,
     NEVER touches X5Agent/Data, embedding_model/, or any user data)
  5. bundled embedding check (offline) + tool/trigger embeddings ready
     (HF_TOKEN optional: higher HuggingFace rate limits on slow networks)
  6. firewall step (best-effort open of TCP 7777 via ufw/firewalld)
  7. browser check (X5Browser doctor: Chrome + environment, offers --fix)
  8. email + password (flags/env X5_EMAIL+X5_PASSWORD or prompt; salted hash
     in Data, never plaintext)
  9. public link via Tailscale Funnel (install check -> login URL
     shown + opened -> funnel on -> permanent public URL printed)
  10. opens the console in the browser + prints local and public URLs
  11. with --run: replaces itself with the console server
     (same as `python -m web.server.server`) so install+start is one command.

Run from the project/ directory:  python install.py --run
Start the console only after:     python -m web.server.server
One-liner from any VPS:  curl -fsSL https://raw.githubusercontent.com/X5Coder/X5Agent/main/setup.sh | bash
"""
from __future__ import annotations

import getpass
import hashlib
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
GIT_ROOT = next((p for p in (PROJECT_ROOT, PROJECT_ROOT.parents[0])
                 if (p / ".git").exists()), None)
REPO_URL = "https://github.com/X5Coder/X5Agent"
UI_URL = "http://127.0.0.1:7777/"
CONSOLE_PORT = 7777
MIN_PYTHON = (3, 10)
BROWSER_PYTHON = (3, 12)  # x5browser 1.5.2 floor is 3.10; 3.12 still recommended for full browser support
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def parse_args(argv=None):
    """Non-interactive flags so the installer is fully automatable.

    Examples:
      python install.py --run
      python install.py --run --email owner@mail.com --password S3cret123 --public
      HF_TOKEN=hf_xxx python install.py --run --email a@b.c --password S3cret123 --no-public
      X5_EMAIL=a@b.c X5_PASSWORD=S3cret123 python install.py --run --yes
    Env fallbacks: X5_EMAIL / X5_PASSWORD / HF_TOKEN.
    """
    import argparse

    p = argparse.ArgumentParser(description="X5Agent install / update (one command)")
    p.add_argument("--email", default=os.environ.get("X5_EMAIL", ""),
                   help="Owner email (else prompted)")
    p.add_argument("--password", default=os.environ.get("X5_PASSWORD", ""),
                   help="Owner password, min 8 chars (env X5_PASSWORD preferred over CLI)")
    p.add_argument("--yes", "-y", action="store_true",
                   help="Assume yes to optional prompts (Chrome install, public link)")
    p.add_argument("--public", dest="public", action="store_true", default=None,
                   help="Enable the Tailscale Funnel public link without prompting")
    p.add_argument("--no-public", dest="public", action="store_false",
                   help="Skip the public link without prompting")
    p.add_argument("--skip-browser", action="store_true",
                   help="Skip the browser doctor check")
    p.add_argument("--skip-firewall", action="store_true",
                   help="Skip the firewall port step")
    p.add_argument("--hf-token", default=os.environ.get("HF_TOKEN", ""),
                   help="Optional HuggingFace token (env HF_TOKEN): authenticated "
                        "downloads get higher rate limits on slow networks")
    p.add_argument("--no-open-browser", action="store_true",
                   help="Do not open the console URL in a browser at the end")
    p.add_argument("--run", action="store_true",
                   help="Install + start in one step: no prompts, the public "
                        "link is attempted automatically unless --no-public, "
                        "then the console server takes over on 0.0.0.0:7777")
    return p.parse_args(argv)


def _pip_env() -> dict:
    """Environment for pip subprocess calls (quiet root warning).

    PIP_ROOT_USER_ACTION is only understood by new pip (ours is refreshed
    first); old pip ignores unknown PIP_* names, so this is safe everywhere.
    """
    env = dict(os.environ)
    env.setdefault("PIP_ROOT_USER_ACTION", "ignore")
    return env


def _has_nvidia_gpu() -> bool:
    """Best-effort NVIDIA GPU check via nvidia-smi. False when unsure.

    When unsure the caller keeps the default (CUDA) install path so GPU
    machines never lose GPU support because of a failed detection.
    """
    try:
        if shutil.which("nvidia-smi") is None:
            return False
        proc = subprocess.run(
            ["nvidia-smi", "-L"], capture_output=True, text=True, timeout=30,
        )
        return proc.returncode == 0 and "GPU" in (proc.stdout or "")
    except Exception:
        return False


def ensure_dependencies() -> bool:
    """Install project dependencies once when missing (pip install -e .).

    This is what collapses the old 2-step flow
    (`pip install -e .` then `python install.py`) into a single command.
    First refreshes the build toolchain (pip/setuptools/wheel/packaging)
    because stock Ubuntu python3.10 ships an old pip+setuptools pair that
    fails editable installs with
    `canonicalize_version() got ... strip_trailing_zero`.
    On machines without an NVIDIA GPU it pre-installs the CPU-only torch
    wheel (~200MB) so pip does not pull the ~2.5GB CUDA bundle on a
    CPU-only VPS (that download alone exceeds the install timeout).
    Bounded: three pip runs max (toolchain 10min, torch-CPU 20min,
    project 30min), never loops. A timeout keeps pip's cache, so just
    re-running the installer resumes where it stopped.
    Returns True when the required packages import cleanly afterwards.
    """
    missing = []
    for pkg in ("sentence_transformers", "numpy"):
        try:
            __import__(pkg)
        except Exception:
            missing.append(pkg)
    want_cmd = shutil.which("x5agent") is None
    if not missing and not want_cmd:
        return True
    what = ", ".join(missing) if missing else "x5agent command"
    print(f"  installing missing packages: {what} (pip install -e .)...")
    try:
        tool = subprocess.run(
            [sys.executable, "-m", "pip", "install", "-U",
             "pip", "setuptools", "wheel", "packaging>=24.2"],
            cwd=str(PROJECT_ROOT), timeout=600, env=_pip_env(),
        )
        toolchain_ok = tool.returncode == 0
        if toolchain_ok:
            _ok("build toolchain refreshed (pip/setuptools/wheel/packaging)")
        else:
            _warn(f"toolchain refresh exited {tool.returncode} - "
                  "trying install anyway")
        # --break-system-packages exists on the refreshed pip (Debian/Ubuntu
        # require it); the stock old pip does not accept it, so only use it
        # when the refresh succeeded.
        extra = ["--break-system-packages"] if toolchain_ok else []
        if not _has_nvidia_gpu():
            print("  no NVIDIA GPU detected - installing CPU-only torch "
                  "first (avoids the ~2.5GB CUDA download)...")
            cpu = subprocess.run(
                [sys.executable, "-m", "pip", "install", *extra,
                 "--index-url", "https://download.pytorch.org/whl/cpu",
                 "torch"],
                cwd=str(PROJECT_ROOT), timeout=1200, env=_pip_env(),
            )
            if cpu.returncode == 0:
                _ok("CPU-only torch installed")
            else:
                _warn(f"CPU torch install exited {cpu.returncode} - "
                      "trying full install anyway (slower)")
        proc = subprocess.run(
            [sys.executable, "-m", "pip", "install", *extra, "-e", "."],
            cwd=str(PROJECT_ROOT), timeout=1800, env=_pip_env(),
        )
        if proc.returncode != 0:
            print("  ERROR: pip install -e . failed "
                  f"(exit {proc.returncode})", file=sys.stderr)
            print("  run manually: pip install -e .  (from project/) "
                  "then re-run install", file=sys.stderr)
            return False
    except subprocess.TimeoutExpired:
        print("  ERROR: pip install timed out (slow network?) - pip cached "
              "the progress, just re-run the same command to resume",
              file=sys.stderr)
        return False
    except Exception as e:
        print(f"  ERROR: pip install failed ({str(e)[:120]})", file=sys.stderr)
        return False
    still_missing = []
    for pkg in ("sentence_transformers", "numpy"):
        try:
            __import__(pkg)
        except Exception:
            still_missing.append(pkg)
    if still_missing:
        print(f"  ERROR: still missing: {', '.join(still_missing)}", file=sys.stderr)
        return False
    _ok("dependencies installed")
    return True


def _step(title: str) -> None:
    print(f"\n[install] {title}")


def _ok(msg: str) -> None:
    print(f"  OK: {msg}")


def _warn(msg: str) -> None:
    print(f"  WARN: {msg}")


def _fail(msg: str) -> int:
    print(f"  ERROR: {msg}", file=sys.stderr)
    return 1


def check_environment() -> bool:
    """Python version, CPU/GPU (once), disk, required packages. Returns True when ready."""
    _step("1/7 environment check")
    if sys.version_info < MIN_PYTHON:
        print(f"  ERROR: python {MIN_PYTHON[0]}.{MIN_PYTHON[1]}+ required, got {sys.version.split()[0]}",
              file=sys.stderr)
        return False
    _ok(f"python {sys.version.split()[0]}")
    # Fast GPU gate first: importing torch takes tens of seconds and
    # cuda.is_available() can block on driver probing. On machines with no
    # NVIDIA GPU both answers are known to be cpu without touching torch.
    if _has_nvidia_gpu():
        try:
            import torch  # type: ignore

            device = "cuda" if torch.cuda.is_available() else "cpu"
        except Exception:
            device = "cpu (torch not installed)"
        print(f"  device: {device} (GPU used automatically when available)")
        from core.embedding.bootstrap import detect_device  # noqa: E402

        print(f"  embedding device: {detect_device()} (detected once, cached)")
    else:
        print("  hardware: CPU only - no GPU on this machine, everything runs on CPU")
    if not ensure_dependencies():
        return False
    _ok("dependencies present")
    try:
        free_gb = shutil.disk_usage(PROJECT_ROOT).free / (1024 ** 3)
        print(f"  disk free: {free_gb:.1f} GB")
    except Exception:
        pass
    return True


def _local_version() -> str:
    try:
        for line in (PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("version"):
                return line.split("=", 1)[1].strip().strip("\"'")
    except Exception:
        pass
    return "unknown"


def _latest_version() -> str | None:
    """Latest version from the repo (best-effort, 10s timeout, never raises).

    Single source of truth is updates.json at the repo root (raw file on
    main, then master) — no GitHub releases needed (a repo with zero
    releases would make the releases/latest API 404).
    """
    for branch in ("main", "master"):
        try:
            import urllib.request

            req = urllib.request.Request(
                f"https://raw.githubusercontent.com/X5Coder/X5Agent/{branch}/updates.json",
                headers={"User-Agent": "x5agent-installer", "Accept": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=10) as r:
                data = json.loads(r.read().decode("utf-8"))
            ver = str((data or {}).get("version") or "").strip().lstrip("v")
            if ver:
                return ver
        except Exception:
            continue
    return None


def check_project_state() -> dict:
    """Missing / complete / has live data. Never modifies anything."""
    _step("2/7 project state check")
    sys.path.insert(0, str(PROJECT_ROOT))
    from core.embedding.bootstrap import is_complete as _emb_ok

    embedding = bool(_emb_ok())
    data_dir = PROJECT_ROOT.parents[0] / "X5Agent" / "Data"
    conv = data_dir / "database" / "conversation.json"
    has_data = False
    try:
        if conv.exists() and len(json.loads(conv.read_text(encoding="utf-8"))) > 0:
            has_data = True
    except Exception:
        has_data = conv.exists()
    git = shutil.which("git") is not None and GIT_ROOT is not None
    local = _local_version()
    print(f"  source version: v{local}")
    print(f"  embedding model: {'present' if embedding else 'MISSING'}")
    print(f"  existing user data: {'yes (will be preserved)' if has_data else 'no (fresh)'}")
    print(f"  git updates: {'available' if git else 'unavailable (not a git checkout)'}")
    latest = _latest_version()
    if latest:
        print(f"  latest release: v{latest} "
              f"({'up to date' if latest == local else 'update available'})")
    else:
        _warn("could not reach GitHub for version check (offline?)")
    return {"embedding": embedding, "has_data": has_data, "git": git, "local": local}


def _git_head() -> str:
    """Current commit hash, or '' when unavailable. Never raises."""
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=str(GIT_ROOT),
            capture_output=True, text=True, timeout=30,
        )
        if proc.returncode == 0:
            return (proc.stdout or "").strip()
    except Exception:
        pass
    return ""


def update_source() -> bool:
    """Source-only update: tracked code via git fast-forward. Never deletes data.

    Returns True when the pull moved to a newer commit (caller may need a
    restart to run the new code).
    """
    _step("3/7 source update (code only, data untouched)")
    if not (shutil.which("git") and GIT_ROOT is not None):
        _warn("no git checkout - skipping update (download the release zip instead)")
        return False
    before = _git_head()
    try:
        proc = subprocess.run(
            ["git", "pull", "--ff-only"], cwd=str(GIT_ROOT),
            capture_output=True, text=True, timeout=300,
        )
        out = (proc.stdout + proc.stderr).strip()
        for line in out.splitlines()[:20]:
            print(f"  git: {line[:160]}")
        if proc.returncode == 0:
            _ok("source is now latest (user data untouched: X5Agent/ + embedding_model/ are untracked)")
            after = _git_head()
            return bool(before and after and before != after)
        _warn("git pull did not fast-forward (local changes?) - continuing with current code")
        return False
    except Exception as e:
        _warn(f"update failed ({str(e)[:120]}) - continuing with current code")
        return False


def ensure_resources(hf_token: str = "") -> bool:
    """Embedding model (auto-download on first run, delta check on updates) + prebuilt tool/trigger embeddings.

    hf_token is strictly optional: when given (flag or HF_TOKEN env) it is
    used only to authenticate HuggingFace downloads (higher rate limits on
    slow networks). Anonymous download is the default and works fine.
    """
    _step("4/7 resources (embedding auto-fetch + prebuilt embeddings)")
    sys.path.insert(0, str(PROJECT_ROOT))
    from core.embedding.bootstrap import ensure_embedding_model

    token = (hf_token or os.environ.get("HF_TOKEN") or "").strip()
    if token:
        os.environ["HF_TOKEN"] = token
        _ok("HF_TOKEN provided — authenticated HuggingFace download (higher rate limits)")
    else:
        print("  info: no HF_TOKEN — anonymous HuggingFace download "
              "(fine on normal networks; set HF_TOKEN=hf_xxx on slow/capped ones)")
    try:
        # sync=True: check first - current files are skipped, only deltas download.
        ensure_embedding_model(sync=True)
    except Exception as e:
        print(f"  ERROR: embedding model not ready: {e}", file=sys.stderr)
        return False
    _ok("embedding model ready")
    try:
        import importlib.util

        def _load(rel, name):
            spec = importlib.util.spec_from_file_location(name, str(PROJECT_ROOT / rel))
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)  # type: ignore
            return mod

        tools = _load("plugins/tool-discovery/search/store.py", "inst_tools").ensure_embeddings()
        trigs = _load("plugins/trigger-discovery/search/store.py", "inst_trigs").ensure_embeddings()
        _ok(f"tool embeddings ready ({len(tools)} tools, {len(trigs)} triggers)")
    except Exception as e:
        _warn(f"embedding warmup skipped ({str(e)[:120]})")
    return True


def check_browser(assume_yes: bool = False) -> bool:
    """Browser check via the X5Browser doctor (inspect only, offers --fix).

    Runs BEFORE the owner email step: answers "can this machine run the
    browser right now?" (OS, official Chrome, network, elevation, port,
    profile, display). Never fatal: problems only warn, the install
    continues so the console still works without the browser.
    """
    _step("5/7 browser check (X5Browser doctor: Chrome + environment)")
    try:
        from x5browser import run_doctor
    except Exception as e:
        _warn(f"browser check skipped (x5browser not installed: {str(e)[:100]})")
        return True
    try:
        report = run_doctor()
    except Exception as e:
        _warn(f"browser check failed ({str(e)[:120]}) - continuing anyway")
        return True
    try:
        checks = list(getattr(report, "checks", []) or [])
    except Exception:
        checks = []
    for item in checks:
        try:
            mark = "ok  " if getattr(item, "ok", False) else "FAIL"
            print(f"  [{mark}] {getattr(item, 'id', '?')}: {getattr(item, 'message', '')}"[:200])
            hint = str(getattr(item, "hint", "") or "")
            if not getattr(item, "ok", False) and hint:
                print(f"         fix: {hint}"[:220])
        except Exception:
            continue
    if bool(getattr(report, "ok", False)):
        _ok("browser ready (official Chrome + environment)")
        return True
    chrome_ok = True
    for item in checks:
        try:
            if getattr(item, "id", "") == "chrome":
                chrome_ok = bool(getattr(item, "ok", False))
        except Exception:
            continue
    if not chrome_ok:
        if assume_yes:
            ans = "y"
        else:
            try:
                ans = input("  Official Chrome is missing. Install it now? [Y/n]: ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                print(file=sys.stderr)
                _warn("Chrome install skipped (non-interactive) - install it later, then re-run")
                return True
        if ans not in ("n", "no"):
            print("  installing official Chrome (one system prompt may appear)...")
            try:
                fixed = run_doctor(fix=True)
            except Exception as e:
                _warn(f"Chrome install failed ({str(e)[:120]}) - install manually from https://www.google.com/chrome/")
                return True
            fixed_ok = bool(getattr(fixed, "ok", False))
            for item in list(getattr(fixed, "checks", []) or []):
                try:
                    if getattr(item, "id", "") == "chrome":
                        mark = "ok  " if getattr(item, "ok", False) else "FAIL"
                        print(f"  [{mark}] chrome: {getattr(item, 'message', '')}"[:200])
                except Exception:
                    continue
            if fixed_ok:
                _ok("browser ready after Chrome install")
            else:
                _warn("browser still not ready (see fixes above) - continuing, console still works")
            return True
    _warn("browser not ready (see fixes above) - continuing, console still works")
    return True


def ensure_firewall(port: int = CONSOLE_PORT) -> None:
    """Open the console port in the host firewall (best-effort, never fatal).

    Tries `ufw allow <port>/tcp` when ufw exists, else `firewall-cmd`
    (--add-port/--reload) when firewalld exists. Otherwise prints the exact
    manual command. The console on localhost works without this; it is only
    needed to reach the console from other machines / the public link.
    """
    _step(f"firewall (open TCP port {port}, best-effort)")
    try:
        if shutil.which("ufw"):
            proc = subprocess.run(
                ["ufw", "allow", f"{port}/tcp"],
                capture_output=True, text=True, timeout=60,
            )
            out = ((proc.stdout or "") + (proc.stderr or "")).strip().splitlines()
            for line in out[:5]:
                print(f"  ufw: {line[:160]}")
            if proc.returncode == 0:
                _ok(f"firewall open: TCP {port} (ufw)")
                return
            _warn(f"ufw exited {proc.returncode} — run manually: sudo ufw allow {port}/tcp")
            return
        if shutil.which("firewall-cmd"):
            proc = subprocess.run(
                ["firewall-cmd", "--permanent", f"--add-port={port}/tcp"],
                capture_output=True, text=True, timeout=60,
            )
            if proc.returncode == 0:
                subprocess.run(["firewall-cmd", "--reload"],
                               capture_output=True, text=True, timeout=60)
                _ok(f"firewall open: TCP {port} (firewalld)")
                return
            _warn(f"firewall-cmd failed — run manually as root: "
                  f"firewall-cmd --permanent --add-port={port}/tcp; firewall-cmd --reload")
            return
    except Exception as e:
        _warn(f"firewall step failed ({str(e)[:120]}) - local console still works")
        return
    _warn(f"no supported firewall tool found (ufw/firewalld) — "
          f"if port {port} is filtered, run manually: sudo ufw allow {port}/tcp")


def _owner_exists() -> bool:
    """True when the owner was already registered once (local only).

    Prints the skip note. Explicit --email/--password flags bypass this
    check so the owner can still be changed on purpose.
    """
    try:
        sys.path.insert(0, str(PROJECT_ROOT))
        from web.server.console_auth import is_setup, owner_email

        if is_setup():
            print(f"  OK: owner already set ({owner_email()})")
            return True
    except Exception:
        pass
    return False


def _clear_screen() -> None:
    """Best-effort terminal clear before the owner prompt (fresh screen).

    Falls back to blank lines when there is no terminal clearer.
    """
    try:
        if sys.stdout.isatty() and shutil.which("clear"):
            subprocess.run(["clear"], timeout=5)
            return
    except Exception:
        pass
    print("\n\n", end="")


def _read_password() -> str | None:
    """Hidden password read. Returns None on abort (EOF/interrupt)."""
    try:
        if not sys.stdin.isatty():
            # Piped stdin (automation): read one line without getpass noise.
            line = sys.stdin.readline()
            if not line:
                return None
            return line.rstrip("\r\n")
        return getpass.getpass("  password (min 8, hidden): ")
    except (EOFError, KeyboardInterrupt):
        print(file=sys.stderr)
        return None


def ask_credentials(email: str = "", password: str = "") -> bool:
    """Email + hidden password prompt. Stores salted hash only. Returns True on success.

    Fully non-interactive when email+password are passed (flags or
    X5_EMAIL/X5_PASSWORD env): no input()/getpass() calls at all, so no
    stdin injection and no GetPassWarning. Interactive prompts retry until
    the value is valid (bad email or weak/unreadable password re-asks
    instead of aborting the whole install); EOF/interrupt still aborts.
    Any letters, numbers and symbols (e.g. kimo1234##xxz) are accepted -
    the only rules are a valid email shape and min 8 password chars.
    """
    _clear_screen()
    _step("6/7 console owner")
    if not email and not password and _owner_exists():
        return True
    email = (email or "").strip().lower()
    while True:
        if not email:
            try:
                email = input("  email: ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                print(file=sys.stderr)
                return False
        if EMAIL_RE.match(email):
            break
        print("  ERROR: invalid email format - please type it again",
              file=sys.stderr)
        email = ""
    pw = (password or "")
    while True:
        if not pw:
            got = _read_password()
            if got is None:
                return False
            pw = got
        if len(pw) < 8:
            print("  ERROR: password too short (min 8) - please type it again",
                  file=sys.stderr)
            pw = ""
            continue
        try:
            pw.encode("utf-8")
        except UnicodeError:
            print("  ERROR: password has unreadable characters - please "
                  "retype it with plain letters/numbers/symbols",
                  file=sys.stderr)
            pw = ""
            continue
        break
    try:
        sys.path.insert(0, str(PROJECT_ROOT))
        from core.paths import data_db_file

        salt = secrets.token_hex(16)
        digest = hashlib.pbkdf2_hmac("sha256", pw.encode("utf-8"), bytes.fromhex(salt), 200_000).hex()
        del pw
        record = {"email": email, "salt": salt, "hash": digest,
                  "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
        path = data_db_file("console_auth.json")
        path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        _ok(f"owner saved ({email}) - hash only, never plaintext")
        return True
    except Exception as e:
        print(f"  ERROR: could not save owner ({str(e)[:120]})", file=sys.stderr)
        return False


def setup_public_share(mode=None, assume_yes: bool = False) -> tuple[str | None, str]:
    """Step 7/7: Tailscale install + login. Returns (url, reason).

    Funnel itself is NOT enabled here on purpose: it needs a listening
    server (enabling it against a dead port hangs), so the server turns it
    on in the background when started with --public, after the port answers.
    Returns the expected public URL (tailnet DNS) when logged in, else None
    with a short reason. Never fatal: the local console works regardless.
    mode: True/False/None (flag --public/--no-public/unset). With --run and
    no explicit flag the login is attempted automatically (no prompt).
    """
    _step("7/7 public link (Tailscale Funnel — anyone can open)")
    if mode is False:
        _ok("skipped (--no-public) - local console only")
        return None, "skipped (--no-public)"
    try:
        sys.path.insert(0, str(PROJECT_ROOT))
        from web.server.tailscale_share import (
            is_installed, login_interactive, login_status)
    except Exception as e:
        _warn(f"public link skipped ({str(e)[:120]}) - local console still works")
        return None, "unavailable"
    auto = bool(mode) or assume_yes
    if not auto:
        try:
            ans = input("  Enable the public internet link? [Y/n]: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print(file=sys.stderr)
            return None, "skipped"
        if ans == "n":
            _ok("skipped - local console only")
            return None, "skipped"
    installed, bin_path = is_installed()
    if not installed and not _install_tailscale():
        return None, "needs Tailscale (see above)"
    installed, bin_path = is_installed()
    if not installed or not bin_path:
        _warn("install Tailscale, then re-run with --public")
        return None, "needs Tailscale (see above)"
    info = login_status(bin_path)
    if not info.get("logged_in"):
        if not login_interactive(bin_path):
            _warn("login not completed - re-run to retry it")
            return None, "login incomplete (re-run to retry)"
        info = login_status(bin_path)
    dns = str(info.get("dns") or "")
    if dns:
        _ok(f"logged in ({dns}) - funnel activates when the server starts with --public")
        return f"https://{dns}", ""
    _warn("login state unclear - re-run to retry it")
    return None, "login unclear (re-run to retry)"


def _install_tailscale() -> bool:
    """Best-effort Tailscale install on Linux via the official script.

    Mirrors the browser Chrome --fix pattern: one attempt, bounded
    (5 minutes), never fatal - the local console works without it.
    Returns True when `tailscale` is on PATH afterwards.
    """
    import platform

    if platform.system() != "Linux":
        _warn("automatic Tailscale install supports Linux only - "
              "install manually: https://tailscale.com/download")
        return False
    if shutil.which("tailscale"):
        return True
    if shutil.which("curl") is None:
        _warn("cannot auto-install Tailscale (curl missing) - "
              "install manually: https://tailscale.com/download")
        return False
    needs_root = hasattr(os, "geteuid") and os.geteuid() != 0
    if needs_root and shutil.which("sudo") is None:
        _warn("cannot auto-install Tailscale (needs root) - "
              "install manually: https://tailscale.com/download")
        return False
    print("  installing Tailscale (official script)...")
    try:
        cmd = "curl -fsSL https://tailscale.com/install.sh | sh"
        if needs_root:
            cmd = "curl -fsSL https://tailscale.com/install.sh | sudo sh"
        proc = subprocess.run(
            cmd, shell=True, capture_output=True, text=True, timeout=300,
        )
        if proc.returncode == 0 and shutil.which("tailscale"):
            _ok("Tailscale installed")
            return True
        _warn("Tailscale auto-install failed - "
              "install manually: https://tailscale.com/download")
        return False
    except Exception as e:
        _warn(f"Tailscale auto-install failed ({str(e)[:120]}) - "
              "install manually: https://tailscale.com/download")
        return False


def main(argv=None) -> int:
    args = parse_args(argv)
    print("=" * 70)
    print("  X5Agent install / update (one command)")
    print("=" * 70)
    # --run means fully automatic: no prompts (browser fix + public link),
    # and the public link is attempted unless explicitly disabled.
    auto = args.yes or args.run
    if not check_environment():
        return 1
    check_project_state()
    code_updated = update_source()
    if code_updated and not os.environ.get("X5_UPDATED"):
        # The pull just replaced this very file on disk: the running
        # process still executes the OLD code. Re-exec so the new code
        # takes over from a clean state (same CLI args). The marker env
        # var stops any repeat (pulls are idempotent afterwards anyway).
        print("  source updated mid-run - restarting installer with the new code ...")
        os.environ["X5_UPDATED"] = "1"
        script = str(Path(__file__).resolve())
        cli_args = list(argv) if argv is not None else sys.argv[1:]
        os.execv(sys.executable, [sys.executable, script, *cli_args])
    if not ensure_resources(hf_token=args.hf_token):
        return 1
    if not args.skip_browser:
        check_browser(assume_yes=auto)
    if not args.skip_firewall:
        ensure_firewall()
    if not ask_credentials(email=args.email, password=args.password):
        print("  hint: stdin is not interactive - pass --email/--password "
              "flags (or X5_EMAIL/X5_PASSWORD env) for non-interactive use",
              file=sys.stderr)
        return 1
    public_mode = args.public
    if args.run and public_mode is None:
        public_mode = True
    public_url, public_reason = setup_public_share(mode=public_mode, assume_yes=auto)
    if not args.no_open_browser:
        try:
            import webbrowser

            webbrowser.open(UI_URL)
        except Exception:
            pass
    print("\n" + "=" * 70)
    print(f"  Web Server (Local):  {UI_URL}")
    print(f"  Web Server (Public): {public_url or 'off - ' + public_reason}")
    print("  Server Running Now" if args.run else "  Server Ready")
    print("=" * 70)
    if args.run:
        already, stopper = False, None
        try:
            sys.path.insert(0, str(PROJECT_ROOT))
            from scripts.cli import _port_answers as _probe, _stop_server as _stopfn
            already = _probe(CONSOLE_PORT)
            stopper = _stopfn
        except Exception:
            already, stopper = False, None
        if already:
            print("\n[install] port busy - freeing it so exactly one server runs ...")
            ok = False
            if stopper is not None:
                try:
                    ok = stopper(CONSOLE_PORT)
                except Exception:
                    ok = False
            if not ok:
                print("  start aborted (see above).", file=sys.stderr)
                return 1
        print("\n[install] starting console now (--run)...")
        cmd = [sys.executable, "-m", "web.server.server",
               "--host", "127.0.0.1", "--port", "7777"]
        if public_mode:
            cmd.append("--public")
        os.chdir(str(PROJECT_ROOT))
        os.execv(sys.executable, cmd)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
