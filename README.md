# X5Agent

Professional local agent with 4 offline models.

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
python main.py
```

---

## Doctor

```bash
python scripts/doctor.py
python scripts/setup_models.py --check
```

---

## Models

| Model | Path | Size |
| --- | --- | --- |
| Embedding | `models/embedding/Qwen3-Embedding-0.6B` | 1.15 GB |
| STT | `models/stt/whisper-medium-arabic-codeswitched-ct2` | 2.94 GB |
| Vision | `models/vision/SmolVLM2-500M` | 1.94 GB |
| TTS | `models/tts/supertonic-3` | 0.39 GB |

Total: 6.28 GB

---

## Commands

```bash
python main.py
python scripts/doctor.py
python scripts/setup_models.py
python scripts/setup_models.py --check
python scripts/setup_models.py --only vision
python scripts/setup_models.py --force
```

---

## Structure

```
project/
├── main.py
├── models/
│   ├── manifest.json
│   ├── loader.py
│   ├── embedding/
│   ├── stt/
│   ├── vision/
│   └── tts/
└── scripts/
    ├── setup_models.py
    └── doctor.py
```
