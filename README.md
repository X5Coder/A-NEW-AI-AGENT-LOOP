# X5Agent — v0.15.0

Professional agent with one built-in model (embedding) plus universal
connections to any AI model through **AI Models** templates.

---

## Setup — unified install / update command (v0.15.0)

**One command installs a fresh copy OR updates an existing one** (same command,
safe to re-run — source only via `git pull --ff-only`, `X5Agent/Data` untouched):

```bash
curl -fsSL https://raw.githubusercontent.com/X5Coder/X5Agent/main/setup.sh | bash
```

Verify after update: open Settings → Updates (must show `0.15.0`) or check
`updates.json` → `"version": "0.15.0". Then hard-refresh the console page
(Ctrl+Shift+R) so the new `live-url.js`/`chat.js` take effect.

**Requirements: Python 3.10+** (full browser support — `x5browser==1.5.1`
runs on 3.10+, 3.12 recommended), TCP port **7777** (the installer opens it via `ufw`/`firewalld`
automatically; else `sudo ufw allow 7777/tcp`).

**One command (Linux / Mac, fresh VPS)**

```bash
curl -fsSL https://raw.githubusercontent.com/X5Coder/X5Agent/main/setup.sh | bash
```

Non-interactive (email/password/public passed through to the installer):

```bash
curl -fsSL https://raw.githubusercontent.com/X5Coder/X5Agent/main/setup.sh | bash -s -- --email owner@mail.com --password S3cret123 --public
```

What the one-liner does: clones (or fast-forward updates) the repo into
`~/X5Agent`, auto-installs dependencies (`pip install -e .` inside
`install.py` when missing), runs the full installer (env + browser doctor +
owner once + firewall), then starts the console on `127.0.0.1:7777`
(localhost only — remote access goes through the Tailscale Funnel public
link, which always requires the owner login first).
Re-running it only updates the source (`git pull --ff-only`, data untouched).

**Manual (Linux / Mac)**

```bash
git clone https://github.com/X5Coder/X5Agent.git
cd X5Agent/project
python3.12 install.py --run
```

`install.py --run` = install + start in one step (dependencies auto-install,
server binds `127.0.0.1:7777` - localhost only, never exposed publicly).
The owner email + password are asked only once (saved locally, skipped on
later runs). Every browser visit lands on the login page until the owner
signs in (session cookie kept after that).
Without `--run` it only installs; then start manually with `x5agent`.

**Start directly (from anywhere, no update check)**

```bash
x5agent              # serve now on 127.0.0.1:7777 (localhost only)
x5agent --public     # also enable the Tailscale Funnel public link (login required)
```

The `x5agent` command is installed on PATH by `pip install -e .`
(the installer adds it automatically when missing). Restart semantics:
the console port is always freed first — ANY stale holder on it is stopped
(graceful SIGINT, then forced SIGKILL; suspended ones resumed first) before
the fresh server binds, so exactly one server ends up running.

**Windows**

```powershell
git clone https://github.com/X5Coder/X5Agent.git
Set-Location X5Agent/project
py -3.12 -m pip install -e .
py -3.12 install.py              # one command: checks + updates + owner + console URL
py -3.12 -m web.server.server   # start the console (http://127.0.0.1:7777/)
```

Non-interactive: `python install.py --email owner@mail.com --password S3cret123 --public`
(or `X5_EMAIL`/`X5_PASSWORD` env). Optional `HF_TOKEN=hf_xxx` only raises
HuggingFace download rate limits on slow networks — never required.
Local vs public: `scripts/x5-local` / `scripts/x5-public`
(systemd: `scripts/x5console*.service`).

The bundled `embedding_model/` folder (~1.2 GB) ships with the project
copy (release zip) and is used offline - nothing is downloaded on first run
(GPU is used automatically when available, else CPU).

---

## Models

| Model | Path | Size |
| --- | --- | --- |
| Embedding (built-in, hidden) | `embedding_model/` | 1.15 GB |

Chat, vision, image, speech-to-text and text-to-speech are NOT shipped:
connect any of them in the console under **AI Models** (5 fixed templates,
same 4 fields each, universal Connection JSON).

---

## Commands

```bash
python main.py
```

---

## Structure

```
project/
├── main.py
├── embedding_model/      # bundled embedding weights (offline, ships with release zip)
├── core/
│   ├── embedding/        # built-in embedding bootstrap + service
│   └── ai_models/        # universal connection engine (5 templates)
```
