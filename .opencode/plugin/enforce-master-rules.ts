import type { Plugin } from "@opencode-ai/plugin"

// Plugin: enforce Master Code Rules at session start via system prompt transform
// This is a defense-in-depth layer - opencode.json instructions + agent prompts are primary,
// this plugin ensures even if instructions fail, the system prompt contains the mandatory directive.

export default (async () => {
  return {
    // Inject mandatory instruction into system prompt for every session
    "experimental.chat.system.transform": async (_input: any, output: any) => {
      const mandatory = `
[ABSOLUTE MANDATORY PROJECT DIRECTIVE - C:\\Users\\KIMO\\Documents\\A New Agent Loop]
You are operating in project "A New Agent Loop" governed by Master Code Rules.md (83 rules + 43 Absolute Rules + Final Principle Sec 84) and PROJECT_MAP.md (standard structure).
MANDATORY FIRST STEP OF EVERY NEW CHAT: Read "Master Code Rules.md", "AGENTS.md", and "PROJECT_MAP.md" in full BEFORE any other task, confirm loading to user, and enforce all rules + build strictly on PROJECT_MAP.md blueprint. If you add/move/rename any file, update PROJECT_MAP.md in same session.
Priority: 1.Correctness > 2.Security > 3.Data integrity > 4.Explicit requirements > 5.Maintainability > 6.Performance > 7.Convenience.
Never skip validation, never over-engineer, never ignore errors, max 3 retries, no hardcoded secrets.
If you have not read Master Code Rules.md and PROJECT_MAP.md this session, STOP and read them now.
`;
      // output.system is the assembled system prompt array/string - normalize and prepend
      if (Array.isArray(output.system)) {
        output.system.unshift(mandatory);
      } else if (typeof output.system === "string") {
        output.system = mandatory + "\n" + output.system;
      } else if (output.system) {
        // fallback
        output.system = mandatory;
      }
    },
  };
}) satisfies Plugin
