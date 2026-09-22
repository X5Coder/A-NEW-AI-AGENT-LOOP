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

Project root contains:
- `Master Code Rules.md` — the canonical rules file (source of truth)
- `PROJECT_MAP.md` — the standard project structure blueprint (must stay in sync with disk)
- `project/` — physical skeleton (see map for layout)
- `opencode.json` — enforces auto-loading via `instructions` field
- `.opencode/plugin/enforce-master-rules.ts` — defense-in-depth plugin for mandatory loading
