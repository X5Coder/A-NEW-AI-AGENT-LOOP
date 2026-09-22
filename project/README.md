# A New Agent Loop

## 🚀 One-Command VPS Setup (copy & run)

**Linux / Mac:**
```bash
git clone https://github.com/X5Coder/A-NEW-AI-AGENT-LOOP.git && cd A-NEW-AI-AGENT-LOOP/project && pip install -e . && python main.py
```

**Windows PowerShell:**
```powershell
git clone https://github.com/X5Coder/A-NEW-AI-AGENT-LOOP.git; Set-Location A-NEW-AI-AGENT-LOOP/project; pip install -e .; python main.py
```

This single command:
1. Checks disk space (requires 14.2 GB free = 6.3 GB models + 5 GB user buffer + 1 GB reserve) — stops with clear error if insufficient
2. Auto-downloads all 4 models to correct locations with resume support
3. Verifies each model is working
4. Starts the agent — second run starts instantly

## 🩺 Health Check

```bash
python scripts/doctor.py
python scripts/setup_models.py --check
```

**Resume logic:**
- Complete → skipped (marker exists)
- Incomplete → resumes from last checkpoint
- Missing → fresh download

## Models (6.28 GB total, git-ignored)

- `models/embedding/Qwen3-Embedding-0.6B` (1.15 GB) - Tool search
- `models/stt/whisper-medium-arabic-codeswitched-ct2` (2.94 GB CT2) - Arabic/English/code-switched + live
- `models/vision/SmolVLM2-500M` (1.94 GB) - Image analysis
- `models/tts/supertonic-3` (0.39 GB) - Text-to-speech

## Commands

```bash
python main.py                          # Run (auto-downloads on first run)
python scripts/doctor.py                # Full health check
python scripts/setup_models.py          # Manual download
python scripts/setup_models.py --check  # Check only
```
