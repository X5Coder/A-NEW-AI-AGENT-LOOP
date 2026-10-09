# X5Agent

---

## Setup

**Linux / Mac**

```bash
git clone https://github.com/X5Coder/A-NEW-AI-AGENT-LOOP.git
cd A-NEW-AI-AGENT-LOOP/project
pip install -e .
python install.py              # one command: checks + updates + owner + console URL
python -m web.server.server   # start the console (http://127.0.0.1:5173/)
```

**Windows**

```powershell
git clone https://github.com/X5Coder/A-NEW-AI-AGENT-LOOP.git
Set-Location A-NEW-AI-AGENT-LOOP/project
pip install -e .
python install.py              # one command: checks + updates + owner + console URL
python -m web.server.server   # start the console (http://127.0.0.1:5173/)
```

---

## Commands

```bash
python main.py
```

The bundled `embedding_model/` folder (~1.2 GB) ships with the project
copy (release zip) and is used offline - nothing is downloaded on first run.

---

## Models

The only built-in model is the hidden embedding model:

```
embedding_model/  (bundled embedding model, ~1.2 GB, offline, GPU auto / CPU fallback)
```

Chat, vision, image, speech-to-text and text-to-speech models are NOT
shipped: connect any of them in the console under **AI Models**
(5 fixed templates, same 4 fields each, universal Connection JSON).
