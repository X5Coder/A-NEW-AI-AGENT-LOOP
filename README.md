# X5Agent

Professional agent with one built-in model (embedding) plus universal
connections to any AI model through **AI Models** templates.

---

## Setup

**Requirements: Python 3.10+** (full browser support — `x5browser==1.5.1`
runs on 3.10+, 3.12 recommended), TCP port **7777** (the installer opens it via `ufw`/`firewalld`
automatically; else `sudo ufw allow 7777/tcp`).

**Linux / Mac**

```bash
git clone https://github.com/X5Coder/X5Agent.git
cd X5Agent/project
python3.12 -m pip install -e .
python3.12 install.py
python3.12 -m web.server.server
```

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
