# A New Agent Loop — Professional Local Agent

Professional personal agent with **System Prompt + Fixed Core Tools + Tool Discovery via Embeddings** (`Qwen3-Embedding-0.6B`) and 4 local models (Embedding / STT / Vision / TTS) running fully offline.

> **For beginners on VPS: one command clones, installs, and downloads everything automatically.**

---

## 🚀 One-Command Setup for Any VPS (Linux / Windows / Mac)

### Linux / Mac (Ubuntu, Debian, VPS)
```bash
git clone https://github.com/X5Coder/A-NEW-AI-AGENT-LOOP.git && cd A-NEW-AI-AGENT-LOOP/project && pip install -e . && python main.py
```

### Windows (PowerShell)
```powershell
git clone https://github.com/X5Coder/A-NEW-AI-AGENT-LOOP.git; Set-Location A-NEW-AI-AGENT-LOOP/project; pip install -e .; python main.py
```

**What `python main.py` does automatically:**

1. **Disk check:** requires **~6.3 GB for models + 5 GB buffer for user data + 1 GB system reserve = 14.2 GB free**. If insufficient, it shows a clear error and stops — no partial download.
2. **Auto-download:** resumes incomplete models, restarts missing ones, skips complete ones. Uses `snapshot_download(resume_download=True)` with 3 retries and optimal file filtering.
3. **Verification:** checks `model.bin` for STT, `processor` for Vision, etc.
4. **Start:** launches the agent loop. Second run starts instantly (skips download via `.download_complete` marker).

**No hardcoded secrets, no models pushed to GitHub — all models are local and downloaded once on first run.**

---

## 🩺 Doctor — Health Check

```bash
cd project
python scripts/doctor.py                 # Full check: Python, Disk, Dependencies, Models, Load Test
# or
python scripts/setup_models.py --check   # Models only
```

Example output:
```
✅ Python 3.12 OK
✅ Disk: Free 155GB / Required 14.2GB
✅ Dependencies: huggingface_hub, transformers, faster_whisper...
✅ READY embedding 1152 MB
✅ READY stt     2945 MB (CT2)
✅ READY vision  1940 MB
✅ READY tts      396 MB
✅ System healthy - ready to run: python main.py
```

**Resume behavior:**
- ✅ Complete model (marker exists) → skipped
- 🔄 Incomplete model (files exist but no marker) → resumes from where it stopped
- ⬇️ Missing model → starts fresh download

---

## 📦 Local Models (git-ignored, 6.28 GB total)

| Model | Source | Local Path | Size | Purpose |
|---|---|---|---|---|
| **Embedding** | `Qwen/Qwen3-Embedding-0.6B` | `project/models/embedding/Qwen3-Embedding-0.6B` | 1.15 GB | Tool discovery `tool-discovery/search` |
| **STT** | `MohamedRashad/Arabic-Whisper-CodeSwitching-Edition` → CT2 float16 | `project/models/stt/whisper-medium-arabic-codeswitched-ct2` | 2.94 GB | Arabic + English + code-switched + live (faster-whisper, VAD, chunk) |
| **Vision** | `HuggingFaceTB/SmolVLM2-500M-Video-Instruct` | `project/models/vision/SmolVLM2-500M` | 1.94 GB | Image/video understanding |
| **TTS** | `Supertone/supertonic-3` | `project/models/tts/supertonic-3` | 0.39 GB | Text-to-speech 31 languages ONNX |

`git clone` is only kilobytes. Models download on first run via `main.py` and are cached locally.

---

## 🔧 Additional Commands

```bash
# Manual download (same as main.py)
python scripts/setup_models.py
python scripts/setup_models.py --only vision   # Single model
python scripts/setup_models.py --force         # Re-download
python scripts/setup_models.py --check         # Check only

# Health check
python scripts/doctor.py

# Run agent (after models are ready)
python main.py
```

---

## 📁 Structure

See `PROJECT_MAP.md` — the canonical blueprint. Every file is placed per map; any change updates the map.

```
project/
├── main.py              # Single VPS entrypoint: space check -> auto-download -> start loop
├── models/
│   ├── manifest.json    # Single source of truth for models
│   ├── loader.py        # ensure_models() with resume logic
│   ├── embedding/       # Qwen3 (git-ignored)
│   ├── stt/             # whisper CT2 (git-ignored)
│   ├── vision/          # SmolVLM2 (git-ignored)
│   └── tts/             # supertonic-3 (git-ignored)
├── scripts/
│   ├── setup_models.py  # 3 retries, disk check +5GB buffer, CT2 conversion, resume
│   └── doctor.py        # Health check
└── pyproject.toml
```

---

## 🔒 Quality & Security

Built per `Master Code Rules.md` (83 rules): correctness first, no hardcoded secrets (`HF_TOKEN` from env only), path validation, 3 retries max, resource cleanup, clear English error messages, no data leaks.

---

## 📄 License

MIT
