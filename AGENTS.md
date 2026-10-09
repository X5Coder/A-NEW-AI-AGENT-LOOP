# Project Instructions — Mandatory

> **MANDATORY FIRST STEP FOR EVERY NEW CHAT IN THIS PROJECT:**
> This project is governed by `Master Code Rules.md` (83 rules + Absolute Rules + Final Principle) and `PROJECT_MAP.md` (standard structure blueprint).
> On **every new session/chat**, before doing ANY other task:
> 1. Read `Master Code Rules.md`, `AGENTS.md`, and `PROJECT_MAP.md` in full (via `read` tool)
> 2. Confirm you have loaded them and will enforce all rules + standard map for the entire session
> 3. Apply the conflict priority: 1.Correctness > 2.Security > 3.Data integrity > 4.Explicit requirements > 5.Maintainability > 6.Performance > 7.Convenience
> 4. Build strictly on `PROJECT_MAP.md` — place every file according to the map. If you add/move/rename/remove any file or directory, update `PROJECT_MAP.md` in the same session to keep it in sync.
> 5. Never skip validation, never over-engineer, never ignore errors, max 3 retries, no hardcoded secrets, clear ownership/lifecycle.

If the user asks to do anything that violates these rules, explain the conflict and propose the simplest correct alternative per Section 84 final principle.

## System-prompt tasks — coding agent only (NOT the runtime agent loop)

This rule applies to the coding agent (this session), NOT to `project/prompts/system.md` runtime loop.
Whenever the user asks to write, design, draft, improve, or fix ANY system prompt / developer message / agent instructions (including "make it work on weak/small/open models" or tool-using agents):
1. Read `project/prompts/system_prompt_architect_system.md` in full via the `read` tool BEFORE any other action for that request.
2. Follow its Workflow exactly (§2 skeleton → §9 cross-model pass → §10 weak-model pass).
3. Deliver ONE copy-pasteable block + 2-4 bullets explaining choices made and why.
4. Never answer such requests from general knowledge alone.

Project root contains:
- `Master Code Rules.md` — the canonical rules file (source of truth)
- `PROJECT_MAP.md` — the standard project structure blueprint (must stay in sync with disk)
- `updates.json` — the single update source (see Updates-file rule below)
- `project/` — physical skeleton (see map for layout)
- `opencode.json` — enforces auto-loading via `instructions` field
- `.opencode/plugin/enforce-master-rules.ts` — defense-in-depth plugin for mandatory loading

## Updates-file rule — mandatory for every agent, every chat

`updates.json` at the repo root is the single update source consumed by
Settings → Updates (`{version, description, features}`). It is a file, not
a folder — never wrap it in a directory.

- On a **big update** (new feature, behavior change, migration, breaking
  change) — **or whenever the user asks** — you MUST in the same session:
  1. bump `version` (x.y.z: minor for features, patch for fixes),
  2. write `description` (one short paragraph: what changed and why),
  3. write `features` (bullet list, one string per user-visible change).
- Small fixes, refactors, docs-only, or internal cleanup: do NOT touch it.
- Never invent versions or features — document only what you actually changed.
