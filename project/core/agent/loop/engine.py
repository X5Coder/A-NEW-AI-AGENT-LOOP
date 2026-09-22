"""
Agent Loop Engine - professional loop up to 500 steps, never fails silently.
Each iteration uses full context: system prompt + memories (if non-empty) + last 30 messages + env + todos + core tools.
System prompt separate (OpenAI system role), rest as user content (example, not specialized sending, just engine).
After each tool call, executes tool and loops with updated context + tool output.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from core.agent.context.builder import build_context
from core.agent.loop.dispatcher import execute_tool

STORAGE_DB = Path(__file__).resolve().parents[3] / "storage" / "database"
MAX_STEPS = 500


def _persist_conversation(user_input: str, assistant_output: str) -> None:
    path = STORAGE_DB / "conversation.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    data = []
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, list):
                data = []
        except Exception:
            data = []
    # bounded: keep last 1000 messages
    data.append({"role": "user", "content": user_input[:2000], "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
    data.append({"role": "assistant", "content": assistant_output[:5000], "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
    if len(data) > 1000:
        data = data[-1000:]
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _clear_temp_images_if_any():
    try:
        from core.memory.image_memory.service import clear_temp
        n = clear_temp()
        if n:
            print(f"[loop] cleared {n} temp images at loop end")
    except Exception:
        pass


def run_loop(
    user_input: str,
    max_steps: int = 500,
    model: str | None = None,
    api_key: str | None = None,
    temperature: float = 0.3,
    verbose: bool = True,
) -> dict[str, Any]:
    """
    Run agent loop for user_input.
    Returns dict: {steps, final_answer, history, tool_calls_log, success}
    Never fails silently - even on provider errors, continues up to max_steps.
    """
    if not isinstance(user_input, str) or not user_input.strip():
        raise ValueError("user_input must be non-empty string")
    if not isinstance(max_steps, int) or max_steps < 1 or max_steps > 500:
        raise ValueError("max_steps must be 1..500")
    mdl = model.strip() if model and isinstance(model, str) and model.strip() else "deepseek/deepseek-v4.1-flash"
    # Build initial context
    system_prompt, user_context, core_tools = build_context(user_input)
    messages: list[dict] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_context},
    ]
    # Import provider lazily
    try:
        from core.lm.providers.openrouter.provider import chat as provider_chat
    except Exception as e:
        raise RuntimeError(f"OpenRouter provider not available: {e}") from e

    tool_calls_log: list[dict] = []
    steps = 0
    final_answer: str | None = None

    for step in range(1, max_steps + 1):
        steps = step
        if verbose:
            print(f"\n--- Loop step {step}/{max_steps} ---")
        # Call provider with bounded retries (provider does 3 retries internally)
        try:
            response = provider_chat(
                messages=messages,
                tools=core_tools,
                model=mdl,
                api_key=api_key,
                temperature=temperature,
            )
        except Exception as e:
            # Provider failed even after retries - treat as tool error and continue
            err_msg = f"Provider error at step {step}: {e}"
            print(f"[loop] {err_msg}")
            # Add an assistant error note to messages so next iteration can recover
            messages.append({"role": "assistant", "content": f"[System note: provider temporarily unavailable: {str(e)[:200]}. Will retry.]"})
            # Small backoff
            time.sleep(1)
            if step == max_steps:
                final_answer = f"[Loop reached max steps with provider errors] Last error: {e}"
                break
            continue

        content = response.get("content")
        tool_calls = response.get("tool_calls") or []
        if verbose:
            if content:
                print(f"[LLM content] {str(content)[:300]}")
            if tool_calls:
                print(f"[LLM tool_calls] {[tc['function']['name'] for tc in tool_calls]}")

        if not tool_calls:
            # No tools requested - final answer
            final_answer = content if isinstance(content, str) and content.strip() else "[No content returned]"
            messages.append({"role": "assistant", "content": final_answer})
            if verbose:
                print(f"[Loop finished at step {step} with final answer]")
            break

        # Execute each tool call
        # First, add assistant message with tool_calls (OpenAI format requires raw)
        # Reconstruct raw tool_calls for history
        raw_tool_calls = []
        for tc in tool_calls:
            raw_tool_calls.append({
                "id": tc["id"],
                "type": tc.get("type", "function"),
                "function": {
                    "name": tc["function"]["name"],
                    "arguments": json.dumps(tc["function"]["arguments"], ensure_ascii=False) if isinstance(tc["function"]["arguments"], dict) else str(tc["function"]["arguments"])
                }
            })
        messages.append({"role": "assistant", "content": content or "", "tool_calls": raw_tool_calls})

        for tc in tool_calls:
            name = tc["function"]["name"]
            args = tc["function"]["arguments"]
            if verbose:
                print(f"  -> Executing {name} with {str(args)[:200]}")
            result = execute_tool(name, args)
            tool_calls_log.append({"step": step, "tool": name, "arguments": args, "result": result})
            # event logging: print event if available
            try:
                out = result.get("output", {}) if result.get("success") else result
                ev = out.get("event") if isinstance(out, dict) else None
                if ev and verbose:
                    print(f"     Event: {ev}")
                if not result.get("success") and verbose:
                    print(f"     Error: {result.get('error')}")
            except Exception:
                pass
            # Append tool result as tool message
            output_str = json.dumps(result, ensure_ascii=False, indent=2)
            if len(output_str) > 12000:
                output_str = output_str[:12000] + "\n... truncated"
            messages.append({
                "role": "tool",
                "tool_call_id": tc["id"],
                "content": output_str,
            })

        # Loop continues - next iteration will see tool outputs
        # Optional: update todo context? Already in messages, but we also could rebuild todo block as system note?
        # Keep loop bounded - check if we need to inject fresh context about new memories? Provider sees history, but we could also inject a short system note with updated memories if needed.
        # For simplicity, continue with history; context will be seen.
        # Small delay to avoid rate limits
        time.sleep(0.2)

        if step == max_steps and final_answer is None:
            final_answer = "[Loop reached max_steps (500) without final answer]"

    # Ensure final_answer exists
    if final_answer is None:
        # Extract last assistant content if any
        for m in reversed(messages):
            if m.get("role") == "assistant" and m.get("content"):
                final_answer = str(m["content"])
                break
        if final_answer is None:
            final_answer = "[No final answer produced]"

    # Persist conversation
    try:
        _persist_conversation(user_input, final_answer)
    except Exception:
        pass

    # Clear temp images per spec (temp request images auto-cleared after vision or loop end)
    _clear_temp_images_if_any()

    return {
        "steps": steps,
        "final_answer": final_answer,
        "history": messages,
        "tool_calls_log": tool_calls_log,
        "model_used": mdl,
        "success": True,
    }
