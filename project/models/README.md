# Models

All model binaries are **local** and **git-ignored**. They are auto-downloaded on first run.

## Manifest
Single source of truth: `manifest.json`

| id | repo | local path |
|---|---|---|
| embedding | `Qwen/Qwen3-Embedding-0.6B` | `project/models/embedding/Qwen3-Embedding-0.6B` |
| stt | `MohamedRashad/Arabic-Whisper-CodeSwitching-Edition` (CT2) | `project/models/stt/whisper-medium-arabic-codeswitched-ct2` |
| vision | `HuggingFaceTB/SmolVLM2-500M-Video-Instruct` | `project/models/vision/SmolVLM2-500M` |
| tts | `Supertone/supertonic-3` | `project/models/tts/supertonic-3` |

## Auto-download (Resume-aware)
- First run, `scripts/setup_models.py` is invoked via `models/loader.py:ensure_models()` or `python main.py`
- **Resume logic:**
  - Complete (`.download_complete` exists) → skipped
  - Incomplete (partial files, no marker) → resumes from last checkpoint via `snapshot_download(resume_download=True)`
  - Missing → fresh download
- Disk check: requires models size + 5 GB user buffer + 1 GB reserve; stops with clear English error if insufficient
- Bounded retries: max 3 per model, with backoff 2s/5s/10s
- Path security: all `local_dir` values are validated to stay inside `project/models/`
- No hardcoded secrets: uses `HF_TOKEN` env var if present

## Manual usage
```bash
# from project/ directory
python -m pip install -r requirements  # or pip install -e .
python scripts/setup_models.py --check
python scripts/setup_models.py
python scripts/setup_models.py --only embedding
python scripts/setup_models.py --force
```

## STT CT2 note
`whisper-medium-arabic-codeswitched-ct2` is a CTranslate2 conversion of the Arabic code-switched Whisper medium.
If `ctranslate2` is not installed, the script keeps the original HF model as fallback (still usable via `transformers`).

Install converter for true CT2:
```bash
pip install ctranslate2 transformers
python scripts/setup_models.py --only stt --force
```

## Git hygiene
`project/models/` weights are git-ignored (root `.gitignore`).
Only code (`service.py`, configs) is tracked.

## Runtime helper
```python
from models.loader import ensure_models, get_model_path
ensure_models(auto_download=True)
emb_path = get_model_path("embedding")
```
