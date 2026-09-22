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
│   │   ├── loop/                       # 500-step loop, never fails silently
│   │   │   ├── engine.py               # run_loop(user_input, max_steps=500, provider=openrouter)
│   │   │   ├── dispatcher.py           # maps tool name -> execute (core + discoverable)
│   │   │   └── __init__.py
│   │   ├── context/
│   │   │   ├── builder.py              # build_context(): system.md + memories + last30 + env + todo + core tools
│   │   │   └── __init__.py
│   │   └── prompts/
│   │       └── system.md               # editable system prompt (separate file, user can change)
│   ├── lm/
│   │   └── providers/
│   │       └── openrouter/             # OpenRouter provider for deepseek/deepseek-v4.1-flash
│   │           ├── provider.py         # chat() with tool-calling, 3 retries, timeout, no hardcoded secrets
│   │           └── __init__.py
│   ├── memory/                         # 4 memories - each own folder & lifecycle
│   │   ├── user_memory/                # NOT tool - internal user facts (key/value, 200 max)
│   │   │   ├── service.py              # set/get/list/delete for user data
│   │   │   └── __init__.py
│   │   ├── agent_memory/               # NOT tool - internal agent state
│   │   │   ├── service.py
│   │   │   └── __init__.py
│   │   ├── image_memory/               # storage service for image metadata (persistent vs temp)
│   │   │   ├── service.py              # store/update/delete/list/clear_temp + get_context_payload()
│   │   │   └── __init__.py
│   │   └── important_memory/           # storage service for important facts
│   │       ├── service.py              # store/update/delete/list for important facts
│   │       └── __init__.py
│   └── tools/
│       ├── registry.py             # tool registry (core + discoverable listing)
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
│       ├── scheduler/              # CORE: schedule tasks (create/list/update/delete/pause/resume)
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
│       └── terminal/               # CORE: execute shell commands
│           ├── spec.json
│           ├── tool.py
│           ├── events.py           # Running · {command}
│           └── __init__.py
├── plugins/
│   ├── tool-discovery/
│   │   └── search/
│   │       ├── store.py            # embedding store: auto-embed, hash, change detection, search
│   │       └── __init__.py
│   ├── filesystem/
│   │   ├── read_file/              # DISCOVERABLE: behind tool_search
│   │   │   ├── spec.json
│   │   │   ├── tool.py
│   │   │   ├── events.py           # Reading · {file_name}
│   │   │   └── __init__.py
│   │   ├── write_file/             # DISCOVERABLE
│   │   │   ├── spec.json
│   │   │   ├── tool.py
│   │   │   ├── events.py           # Writing · {file_name}
│   │   │   └── __init__.py
│   │   └── search_files/           # DISCOVERABLE
│   │       ├── spec.json
│   │       ├── tool.py
│   │       ├── events.py           # Searching files
│   │       └── __init__.py
│   ├── vision/
│   │   └── vision_analyze/         # DISCOVERABLE: uses SmolVLM2-500M
│   │       ├── spec.json
│   │       ├── tool.py             # delegates to models/vision/service.py
│   │       ├── events.py           # Analyzing · {image_name}
│   │       └── __init__.py
│   ├── skills/
│   │   └── skill_create/           # DISCOVERABLE
│   │       ├── spec.json
│   │       ├── tool.py
│   │       ├── events.py           # Creating skill · {skill_name}
│   │       └── __init__.py
│   └── memory/                         # DISCOVERABLE memory tools (behind tool_search)
│       ├── image_memory/               # tool: store/update/delete/list image metadata (persistent sent every request, temp auto-cleared)
│       │   ├── spec.json               # verbatim: image metadata + vision_text + persistent logic
│       │   ├── tool.py                 # delegates to core/memory/image_memory/service.py
│       │   ├── events.py               # Storing/Updating/Listing images
│       │   └── __init__.py
│       └── important_memory/           # tool: store/view/delete important facts
│           ├── spec.json
│           ├── tool.py                 # delegates to core/memory/important_memory/service.py
│           ├── events.py               # Saving/Listing memories
│           └── __init__.py
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
│   ├── database/                   # todos.json, scheduler.json, conversation.json + user_memory.json, agent_memory.json, image_memory.json, important_memory.json
│   ├── vector/
│   │   └── tool_embeddings.json    # auto-generated embeddings + hash (detect change -> rebuild, now 7 discoverable tools)
│   └── files/
│       └── images/                 # persistent images stored here
├── models/
│   ├── embedding/
│   │   ├── Qwen3-Embedding-0.6B/   # auto-downloaded, git-ignored
│   │   └── service.py              # embed_texts(), embed_query(), lazy load, 3 retries
│   ├── vision/
│   │   ├── SmolVLM2-500M/          # auto-downloaded, git-ignored
│   │   └── service.py              # analyze_image(), lazy load, 3 retries
│   ├── tts/                        # supertonic-3 (auto-downloaded, git-ignored)
│   ├── stt/                        # whisper-medium-arabic-codeswitched-ct2 (auto-downloaded, git-ignored)
│   ├── manifest.json               # single source of truth for model auto-setup
│   ├── loader.py                   # runtime ensure_models() helper (auto-download on first run)
│   └── README.md
├── scripts/
│   └── setup_models.py             # idempotent setup (3 retries, disk check +5GB buffer, CT2 conversion)
├── main.py                         # VPS entrypoint: check space -> auto-download -> auto-embed tool descriptions -> start loop
├── tests/
├── docs/
├── .env
├── README
├── LICENSE
└── pyproject
```

## Ownership Rules
- `core/memory/` — 4 memories: `user_memory` + `agent_memory` (NOT tools, internal key/value, 200 max), `image_memory` + `important_memory` (storage services, 100/200 max, image has persistent vs temp)
- `core/tools/` — 8 core tools each own folder with Event (tool_search, question, vector_chat_search, todo_list, scheduler, skills_search, skills_view, terminal)
- `plugins/` — discoverable tools grouped by category (filesystem/vision/skills/memory), all behind `tool_search` embedding (now 7: read_file, write_file, search_files, vision_analyze, skill_create, image_memory, important_memory)
- `skills/` — reusable skills
- `web/` — server + frontend/dashboard
- `gateway/` — telegram/whatsapp/discord adapters (events rendered as per Events table)
- `storage/` — database (todos/scheduler/conversation + 4 memories) / vector (tool_embeddings.json auto-rebuilt) / files/images
- `models/` — embedding/vision/tts/stt + service.py wrappers (vision_analyze uses vision model, discoverable tools use embedding model)
- `tests/`, `docs/` — tests & documentation

## Events Contract
- Core Tools: tool_search→Finding tools, question→Waiting for you, vector_chat_search→Searching memory, todo_list→Planning task, scheduler→Scheduling · {short schedule}, skills_search→Finding skills, skills_view→Opening · {skill_name}, terminal→Running · {command}
- Discoverable: read_file→Reading · {file_name}, write_file→Writing · {file_name}, search_files→Searching files, vision_analyze→Analyzing · {image_name}, skill_create→Creating skill · {skill_name}, image_memory→Storing/Updating/Listing images, important_memory→Saving/Listing memories
- Rules: {file_name}/{skill_name}/{image_name} filename only, {command} truncated, {short schedule} compact, never display long free-form inputs

## Embedding Auto-Logic
- On startup (`main.py` + `tool_search` first call) -> `plugins/tool-discovery/search/store.py:ensure_embeddings()`:
  - collects all discoverable specs (verbatim descriptions, now 7)
  - computes SHA256 hash
  - if hash matches stored `storage/vector/tool_embeddings.json` -> reuse
  - else delete old, embed all descriptions via `models/embedding/service.py` (Qwen3-Embedding-0.6B, 3 retries), save new hash+embeddings
- If user edits any description in spec.json and reruns code, change is detected automatically and old embeddings are purged + rebuilt.

## Update Protocol
When structure changes:
1. Update the tree above to match disk reality
2. Update physical directories (create/remove `.gitkeep`)
3. Mention the map change in commit/message
