# Project Standard Map — Mandatory Blueprint

> **MANDATORY FOR EVERY NEW CHAT:** This map is the standard project structure.
> - Every new feature, file, or directory MUST be placed according to this map
> - If any file/directory is added, moved, renamed, or removed, you MUST update this map in the same commit/session
> - New chats MUST read this file together with `Master Code Rules.md` and `AGENTS.md` before any coding

Source of truth for structure: `PROJECT_MAP.md` (this file). `storage/` keeps legacy fallback copies; authoritative home is `X5Agent/Data`.

```
X5Agent/                           # runtime sandbox (auto-created on first load via core/paths.py:ensure_x5agent())
├── Workspace/                     # model device - ONLY place model may read/write/execute (terminal+filesystem jailed here, open inside)
└── Data/                          # ALL user data - zip this for full backup/restore
    ├── database/                  # conversation.json, todos.json, scheduler.json, triggers.json + 4 memories (preferred read, fallback project/storage/database)
    ├── vector/                    # tool/chat/trigger/document embeddings (Data authoritative, writes ALWAYS here)
    ├── files/images|audio|videos|texts/  # user files (persistent vs temp) + pasted-txt uploads
    ├── skills/                    # skill TEXT content (not skill tool code)
    └── configs/plugin_configs/    # per-plugin settings only (not plugin code) + .env

(Model weights live in project/models/<type>/<name> - NOT in X5Agent.)

project/                           # == X5Agent/System Core (immutable engine, read-only for model)
├── prompts/                        # ALL system prompts - 12 files (8-layer context: L1-L3 system, L4-L8 user)
│   ├── core_identity.md            # L1 core: who he is + purpose + Workspace device (stable)
│   ├── general_mission.md          # L2 mission: how to behave + narration + output + 5 examples
│   ├── policy.md                   # L3 strict policy: Workspace jail + read-only Core + 3 retries + no secrets
│   ├── system.md                   # legacy fallback (used only if L1-L3 missing)
│   ├── system_prompt_architect_system.md # system-prompt architect: write/improve prompts cross-model (Claude/GPT/Gemini/open) + weak-model pass + workflow
│   ├── scheduler_skill_system.md   # scheduled skill generation
│   ├── trigger_skill_system.md     # trigger skill generation (one-time)
│   ├── stt_correction_system.md    # no invention, only clean
│   ├── user_memory_extract_system.md
│   ├── user_memory_organize_system.md
│   ├── agent_memory_extract_system.md
│   └── agent_memory_organize_system.md
├── X5Browser/                      # versioned browser layer (wrapper 1.3.0 over lib x5browser==1.2.0, see X5Browser/VERSION)
│   ├── __init__.py                 # exports version + tool lists (all 39 are core plugins)
│   ├── VERSION                     # wrapper + lib pin (bump on library updates)
│   ├── catalog.py                  # single source: 39 tool specs (38 library + show_preview), ALL core plugins (4 dangerous)
│   ├── service.py                  # single browser owner: EXPLICIT lifecycle (only open_browser/restart_browser boot; any other tool while stopped fails 'browser stopped', never boots WSL/units/Chrome on any OS) + orphan-stop (close from a fresh process stops live-stack units instead of no-op) + adopt (fresh process attaches to a live endpoint, still no boot) + quick probe (single-shot for polls/per-turn context) + bg asyncio loop + live stack boot = one LiveStack.start() call (library owns units/install/self-heal/WSL keep-alive; local fallback) + LIVE LINKS READ FROM THE LIBRARY (live_links() -> control+preview+owner, no cache/heal/persist) + read-only status_report() (never boots) + lifecycle lock/gen (no start-close race) + start/stop + screenshots (Data/files/browser, max 20) + boot fast-path (skip start when the ports answer; fail fast when units are active but Windows-unreachable, so a broken route never burns the stack timeout)
│   ├── tools.py                    # validation (untrusted args, http(s) only, Workspace jail) + execute + normalized output (library ok=false refusals never masked) + browser_status answered locally (never boots) + links copied from the library
│   └── events.py                   # short non-technical label per tool (all 39, user-friendly)
├── core/
│   ├── paths.py                    # X5Agent resolver: workspace/data/models roots + ensure_x5agent() + resolve_inside_workspace()
│   ├── agent/
│   │   ├── loop/                       # 500-step loop, never fails silently
│   │   ├── engine.py               # run_loop(user_input, max_steps=500, provider=openrouter) + dynamic tools + on_event stream (event/reason/media/step/live frames) + optional reason node per tool call (model `reason` stripped before execute, never synthesized; empty → step shows with no arrow unless output/image) + turn elapsed_sec (persisted per message) + restricted approvals + on_question pause (ask/approval/live) + on_live_event + cancel flag + live-URL redaction (model never sees stream links)
│   │   │   ├── dispatcher.py           # maps tool name -> execute (core + discoverable)
│   │   │   └── __init__.py
│   │   ├── context/
│   │   │   ├── builder.py              # build_context(): system + memories + last30 + env + todo + core tools (browser state = owner read from X5Browser live_links, no URL)
│   │   │   └── __init__.py
│   │   ├── filters/                  # filter layers (audio)
│   │   │   ├── stt_correction.py       # audio -> whisper -> correction via stt_correction_system.md (no invention)
│   │   │   ├── pipeline.py             # unified: audio→STT→main loop (direct)
│   │   │   └── __init__.py
│   │   └── memory/
│   │       ├── chat_vector_store.py    # per-message embeddings: add/delete/update per message, sync with conversation.json
│   │       └── __init__.py
│   ├── lm/
│   │   ├── usage.py                    # measured LLM accounting: log_usage() per call + get_daily() (Data/database/usage_log.json, bounded)
│   │   └── providers/
│   │       └── openrouter/             # OpenRouter provider for deepseek/deepseek-v4.1-flash
│   │           ├── provider.py         # chat() with tool-calling, 3 retries, timeout, no hardcoded secrets + returns/logs real usage
│   │           └── __init__.py
│   ├── documents/                      # document store: MarkItDown convert -> 300-word chunks -> embeddings + vision fallback for thin PDF pages
│   │   ├── store.py                    # SUPPORTED (pdf/docx/xls/pptx/csv/txt/md/html/json/xml) + chunk_markdown + ensure_indexed/search + delete/purge orphans (Data/vector/document_embeddings.json)
│   │   └── __init__.py
│   ├── memory/                         # 4 memories + auto-memory service
│   │   ├── user_memory/                # NOT tool - internal user facts (key/value, 200 max)
│   │   │   ├── service.py              # set/get/list/delete for user data
│   │   │   └── __init__.py
│   │   ├── agent_memory/               # NOT tool - internal agent state
│   │   │   ├── service.py
│   │   │   └── __init__.py
│   │   ├── image_memory/               # storage service for image metadata (persistent vs temp)
│   │   │   ├── service.py              # store/update/delete/list/clear_temp + get_context_payload()
│   │   │   └── __init__.py
│   │   ├── important_memory/           # storage service for important facts
│   │   │   ├── service.py              # store/update/delete/list for important facts
│   │   │   └── __init__.py
│   │   └── auto_memory.py              # auto every 50 msgs: extract (with 50 msgs) → store → organize (without chat, deduplicate)
│   ├── scheduler/                  # scheduler engine for large tasks (isolated execution)
│   │   ├── engine.py               # check_and_execute_due_jobs() every 60s, computes next_run, isolated mode, concurrent via ThreadPool
│   │   └── __init__.py
│   ├── trigger/                    # trigger watcher engine (one-time filesystem triggers)
│   │   └── engine.py               # watchers per trigger, fires skill once then dies + deletes skill
│   └── tools/
│       ├── registry.py             # tool registry + plugin/model/action enablement state (13 CORE_TOOLS incl. document_search)
│       ├── tool_search/            # CORE: semantic search over discoverable tools (embedding)
│       │   ├── spec.json           # verbatim description per requirement
│       │   ├── tool.py             # execute() + validation + auto-embedding via store
│       │   ├── events.py           # Finding tools
│       │   └── __init__.py
│       ├── question/               # CORE: ask user, wait response
│       │   ├── spec.json
│       │   ├── tool.py
│       │   ├── events.py           # Waiting for you
│       │   └── __init__.py
│       ├── document_search/          # CORE: search inside one uploaded doc (file_id+query+top_k min 1, validates supported type)
│       │   ├── spec.json           # lists supported files: PDF/Word/Excel/PowerPoint + csv/txt/md/html/json/xml
│       │   ├── tool.py             # execute() auto-indexes (convert->chunk->embed), rejects unsupported file_id
│       │   ├── events.py           # Searching docs · {file_name}
│       │   └── __init__.py
│       ├── vector_chat_search/     # CORE: search conversation memory (embedding)
│       │   ├── spec.json
│       │   ├── tool.py
│       │   ├── events.py           # Searching memory
│       │   └── __init__.py
│       ├── todo_list/              # CORE: structured task planning
│       │   ├── spec.json
│       │   ├── tool.py
│       │   ├── events.py           # Planning task
│       │   └── __init__.py
│       ├── scheduler/              # CORE: schedule tasks (practical: every create → skill in scheduled_skills via internal LLM, every run sends Skill)
│       │   ├── spec.json
│       │   ├── tool.py
│       │   ├── events.py           # Scheduling · {short schedule}
│       │   └── __init__.py
│       ├── skills_search/          # CORE: search skills semantically
│       │   ├── spec.json
│       │   ├── tool.py
│       │   ├── events.py           # Finding skills
│       │   └── __init__.py
│       ├── skills_view/            # CORE: view skill instructions
│       │   ├── spec.json
│       │   ├── tool.py
│       │   ├── events.py           # Opening · {skill_name}
│       │   └── __init__.py
│       ├── terminal/               # CORE: jailed in X5Agent/Workspace only (.. + absolute outside blocked)
│       │   ├── spec.json
│       │   ├── tool.py             # execute() jailed: workdir defaults Workspace, escape commands blocked
│       │   ├── events.py           # Running · {command}
│       │   └── __init__.py
│       ├── trigger/                # CORE: create/list/get/update/delete event triggers
│       │   ├── spec.json
│       │   ├── tool.py
│       │   ├── events.py           # Managing trigger · {trigger_name}
│       │   └── __init__.py
│       ├── trigger_search/         # CORE: semantic search over available trigger types
│       │   ├── spec.json
│       │   ├── tool.py
│       │   ├── events.py           # Finding triggers
│       │   └── __init__.py
│       ├── wait/                   # CORE: pause before continuing (for polling video jobs)
│       │   ├── spec.json
│       │   ├── tool.py             # sleep(duration) then continue loop
│       │   ├── events.py           # Waiting · {duration}
│       │   └── __init__.py
│       └── workspace/              # CORE: list/create/delete_file jailed in X5Agent/Workspace (one level, counts; delete_file also drops doc embeddings)
│           ├── spec.json
│           ├── tool.py             # execute() jailed: list bounded 200, mkdir validated, escape blocked
│           ├── events.py           # Workspace · {action}
│           └── __init__.py
├── plugins/
│   ├── core_plugins/               # CORE PLUGINS: always on, hidden from UI, no switches (9 tools, flat)
│   │   ├── read_file/              # jailed Workspace only
│   │   │   ├── spec.json
│   │   │   ├── tool.py
│   │   │   ├── events.py           # Reading · {file_name}
│   │   │   └── __init__.py
│   │   ├── write_file/             # Events: Writing · {file_name}
│   │   ├── search_files/           # Events: Searching files
│   │   ├── vision_analyze/         # Events: Analyzing · {image_name}
│   │   ├── video_analyze/          # Events: Analyzing video · {video_name}
│   │   ├── video_transcribe/       # Events: Transcribing video · {video_name}
│   │   ├── skill_create/           # Events: Creating skill · {skill_name}
│   │   ├── image_memory/           # Events: Storing/Updating/Listing images
│   │   ├── important_memory/       # Events: Saving/Listing memories
│   │   └── __init__.py
│   ├── tool-discovery/
│   │   └── search/
│   │       ├── store.py            # embedding store: auto-embed, hash, change detection, search
│   │       └── __init__.py
│   ├── trigger-discovery/
│   │   └── search/
│   │       ├── store.py            # trigger embedding store: auto-embed, hash, search
│   │       └── __init__.py
│   ├── filesystem/
│   │   └── triggers/
│   │       ├── filesystem_new_file/
│   │       │   └── spec.json
│   │       └── filesystem_file_modified/
│   │           └── spec.json
├── skills/
│   ├── frontend-design-pro/         # design-craft skill (auto-loaded via opencode.json instructions + discoverable via skills_search/view reading SKILL.md)
│   │   └── SKILL.md                # frontend-design-pro: method + rules + motion + RTL/Arabic + checklist
│   ├── scheduled_skills/           # auto-generated scheduled skills (practical system: each job → skill via internal LLM)
│   │   └── scheduled_<job_id>_<name>/ # contains skill.json + README.md, auto-deleted when job deleted
│   ├── trigger_skills/             # one-time trigger skills (fire once, then trigger + skill deleted)
│   │   └── trigger_<id>_<name>/    # contains skill.json + README.md
│   ├── Skill 1/
│   ├── Skill 2/
│   └── Skill 3/
├── web/
│   ├── server/                         # local dev server (127.0.0.1:5173, path-validated, document attachments (100MB) -> Workspace copy + priority block)
│   │   ├── server.py                   # static server + SPA fallback (extensionless→index.html) + JSON APIs (dashboard/cost/balance/health/conversation/plugins/skills/models/chat/chat_stream/chat_answer/chat_cancel/message/stt/plugin_state/model_state/providers/providers/test/live_state/shot/workspace) + /api/events SSE push hub (one stream/tab, hello snapshot, singleton watchers broadcast on change only) + secrets: UI save→vault+Data/.env+environ, UI clear→deleted everywhere, cost measured (usage_log + live pricing), SSE live loop on chat_stream (event/reason/media/live frames + live question pause) + LIVE STATE READ FROM THE LIBRARY: live_snapshot()/live_active() take running + control/preview links straight from X5Browser (no live_state.json, no host-heal, no relay/viewer restart) + in-memory live pause (_LIVE_PAUSE: qid→tool/reason, survives refresh, dropped on answer/expire/loop-end) + /api/chat_frames (bounded run-progress frames: event_start/event/question/live/live_end, never tokens) fanned out as `run` pushes so a refreshed page rebuilds the running Working block + steps against a route that is unreachable from Windows while units answer inside WSL (fail fast, never burn the stack timeout)
│   │   └── __init__.py
│   └── frontend/
│       └── dashboard/                  # X5Agent Console — Manus-adapted DARK-ONLY theme, modular (no inline style/script)
│           ├── index.html              # semantic structure only + skills page (cards grid + glass +Add button, nav under plugins, no inner title)
│           ├── index.backup-before-manus.html # pre-Manus backup (restore to revert)
│           ├── img/
│           │   ├── providers/            # lobe-icons static-png (MIT, trimmed): openai/anthropic/openrouter/deepseek/gemini
│           │   └── icons/                # lobe-icons static-png (MIT, trimmed): vllm (local models) + mcp (plugins)
│           ├── css/
│           │   ├── tokens.css          # Manus DARK-ONLY neutrals + bright blue accent + light primary, red danger-only
│           │   ├── tokens.backup-before-lighten.css # older workshop backup (pre-2026-09-28 lighten)
│           │   ├── tokens.backup-before-manus.css # pre-Manus backup (restore to revert)
│           │   ├── base.css            # reset, system+Cairo type, blue progress bar, transform-only keyframes, no-select only on agent chat output, no tap highlight
│           │   ├── base.backup-before-manus.css
│   │   ├── layout.css          # 308px nav / 56px toolbar / gray canvas + RTL drawer + icon rail with transform slide + hidden nav scrollbar
│           │   ├── layout.backup-before-manus.css
│           │   ├── components.css      # white cards, pills, black send, blue charts, thinking-block (dot-square ring wave + wave Working + per-step arrow → reason/output/image), red errors only
│           │   ├── components.backup-before-manus.css
│           │   ├── responsive.css      # breakpoints 1024/640 (logical drawer side + slide)
│           │   └── responsive.backup-before-manus.css
│           └── js/
│               ├── app.js              # entry: wires all modules + copy guard (agent page only)
│               ├── ui.js               # shared: animateIn + modal shell (drag-safe backdrop close + unified slow transform open/close) + toast + chart tooltip
│               ├── navigation.js       # drawer + desktop slide collapse + real-URL routing (/agent...) + toolbar titles
│               ├── dashboard.js        # 4 stats (Requests/Tokens/Balance/Model) + toolbar status: /api/dashboard + /api/cost + /api/balance + /api/models
│               ├── charts.js           # Tokens+Requests+Cost charts, 7D/30D/months ranges, /api/cost (silent refresh: sig-skip, first-paint anim only, peak highlight, hover desktop, tap mobile)
│               ├── chat.js             # agent: rich RTL/LTR typing (dir per language, Space Grotesk Working pinned LTR-left) + thinking-block per turn (dot-square 5x5 ring wave + wave Working, header arrow pops in with first step; reply typed once at done with normal animation, tool-phase text moves inside its event; settled Done + elapsed seconds + collapsed with reply below, empty block dropped, cancel renders Stopped line only; every content step has arrow → reason + output + lazy image; history reloads settled + expandable anytime) + live control card (interactive iframe + fullscreen + glass متابعة) + floating preview bar (attached strip, single icon btn + close-confirm) + fixed-right vertical-only tab + free-floating player (instant open, free drag/resize, half-off-right forceful dock only, snap-back inside otherwise, viewport-clamped open/drag/resize, stale-URL self-heal + auto-mint + tap-recover, down-hysteresis 4, insecure-context notice + https-upgrade) + live control card (ask sentence + fullscreen + confirm-continue, animated close) + push-driven (events.js, zero polling: browser tab/live/chat/reload via SSE) + live_state restore (one-shot) + @mentions + thumbs + voice recording panel + STT to composer/direct-send + attach menu (image/video/document→fileInputDocument max 100MB, 280px doc card with progress ring inside composer + entrance animation + per-type icon) + mode/approvals/questions + hover/long-press msg actions + hash route persists + refresh mid-run replays /api/chat_frames so the same task keeps its Working block + every step and keeps following `run` pushes + human_control closes the floating preview/edge tab before its card + right-edge tab slides in from the right + step bullets run ORANGE then turn GREEN with a check on success + agent chat is selectable/copyable again + composer focus glow animates in and fades out + show_preview opens the floating watch-only window (idempotent)
│               ├── events.js             # single SSE push channel: one EventSource/tab, hello snapshot + change-only frames (browser/live/chat/chat_done/dashboard/workspace), auto-reconnect with hello resync, no polling anywhere
│               ├── workspace.js          # workspace page: one-level file browser + breadcrumb + upload/mkdir + download-only + push reload on version change (no poll)
│               ├── plugins.js          # discoverable only: action=permission switches, silent save, core hidden
│               ├── skills.js           # user skills: cards grid (unified icon copies skill, no badge) + glass +Add + glass create/edit dialog (glass fields + persistent red errors + local draft, cleared on save + glass save/cancel, locked name on edit) + view modal with Copy/Edit, searchable via skills_search
│               ├── gateway.js          # WhatsApp/Telegram: badges + config modals + localStorage
│               ├── models.js           # Cloud provider cards only, no section title + vault (masked keys)
│               └── settings.js         # password validation + logout (local-only)
├── gateway/
│   ├── telegram/
│   ├── whatsapp/
│   └── discord/
├── storage/                      # LEGACY read-only fallback (authoritative home is X5Agent/Data, migrated once at startup)
│   ├── database/                   # fallback copies (todos/scheduler/conversation/triggers + 4 memories + plugin_state + model_providers)
│   ├── vector/                     # fallback embeddings (tool/chat/trigger)
│   └── files/                      # fallback media (images/videos)
├── models/                         # code only: embedding|vision|stt service.py + manifest + loader (weights live in project/models/<type>/<name>)
│   ├── embedding/
│   │   └── service.py              # embed_texts(), embed_query(), lazy load via loader.get_model_path(), 3 retries
│   ├── vision/
│   │   └── service.py              # analyze_image() (Data/files images first, legacy fallback), lazy load, 3 retries
│   ├── stt/
│   │   └── service.py              # transcribe_file(), lazy load, raw text (no correction layer)
│   ├── tts/                            # reserved placeholder (no service yet)
│   ├── manifest.json               # single source of truth: local_dir -> models/<type>/<name>
│   ├── loader.py                   # ensure_models() + get_model_path() (manifest primary, merged copies, never re-downloads) + scan_local_models()
│   ├── local_models/               # user-facing registry (local_dir -> models/<type>/<name>)
│   │   └── models.json             # index: name/desc/type/dir/used_by/plugins links (new dirs auto-appear)
│   └── README.md
├── scripts/
│   ├── setup_models.py             # idempotent setup into project/models (3 retries, disk check +5GB buffer, CT2 conversion)
│   └── doctor.py                   # environment/dependency diagnostics
├── main.py                         # VPS entrypoint: X5Agent ensure + legacy->Data migrate -> auto-download Models -> auto-embed tools -> start loop
├── tests/                          # all test files here (fixtures unified: read/write X5Agent/Data, same as runtime)
│   ├── test_auto_memory.py
│   ├── test_new_layers.py
│   ├── test_scheduler_large_reliable.py
│   └── test_workspace.py             # workspace tool jail/list/mkdir + top-level-only context
│   └── test_documents.py             # documents: 300-word chunks + markitdown + vision fallback + delete purges embeddings + wiring
│   └── test_streaming.py             # SSE decoding (no mojibake) + tool-delta rebuild
│   └── test_browser_lifecycle.py     # browser close-noop (no boot) + lifecycle lock
├── docs/
│   └── README.md
├── .env
├── .env.example
├── README.md
├── LICENSE
└── pyproject.toml
```

## Ownership Rules
- `prompts/` — ALL system prompts (12 files: L1 core_identity + L2 general_mission + L3 policy + legacy system.md + 8 others) visible at project root, editable
- `core/paths.py` — X5Agent sandbox resolver (workspace/data/models) + unified Data read/write helpers + migrate_legacy_to_data_once(), single source of truth for jail and Data authority
- `core/memory/` — 4 memories (writes ALWAYS X5Agent/Data, reads Data-first) + auto_memory service
- `core/documents/` — document store (file_id = Workspace-relative path; convert->chunk->embed; vision fallback for thin PDF pages; delete/purge orphans with Workspace)
- `core/tools/` — 13 core tools each own folder with Event (tool_search, question, vector_chat_search, document_search, todo_list, scheduler, skills_search, skills_view, terminal, wait, workspace, trigger, trigger_search) + workspace block in context (L4 top-level only) + documents metadata block (L4) + chat-uploaded documents priority block (L8) + direct-answer rule: no summary suffix when no tools were called (general_mission.md:7, system.md:6)
- `X5Browser/` — versioned single-folder browser layer (wrapper 1.3.0 over lib x5browser==1.2.0): catalog (39 specs: 38 library 1:1 + project `show_preview`, ALL core plugins, 4 dangerous) + service (sole browser owner, EXPLICIT lifecycle: only open/restart boot, all others fail 'browser stopped' while down, bg asyncio loop, live stack = one LiveStack.start(), LIVE LINKS READ FROM THE LIBRARY via live_links(), read-only status_report(), profile stays inside library, screenshots in Data/files/browser max 20) + tools (validation + normalized output, browser_status never boots) + events (plain-English label per tool). All 39 behind `tool_search` embedding (store scans catalog, hash auto-rebuilds). Dangerous 4 (fill_form, upload_files, export_page, console_command): restricted mode asks via question, otherwise direct.
- `plugins/` — `core_plugins/` (9 always-on hidden tools, flat: read_file, write_file, search_files, vision_analyze, video_analyze, video_transcribe, skill_create, image_memory, important_memory) — always effective, no UI switches, still searchable via `tool_search` embedding. `plugins/` UI page is intentionally empty. Filesystem trigger types stay in `plugins/filesystem/triggers/` (trigger_search system). Models page is cloud-only (no local section).
- `skills/` — reusable skills (frontend-design-pro auto-loaded + discoverable via SKILL.md) + scheduled_skills (auto-generated) + trigger_skills (one-time)
- `web/` — server + frontend/dashboard
- `gateway/` — telegram/whatsapp/discord adapters (events rendered as per Events table)
- `storage/` — LEGACY read-only fallback only (never written by runtime; Data is authoritative; migrated once at startup)
- `models/` — code only (services + manifest -> project/models/<type>; weights gitignored via project/models rule)
- `tests/`, `docs/` — tests & documentation

## Events Contract
- Core Tools: tool_search→Finding tools, question→Waiting for you, vector_chat_search→Searching memory, document_search→Searching docs · {file_name}, todo_list→Planning task, scheduler→Scheduling · {short schedule}, skills_search→Finding skills, skills_view→Opening · {skill_name}, terminal→Running · {command}, wait→Waiting · {duration}, workspace→Workspace · {action}, trigger→Managing trigger · {trigger_name}, trigger_search→Finding triggers + browser core: page_snapshot→Viewing page snapshot, navigate→Navigating to {url}, click→Clicking element {ref}, fill→Typing into element {ref}
- Discoverable (all core plugins, always on, hidden from UI): read_file→Reading · {file_name}, write_file→Writing · {file_name}, search_files→Searching files, vision_analyze→Analyzing · {image_name}, video_analyze→Analyzing video · {video_name}, video_transcribe→Transcribing video · {video_name}, skill_create→Creating skill · {skill_name}, image_memory→Storing/Updating/Listing images, important_memory→Saving/Listing memories + 39 browser events (see X5Browser/events.py)
- Rules: {file_name}/{skill_name}/{image_name} filename only, {command} truncated, {short schedule} compact, never display long free-form inputs
- Tool Search: semantic top-k over discoverable tools, loop bounded by max_steps=500
- Video Handling: video not saved, only metadata sent; frame analysis via ThreadPool(4) for speed; STT per timestamp

## Embedding Auto-Logic
- On startup (`main.py` + `tool_search` first call) -> `plugins/tool-discovery/search/store.py:ensure_embeddings()`:
  - collects all discoverable specs (verbatim descriptions, now 9 core_plugins + 39 browser)
  - computes SHA256 hash
  - if hash matches stored `storage/vector/tool_embeddings.json` -> reuse
  - else delete old, embed all descriptions via `models/embedding/service.py` (Qwen3-Embedding-0.6B, 3 retries), save new hash+embeddings
- If user edits any description in spec.json and reruns code, change is detected automatically and old embeddings are purged + rebuilt.

## Update Protocol
When structure changes:
1. Update the tree above to match disk reality
2. Update physical directories (create/remove `.gitkeep`)
3. Mention the map change in commit/message
