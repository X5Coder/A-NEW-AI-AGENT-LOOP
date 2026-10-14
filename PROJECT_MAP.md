# Project Standard Map — Mandatory Blueprint

> **MANDATORY FOR EVERY NEW CHAT:** This map is the standard project structure.
> - Every new feature, file, or directory MUST be placed according to this map
> - If any file/directory is added, moved, renamed, or removed, you MUST update this map in the same commit/session
> - New chats MUST read this file together with `Master Code Rules.md` and `AGENTS.md` before any coding

Source of truth for structure: `PROJECT_MAP.md` (this file). `storage/` keeps legacy fallback copies; authoritative home is `X5Agent/Data`.

```
updates.json                   # THE single update source at the repo root (one file, NO folder): {version, description, features} — bump only on big updates or on request (see AGENTS.md Updates-file rule)
Dockerfile                     # runtime image: code only (python 3.12-slim + git + official Google Chrome + pip ./project); .dockerignore keeps X5Agent/ + weights + secrets OUT; .git stays for pull
compose.yml                    # THE one command: `docker compose up --build` — image build + git-pull-on-start + console on :7777; named volumes x5-data + x5-models (data never lost)
.dockerignore                  # image hygiene: excludes X5Agent/ (live data), weight binaries, .env, junk — keeps .git for source refresh
docker-entrypoint.py           # container start (stdlib only): git pull --ff-only (source only, warn-continue offline) -> seed models volume -> ensure X5Agent dirs -> exec server 0.0.0.0:7777 (+ --public when X5_PUBLIC=1)
setup.sh                       # VPS one-liner: curl -fsSL .../setup.sh | bash [-s -- <install.py flags>] — clones (or --ff-only updates) ~/X5Agent then execs project/install.py --run (persistent detached via cli.launch_detached: old server killed, new one on 7777, SSH-safe; --no-run opts out of starting)

X5Agent/                           # runtime sandbox (auto-created on first load via core/paths.py:ensure_x5agent())
├── Workspace/                     # model device - ONLY place model may read/write/execute (terminal+filesystem jailed here, open inside)
└── Data/                          # ALL user data - zip this for full backup/restore
    ├── database/                  # conversation.json, todos.json, scheduler.json, triggers.json, pdf_tasks.json + 4 memories (preferred read, fallback project/storage/database)
    ├── vector/                    # tool/chat/trigger/document embeddings (Data authoritative, writes ALWAYS here)
    ├── files/images|audio|videos|texts/  # user files (persistent vs temp) + pasted-txt uploads
    ├── skills/                    # skill TEXT content (not skill tool code)
    └── configs/plugin_configs/    # per-plugin settings only (not plugin code) + .env

(Model weights live in project/models/<type>/<name> - NOT in X5Agent.)

project/                           # == X5Agent/System Core (immutable engine, read-only for model)
├── prompts/                        # ALL system prompts - 14 files (8-layer context: L1-L3 system, L4-L8 user)
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
│   ├── agent_memory_organize_system.md
│   └── pdf_slide_system.md           # pdf_create internal slide session (separate system prompt, one HTML per slide)
│   └── svg_icon_system.md            # svg_maker internal icon session (separate system prompt, simple 24x24 icon basics, raw SVG only)
├── X5Browser/                      # versioned browser layer (wrapper 1.5.2 over lib x5browser==1.5.2, see X5Browser/VERSION)
│   ├── __init__.py                 # exports version + tool lists (all 42 are core plugins)
│   ├── VERSION                     # wrapper + lib pin (bump on library updates)
│   ├── catalog.py                  # single source: 42 tool specs (41 library + show_preview), ALL core plugins (4 dangerous)
│   ├── service.py                  # single browser owner: EXPLICIT lifecycle (only open_browser/restart_browser boot; any other tool while stopped fails 'browser stopped', never boots WSL/units/Chrome on any OS) + orphan-stop (close from a fresh process stops live-stack units instead of no-op) + adopt (fresh process attaches to a live endpoint, still no boot) + quick probe (single-shot for polls/per-turn context) + bg asyncio loop + live stack boot = one LiveStack.start() call (library owns units/install/self-heal/WSL keep-alive; headless local fallback when stack unavailable: no visible window, library profile only, never real User Data) + LIVE LINKS READ FROM THE LIBRARY (live_links() -> control+preview+owner, no cache/heal/persist) + read-only status_report() (never boots) + lifecycle lock/gen (no start-close race) + start/stop + screenshots (Data/files/browser, max 20) + boot fast-path (skip start when the ports answer; fail fast when units are active but Windows-unreachable, so a broken route never burns the stack timeout) + reap_stale_viewers() (newest websockify on 6080 kept, extras killed; orphaned /tmp-profile headless Chromes killed + stale dirs removed; X5 live Chrome never touched)
│   ├── tools.py                    # validation (untrusted args, http(s) only, Workspace jail) + execute + normalized output (library ok=false refusals never masked) + browser_status answered locally (never boots) + show_preview answers the preview token only (prefer library preview link, force view_only form) + links copied from the library
│   └── events.py                   # short non-technical label per tool (all 42, user-friendly)
├── core/
│   ├── paths.py                    # X5Agent resolver: workspace/data/models roots + ensure_x5agent() + resolve_inside_workspace()
│   ├── agent/
│   │   ├── loop/                       # 500-step loop, never fails silently
│   │   │   ├── engine.py               # run_loop(user_input, max_steps=500, llm=universal-text-model via core/ai_models, text-only in v1) + dynamic tools + on_event stream (event/reason/media/step/live frames) + optional reason node per tool call (model `reason` stripped before execute, never synthesized; empty → step shows with no arrow unless curated detail/image) + curated per-tool step detail UNDER reason (todo steps, file names, terminal full command + exit + output tail, pdf_create per-slide Arabic lines + merge line, svg_maker icon name + size, question Q+A, model-skip sentence; default reason-only, raw output never dumped; failures show one short cause, titles never raw errors) + missing-model pause (Connect retries the SAME tool once connected, Skip sends the Arabic skip sentence to the model and continues; strip above composer, refresh-safe, STOP drops it) + turn elapsed_sec (persisted per message) + final_audio (text_to_speech records → standalone voice bubble, persisted as assistant audio) + restricted approvals + on_question pause (ask/approval/live) + on_live_event + cancel flag + live-URL redaction (model never sees stream links) + mid-step cancel break (STOP during a pause drops the result silently) + real STOP first time: AIP requests carry the cancel event (abort between read chunks, interruptible retry backoff) + STOP after the model replies skips remaining tool calls + provider-error path breaks at once on cancel + re-stop of a resumed task refreshes its marker in place (never stacks Stopped dividers) + seamless resume_stopped (Continue): no L8, trailing stopped markers excluded from L6 context-only, refused resumes persist nothing so the pill stays retryable + STOP stores resume_units on the marker so Continue restores the same loop window (or UI-step fallback) instead of restarting the task
│   │   │   ├── dispatcher.py           # maps tool name -> execute (core + discoverable)
│   │   │   └── __init__.py
│   │   ├── context/
│   │   │   ├── builder.py              # build_context(): system + memories + last30 + env + todo + core tools (browser state = owner read from X5Browser live_links, no URL) + Workspace map files carry id+path+type+size + path-link/copy guidance + resume_stopped (seamless Continue): omits the L8 input block and drops trailing stopped markers from L6 context-only
│   │   │   └── __init__.py
│   │   ├── filters/                  # filter layers (audio)
│   │   │   ├── stt_correction.py       # audio -> QwenCleo-ASR (via models/stt/service.py, single owner) -> correction via stt_correction_system.md (no invention)
│   │   │   ├── pipeline.py             # unified: audio→STT→main loop (direct)
│   │   │   └── __init__.py
│   │   └── memory/
│   │       ├── chat_vector_store.py    # per-message embeddings: add/delete/update per message, sync with conversation.json
│   │       └── __init__.py
│   ├── lm/
│   │   ├── usage.py                    # measured accounting: log_usage() per call + get_daily() (Data/database/usage_log.json, bounded)
│   │   └── providers/
│   │       └── openrouter/             # RETIRED (unwired): superseded by core/ai_models templates; kept only because tests/test_streaming.py exercises its SSE decoder
│   │           ├── provider.py         # (dead) old OpenRouter chat() — nothing imports it except the streaming test
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
│   ├── scheduler/                  # scheduler engine (each run = a normal message in the job's own branch session, same full main-chat context L1-L8 + branch history)
│   │   ├── engine.py               # check_and_execute_due_jobs() every 60s, computes next_run (daily/every s/m/h/d; sub-minute cadences run per 60s tick), non-isolated branch runs, concurrent via ThreadPool + on_start/on_done executing callbacks + run_history output bounded 2000/run (last 10) + persists skill-as-user-message + reply into the job's branch session (never main chat)
│   │   ├── session.py              # one branch session per job: Data/database/scheduler_sessions/<job_id>.json (last 100 msgs, history_block for L6, auto-deleted with the job)
│   │   └── __init__.py
│   ├── trigger/                    # trigger watcher engine (one-time filesystem + library triggers)
│   │   ├── session.py              # one hidden branch session per trigger: Data/database/trigger_sessions/<trigger_id>.json (last 100 msgs, history_block for L6, no UI, auto-deleted with the trigger)
│   │   └── engine.py               # re-armed at server startup (watchers are in-memory) + capped watchers + restart on config update + registered polling fallback; per trigger: fires skill into its own hidden branch session (same full main-chat context L1-L8 + own history, never main chat, no UI) then dies + deletes skill + session + library layer: check_library_triggers() every 60s (fired log → bound skill runs branch, both sides deleted) + _rearm_library_entry() (browser died → re-arm inside browser, no duplicates) + bounded handled-ids (200 max)
│   └── tools/
│       ├── registry.py             # tool registry + plugin/model/action enablement state (17 CORE_TOOLS: tool_search/question/vector_chat_search/todo_list/scheduler/skills_search/skills_view/skill_create/terminal/trigger_search/wait + 6 browser core open_browser/navigate/page_snapshot/click/fill/human_control; workspace + document_search are discoverable)
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
│       ├── document_search/          # DISCOVERABLE (behind tool_search): search inside one uploaded doc (file_id+query+top_k min 1, validates supported type)
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
│       ├── scheduler/              # CORE: schedule tasks (practical: every create → skill in scheduled_skills via the SAME AIP/1 path as the loop: system skill-prompt + task + last-10 chat + todo, internal bounded retries, every run sends Skill; update(task) regenerates it; repeat validated daily|every s/m/h/d; orphan skill removed if save fails)
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
│       │   ├── events.py           # fixed "Terminal Command" title (full command + exit + output tail live under the arrow)
│       │   └── __init__.py
│       ├── trigger/                # INTERNAL impl only (no spec.json, never exposed): core.tools.trigger.tool used by trigger_search for create/list/get/update/delete + tests import its validators
│       │   ├── tool.py             # (no spec.json — there is no "trigger" tool) + library layer for manage_triggers (validate config → same-model skill → arm inside browser, 1 entry = 1 library trigger + 1 skill; stop+restart on identity change, best-effort stop on delete, orphan-skill guard on failed create)
│       │   ├── events.py
│       │   └── __init__.py
│       ├── trigger_search/         # CORE: THE single trigger tool — embedding search over ALL library+project trigger types + create/list/get/update/delete (delegates to trigger/tool.py)
│       │   ├── spec.json           # action enum search|create|list|get|update|delete (default search)
│       │   ├── tool.py
│       │   ├── events.py           # Finding triggers
│       │   └── __init__.py
│       ├── wait/                   # CORE: pause before continuing (for polling video jobs)
│       │   ├── spec.json
│       │   ├── tool.py             # chunked sleep(duration, cancel_flag) — STOP interrupts the wait at once, then the loop breaks
│       │   ├── events.py           # Waiting · {duration}
│       │   └── __init__.py
│       └── workspace/              # DISCOVERABLE (behind tool_search): list/create/delete_file jailed in X5Agent/Workspace (one level, counts; file entries carry id+path+size; delete_file also drops doc embeddings)
│           ├── spec.json
│           ├── tool.py             # execute() jailed: list bounded 200, mkdir validated, escape blocked
│           ├── events.py           # Workspace · {action}
│           └── __init__.py
├── plugins/
│   ├── core_plugins/               # CORE PLUGINS: always on, hidden from UI, no switches (16 tools, flat)
│   │   ├── read_file/              # jailed Workspace only
│   │   │   ├── spec.json
│   │   │   ├── tool.py
│   │   │   ├── events.py           # Reading · {file_name}
│   │   │   └── __init__.py
│   │   ├── write_file/             # Events: Writing · {file_name}
│   │   ├── search_files/           # Events: Searching files
│   │   ├── vision_analyze/         # Events: Analyzing · {image_name} (images + video visual content incl. Workspace videos/video_analyze frames)
│   │   ├── video_analyze/          # Events: Analyzing video · {video_name} (accepts Workspace + project + Data/files)
│   │   ├── video_transcribe/       # Events: Transcribing video · {video_name} (accepts Workspace + project + Data/files)
│   │   ├── video_download/         # Events: Downloading video · {host} (yt-dlp URL -> Workspace mp4 + metadata for vision)
│   │   │   ├── spec.json
│   │   │   ├── tool.py
│   │   │   ├── events.py
│   │   │   └── __init__.py
│   │   ├── audio_extract/          # Events: Extracting audio · {video_name} (FFmpeg video -> Workspace mp3/wav + metadata)
│   │   │   ├── spec.json
│   │   │   ├── tool.py
│   │   │   ├── events.py
│   │   │   └── __init__.py
│   │   ├── embedding_memory/         # Events: Saving/Searching memory (reason+text=store, query+top_k=search, Qwen3 -> Data/vector/embedding_memory.json, conditional L5 count line hidden when empty)
│   │   │   ├── spec.json
│   │   │   ├── tool.py
│   │   │   ├── events.py
│   │   │   └── __init__.py
│   │   ├── text_to_speech/           # Events: Speaking · {words} (fixed nova voice via integrated model only -> Data/files/audio wav, plays as standalone bubble via /api/audio)
│   │   │   ├── spec.json
│   │   │   ├── tool.py
│   │   │   ├── events.py
│   │   │   └── __init__.py
│   │   ├── skill_create/           # Events: Creating skill · {skill_name}
│   │   ├── image_memory/           # Events: Storing/Updating/Listing images
│   │   ├── important_memory/       # Events: Saving/Listing memories
│   │   ├── http_request/           # Events: Fetching/Posting · {host} (generic GET/POST/PUT/PATCH/DELETE to any http(s) URL incl. MCP JSON-RPC, any headers + any text/JSON body on any method, stdlib only, 1MB cap, 3 retries)
│   │   ├── pdf_create/             # Events: Creating/Updating/Reading PDF · {pdf_name} (DISCOVERABLE behind tool_search: create|update|get + size A4/Letter/square default rectangle, one HTML per slide + per-slide headless-Chrome print merged via pypdf with fpdf2 fallback, tasks in Data/database/pdf_tasks.json, fonts/Amiri-Regular.ttf (Arabic) + fonts/Cairo-Variable.ttf (Latin) OFL bundled defaults auto-applied per slide script when the user uploads no font; progress lines in Arabic per finished slide + final merge line shown under the arrow; file refs accept Workspace-relative sub-path ids, jailed copy into the deck, slide HTML uses the exact id as src)
│   │   ├── svg_maker/              # Events: Creating icon · {icon_name} (DISCOVERABLE behind tool_search: description+icon_name+size 16..512 default 48, one internal icon session via svg_icon_system.md with the same text model, returns RAW SVG CODE ONLY + saved .svg in Workspace, max 3 attempts)
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
│   ├── scheduled_skills/           # auto-generated scheduled skills (practical system: each job → skill via internal LLM)
│   │   └── scheduled_<job_id>_<name>/ # contains skill.json + README.md, auto-deleted when job deleted
│   ├── trigger_skills/             # one-time trigger skills (fire once, then trigger + skill deleted)
│   │   └── trigger_<id>_<name>/    # contains skill.json + README.md
│   ├── Skill 1/
│   ├── Skill 2/
│   └── Skill 3/
├── web/
│   ├── server/                         # local dev server (127.0.0.1:7777, path-validated, console auth (first-run setup -> .env pbkdf2 hash + HttpOnly session, login.html gate), chat attachments (image/video/text/document, doc 100MB) -> Workspace mirror copy + priority block)
│   │   ├── server.py                   # static server + SPA fallback (extensionless→index.html) + owner gate (first-run setup redirect, authed APIs 401/pages 302, /api/auth/*) + JSON APIs (dashboard/cost/balance/health/conversation/plugins/skills/models-compat/ai_models+ai_models/save+ai_models/test+ai_models/clear+ai_models/setup_prompt/chat/chat_stream/chat_answer/chat_cancel/message/stt/plugin_state/model_state/live_state/shot/workspace/scheduler/public_share + /api/audio TTS records; REMOVED 410-Gone: model_download/requirements*/doctor/providers*) + chat readiness + models-compat from the AI Models store (universal text model) + /api/events SSE push hub (one stream/tab, hello snapshot, singleton watchers broadcast on change only) + STRICT owner gate (login mandatory, cookie session only, no tokens): no owner -> ONLY setup endpoints open (every other page 302s to login, every other API 401s); owner set -> valid cookie goes straight to / (login.html bounces authed users to /, Back never lands on login); login page setup-mode creates the owner once. TRANSPORT: server->browser is ONE SSE stream only (GET /api/events, opened once per tab; POST /api/chat_stream response-body SSE for live turns), browser->server is POST only (sync threaded); /ws answers 410-Gone (WebSocket removed: ws_proto.py + ws_auth.py + ws.js deleted). hello ORDER + CACHE + HASH: chat_last50 (last 50 agent messages, hashed) FIRST -> dashboard bundle (hashed) second -> rest {browser/chat/workspace/scheduler/gateway} (hashed) last; unchanged hash = cached render kept, broken hash = refresh + rehash; invalidation at run-end/chat_done/dashboard-emit + scheduler snapshot/emit (_get_scheduler_data/_events_emit_scheduler, 15s watcher + force after chat run/skill edit) + scheduler engine ticker (60s, executing callbacks → live "يتم التنفيذ" push) + secrets: UI save→vault+Data/.env+environ, UI clear→deleted everywhere, cost measured (usage_log + live pricing), SSE live loop on chat_stream (event/reason/media/live frames + live question pause) + LIVE STATE READ FROM THE LIBRARY: live_snapshot()/live_active() take running + control/preview links straight from X5Browser (no live_state.json, no host-heal, no relay/viewer restart) + in-memory live pause (_LIVE_PAUSE: qid→tool/reason, survives refresh, dropped on answer/expire/loop-end) + /api/chat_frames (bounded run-progress frames: event_start/event/question/live/live_end, never tokens) fanned out as `run` pushes so a refreshed page rebuilds the running Working block + steps; STOP sets cancelled immediately so chat_active/chat_frames report inactive (refresh never reconnects a stopped task) + steps against a route that is unreachable from Windows while units answer inside WSL (fail fast, never burn the stack timeout) + done frames carry audio (final TTS voice records) + --public flag (background Tailscale Funnel enable after the port answers)
│   │   ├── tailscale_share.py          # PUBLIC link owner (stdlib only, Win+Linux): install check -> `tailscale login` (registration URL printed + opened, bounded wait) -> funnel on (fallback tailnet serve) -> Data/database/tailscale.json; status() read-only, stop() best-effort
│   │   ├── console_auth.py             # OWNER gate owner: setup-once/verify/change (pbkdf2, install.py-compatible format) + in-memory sessions (30d, restart logs out) + cookie parse; secrets never logged/returned
│   │   └── __init__.py
│   └── frontend/
│       └── dashboard/                  # X5Agent Console — Manus-adapted DARK-ONLY theme, modular (no inline style/script)
│           ├── index.html              # semantic structure only + skills page (cards grid + glass +Add button, nav under plugins, no inner title)
│           ├── login.html              # self-contained owner gate page (inline css/js/SVG, single file, no extra requests): English, full-screen, no card — site icon LEFT (black ground + white glowing square, same favicon/mark as the dashboard, with title + description), email+password+Connect RIGHT on the site background, dashboard glass fields, no password confirm, animated error box, gradient divider between image and form — setup mode first run, login mode after; redirects to / on success
│           ├── img/
│           │   ├── providers/            # lobe-icons static-png (MIT, trimmed): openai/anthropic/openrouter/deepseek/gemini
│           │   └── icons/                # lobe-icons static-png (MIT, trimmed): vllm (local models) + mcp (plugins)
│           ├── css/
│           │   ├── tokens.css          # Manus DARK-ONLY neutrals + bright blue accent + light primary, red danger-only
│           │   ├── base.css            # reset, system+Cairo type, blue progress bar, transform-only keyframes, no-select only on agent chat output, no tap highlight
│   │   ├── layout.css          # 308px nav / 56px toolbar / gray canvas + RTL drawer + icon rail with transform slide + hidden nav scrollbar
│           │   ├── components.css      # white cards, pills, black send, blue charts, thinking-block (dot-square ring wave + wave Working + per-step arrow → reason/curated-detail/image, pre-wrap), red errors only + msg-voice bubble styles
│           │   ├── responsive.css      # breakpoints 1024/640 (logical drawer side + slide)
│           └── js/
│               ├── app.js              # entry: wires all modules + copy guard (agent page only)
│               ├── ui.js               # shared: animateIn + modal shell (drag-safe backdrop close + unified slow transform open/close) + toast + chart tooltip
│               ├── navigation.js       # drawer + desktop slide collapse + real-URL routing (/agent...) + toolbar titles
│               ├── dashboard.js        # 4 stats (Requests/Tokens/Balance/Model) + toolbar status: /api/dashboard + /api/cost + /api/balance + /api/models
│               ├── charts.js           # Tokens+Requests+Cost charts, 7D/30D/months ranges, /api/cost (silent refresh: sig-skip, first-paint anim only, peak highlight, hover desktop, tap mobile)
│               ├── chat.js             # agent: rich RTL/LTR typing (dir per language + explicit li dir, typing-live hides empty markers, Space Grotesk Working pinned LTR-left) + pro codeblock (raw-fence extract, tiny highlight, fixed header lang+copy hover/touch, typed with colors, roomy pre + wrap guards) + tables explicit dir + row-headers (spec col right in RTL, left in LTR) + thinking-block per turn (dot-square 5x5 ring wave + wave Working, header arrow pops in with first step; reply typed once at done with normal animation, tool-phase text moves inside its event; settled Done + elapsed seconds + collapsed with reply below, empty block dropped, cancel renders Stopped line only; every content step has arrow → reason + curated per-tool detail + lazy image (default reason-only, raw output never dumped; detail pre-wrap multi-line); history reloads settled + expandable anytime) + live control card (interactive iframe + fullscreen + glass متابعة) + floating preview bar (attached strip, single icon btn + close-confirm) + fixed-right tab (Agent-section-only via body.browser-visible, hover never moves it, pull-out follows the finger 1:1 with bloom + tab slides out right, release past ~48px grabs open, short pull sucks back, × closes browser for real) + free-floating player (reverse-black-hole bloom open ~520ms, free drag/resize, quarter-out/fling black-hole suck dock ~700ms into the tab point, snap-back inside otherwise, × docks animated, same iframe kept alive, no reload), viewport-clamped open/drag/resize, stale-URL self-heal + auto-mint + tap-recover, down-hysteresis 4, insecure-context notice + https-upgrade) + remote-safe viewer links (live-url.js: funnel pages rewrite 127.0.0.1:6080 links to the /live/ route, local pages untouched) + live control card (ask sentence + fullscreen + confirm-continue, animated close) + push-driven (events.js, zero polling: browser tab/live/chat/reload via SSE) + live_state restore (one-shot) + @mentions + thumbs + voice recording panel + STT to composer/direct-send + attach menu (image/video/document→fileInputDocument max 100MB, 280px doc card with progress ring inside composer + entrance animation + per-type icon) + mode/approvals/ask-strip (fused lettered rows above composer, essay = question-only + composer reply, STOP flips to SEND + STOP mid-task arms an oval white Continue pill on the send button (empty composer only, animated pop-in, survives refresh via history stopped-marker + localStorage; tap resumes in place via skip_history_ids + resume:true with an EMPTY message body (the internal instruction never leaves the browser, the reply carries no prompt so regenerate uses the original bubble) with no new bubble (the Stopped divider is dropped while all steps stay, and the run continues inside the SAME Working block via resumeThinkingBlock — adopted live or history-rendered; unknown id resolves from history, expired id fails clearly with a toast), any typed text sends as a brand-new request instead; state-aware: emptied chat or no task falls back to a disabled normal send, edit mode forces normal send, question chips keep send enabled) + Working freezes while asking, +/mode/mic hidden, chip-or-typed + SEND only, dedicated ask-in enter + ask-out keyframe exit (fill-proof), frees scroll below last message; answer lands inside the step arrow as Q+A + loop resumes, never a user bubble) + run tokens (superseded turns exit quietly) + step keying by call id (completion retitles the same row, no orphans) + hover/long-press msg actions + hash route persists + refresh mid-run replays /api/chat_frames so the same task keeps its Working block + every step and keeps following `run` pushes (cancelled/stopped runs return inactive — no second Working, no auto-restart) + human_control closes the floating preview/edge tab before its card + right-edge tab slides in from the right + step bullets run ORANGE then turn GREEN on success (no check) + agent chat is selectable/copyable again + TTS voice bubble (appendFinalVoice/createVoiceBubble: play/pause + seekable hash-wave bars + time, one-at-a-time, persisted via m.audio) + composer focus glow animates in and fades out + show_preview opens the floating watch-only window (idempotent) + agent-open auto-check (browser running → live edge tab auto-shows, no-link → bounded mint)
│               ├── events.js             # THE ONE SSE stream (opened once, never duplicated): ordered hello chat_last50->dashboard->rest + per-section hashes (re-render only on hash break) + same onPush contract; no WebSocket anywhere
│               ├── schedule.js           # scheduler side panel (Agent section only via body.sched-visible): SEPARATE right-edge floating template mirroring #browserTab/#liveMini (never merged: browser template untouched, English "Scheduled Tasks" tab stacked below it (pro ring count, no fill; 1s separation guard so the two tabs never overlap), vertical rail + tap opens (reverse-black-hole bloom out of the tab ~520ms) + horizontal pull-out follows the finger 1:1 (born at the tab, blooms over ~140px; release far parks with a FLIP settle, short pull sucks back in 320ms, fast rightward fling docks from anywhere) + tab slides out right with the pull (glides back in on cancel) + hover never moves the tab (tint only), window closed by default + tab-tap opens only (tab hides while open like #browserTab.off, ×/dock brings it back) with schedule-only motion (black-hole suck dock into the tab point via WAAPI ~700ms, tab steady, no x5-rise) + lifecycle generation guard (stale timers never kill newer state, guard checks generation-captured) + visibility watchdog (tab or window always recoverable), dock lands the collapsed tab at the drop Y, opens in the free area away from the browser template, COMPACT FIXED size (no resize: 272px, exactly 5 small rectangles + inner list scroll), free drag (half-off-right allowed mid-drag to dock, like the browser player) + forceful-dock glide + release/resize/reopen always snap strictly inside + every open blooms out of the tab (reverse black hole) + minimize parks alive + floating minis never overlap (same 1s guard moves ours below/above/left)) + task rectangles (soonest-first, paused last; whole-card click opens dialog: status edge-strip + icon + name + live countdown + AM/PM creation time) + detail dialog (all-English dashboard glass, plain title head, status lives only in the Schedule card, 3 parts + hidden scrollbars: Schedule part + big Task Instructions part (copy + pencil-flips-to-save) + redesigned Executions part (status-dot + #RUN chip + status pill + time + open arrow + output preview); footer Delete + Pause/Resume (Pause hidden for completed/done jobs, server pause/resume actions) + Cancel + Delete with confirm dialog; #RUN<n> rectangles open session) + per-run FULL normal session page (hash session /#sched-<job>-<run>: #RUN chip + status + run time, branch session rendered like the main chat — rich formatting + hover copy/edit/regenerate/delete bars + settled Working block BEFORE the reply (header stays Working, never Done; no block when no steps) + edit banner, skill as a normal user message, edit/regenerate reuse the persisted id (no duplicate bubble; server adopts ids), live replies type out (same typeRich animation as the main chat), Back returns deterministically to the main session, full composer attach/mode/mic, refresh-safe; server chat sends the message raw with the skill seeded once in branch history + the same full main-chat context (non-isolated, branch layer extends L6), returns events+steps+ids+media; branch-local msg_edit/msg_delete/msg_prune endpoints)
│               ├── workspace.js          # workspace page: one-level file browser + breadcrumb + upload/mkdir + download-only + push reload on version change (no poll)
│               ├── plugins.js          # discoverable only: action=permission switches, silent save, core hidden
│               ├── skills.js           # user skills: cards grid (unified icon copies skill, no badge) + glass +Add + glass create/edit dialog (glass fields + persistent red errors + local draft, cleared on save + glass save/cancel, locked name on edit) + view modal with Copy/Edit, searchable via skills_search
│               ├── gateway.js          # WhatsApp-only setup: locked agent number, QR/code linking, allowed numbers, live status, disconnect
│               ├── ai_models.js        # AI Models page: fixed template cards (one now: AI Text Model) + 4-field dialog (model name/server URL/optional key/Connection JSON) + Test (structured copyable errors) + Save (needs a Test pass unless Override) + setup-prompt copy button
│               └── settings.js         # project card (repo mark + link + version pill) + updates card (check GitHub release, auto-update with live log, restart note) + black full-width Delete-chat-history button (live count pill + loading spinner, clears main chat + chat embeddings only, memories stay) + password validation + compact darker logout (local-only)
├── gateway/                            # messaging channels (server-authoritative config in Data/database/gateway.json)
│   ├── store.py                        # validated WA/TG settings (token masked on read, never logged)
│   ├── channels.py                     # outbound text senders per channel (chunked, best-effort) + tracked send/edit for the live message (WA bridge id / TG message_id)
│   ├── dispatch.py                     # unified commands (/browser /dashboard /cancel) + 5s coalescing (each msg restarts 5s, merged one-per-line) + queued runs with numbered Queued (N) acks + ONE live-edited message (reason+....., throttled, plain-line fallback; final reply edits it last, new message on ANY refusal) + control pause till continue + final reply with 3-attempt retry (crash guard never silent) + full /cancel (loop + all queued dropped) + WA send options (read/typing/reply) + same-account LID auto-allow + runs execute once via the main chat_stream pipeline (mirrored live, bounded busy-wait, local fallback when unreachable) + gateway uploads use the unified file logic (original-name mirror + full id/path/type/size hints + priority pointers)
│   ├── service.py                      # auto-start: TG poller thread + WA push wiring (no WA threads, zero polling)
│   ├── telegram/
│   │   ├── adapter.py                  # Bot API: getMe + per-chat sendMessage test (stdlib only)
│   │   ├── poller.py                   # long-poll getUpdates -> normalize (text/voice/photo/doc) -> dispatch
│   │   └── __init__.py
│   ├── whatsapp/
│   │   ├── adapter.py                  # owns Baileys bridge lifecycle (spawn/use/stop, loopback only) + per-boot push secret/URL + pidfile orphans + wedge watchdog (transport failures in a 60s window only, 500s never condemn) + LID-first sends (send heals a dropped engine inline, one bounded respawn + 30s cooldown, connected-state verified) + segno QR data-URLs + inbox/send
│   │   ├── listener.py                 # bridge wakeup drain: /inbox -> normalize (DM only, LID/phone identity + own-LID capture) -> dispatch (push-driven, zero polling)
│   │   ├── bridge/                     # node service: package.json (baileys 6.x) + bridge.js (/health|status|connect|pair|disconnect|inbox|send(id)|edit + media)
│   │   └── __init__.py
│   └── discord/                          # reserved
├── storage/                      # LEGACY read-only fallback (authoritative home is X5Agent/Data, migrated once at startup)
│   ├── database/                   # fallback copies (todos/scheduler/conversation/triggers + 4 memories + plugin_state + model_providers)
│   ├── vector/                     # fallback embeddings (tool/chat/trigger) + committed *.seed.json (prebuilt tool/trigger embeddings, instant offline on fresh copies)
│   └── files/                      # fallback media (images/videos)
├── embedding_model/                # THE ONLY bundled weights: embedding model (weights+tokenizer ship with release zip, offline-first, .download_complete marker, hidden from UI, name never shown)
├── core/embedding/                 # bundled embedding code (hidden): bootstrap.py (offline-first ensure, device detected once + cached CUDA/CPU) + service.py (embed_texts/embed_query/is_ready)
├── scripts/
│   ├── __init__.py
│   ├── build_embedding_seed.py     # builds committed *.seed.json (tool+trigger, --snapshot-only instant, auto-fetch when the model is missing)
│   ├── set_owner.py                # terminal-only owner set/replace (interactive prompt or X5_EMAIL/X5_PASSWORD env; salted hash only)
│   ├── tests/test_terminal_scope.py  # terminal scopes: default limited, limited blocks escape, full allows anywhere
│   ├── x5-local                    # local console starter (127.0.0.1:7777, no prompt)
│   ├── x5-public                   # public console starter (127.0.0.1:7777 --public via Tailscale Funnel)
│   ├── cli.py                      # the `x5agent` PATH command: persistent serve on 127.0.0.1:7777 (localhost only, detached new-session + Data/logs/console.log, survives SSH close) from anywhere, no update check (restart: port always freed first — ANY stale holder stopped graceful-then-forced incl. suspended, exactly one server ends up running; `x5agent --off` / `x5agent-off` stops every server on the port; `--foreground` keeps the old blocking run)
│   ├── x5console.service           # systemd unit: local console (edit User + WorkingDirectory, then enable --now)
│   └── x5console-public.service    # systemd unit: public console (needs host tailscale login)
├── main.py                         # entrypoint: bundled embedding check (offline, GPU auto/CPU) -> X5Agent ensure + legacy->Data migrate -> seed/rebuild tool embeddings -> start loop
├── install.py                      # THE single install/update command: env check (python/CPU/GPU/disk/deps) -> project state -> git pull --ff-only (code only, Data never touched) -> offline resources -> browser doctor check 5/7 (X5Browser run_doctor: Chrome+env, offers official install on missing, never fatal) -> email+password once (salted hash in Data, skipped when already set, --email/--password to change) -> public link step 7/7 (Tailscale: install check + login URL shown+opened; funnel itself always activates in the server --public background thread after the port answers, never against a dead port) -> prints Web Server (Local, reachable URL) + (Public) + Server Running Now -> opens console + prints local and public URLs -> --run starts the console PERSISTENTLY via scripts/cli.launch_detached (old server killed, new one detached on 7777, SSH-safe; --foreground keeps the old blocking exec)
├── tests/                          # all test files here (fixtures unified: read/write X5Agent/Data, same as runtime)
│   ├── test_auto_memory.py
│   ├── test_new_layers.py
│   ├── test_scheduler_large_reliable.py
│   └── test_workspace.py             # workspace tool jail/list/mkdir + top-level-only context
│   └── test_workspace_media_logic.py   # stable Workspace file ids (entries/map/@id+path) + upload mirror names + vision/PDF id resolution + 4-field id/path/type+size shapes + path-link guidance + pdf sub-path + arabic/HTML-path validation
│   └── test_triggers.py              # trigger logic: 8 approved inbox/outbox configs + forbidden guards + resolver + one-time death
│   └── test_context_triggers.py      # execution-context contract (isolated: system+memories+env+skill-as-L8+same tools, no chat) + Active-Triggers block (count, tasks, management hint)
│   └── test_tailscale_share.py       # public share: login-URL extract + offline status shape + saved-URL + login states + funnel save (no real tailscale)
│   └── test_docker_layout.py         # docker contract: Data+models paths inside named volumes, no bind mounts, image hygiene, --ff-only bounded pull (no daemon needed)
│   └── test_console_auth.py          # console auth: email/pbkdf2/cookie flags (no secrets, no I/O)
│   └── test_documents.py             # documents: 300-word chunks + markitdown + vision fallback + delete purges embeddings + wiring
│   └── test_streaming.py             # SSE decoding (no mojibake) + tool-delta rebuild
│   └── test_browser_lifecycle.py     # browser close-noop (no boot) + lifecycle lock
│   └── test_install_unified.py     # unified installer: OS report + port validation + stdlib-only (4 passed)
│   └── test_ai_models_engine.py      # Universal Connection Engine: mock-server A/B/C/D + NDJSON + messages_map + injection + all error categories (secret-free copyable) + validation + gating + kinds vision/image/tts/stt + binary body + poll flow (V/I/T/S/P checks, all green)
│   └── test_ai_models_engine.py      # Universal Connection Engine: mock-server A/B/C/D + NDJSON + messages_map + injection + all error categories (secret-free copyable) + validation + gating (21 checks green)
│   │   └── test_aip.py                   # AIP/1 interpreter: ops + missing-vs-null + malicious-parser reject + URL/SSRF policy + OpenAI body build (secret never in parser) + response/tool-call normalize + bad-args skip + mock end-to-end + parts/modalities/vision/async + private-server shape/errors + auto unknown-JSON + nameless shapes + auth placements + compile-once + AIP/1 setup prompts (29 passed)
│   └── test_svg_maker.py             # svg_maker validation: spec + icon system prompt + description/size/name guards + extract/sanitize/validate + events/dispatcher (8 passed, no LLM)
│   └── test_chat_run_lifecycle.py    # STOP then refresh: cancelled run is not live; old thread finally cannot wipe a newer run's frames
├── docs/
│   └── README.md
├── .env
├── .env.example
├── README.md
├── LICENSE
└── pyproject.toml
```

## Ownership Rules
- `prompts/` — ALL system prompts (14 files: L1 core_identity + L2 general_mission + L3 policy + legacy system.md + 8 others + pdf_slide_system.md + svg_icon_system.md) visible at project root, editable
- `core/paths.py` — X5Agent sandbox resolver (workspace/data/models) + unified Data read/write helpers + migrate_legacy_to_data_once(), single source of truth for jail and Data authority
- `core/memory/` — 4 memories (writes ALWAYS X5Agent/Data, reads Data-first) + auto_memory service
- `core/documents/` — document store (file_id = Workspace-relative path; convert->chunk->embed; vision fallback for thin PDF pages; delete/purge orphans with Workspace)
- `core/tools/` — 13 folders on disk (tool_search, question, vector_chat_search, document_search, todo_list, scheduler, skills_search, skills_view, terminal, wait, workspace, trigger, trigger_search) BUT core-in-context = all EXCEPT workspace (workspace is discoverable behind tool_search; folder stays for import compat) + skill_create (folder stays in plugins/core_plugins, classified core) + 6 browser core (open_browser/navigate/page_snapshot/click/fill/human_control). CONDITIONAL skills: `has_user_skills()` (registry) is false when no real user skill exists → skills_search/skills_view are dropped from context + L7 (dynamic, mirrors the real list) and only skill_create is offered. Env block (L4) shows Terminal access FULL vs LIMITED + the full-access meaning line. Workspace block (L4) is an outer-level-only map (name + type + size, first 100, never walks inside) + documents metadata block (L4) + chat-uploaded documents priority block (L8) + direct-answer rule: no summary suffix when no tools were called (general_mission.md:7, system.md:6)
- `X5Browser/` — versioned single-folder browser layer (wrapper 1.5.2 over lib x5browser==1.5.2): catalog (42 specs: 41 library 1:1 + project `show_preview`, 6 CORE open_browser/navigate/page_snapshot/click/fill/human_control + 36 discoverable behind tool_search, 4 dangerous; new in 1.5.0: quick_search, fetch_pages, read_markdown, extract_image, manage_triggers; 1.5.1 adds no tools — Linux live hardening + CDP repair_session self-heal only; 1.5.2 removes take_control/agent_control from the agent catalog — human_control is the single handoff tool — + WSL units_installed fix, no other tool changes) + service (sole browser owner, EXPLICIT lifecycle: only open/restart boot, all others fail 'browser stopped' while down, bg asyncio loop, transient CDP drops self-heal via the library's repair_session() — same browser, never boots, acting tools still run exactly once — live stack = one LiveStack.start(), LIVE LINKS READ FROM THE LIBRARY via live_links(), read-only status_report(), profile stays inside library, screenshots in Data/files/browser max 20) + tools (validation + normalized output, browser_status never boots) + events (plain-English label per tool). All 42 are core plugins (always on); the 6 CORE are in-context, the other 36 discoverable behind `tool_search` embedding (store scans catalog, hash auto-rebuilds). Dangerous 4 (fill_form, upload_files, export_page, console_command): restricted mode asks via question, otherwise direct. Library doctor (`run_doctor` / `python -m x5browser.doctor --fix --smoke`): setup-time Chrome+env check wired into install.py step 5/7 before the owner email.
- `plugins/` — `core_plugins/` (16 folders on disk, flat: read_file, write_file, search_files, vision_analyze, video_analyze, video_transcribe, video_download, audio_extract, embedding_memory, text_to_speech, skill_create, image_memory, important_memory, http_request, pdf_create, svg_maker) — always effective, no UI switches; skill_create is CORE-in-context (not via tool_search), the other 15 + workspace (lives in core/tools) are discoverable via `tool_search` embedding. `plugins/` UI page is intentionally empty. Filesystem trigger types stay in `plugins/filesystem/triggers/` (trigger_search system). There is no Models page anymore — chat models live in the AI Models section (see `core/ai_models/` + `web/`).
- `core/ai_models/` — Universal Model Connection Engine + AIP/1 interpreter, kinds text/vision/image/tts/stt v1 (no provider-specific code): `engine.py` (ROUTER + legacy: every public fn routes AIP/1 records via is_aip_record() — same check as the loop — to aip.execute with kind-keyed cached plan (text/vision/tts/stt/image canonical + modalities gate; media adapted to {media,source,type}); legacy connection-JSON records keep validate/chat/test_connection/analyze/generate_image/synthesize/transcribe + paths/vars/SSE-NDJSON/binary bodies/media outputs/submit-then-poll/errors/redaction, stdlib urllib only) + `aip.py` (AIP/1: canonical request/response contracts + declarative op engine literal/ref/object/array/map/if/coalesce/omit_if_null/rename/select/parse_json/to_string/to_number/to_boolean/merge/encode/transform_content + URL/SSRF policy + capability check + bearer/header/query/body_token auth via Secret Store only + bounded retries max 3 + normalize() never fabricates (text/tools/media + dotted names + per-call status/metadata + provider_metadata + errors.path) + typed parts text/image/audio/video + collect_media + is_list + map compact + modalities gate + async-workflow explicit refusal + execute()/test_connection() canonical-in/canonical-out + templates openai_text/vision/image_gen + auto mode (unknown JSON responses: name-based + nameless shape-based text/media extraction with low-confidence warnings, calls need name+args so plain names are never tools, explicit refusal when unrecognized; requests can never be automatic; auth token in bearer/header/query/body/none + compile-once (interpreter validates once per config, cached plan per kind, fast path still validates input + normalizes every response); Black Box uses this only) + `store.py` (one record per kind {model_name/server_url/api_url/api_key/parser_definition|config_json/kind} in Data/database/ai_models.json + key mirrored to Data/.env AI_<KIND>_API_KEY, never returned to UI, save needs a Test pass unless force; missing_message() = fixed refusal + /ai-models link) + `media.py` (image/audio resolve + base64) + `models_prompts.md` (single file, `## KIND:` sections text/vision/image/tts/stt, all English, each teaches AIP/1: 5 fields + ops + auth placements + kind mandatory refs + verification gate) + `models_prompts.py` (section reader read_all/get_prompt; server setup_prompt endpoint + tests import it) + compile_model enforces mandatory refs per kind (missing_variable names the var) + canonical_from_loop() (loop L1-L8 messages+tools to canonical; loop uses the AIP path when the record is AIP/1, legacy engine otherwise) + `__init__.py`. Loop + auto_memory + scheduler/trigger skill generation + stt_correction + vision/video/tts/stt tools all call it; server `/api/ai_models/test` routes AIP/1 parsers to `aip.test_connection`, legacy JSON to `engine.test_connection`.
- `core/embedding/` (+ `embedding_model/` weights) — the ONLY built-in model (hidden from UI): check-first auto-fetch (complete = ready instantly offline; missing/incomplete = deltas only, resume, max 3 attempts; install/update syncs; optional HF_TOKEN env only raises HuggingFace rate limits on slow networks, never required, never logged), device detected once + cached CUDA/CPU, .download_complete marker. Used by tool/chat/trigger/document embedding stores. Fresh copies load prebuilt `storage/vector/*.seed.json` instantly (hash-verified) with zero model calls.
- `skills/` — user skills (discoverable via skills_search/view) + scheduled_skills (auto-generated) + trigger_skills (one-time). Chat pickers (chat.js + components.css `.pk-*` + `.composer-hlwrap`): `@` and `/` open the SAME attached transparent panel above the composer (plain-text `Workspace \ a \ b` breadcrumb, last segment colored; scrollable list, max-height 240px, in/out animations, Escape closes; strict rule — zero rows anywhere means no panel at all, for `@` (needs files/uploads) and `/` (needs skills); folders navigate silently writing nothing, files/skills insert `@name` / `/name` tokens and the panel closes animated). Composer highlight: transparent-text textarea over a colored backdrop (same pattern as the AI-models editor) paints ACTIVE `@file` / `/skill` tokens blue, inactive mentions stay default white; selection renders white-on-blue. Server injects IN PLACE: each `@name` becomes that file's metadata exactly where it stands, each `/skillname` becomes the skill instructions in place (boundary-aware, emails/URLs never match); unmatched leftovers are appended bounded. Payload carries `skill:{name,instructions}` + `workspace_refs:[{name,path}]` (plain path strings also accepted). Picks are one-shot per send and count only while their token is still in the text. Composer highlight mirrors the resolved base direction for correct Arabic RTL. Typing next to a picked (blue) token never reopens the panel; picking auto-appends one space with the caret after it. Clicking a blue file token navigates to the Workspace page revealing that file exactly at its path (blue flash + scroll, via `x5:reveal-workspace`); clicking a blue skill token opens its detail on the Skills page (`x5:reveal-skill`). Every chat upload (any kind) is mirrored into Workspace AND listed in a priority block in the loop input (documents via document_search, others via ids + Workspace copy).
- `updates.json` (repo root) — the single update source `{version, description, features}`; bumped only on big updates or on request per the AGENTS.md Updates-file rule (never a folder).
- Docker layer (repo root: `Dockerfile` + `compose.yml` + `.dockerignore` + `docker-entrypoint.py`) — image = code only (never data/secrets); volumes = `x5-data`→`/app/X5Agent/Data` + `x5-models`→`/app/project/embedding_model`; every start pulls newest source from GitHub (`--ff-only`, bounded, warn-continue offline); one command `docker compose up --build` installs + updates; `--public` opt-in via `X5_PUBLIC=1` (needs logged-in host-independent tailscale, else warn-only).
- `install.py` — the ONE install/update entry: argparse flags (--run = fully automatic install+start on 127.0.0.1:7777 (localhost only, remote via funnel), frees port 7777 first (ANY holder stopped graceful-then-forced, one server left), with public link attempted and Tailscale auto-installed on Linux unless --no-public, --email/--password or X5_EMAIL/X5_PASSWORD, --yes, --public/--no-public, --skip-browser, --skip-firewall, --hf-token/HF_TOKEN optional, --no-open-browser) -> env check (3.10 floor, 3.12 recommended: x5browser 1.5.2 runs on 3.10+) -> state check (latest version from raw updates.json on GitHub, no releases needed) -> `git pull --ff-only` (tracked code only, X5Agent/Data + embedding_model/ never touched) -> embedding auto-fetch (deltas only, optional HF_TOKEN auth for rate limits) + tool/trigger ensure -> firewall best-effort open of TCP 7777 (ufw/firewalld, never fatal) -> browser doctor check (Chrome+env via run_doctor, offers official install, never fatal) -> owner email+password once (salted hash in Data/database/console_auth.json, never plaintext; skipped when already set) -> public link (Tailscale Funnel, flaggable) -> prints console URL only.
- `x5agent` command (`scripts/cli.py`, on PATH via pip `[project.scripts]`): `x5agent [--public]` serves the console now on 127.0.0.1:7777 (localhost only) from anywhere, no update check (the one-liner setup.sh stays the install+update+start path)
- `web/` — server + frontend/dashboard (chat renders TTS voice records as standalone bubbles via /api/audio; no raw player in steps; /api/update_check (local updates.json at repo root vs GitHub raw main→master, version compare, description + features as notes, always 200 with local, no releases needed) + /api/update_run (background source-only pull + embedding re-ensure, in-memory live log) + /api/update_progress; pages are dashboard/agent/schedrun/workspace/gateway/plugins/skills/ai-models/settings — settings.js owns check-then-confirm updates (Check only checks, shows features + no-data-loss reassurance + one glass button visible at a time: Check → Update now after approval, progress bar + live log while updating) — ai_models.js owns the 5 fixed template cards + per-kind dialog (glass guide card on top (2 steps: Open Claude link + red underlined configuration-prompt copy words with a tiny icon (the words copy on click) + plain guiding sentence nested under step 2); glass writing fields only, no panel backgrounds; big single Configuration Protocol editor (type-to-color overlay, auto-expanded JSON, no separate preview); Connect → black log panel with copy (green borders ok / red fail + red "Copy the log and paste it into Claude") → Save (closes; reopen shows saved values, log gone; any edit restarts at Connect), transform-only anims); /api/ai_models* per kind + /api/embedding_status; old providers/requirements/doctor/model_download/model_state APIs answer 410-Gone; dead model handlers + provider vault + balance-quota code removed; _get_models_data restored as AI-store compat)
- `gateway/` — telegram/whatsapp/discord adapters (events rendered as per Events table)
- `storage/` — LEGACY read-only fallback only (never written by runtime; Data is authoritative; migrated once at startup) — except committed `storage/vector/*.seed.json` prebuilt embeddings (tracked by git, rebuilt via `scripts/build_embedding_seed.py`)
- `embedding_model/` — THE single weights folder (bundled embedding model only): tokenizer+configs are tracked in git, but `model.safetensors` (~1.1GB) stays local-only — GitHub rejects any file over 100MB, so it auto-downloads from HuggingFace on first run (deltas only, resume on restart) — no second copy anywhere; tools needing a template model refuse with the fixed message from store.missing_message() (names the template + /ai-models link)
- `deploy/` — removed (the 4-model Docker stack is obsolete: only the built-in embedding model ships locally; everything else connects via AI Models templates)
- `tests/`, `docs/` — tests & documentation

## Events Contract
- Core Tools: tool_search→Finding tools, question→Waiting for you, vector_chat_search→Searching memory, todo_list→Planning task, scheduler→Scheduling · {short schedule}, skills_search→Finding skills (only when a real skill exists, else hidden), skills_view→Opening · {skill_name} (same condition), skill_create→Creating skill · {skill_name}, terminal→Terminal Command, wait→Waiting · {duration}, trigger_search→Finding triggers / Managing trigger · {action} (single trigger tool: search + create/list/get/update/delete) + browser core (6): open_browser→Starting browser, page_snapshot→Viewing page snapshot, navigate→Navigating to {url}, click→Clicking element {ref}, fill→Typing into element {ref}, human_control→Handing you control
- Discoverable (always on, hidden from UI, behind tool_search): workspace→Workspace · {action}, document_search→Searching docs · {file_name} + read_file→Reading · {file_name}, write_file→Writing · {file_name}, search_files→Searching files, pdf_create→Creating/Updating/Reading PDF · {pdf_name}, svg_maker→Creating icon · {icon_name}, vision_analyze→Analyzing · {image_name}, video_analyze→Analyzing video · {video_name}, video_transcribe→Transcribing video · {video_name}, video_download→Downloading video · {host}, audio_extract→Extracting audio · {video_name}, embedding_memory→Saving/Searching memory, text_to_speech→Speaking · {words}, image_memory→Storing/Updating/Listing images, important_memory→Saving/Listing memories, http_request→Fetching/Posting · {host} + 36 browser events (see X5Browser/events.py)
- Rules: {file_name}/{skill_name}/{image_name} filename only, {command} truncated, {short schedule} compact, never display long free-form inputs
- Tool Search: semantic top-k over discoverable tools, loop bounded by max_steps=500
- Video Handling: video_download saves mp4 to Workspace (yt-dlp, 100MB cap) + returns metadata; video_analyze/transcribe accept Workspace files; frame analysis via ThreadPool(4) for speed; STT per timestamp; audio_extract saves mp3/wav to Workspace via FFmpeg

## Embedding Auto-Logic
- On startup (`main.py` + `tool_search` first call) -> `plugins/tool-discovery/search/store.py:ensure_embeddings()`:
  - collects all discoverable specs (verbatim descriptions, now 14 core_plugins + workspace + document_search + 36 browser)
  - computes SHA256 hash
  - if hash matches stored `storage/vector/tool_embeddings.json` -> reuse
  - else if runtime store is missing and the shipped `storage/vector/tool_embeddings.seed.json` hash matches -> load seed instantly (zero model calls, fully offline)
  - else delete old, embed all descriptions via `core/embedding/service.py` (bundled embedding model, 3 retries), save new hash+embeddings
- If user edits any description in spec.json and reruns code, change is detected automatically and old embeddings are purged + rebuilt.
- Every user message + assistant reply is embedded automatically on persist (`core/agent/loop/engine.py:_persist_conversation` -> `chat_vector_store.add_message`).
- Device (GPU/CPU) is detected once at startup and cached (`core/embedding/bootstrap.py:detect_device`).
- Rebuild seeds after spec changes: `python scripts/build_embedding_seed.py` (or `--snapshot-only` when the runtime store is fresh).

## Update Protocol
When structure changes:
1. Update the tree above to match disk reality
2. Update physical directories (create/remove `.gitkeep`)
3. Mention the map change in commit/message

## Known Gaps — 2026-10-08 (recovered from opencode/Codex session logs; verified by compile+checks)
> A cleanup accident wiped uncommitted work; it was rebuilt from
> `~/.local/share/opencode/opencode.db` tool history (writes/reads/edits) plus
> `~/.codex/sessions` apply_patch blocks (Oct-07 WhatsApp sessions), verified with
> `py_compile` (31 files), `node --check` (13 files) and JSON parsing (27 specs).
> Items below are the only deltas left vs the tree. `project/.env` (gitignored)
> must be re-created locally AND the exposed OpenRouter key rotated (it lives on
> in git history).
- RESTORED ✅: all `project/gateway/*.py` + `telegram/*` + `whatsapp/*` + `bridge/`, all 5 missing core plugins (14/14 present), `core/scheduler/session.py`, `js/schedule.js`, `tests/test_tts_voice.py`, `models/local_models/models.json`, and all post-`fd42295` tracked edits (server/engine/chat/css/…).
- RESTORED EXTRA (needs tree entries): `project/web/server/download_sync.py` + `project/tests/test_download_sync.py` (Workspace download sync, bounded).
- DELIBERATE DEVIATION: `js/gateway.js` keeps the integrated opencode version (exports `initGateway`, used by `app.js`); the Codex rewrite was dropped after it caused a blank-template regression — only compatible Codex hunks were kept.
- DEFERRED (no breakage, verified): `dispatch.py` helpers `_reply_to`/`_notify_main_chat_changed`, `channels.py` `mark_read`/status check (zero callers), `chat.js` duplicate `highlightCode` (last-wins, syntax valid), `components.css` variant rules (CSS never breaks).
- debt: `_an.py/_an2.py/_c.py/out.txt` debug junk deleted; hardcoded test API keys replaced by env `setdefault` + live-API skip guards.

## Speech-model swap — 2026-10-08 (Egyptian stack, code-only, no weights downloaded)
- `manifest.json` stt → `mohammedaly22/QwenCleo-ASR` (`models/stt/QwenCleo-ASR`, ~4000MB, ignores `assets/*`+`training_args.bin`); tts → `mohammedaly22/VoiceTut-TTS` (`models/tts/VoiceTut-TTS`, ~2500MB, ignores `optimizer.bin`+`scheduler.bin`+`random_states_*.pkl`+`train_config.json`); vision → `Qwen/Qwen3.5-4B` (`models/vision/Qwen3.5-4B`, ~9570MB, `AutoModelForMultimodalLM` + `AutoProcessor`, matching deploy stack). Total fresh download ≈ 20.5GB (1.2 emb + 9.6 vision + 4 asr + 6.9 tts, HF real sizes).
- NEW `models/stt/service.py` (was missing entirely - old `dispatch.py`/`server.py` imports would have crashed): `transcribe_file()` + chunked `transcribe_stream()` + vLLM `stream_tokens()` (fails clearly offline). Single STT owner; `stt_correction.py` + `video_transcribe` delegate to it (faster-whisper removed).
- `models/tts/service.py` rewritten on VoiceTut-TTS SDK (streaming-native `synthesize()` over sentence `synthesize_stream()`); `voices.py` = 15 built-in speakers, fixed `DEFAULT_SPEAKER=Mohamed`; `postprocess.py` (supertonic-only shaping) deleted.
- contracts preserved: `dispatch.py`, `server.py` (`/api/stt` + preload `_load`), `text_to_speech` tool unchanged. `pyproject.toml` deps: +torch/torchaudio/soundfile/qwencleo-asr/qwen-asr/voicetut-tts, −supertonic/faster-whisper/ctranslate2. OmniVoice backbone stays GitHub-only (see `models/README.md`). `project/.gitignore` now tracks `models/stt/service.py` + `models/tts/{service,voices}.py`.
- verified: `py_compile` all touched files, `test_tts_voice.py` + `test_stt_service.py` PASS (weight paths skip cleanly), `setup_models.py --check` resolves the 4 new entries (all MISSING, no download run).
