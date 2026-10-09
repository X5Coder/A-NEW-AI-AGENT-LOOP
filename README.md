# X5Agent

Professional agent with one built-in model (embedding) plus universal
connections to any AI model through **AI Models** templates.

---

## Setup

**Linux / Mac**

```bash
git clone https://github.com/X5Coder/A-NEW-AI-AGENT-LOOP.git
cd A-NEW-AI-AGENT-LOOP/project
pip install -e .
python main.py
```

**Windows**

```powershell
git clone https://github.com/X5Coder/A-NEW-AI-AGENT-LOOP.git
Set-Location A-NEW-AI-AGENT-LOOP/project
pip install -e .
python install.py              # one command: checks + updates + owner + console URL
python -m web.server.server   # start the console (http://127.0.0.1:7777/)
```

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
