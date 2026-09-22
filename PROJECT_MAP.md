# Project Standard Map — Mandatory Blueprint

> **MANDATORY FOR EVERY NEW CHAT:** This map is the standard project structure.
> - Every new feature, file, or directory MUST be placed according to this map
> - If any file/directory is added, moved, renamed, or removed, you MUST update this map in the same commit/session
> - New chats MUST read this file together with `Master Code Rules.md` and `AGENTS.md` before any coding

Source of truth for structure: `PROJECT_MAP.md` (this file). Physical skeleton exists under `project/` with `.gitkeep` placeholders.

```
project/
├── core/
│   ├── agent/
│   │   ├── loop/
│   │   ├── context/
│   │   └── prompts/
│   ├── lm/
│   │   └── providers/
│   │       ├── Provider 1/
│   │       ├── Provider 2/
│   │       └── Provider 3/
│   ├── memory/
│   │   ├── Memory 1/
│   │   ├── Memory 2/
│   │   ├── Memory 3/
│   │   └── Memory 4/
│   └── tools/
│       ├── Core Tool 1/
│       │   └── Action/
│       │       ├── Event
│       │       └── Action Code
│       ├── Core Tool 2/
│       │   └── Action/
│       │       ├── Event
│       │       └── Action Code
│       └── Core Tool 3/
│           └── Action/
│               ├── Event
│               └── Action Code
├── plugins/
│   ├── tool-discovery/
│   │   └── search/
│   ├── Category 1/
│   │   ├── Action 1/
│   │   │   ├── Event
│   │   │   └── Action Code
│   │   ├── Action 2/
│   │   │   ├── Event
│   │   │   └── Action Code
│   │   └── Action 3/
│   │       ├── Event
│   │       └── Action Code
│   ├── Category 2/
│   │   ├── Action 1/
│   │   │   ├── Event
│   │   │   └── Action Code
│   │   └── Action 2/
│   │       ├── Event
│   │       └── Action Code
│   └── Category 3/
│       └── ...
├── skills/
│   ├── Skill 1/
│   ├── Skill 2/
│   └── Skill 3/
├── web/
│   ├── server/
│   └── frontend/
│       └── dashboard/
├── gateway/
│   ├── telegram/
│   ├── whatsapp/
│   └── discord/
├── storage/
│   ├── database/
│   ├── vector/
│   └── files/
│       └── images/
├── models/
│   ├── embedding/              # Qwen3-Embedding-0.6B (auto-downloaded, git-ignored)
│   ├── vision/                 # SmolVLM2-500M (auto-downloaded, git-ignored)
│   ├── tts/                    # supertonic-3 (auto-downloaded, git-ignored)
│   ├── stt/                    # whisper-medium-arabic-codeswitched-ct2 (auto-downloaded, git-ignored)
│   ├── manifest.json           # single source of truth for model auto-setup
│   ├── loader.py               # runtime ensure_models() helper (auto-download on first run)
│   └── README.md
├── scripts/
│   └── setup_models.py         # idempotent setup (3 retries, disk check +5GB buffer, CT2 conversion)
├── main.py                     # VPS entrypoint: check space -> auto-download -> start loop (zero-knowledge)
├── tests/
├── docs/
├── .env
├── README
├── LICENSE
└── pyproject
```

## Ownership Rules
- `core/` — core agent loop, LM providers, memory, tools
- `plugins/` — discoverable tools grouped by category
- `skills/` — reusable skills
- `web/` — server + frontend/dashboard
- `gateway/` — telegram/whatsapp/discord adapters
- `storage/` — database/vector/files
- `models/` — embedding/vision/tts/stt
- `tests/`, `docs/` — tests & documentation

## Update Protocol
When structure changes:
1. Update the tree above to match disk reality
2. Update physical directories (create/remove `.gitkeep`)
3. Mention the map change in commit/message
