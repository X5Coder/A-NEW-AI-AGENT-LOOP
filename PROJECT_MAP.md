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
├── X5Browser/                      # versioned browser layer (wrapper 1.4.0 over lib x5browser==1.4.0, see X5Browser/VERSION)
│   ├── __init__.py                 # exports version + tool lists (all 39 are core plugins)
│   ├── VERSION                     # wrapper + lib pin (bump on library updates)
│   ├── catalog.py                  # single source: 39 tool specs (38 library + show_preview), ALL core plugins (4 dangerous)
│   ├── service.py                  # single browser owner: EXPLICIT lifecycle (only open_browser/restart_browser boot; any other tool while stopped fails 'browser stopped', never boots WSL/units/Chrome on any OS) + orphan-stop (close from a fresh process stops live-stack units instead of no-op) + adopt (fresh process attaches to a live endpoint, still no boot) + quick probe (single-shot for polls/per-turn context) + bg asyncio loop + live stack boot = one LiveStack.start() call (library owns units/install/self-heal/WSL keep-alive; headless local fallback when stack unavailable: no visible window, library profile only, never real User Data) + LIVE LINKS READ FROM THE LIBRARY (live_links() -> control+preview+owner, no cache/heal/persist) + read-only status_report() (never boots) + lifecycle lock/gen (no start-close race) + start/stop + screenshots (Data/files/browser, max 20) + boot fast-path (skip start when the ports answer; fail fast when units are active but Windows-unreachable, so a broken route never burns the stack timeout)
│   ├── tools.py                    # validation (untrusted args, http(s) only, Workspace jail) + execute + normalized output (library ok=false refusals never masked) + browser_status answered locally (never boots) + links copied from the library
│   └── events.py                   # short non-technical label per tool (all 39, user-friendly)
├── core/
│   ├── paths.py                    # X5Agent resolver: workspace/data/models roots + ensure_x5agent() + resolve_inside_workspace()
│   ├── agent/
│   │   ├── loop/                       # 500-step loop, never fails silently
│   │   │   ├── engine.py               # run_loop(user_input, max_steps=500, provider=openrouter) + dynamic tools + on_event stream (event/reason/media/step/live frames) + optional reason node per tool call (model `reason` stripped before execute, never synthesized; empty → step shows with no arrow unless curated detail/image) + curated per-tool step detail UNDER reason (todo steps, file names, short code/terminal preview incl. exit code, question Q+A; default reason-only, raw output never dumped; failures show one short cause, titles never raw errors) + turn elapsed_sec (persisted per message) + final_audio (text_to_speech records → standalone voice bubble, persisted as assistant audio) + restricted approvals + on_question pause (ask/approval/live) + on_live_event + cancel flag + live-URL redaction (model never sees stream links) + mid-step cancel break (STOP during a pause drops the result silently)
│   │   │   ├── dispatcher.py           # maps tool name -> execute (core + discoverable)
│   │   │   └── __init__.py
│   │   ├── context/
│   │   │   ├── builder.py              # build_context(): system + memories + last30 + env + todo + core tools (browser state = owner read from X5Browser live_links, no URL)
│   │   │   └── __init__.py
│   │   ├── filters/                  # filter layers (audio)
│   │   │   ├── stt_correction.py       # audio -> QwenCleo-ASR (via models/stt/service.py, single owner) -> correction via stt_correction_system.md (no invention)
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
│   │   ├── engine.py               # check_and_execute_due_jobs() every 60s, computes next_run, isolated mode, concurrent via ThreadPool + on_start/on_done executing callbacks + run_history output bounded 2000/run (last 10) + persists into the job's branch session (never main chat)
│   │   ├── session.py              # one branch session per job: Data/database/scheduler_sessions/<job_id>.json (last 100 msgs, history_block for L6, auto-deleted with the job)
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
│   ├── core_plugins/               # CORE PLUGINS: always on, hidden from UI, no switches (14 tools, flat)
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
│   ├── server/                         # local dev server (127.0.0.1:5173, path-validated, console auth (first-run setup -> .env pbkdf2 hash + HttpOnly session, login.html gate), chat attachments (image/video/text/document, doc 100MB) -> Workspace mirror copy + priority block)
│   │   ├── server.py                   # static server + SPA fallback (extensionless→index.html) + JSON APIs (dashboard/cost/balance/health/conversation/plugins/skills/models/chat/chat_stream/chat_answer/chat_cancel/message/stt/plugin_state/model_state/providers/providers/test/live_state/shot/workspace/scheduler + /api/audio TTS records) + /api/events SSE push hub (one stream/tab, hello snapshot, singleton watchers broadcast on change only) + scheduler snapshot/emit (_get_scheduler_data/_events_emit_scheduler, 15s watcher + force after chat run/skill edit) + scheduler engine ticker (60s, executing callbacks → live "يتم التنفيذ" push) + secrets: UI save→vault+Data/.env+environ, UI clear→deleted everywhere, cost measured (usage_log + live pricing), SSE live loop on chat_stream (event/reason/media/live frames + live question pause) + LIVE STATE READ FROM THE LIBRARY: live_snapshot()/live_active() take running + control/preview links straight from X5Browser (no live_state.json, no host-heal, no relay/viewer restart) + in-memory live pause (_LIVE_PAUSE: qid→tool/reason, survives refresh, dropped on answer/expire/loop-end) + /api/chat_frames (bounded run-progress frames: event_start/event/question/live/live_end, never tokens) fanned out as `run` pushes so a refreshed page rebuilds the running Working block + steps against a route that is unreachable from Windows while units answer inside WSL (fail fast, never burn the stack timeout) + done frames carry audio (final TTS voice records)
│   │   └── __init__.py
│   └── frontend/
│       └── dashboard/                  # X5Agent Console — Manus-adapted DARK-ONLY theme, modular (no inline style/script)
│           ├── index.html              # semantic structure only + skills page (cards grid + glass +Add button, nav under plugins, no inner title)
│           ├── login.html              # RTL full-page sign-in (no card): email+password only, Gmail-only; form right + visual left (desktop), visual display:none on phone; first-run setup then login
│           ├── login.css               # self-contained glass tokens (public, no auth needed) + responsive banner
│           ├── login.js                # setup/login POST + Arabic errors + autoredirect when already signed in
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
│               ├── chat.js             # agent: rich RTL/LTR typing (dir per language + explicit li dir, typing-live hides empty markers, Space Grotesk Working pinned LTR-left) + pro codeblock (raw-fence extract, tiny highlight, fixed header lang+copy hover/touch, typed with colors, roomy pre + wrap guards) + tables explicit dir + row-headers (spec col right in RTL, left in LTR) + thinking-block per turn (dot-square 5x5 ring wave + wave Working, header arrow pops in with first step; reply typed once at done with normal animation, tool-phase text moves inside its event; settled Done + elapsed seconds + collapsed with reply below, empty block dropped, cancel renders Stopped line only; every content step has arrow → reason + curated per-tool detail + lazy image (default reason-only, raw output never dumped; detail pre-wrap multi-line); history reloads settled + expandable anytime) + live control card (interactive iframe + fullscreen + glass متابعة) + floating preview bar (attached strip, single icon btn + close-confirm) + fixed-right vertical-only tab + free-floating player (instant open, free drag/resize, half-off-right forceful dock only, snap-back inside otherwise, viewport-clamped open/drag/resize, stale-URL self-heal + auto-mint + tap-recover, down-hysteresis 4, insecure-context notice + https-upgrade) + live control card (ask sentence + fullscreen + confirm-continue, animated close) + push-driven (events.js, zero polling: browser tab/live/chat/reload via SSE) + live_state restore (one-shot) + @mentions + thumbs + voice recording panel + STT to composer/direct-send + attach menu (image/video/document→fileInputDocument max 100MB, 280px doc card with progress ring inside composer + entrance animation + per-type icon) + mode/approvals/ask-strip (fused lettered rows above composer, essay = question-only + composer reply, STOP flips to SEND + Working freezes while asking, +/mode/mic hidden, chip-or-typed + SEND only, dedicated ask-in enter + ask-out keyframe exit (fill-proof), frees scroll below last message; answer lands inside the step arrow as Q+A + loop resumes, never a user bubble) + run tokens (superseded turns exit quietly) + step keying by call id (completion retitles the same row, no orphans) + hover/long-press msg actions + hash route persists + refresh mid-run replays /api/chat_frames so the same task keeps its Working block + every step and keeps following `run` pushes + human_control closes the floating preview/edge tab before its card + right-edge tab slides in from the right + step bullets run ORANGE then turn GREEN on success (no check) + agent chat is selectable/copyable again + TTS voice bubble (appendFinalVoice/createVoiceBubble: play/pause + seekable hash-wave bars + time, one-at-a-time, persisted via m.audio) + composer focus glow animates in and fades out + show_preview opens the floating watch-only window (idempotent) + agent-open auto-check (browser running → live edge tab auto-shows, no-link → bounded mint)
│               ├── events.js             # single SSE push channel: one EventSource/tab, hello snapshot + change-only frames (browser/live/chat/chat_done/dashboard/workspace/scheduler), auto-reconnect with hello resync, no polling anywhere
│               ├── schedule.js           # scheduler side panel (Agent section only via body.sched-visible): right-edge tab "Scheduled Tasks" (same open/dock animation as #liveMini: tab.off while floating, ×/half-off-right docks back) + floating list (current-task rectangles open detail dialog; executed-run rectangles open full session) + detail dialog (status pill + stats grid + تعليمات المهمة edit + clickable run rectangles + two-step delete) + per-run FULL page (hash session /#sched-<job>-<run>: enter slide, status chip, branch session .msg bubbles + full composer, refresh-safe)
│               ├── workspace.js          # workspace page: one-level file browser + breadcrumb + upload/mkdir + download-only + push reload on version change (no poll)
│               ├── plugins.js          # discoverable only: action=permission switches, silent save, core hidden
│               ├── skills.js           # user skills: cards grid (unified icon copies skill, no badge) + glass +Add + glass create/edit dialog (glass fields + persistent red errors + local draft, cleared on save + glass save/cancel, locked name on edit) + view modal with Copy/Edit, searchable via skills_search
│               ├── gateway.js          # WhatsApp-only setup: locked agent number, QR/code linking, allowed numbers, live status, disconnect
│               ├── models.js           # Cloud provider cards only, no section title + vault (masked keys)
│               └── settings.js         # password validation + logout (local-only)
├── gateway/                            # messaging channels (server-authoritative config in Data/database/gateway.json)
│   ├── store.py                        # validated WA/TG settings (token masked on read, never logged)
│   ├── channels.py                     # outbound text senders per channel (chunked, best-effort) + tracked send/edit for the live message (WA bridge id / TG message_id)
│   ├── dispatch.py                     # unified commands (/browser /dashboard /cancel) + 5s coalescing (each msg restarts 5s, merged one-per-line) + queued runs with numbered Queued (N) acks + ONE live-edited message (reason+....., throttled, plain-line fallback; final reply edits it last, new message on ANY refusal) + control pause till continue + final reply with 3-attempt retry (crash guard never silent) + full /cancel (loop + all queued dropped) + WA send options (read/typing/reply) + same-account LID auto-allow + runs execute once via the main chat_stream pipeline (mirrored live, bounded busy-wait, local fallback when unreachable)
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
│   ├── vector/                     # fallback embeddings (tool/chat/trigger)
│   └── files/                      # fallback media (images/videos)
├── models/                         # code only: embedding|vision|stt service.py + manifest + loader (weights live in project/models/<type>/<name>)
│   ├── embedding/
│   │   └── service.py              # embed_texts(), embed_query(), lazy load via loader.get_model_path(), 3 retries
│   ├── vision/
│   │   └── service.py              # analyze_image() (Data/files images first, legacy fallback), lazy load, 3 retries
│   ├── stt/
│   │   └── service.py              # QwenCleo-ASR via qwencleo-asr SDK: transcribe_file() + chunked transcribe_stream() + vLLM stream_tokens() (true token streaming, fails clearly offline), lazy load, 3 retries
│   ├── tts/                            # TTS service: synthesize() streaming-native with the fixed project voice (Mohamed, VoiceTut-TTS built-in) only, 3 retries
│   │   ├── service.py              # synthesize(text, voice=None→Mohamed, lang, out_path) via sentence synthesize_stream(); validation rejects unknown voices/langs (ar/en only)
│   │   └── voices.py               # built-in speaker catalog (15 studio voices + tags, DEFAULT_SPEAKER=Mohamed) + canonical_voice_name() (no external service/model)
│   ├── manifest.json               # single source of truth: local_dir -> models/<type>/<name>
│   ├── loader.py                   # ensure_models() + get_model_path() (manifest primary, merged copies, never re-downloads) + scan_local_models()
│   ├── local_models/               # user-facing registry (local_dir -> models/<type>/<name>)
│   │   └── models.json             # index: name/desc/type/dir/used_by/plugins links (new dirs auto-appear)
│   └── README.md
├── scripts/
│   ├── setup_models.py             # idempotent setup into project/models (3 retries, disk check +5GB buffer, CT2 conversion)
│   └── doctor.py                   # environment/dependency diagnostics
├── deploy/
│   └── models-server/              # one-command Docker stack: 4 models loaded, offline, token+login protected (./install.sh)
│       ├── install.py              # الأمر الواحد الموحّد (يفحص OS: ويندوز/لينكس/ماك + Docker/Python/GPU/RAM/مساحة ثم يشغّل نفس التدفق عبر install.sh)
│       ├── install                   # غلاف رفيع Linux/macOS ينادي install.py
│       ├── install.cmd               # غلاف رفيع Windows ينادي install.py (يحتاج Git-Bash)
│       ├── install.sh              # التدفق الكامل (يُنادَى مباشرةً أو عبر install.py)
│       ├── doctor.sh               # project-wide status (env + model table) + select missing/partial model and download it fully (`./doctor.sh [--check]`)
│       ├── models.yaml             # single config: 4 repo_ids (Qwen3-Embedding-0.6B, Qwen3.5-4B, QwenCleo-ASR, VoiceTut-TTS) + reserve_gb + overhead
│       ├── Dockerfile              # code+libs only, no weights (torch/transformers/sentence-transformers/qwencleo-asr/voicetut-tts + fastapi/python-multipart + ffmpeg)
│       ├── docker-compose.yml      # ../../models:/models bind, offline env, restart unless-stopped, healthcheck 15m start_period
│       ├── select_models.py        # interactive checkbox select (name+HF size+status) -> selection.json; --status / --table / --all
│       ├── download_models.py      # download selected one-by-one with real progress bar (resume on cancel/partial) + .download_complete marker + shrinks total reserve
│       ├── server.py               # FastAPI: preload SELECTED_MODELS only (empty=all, unknown ids fail boot loudly) + GPU plan heaviest-first w/ 1.25x VRAM margin (fp16 vision / bfloat16 asr on GPU) + `/` email+password login (random session per login, 30-day TTL, logout invalidates, Secure on https, 5 fails/min/IP -> 429) + /health (selected models only, public) + /embed (64 texts max) + /vision + /asr + /tts (Bearer token on APIs, wav tmp cleaned after send; fixed English no-local-model message when a model is not loaded)
│       ├── verify.py               # real check for LOADED selected models only (non-loaded skipped, tone fallback diagnostic-only when tts unverified)
│       ├── README.md               # Arabic: flow + doctor + assumptions + model update
│       └── (no local weights dir — binds ../../models, the single project/models folder shared with the local loader)
├── main.py                         # VPS entrypoint: X5Agent ensure + legacy->Data migrate -> auto-download Models -> auto-embed tools -> start loop
├── tests/                          # all test files here (fixtures unified: read/write X5Agent/Data, same as runtime)
│   ├── test_auto_memory.py
│   ├── test_new_layers.py
│   ├── test_scheduler_large_reliable.py
│   └── test_workspace.py             # workspace tool jail/list/mkdir + top-level-only context
│   └── test_console_auth.py          # console auth: email/pbkdf2/cookie flags (no secrets, no I/O)
│   └── test_documents.py             # documents: 300-word chunks + markitdown + vision fallback + delete purges embeddings + wiring
│   └── test_streaming.py             # SSE decoding (no mojibake) + tool-delta rebuild
│   └── test_browser_lifecycle.py     # browser close-noop (no boot) + lifecycle lock
│   └── test_tts_voice.py             # fixed project voice (Mohamed, 15 speakers) catalog/default/reject + streaming-arg bounds + optional model synthesis (skips without weights)
│   └── test_stt_service.py             # STT service: language map + stream-arg bounds + audio validation + vLLM-unreachable path (skips transcription without weights)
│   └── test_gateway_no_model.py        # gateway no-model: vault readiness + Arabic reply + Models link (no secrets, no I/O)
│   └── test_install_unified.py     # unified installer: OS report + port validation + stdlib-only (4 passed)
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
- `X5Browser/` — versioned single-folder browser layer (wrapper 1.4.0 over lib x5browser==1.4.0): catalog (39 specs: 38 library 1:1 + project `show_preview`, ALL core plugins, 4 dangerous) + service (sole browser owner, EXPLICIT lifecycle: only open/restart boot, all others fail 'browser stopped' while down, bg asyncio loop, live stack = one LiveStack.start(), LIVE LINKS READ FROM THE LIBRARY via live_links(), read-only status_report(), profile stays inside library, screenshots in Data/files/browser max 20) + tools (validation + normalized output, browser_status never boots) + events (plain-English label per tool). All 39 behind `tool_search` embedding (store scans catalog, hash auto-rebuilds). Dangerous 4 (fill_form, upload_files, export_page, console_command): restricted mode asks via question, otherwise direct.
- `plugins/` — `core_plugins/` (14 always-on hidden tools, flat: read_file, write_file, search_files, vision_analyze, video_analyze, video_transcribe, video_download, audio_extract, embedding_memory, text_to_speech, skill_create, image_memory, important_memory, http_request) — always effective, no UI switches, still searchable via `tool_search` embedding. `plugins/` UI page is intentionally empty. Filesystem trigger types stay in `plugins/filesystem/triggers/` (trigger_search system). Models page is cloud-only (no local section).
- `skills/` — reusable skills (frontend-design-pro auto-loaded + discoverable via SKILL.md) + scheduled_skills (auto-generated) + trigger_skills (one-time)
- `web/` — server + frontend/dashboard (chat renders TTS voice records as standalone bubbles via /api/audio; no raw player in steps)
- `gateway/` — telegram/whatsapp/discord adapters (events rendered as per Events table)
- `storage/` — LEGACY read-only fallback only (never written by runtime; Data is authoritative; migrated once at startup)
- `models/` — THE single weights folder (code + services + manifest; weights in project/models/<type>/<name>, gitignored, shared with deploy/models-server) — no second copy anywhere; any tool whose model is missing raises the fixed English message from loader.NO_LOCAL_MODEL_MESSAGE ("...run the doctor command")
- `deploy/models-server/` — one-command Docker stack for the 4 models (single source: models.yaml; install.sh = checks→select→total 5GB reserve→download→doctor→up→verify→gmail+UI-password; doctor.sh = project status table + load missing/partial; server.py page = email+password cookie login, APIs = API_TOKEN; weights in the single project/models/ — no second copy, marker-compatible with scripts/setup_models.py; .env secrets + override + sizes.json/selection.json/.reserve-* generated by install.sh, never committed)
- `tests/`, `docs/` — tests & documentation

## Events Contract
- Core Tools: tool_search→Finding tools, question→Waiting for you, vector_chat_search→Searching memory, document_search→Searching docs · {file_name}, todo_list→Planning task, scheduler→Scheduling · {short schedule}, skills_search→Finding skills, skills_view→Opening · {skill_name}, terminal→Running · {command}, wait→Waiting · {duration}, workspace→Workspace · {action}, trigger→Managing trigger · {trigger_name}, trigger_search→Finding triggers + browser core: page_snapshot→Viewing page snapshot, navigate→Navigating to {url}, click→Clicking element {ref}, fill→Typing into element {ref}
- Discoverable (all core plugins, always on, hidden from UI): read_file→Reading · {file_name}, write_file→Writing · {file_name}, search_files→Searching files, vision_analyze→Analyzing · {image_name}, video_analyze→Analyzing video · {video_name}, video_transcribe→Transcribing video · {video_name}, video_download→Downloading video · {host}, audio_extract→Extracting audio · {video_name}, embedding_memory→Saving/Searching memory, text_to_speech→Speaking · {words}, skill_create→Creating skill · {skill_name}, image_memory→Storing/Updating/Listing images, important_memory→Saving/Listing memories, http_request→Fetching/Posting · {host} + 39 browser events (see X5Browser/events.py)
- Rules: {file_name}/{skill_name}/{image_name} filename only, {command} truncated, {short schedule} compact, never display long free-form inputs
- Tool Search: semantic top-k over discoverable tools, loop bounded by max_steps=500
- Video Handling: video_download saves mp4 to Workspace (yt-dlp, 100MB cap) + returns metadata; video_analyze/transcribe accept Workspace files; frame analysis via ThreadPool(4) for speed; STT per timestamp; audio_extract saves mp3/wav to Workspace via FFmpeg

## Embedding Auto-Logic
- On startup (`main.py` + `tool_search` first call) -> `plugins/tool-discovery/search/store.py:ensure_embeddings()`:
  - collects all discoverable specs (verbatim descriptions, now 14 core_plugins + 39 browser)
  - computes SHA256 hash
  - if hash matches stored `storage/vector/tool_embeddings.json` -> reuse
  - else delete old, embed all descriptions via `models/embedding/service.py` (Qwen3-Embedding-0.6B, 3 retries), save new hash+embeddings
- If user edits any description in spec.json and reruns code, change is detected automatically and old embeddings are purged + rebuilt.

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
