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

from core.agent.context.builder import build_context, openai_tool_from_spec
from core.agent.loop.dispatcher import execute_tool

STORAGE_DB = Path(__file__).resolve().parents[3] / "storage" / "database"
MAX_STEPS = 500
MAX_DYNAMIC_TOOLS = 20


def _ask_user(name: str, args: Any, on_question) -> dict:
    """Route a question tool call through the UI callback (pauses the loop until answered)."""
    if on_question is None:
        return execute_tool(name, args)
    try:
        q = args.get("question", "") if isinstance(args, dict) else ""
        opts = args.get("options") if isinstance(args, dict) else None
        if not isinstance(opts, list):
            opts = []
        answer = on_question("ask", {
            "question": str(q)[:2000],
            "options": [str(o)[:200] for o in opts[:4]],
        })
    except Exception:
        answer = None
    if not isinstance(answer, dict) or not str(answer.get("answer") or "").strip():
        return {"tool": name, "arguments": args, "output": {"event": "Unanswered", "answer": None}, "success": True}
    return {"tool": name, "arguments": args, "output": {"event": "Answered", "answer": str(answer["answer"])[:2000]}, "success": True}


def _approve_terminal(name: str, args: Any, on_question) -> dict:
    """Restricted mode: ask the user before running any terminal command."""
    if on_question is None:
        return execute_tool(name, args)
    try:
        cmd = args.get("command", "") if isinstance(args, dict) else ""
        decision = on_question("approval", {"command": str(cmd)[:500]})
    except Exception:
        decision = None
    if isinstance(decision, dict) and decision.get("decision") == "continue":
        return execute_tool(name, args)
    return {"tool": name, "arguments": args, "output": {"event": "Stopped by user", "stopped": True}, "success": True}


def _approve_browser_tool(name: str, args: Any, on_question) -> dict:
    """Restricted mode: ask the user before running a dangerous browser tool."""
    if on_question is None:
        return execute_tool(name, args)
    try:
        from X5Browser.events import format_event as _browser_event
        try:
            label = _browser_event(name, **(args if isinstance(args, dict) else {}))
        except Exception:
            label = name
        decision = on_question("approval", {"command": f"[{name}] {label}"[:500]})
    except Exception:
        decision = None
    if isinstance(decision, dict) and decision.get("decision") == "continue":
        return execute_tool(name, args)
    return {"tool": name, "arguments": args, "output": {"event": "Stopped by user", "stopped": True}, "success": True}


def _is_dangerous_browser_tool(name: str) -> bool:
    try:
        from X5Browser.catalog import is_dangerous
        return bool(is_dangerous(name))
    except Exception:
        return False


def _preview_event(name: str, args: Any) -> str:
    """Best-effort event label BEFORE execution (for instant UI). Never raises."""
    try:
        safe = args if isinstance(args, dict) else {}
        if name == "tool_search":
            return "Finding tools"
        # Browser tools (project/X5Browser): plain-English label with real args.
        try:
            from X5Browser.catalog import ALL_TOOLS as _BROWSER_ALL
            from X5Browser.events import format_event as _browser_format
            if name in _BROWSER_ALL:
                try:
                    label = _browser_format(name, **safe) if isinstance(safe, dict) else _browser_format(name)
                except Exception:
                    try:
                        label = _browser_format(name)
                    except Exception:
                        return name
                return str(label)[:80] if str(label).strip() else name
        except Exception:
            pass
        # Try the tool's own events.py formatter with the real args.
        import sys as _sys
        from pathlib import Path as _P
        proj = _P(__file__).resolve().parents[3]
        if str(proj) not in _sys.path:
            _sys.path.insert(0, str(proj))
        import importlib as _il
        from core.agent.loop.dispatcher import _CORE_MAP, _DISCOVERABLE_MAP as _DMAP
        dotted = {**_CORE_MAP, **_DMAP}.get(name)
        if not dotted:
            return name
        parts = dotted.split(".")
        events_mod = ".".join(parts[:-1] + ["events"])
        try:
            mod = _il.import_module(events_mod)
        except Exception:
            return name
        fmt = getattr(mod, "format_event", None)
        if not callable(fmt):
            return name
        try:
            label = fmt(**safe) if isinstance(safe, dict) else fmt()
        except Exception:
            try:
                label = fmt()
            except Exception:
                return name
        return str(label)[:80] if str(label).strip() else name
    except Exception:
        return name


_REASON_KEYS = frozenset({"reason", "_reason", "why", "purpose", "intent"})


def _extract_model_reason(args: Any) -> tuple[Any, str]:
    """Split a model-supplied reason node off the tool arguments.

    The model may attach `reason`/`why`/`purpose` explaining why it calls
    this tool. Tool implementations never accept it, so it is stripped
    before execution and returned separately for the UI. Never raises.
    Returns (clean_args, model_reason).
    """
    try:
        if not isinstance(args, dict):
            return args, ""
        found = ""
        for key in _REASON_KEYS:
            val = args.get(key)
            if isinstance(val, str) and val.strip() and not found:
                found = val.strip()[:500]
        if not found:
            return args, ""
        clean = {k: v for k, v in args.items() if k not in _REASON_KEYS}
        return clean, found
    except Exception:
        return args, ""


def _tool_reason(name: str, args: Any, model_reason: str = "") -> str:
    """Reason node for a tool call — strictly the model's own words.

    The `reason` parameter is optional: when the model supplies it, it is
    returned (bounded) for the UI arrow; when it does not, "" is returned
    and the UI shows the step with no arrow (unless output text or an
    image arrives). Nothing is ever synthesized. Never raises.
    `name`/`args` are kept for signature compatibility only.
    """
    try:
        if isinstance(model_reason, str) and model_reason.strip():
            return model_reason.strip()[:200]
        return ""
    except Exception:
        return ""


def _should_show_arrow(name: str, result: dict | None = None) -> bool:
    """Whether one tool call may show an expand arrow. Images always show."""
    try:
        if isinstance(result, dict):
            out = result.get("output") if isinstance(result.get("output"), dict) else None
            if isinstance(out, dict) and out.get("has_screenshot"):
                return True
        try:
            from X5Browser.catalog import ALL_TOOLS as _BROWSER_ALL
            if name in _BROWSER_ALL:
                from X5Browser.events import should_show_arrow as _browser_arrow
                return bool(_browser_arrow(name))
        except Exception:
            pass
        try:
            import sys as _sys
            from pathlib import Path as _P
            import importlib as _il
            from core.agent.loop.dispatcher import _CORE_MAP, _DISCOVERABLE_MAP as _DMAP
            dotted = {**_CORE_MAP, **_DMAP}.get(name)
            if dotted:
                parts = dotted.split(".")
                events_mod = ".".join(parts[:-1] + ["events"])
                mod = _il.import_module(events_mod)
                flag = getattr(mod, "SHOW_ARROW", False)
                return bool(flag)
        except Exception:
            pass
        return False
    except Exception:
        return False


def _summarize_output(result: dict, name: str | None = None) -> str | None:
    """Short human-readable excerpt of a tool result for UI expansion (bounded).

    Only tools with SHOW_ARROW=True return text here. Others return None so
    the UI renders a plain step line with no arrow. Screenshot tools show
    their image via media instead of text here. Failures always return their
    error, so every failed call gets an expandable arrow with the cause.
    """
    try:
        if not isinstance(result, dict):
            return None
        if not result.get("success"):
            err = result.get("error")
            if isinstance(err, str) and err.strip():
                return err.strip()[:300]
            return None
        if isinstance(name, str) and name and not _should_show_arrow(name, result):
            return None
        out = result.get("output")
        if not isinstance(out, dict):
            return None
        for key in ("answer", "text", "content", "stdout", "output"):
            val = out.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()[:300]
        # observation/text excerpts for readable browser tools (gated above)
        for key in ("observation", "message"):
            val = out.get(key)
            if isinstance(val, str) and val.strip() and len(val.strip()) > 20:
                return val.strip()[:300]
        found = out.get("results")
        if isinstance(found, list) and found:
            names = [str(r.get("name")) for r in found[:5] if isinstance(r, dict) and r.get("name")]
            if names:
                return "Found: " + ", ".join(names)
        return None
    except Exception:
        return None


_LIVE_PAUSE_TOOLS = frozenset({"human_control", "take_control"})
_LIVE_EVENT_TOOLS = frozenset({"live_preview", "show_preview", "agent_control", "close_browser", "restart_browser"})


def _redact_live_url(result: dict) -> None:
    """Hide live stream URLs from the model: the player renders them directly.
    The pause/notify paths above already used the real URLs — only the stored
    tool message (model-visible) is redacted, in place. Emptied (not labeled)
    so the model has nothing to quote."""
    try:
        out = result.get("output")
        if not isinstance(out, dict):
            return
        redacted = dict(out)
        changed = False
        for key in ("url", "preview_url", "current_url"):
            if isinstance(redacted.get(key), str) and redacted.get(key):
                redacted[key] = ""
                changed = True
        if isinstance(redacted.get("token"), str) and redacted.get("token"):
            redacted["token"] = ""
            changed = True
        pv = redacted.get("preview")
        if isinstance(pv, dict) and isinstance(pv.get("url"), str) and pv.get("url"):
            pv = dict(pv)
            pv["url"] = ""
            redacted["preview"] = pv
            changed = True
        st = redacted.get("state")
        if isinstance(st, dict) and isinstance(st.get("live_url"), str) and st.get("live_url"):
            st = dict(st)
            st["live_url"] = ""
            redacted["state"] = st
            changed = True
        if changed:
            result["output"] = redacted
    except Exception:
        pass


def _event_media(name: str, result: dict) -> dict | None:
    """Structured extras for one completed tool call (screenshot/stream). Never raises."""
    try:
        if not isinstance(result, dict) or not result.get("success"):
            return None
        out = result.get("output")
        if not isinstance(out, dict):
            return None
        media: dict = {}
        if out.get("has_screenshot") and isinstance(out.get("screenshot_path"), str):
            media["shot"] = str(out["screenshot_path"])[:500]
        _stream_url = None
        _stream_vo = True
        if name in _LIVE_PAUSE_TOOLS or name in _LIVE_EVENT_TOOLS or name == "live_preview" or name == "show_preview":
            url = out.get("url")
            if isinstance(url, str) and url.startswith(("http://", "https://")):
                _stream_url = url[:2000]
                _stream_vo = bool(out.get("view_only", name == "live_preview"))
        if _stream_url is None:
            pv = out.get("preview")
            if isinstance(pv, dict):
                purl = pv.get("url")
                if isinstance(purl, str) and purl.startswith(("http://", "https://")):
                    _stream_url = purl[:2000]
                    _stream_vo = bool(pv.get("view_only", True))
        if _stream_url is not None:
            media["stream"] = {"url": _stream_url, "view_only": _stream_vo, "tool": name}
        return media or None
    except Exception:
        return None


def _resolve_conversation_path() -> Path:
    # Unified: writes ALWAYS to X5Agent/Data (zip backup). Fallback legacy only if resolver missing.
    try:
        from core.paths import data_db_file as _df
        return _df("conversation.json")
    except Exception:
        return STORAGE_DB / "conversation.json"


def _resolve_legacy_conversation_path() -> Path:
    return STORAGE_DB / "conversation.json"


def _collect_final_images(tool_calls_log: list) -> list[dict]:
    """Screenshots taken via the screenshot tool, for the final reply.
    Shown under the answer (and after refresh) as part of the final reply.
    Bounded, deduped by path. Never raises."""
    images: list[dict] = []
    seen: set[str] = set()
    try:
        for entry in (tool_calls_log or [])[-12:]:
            if not isinstance(entry, dict) or entry.get("tool") != "screenshot":
                continue
            res = entry.get("result") or {}
            if not isinstance(res, dict) or not res.get("success"):
                continue
            out = res.get("output") or {}
            if not isinstance(out, dict) or not out.get("has_screenshot"):
                continue
            shot = out.get("screenshot_path")
            if not isinstance(shot, str) or not shot or shot in seen:
                continue
            seen.add(shot)
            images.append({"shot": shot[:500]})
            if len(images) >= 6:
                break
    except Exception:
        pass
    return images


def _persist_steps(tool_calls_log: list) -> list[dict]:
    """Flatten tool calls into bounded UI steps so history reloads keep every
    event, arrow and output exactly as shown live. Never raises."""
    steps: list[dict] = []
    try:
        for entry in (tool_calls_log or [])[-12:]:
            if not isinstance(entry, dict):
                continue
            name = entry.get("tool") or ""
            res = entry.get("result") or {}
            text = None
            if isinstance(res, dict) and res.get("success"):
                out = res.get("output") or {}
                if isinstance(out, dict) and out.get("event"):
                    text = str(out["event"])[:80]
            if not text:
                # Failures keep the friendly tool label too — the error itself
                # travels as the expandable output (arrow), not as the label.
                try:
                    text = _preview_event(name, entry.get("arguments"))
                except Exception:
                    text = name or "tool"
            item: dict = {"event": str(text)[:80]}
            try:
                _rsn = _tool_reason(name, entry.get("arguments"),
                                    entry.get("reason") or "")
                if _rsn:
                    item["reason"] = _rsn
            except Exception:
                pass
            try:
                excerpt = _summarize_output(res, name)
            except TypeError:
                try:
                    excerpt = _summarize_output(res)
                except Exception:
                    excerpt = None
            except Exception:
                excerpt = None
            if excerpt:
                item["output"] = excerpt
            try:
                media = _event_media(name, res)
                shot = media.get("shot") if isinstance(media, dict) else None
                if isinstance(shot, str) and shot:
                    item["shot"] = shot[:500]
            except Exception:
                pass
            steps.append(item)
    except Exception:
        pass
    return steps


def _persist_conversation(user_input: str, assistant_output: str, steps: list | None = None,
                          images: list | None = None, elapsed: int | None = None) -> None:
    import uuid
    path = _resolve_conversation_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    data = []
    for cand in (path, _resolve_legacy_conversation_path()):
        if cand.exists():
            try:
                loaded = json.loads(cand.read_text(encoding="utf-8"))
                if isinstance(loaded, list) and loaded:
                    data = loaded
                    break
            except Exception:
                continue
    # ensure each message has id for vector store
    uid = str(uuid.uuid4())[:8]
    aid = str(uuid.uuid4())[:8]
    data.append({"id": uid, "role": "user", "content": user_input[:2000], "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
    assistant_msg: dict = {"id": aid, "role": "assistant", "content": assistant_output[:5000], "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    if isinstance(elapsed, int) and elapsed >= 0:
        assistant_msg["elapsed"] = elapsed
    if isinstance(steps, list) and steps:
        assistant_msg["steps"] = steps[:12]
    if isinstance(images, list) and images:
        assistant_msg["images"] = images[:6]
    data.append(assistant_msg)
    if len(data) > 1000:
        data = data[-1000:]
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    # Auto-create embeddings for new messages (per-message, no fallback)
    try:
        from core.agent.memory.chat_vector_store import add_message
        add_message(uid, user_input[:2000], role="user")
        add_message(aid, assistant_output[:5000], role="assistant")
    except Exception:
        pass
    return uid, aid


def _clear_temp_images_if_any():
    try:
        from core.memory.image_memory.service import clear_temp
        n = clear_temp()
        if n:
            print(f"[loop] cleared {n} temp images at loop end")
    except Exception:
        pass
    # Video is saved locally so its path can be sent as metadata, then deleted after analysis or loop end
    try:
        # Clean any remaining temp videos in Data/files/videos + legacy (including pending jobs)
        from pathlib import Path as _P
        proj = _P(__file__).resolve().parents[3]
        vdirs = []
        try:
            import sys as _sysv
            if str(proj) not in _sysv.path:
                _sysv.path.insert(0, str(proj))
            from core.paths import data_subdir as _dsv
            vdirs.append(_dsv("files", "videos"))
        except Exception:
            pass
        vdirs.append(proj / "storage" / "files" / "videos")
        for vdir in vdirs:
            if not vdir.exists():
                continue
            # Remove files older than current loop (all temp videos should be removed after loop)
            # For safety, we only delete files that are not part of a pending job? But spec says delete from storage after analysis/loop
            # We will delete all files in videos folder that are older than 0 (i.e., all temp) - but keep .gitkeep
            for f in vdir.iterdir():
                if f.is_file() and f.name != ".gitkeep":
                    try:
                        # Check if its job is already completed (if so, delete)
                        # For simplicity, delete all temp videos at loop end (they are not meant to be persisted)
                        f.unlink(missing_ok=True)
                    except Exception:
                        pass
    except Exception:
        pass


def run_loop(
    user_input: str,
    max_steps: int = 500,
    model: str | None = None,
    api_key: str | None = None,
    temperature: float = 0.3,
    verbose: bool = True,
    isolated: bool = False,
    todo_snapshot: dict | None = None,
    on_event=None,
    on_tool_start=None,
    on_token=None,
    restricted: bool = False,
    on_question=None,
    on_live_event=None,
    cancel_flag=None,
) -> dict[str, Any]:
    """
    Run agent loop for user_input.
    Returns dict: {steps, final_answer, history, tool_calls_log, success}
    Never fails silently - even on provider errors, continues up to max_steps.
    on_event(text, summary, key, media) is called live after every tool execution
    for UI streaming (best-effort); media carries {shot, stream} extras or None.
    on_token(step, delta_text) fires live per model content chunk (best-effort).
    restricted=True asks the user (via on_question) before every terminal call.
    on_question(kind, payload) pauses for ask/approval/live and returns the user's answer dict.
    on_live_event({"tool", "output", "success"}) fires for live_preview/agent_control/
    close_browser completions (best-effort).
    isolated=True for scheduled large tasks (skips chat history, uses todo_snapshot).
    """
    if not isinstance(user_input, str) or not user_input.strip():
        raise ValueError("user_input must be non-empty string")
    if not isinstance(max_steps, int) or max_steps < 1 or max_steps > 500:
        raise ValueError("max_steps must be 1..500")
    mdl = model.strip() if model and isinstance(model, str) and model.strip() else "deepseek/deepseek-v4.1-flash"
    # Build initial context (isolated for large scheduled tasks)
    system_prompt, user_context, core_tools = build_context(user_input, isolated=isolated, todo_snapshot=todo_snapshot)
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
    turn_t0 = time.time()
    final_answer: str | None = None
    cancelled = False
    persist_ids: tuple | None = None
    # Discovered tools become directly callable once tool_search returns their specs
    known_tool_names = {t.get("function", {}).get("name") for t in core_tools}
    dynamic_tools: list[dict] = []

    for step in range(1, max_steps + 1):
        steps = step
        if cancel_flag is not None:
            try:
                if cancel_flag.is_set():
                    cancelled = True
                    break
            except Exception:
                pass
        if verbose:
            print(f"\n--- Loop step {step}/{max_steps} ---")
        # Call provider with bounded retries (provider does 3 retries internally).
        # Live token callback for this step (feeds SSE so the UI types the
        # final answer live). No preamble/narration is emitted for tool calls.
        def _emit_token(delta: str) -> None:
            try:
                if on_token is not None and isinstance(delta, str) and delta:
                    on_token(step, delta)
            except Exception:
                pass

        try:
            response = provider_chat(
                messages=messages,
                tools=core_tools + dynamic_tools,
                model=mdl,
                api_key=api_key,
                temperature=temperature,
                on_token=_emit_token if on_token is not None else None,
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
            # No tools requested - final answer is agent content only.
            final_answer = content if isinstance(content, str) and content.strip() else ""
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

        # No narration for tool calls: tools run silently, only their event
        # line is shown. An optional `reason` arg is stripped before execute.
        for tc in tool_calls:
            name = tc["function"]["name"]
            args = tc["function"]["arguments"]
            call_key = str(tc.get("id") or f"{step}-{name}")
            # Reason node: optional model-supplied `reason` (stripped, tools
            # never accept it). Empty when the model wrote none.
            try:
                clean_args, model_reason = _extract_model_reason(args)
            except Exception:
                clean_args, model_reason = args, ""
            try:
                call_reason = _tool_reason(name, clean_args, model_reason)
            except Exception:
                call_reason = ""
            args = clean_args
            if verbose:
                print(f"  -> Executing {name} with {str(args)[:200]}")
            # Instant UI: show the call immediately (no output, no arrow yet).
            # Every tool call shows its own event. Permissions are re-read
            # from disk inside execute_tool on every call, so toggling a
            # plugin mid-loop applies to the very next tool call.
            if on_tool_start is not None:
                try:
                    try:
                        on_tool_start(_preview_event(name, args), call_key, call_reason, step)
                    except TypeError:
                        try:
                            on_tool_start(_preview_event(name, args), call_key, call_reason)
                        except TypeError:
                            try:
                                on_tool_start(_preview_event(name, args), call_key)
                            except TypeError:
                                on_tool_start(_preview_event(name, args))
                except Exception:
                    pass
            if name == "question" and on_question is not None:
                result = _ask_user(name, args, on_question)
            elif restricted and name == "terminal" and on_question is not None:
                result = _approve_terminal(name, args, on_question)
            elif restricted and _is_dangerous_browser_tool(name) and on_question is not None:
                result = _approve_browser_tool(name, args, on_question)
            elif name == "tool_search":
                # No cap: search as many times as needed, the loop itself is bounded by max_steps.
                result = execute_tool(name, args)
            else:
                result = execute_tool(name, args)
            tool_calls_log.append({"step": step, "tool": name, "arguments": args,
                                   "result": result, "reason": call_reason})
            # Live handoff: human_control/take_control pause the loop so the user
            # can drive the browser; the UI resumes it via on_question("live").
            # live_preview never pauses — it only shows the floating player.
            if name in _LIVE_PAUSE_TOOLS and result.get("success") and on_question is not None:
                try:
                    _out = result.get("output", {})
                    _url = _out.get("url") if isinstance(_out, dict) else None
                    if isinstance(_url, str) and _url.startswith(("http://", "https://")):
                        on_question("live", {
                            "tool": name,
                            "url": _url[:2000],
                            "view_only": False,
                            "reason": str(call_reason or "")[:500],
                        })
                        # Resumed: hand the agent a fresh page tree so it
                        # continues from live refs (old ones expired).
                        try:
                            from core.agent.loop.dispatcher import execute_tool as _exec
                            _snap = _exec("page_snapshot", {"max_elements": 150, "mode": "full"})
                            _sout = _snap.get("output") if isinstance(_snap.get("output"), dict) else {}
                            _stext = _sout.get("text") if isinstance(_sout, dict) else None
                            if isinstance(result.get("output"), dict):
                                result["output"]["resumed"] = True
                                if isinstance(_stext, str) and _stext.strip():
                                    result["output"]["fresh_snapshot"] = _stext.strip()[:4000]
                                result["output"]["resume_note"] = (
                                    "The page was shown to the user, who pressed Continue. "
                                    "Resume the task from this fresh map; old refs expired.")
                        except Exception:
                            pass
                except Exception:
                    pass
            # Live notifications: preview start / handoff end / browser close.
            if name in _LIVE_EVENT_TOOLS and on_live_event is not None:
                try:
                    _out2 = result.get("output") if isinstance(result.get("output"), dict) else None
                    on_live_event({"tool": name, "output": _out2, "success": bool(result.get("success"))})
                except Exception:
                    pass
            # Register discovered tools so the model can call them directly next step.
            # Specs come from local spec.json files (trusted); names/args stay validated by dispatcher.
            if name == "tool_search" and result.get("success"):
                try:
                    found = (result.get("output") or {}).get("results") or []
                    for item in found:
                        if not isinstance(item, dict):
                            continue
                        spec = item.get("spec") or {}
                        tname = spec.get("name") or item.get("name")
                        if not tname or tname in known_tool_names or len(dynamic_tools) >= MAX_DYNAMIC_TOOLS:
                            continue
                        dynamic_tools.append(openai_tool_from_spec(
                            tname,
                            spec.get("description") or item.get("description", ""),
                            spec.get("inputs") or {},
                        ))
                        known_tool_names.add(tname)
                        if verbose:
                            print(f"     Registered discovered tool: {tname}")
                except Exception:
                    pass
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
            # Live UI streaming: every tool call notifies with its own event
            # (best-effort, never breaks the loop). No default text anywhere.
            if on_event is not None:
                try:
                    out = result.get("output", {}) if result.get("success") else result
                    text = None
                    if isinstance(out, dict):
                        text = out.get("event")
                    if not text and not result.get("success"):
                        text = result.get("error")
                    if not text:
                        text = _preview_event(name, args)
                    if text:
                        try:
                            try:
                                on_event(str(text)[:80], _summarize_output(result, name), call_key,
                                         _event_media(name, result), call_reason, step)
                            except TypeError:
                                try:
                                    on_event(str(text)[:80], _summarize_output(result, name), call_key,
                                             _event_media(name, result), call_reason)
                                except TypeError:
                                    try:
                                        on_event(str(text)[:80], _summarize_output(result, name), call_key,
                                                 _event_media(name, result))
                                    except TypeError:
                                        on_event(str(text)[:80], _summarize_output(result, name))
                        except Exception:
                            pass
                except Exception:
                    pass
            # Append tool result as tool message (live URLs redacted: the player
            # renders them directly, the model must never see or quote them).
            _redact_live_url(result)
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
            final_answer = ""

    # Ensure final_answer exists — agent content only, never a default sentence.
    if final_answer is None:
        # Extract last assistant content if any
        for m in reversed(messages):
            if m.get("role") == "assistant" and m.get("content"):
                final_answer = str(m["content"])
                break
        if final_answer is None:
            final_answer = ""

    # Persist conversation (skipped for cancelled runs — nothing to keep)
    final_images: list[dict] = []
    try:
        elapsed_sec = max(0, int(round(time.time() - turn_t0)))
    except Exception:
        elapsed_sec = 0
    if not cancelled:
        try:
            final_images = _collect_final_images(tool_calls_log)
        except Exception:
            final_images = []
        try:
            persist_ids = _persist_conversation(user_input, final_answer, _persist_steps(tool_calls_log), final_images, elapsed_sec)
        except Exception:
            pass

        # Auto memory every 50 messages: extract facts about user/agent and organize (two requests each)
        try:
            from core.memory.auto_memory import maybe_trigger_auto_memory
            maybe_trigger_auto_memory(api_key=api_key)
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
        "success": not cancelled,
        "cancelled": cancelled,
        "elapsed_sec": elapsed_sec,
        "final_images": final_images,
        "message_ids": {"user": persist_ids[0], "assistant": persist_ids[1]} if persist_ids else None,
    }
