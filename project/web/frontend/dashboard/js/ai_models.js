/**
 * ai_models.js — AI Models section (universal connection engine UI).
 * Owns: the fixed template cards (text, vision, image, tts, stt), each opening
 * a dialog with Claude-guided steps, 4 glass fields + a big Configuration
 * Protocol JSON editor with live coloring, and a Connect → black log panel
 * (green borders on success, red on failure) → Save state machine.
 * The API key is never displayed in full (dots mask in the field only).
 */
"use strict";

import { modalShell, openModal, closeModal, toast } from "./ui.js?v=13";

const CLAUDE_URL = "https://claude.ai";

const TEMPLATES = [
  { kind: "text", tag: "TX", title: "AI Text Model", desc: "Chat model for the agent. Follow the 2 steps, then Connect." },
  { kind: "vision", tag: "VI", title: "AI Vision Model", desc: "Sees images and answers about them. Follow the 2 steps, then Connect." },
  { kind: "image", tag: "IM", title: "AI Image Model", desc: "Creates pictures from a description. Follow the 2 steps, then Connect." },
  { kind: "tts", tag: "TS", title: "AI Text to Speech", desc: "Reads text aloud as a voice record. Follow the 2 steps, then Connect." },
  { kind: "stt", tag: "ST", title: "AI Speech to Text", desc: "Turns voice notes into written words. Follow the 2 steps, then Connect." },
];

const ICON_COPY = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2" /><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" /></svg>`;
const ICON_CHECK = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12" /></svg>`;

/* Saved-key mask shown in the dialog field (never the real key). */
const KEY_MASK = "••••••••";

function isKeyMask(v) {
  return !!v && /^•+$/.test(String(v));
}

function highlightJson(raw) {
  const e = esc(raw || "");
  return e.replace(/(&quot;(?:[^&]|&(?!quot;))*?&quot;)(\s*:)?|\b(true|false|null)\b|-?\d+(?:\.\d+)?/g,
    (m, str, colon, kw) => {
      if (str) {
        return colon
          ? `<span style="color:#7dd3fc">${str}</span>${colon}`
          : `<span style="color:#86efac">${str}</span>`;
      }
      if (kw) return `<span style="color:#f0abfc">${m}</span>`;
      return `<span style="color:#fcd34d">${m}</span>`;
    });
}

function revealAnimated(el) {
  if (!el || !el.hidden) return;
  el.hidden = false;
  try {
    el.animate(
      [{ opacity: 0, transform: "translateY(8px)" }, { opacity: 1, transform: "translateY(0)" }],
      { duration: 320, easing: "ease-out" },
    );
  } catch (_) { /* static reveal fallback */ }
}

function replayPlay(el) {
  if (!el) return;
  el.classList.remove("play");
  void el.offsetWidth;
  el.classList.add("play");
}

function esc(s) {
  return String(s ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

function titleOf(kind) {
  const t = TEMPLATES.find((t) => t.kind === kind);
  return t ? t.title : kind;
}

async function fetchJson(url, opts) {
  const res = await fetch(url, opts);
  const data = await res.json().catch(() => ({}));
  return { res, data };
}

async function copyText(text, quiet) {
  const value = String(text || "");
  if (!value) {
    if (!quiet) toast("Nothing to copy");
    return false;
  }
  try {
    if (navigator.clipboard && typeof navigator.clipboard.writeText === "function") {
      await navigator.clipboard.writeText(value);
      if (!quiet) toast("Copied");
      return true;
    }
    throw new Error("clipboard-api-unavailable");
  } catch (_) {
    try {
      const ta = document.createElement("textarea");
      ta.value = value;
      ta.setAttribute("readonly", "");
      ta.style.cssText = "position:fixed;top:-9999px;left:-9999px;opacity:0;";
      document.body.appendChild(ta);
      ta.select();
      ta.setSelectionRange(0, ta.value.length);
      const ok = document.execCommand("copy");
      ta.remove();
      if (!quiet) toast(ok ? "Copied" : "Copy failed — long-press to copy manually");
      return !!ok;
    } catch (_2) {
      if (!quiet) toast("Copy failed — long-press to copy manually");
      return false;
    }
  }
}

/* Mark a copy chip as done (icon flips to a check, then flips back). */
function chipDone(btn, ms) {
  if (!btn) return;
  const original = btn.innerHTML;
  const label = btn.querySelector(".cc-label");
  const iconHolder = btn.querySelector(".cc-icon");
  if (label && iconHolder) {
    iconHolder.innerHTML = ICON_CHECK;
    label.textContent = "Copied";
  } else if (iconHolder) {
    iconHolder.innerHTML = ICON_CHECK;
  } else {
    btn.innerHTML = ICON_CHECK;
  }
  btn.classList.add("copied");
  replayPlay(btn);
  setTimeout(() => {
    btn.innerHTML = original;
    btn.classList.remove("copied");
  }, ms || 2000);
}

/* ---------------- configuration prompt (per-kind, from models_prompts.md) ---------------- */

async function copySetupPrompt(kind, btn) {
  if (btn) btn.disabled = true;
  try {
    const { res, data } = await fetchJson(`/api/ai_models/setup_prompt?kind=${encodeURIComponent(kind)}`, { cache: "no-store" });
    if (!res.ok) throw new Error(data?.error || `HTTP ${res.status}`);
    const prompt = String(data.prompt || "");
    if (prompt.length < 100) throw new Error("Prompt looks wrong — reload the page and restart the server, then try again.");
    const ok = await copyText(prompt, true);
    if (ok) {
      if (btn) chipDone(btn);
      toast(`Copied (${prompt.length.toLocaleString("en-US")} chars)`);
    } else {
      toast("Copy failed — allow clipboard access and try again");
    }
  } catch (err) {
    toast(err instanceof Error ? err.message : "Prompt unavailable");
  } finally {
    if (btn) btn.disabled = false;
  }
}

function parseConnectionJson(raw) {
  const text = String(raw || "").trim();
  if (!text) return { ok: false, error: "Configuration Protocol is empty. Paste the block Claude gave you." };
  try {
    const obj = JSON.parse(text);
    if (!obj || typeof obj !== "object" || Array.isArray(obj)) {
      return { ok: false, error: "Configuration Protocol must be an object starting with { and ending with }." };
    }
    return { ok: true, value: obj };
  } catch (e) {
    return { ok: false, error: `Configuration Protocol is not valid JSON: ${e instanceof Error ? e.message : "parse error"}` };
  }
}

/* ---------------- dialog ---------------- */

function openTemplateDialog(tpl, saved) {
  const hasKey = !!saved?.has_key;
  openModal(modalShell({
    icon: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="4" y="4" width="16" height="16" rx="2" /><rect x="9" y="9" width="6" height="6" /></svg>`,
    title: tpl.title,
    subtitle: saved?.configured ? "Saved — edit any field to test again" : "Follow the 2 steps, then Connect",
    body: `
      <div class="ai-guide">
        <ol class="ai-steps">
          <li><span class="ai-step-n">1.</span> <a class="ai-link" href="${CLAUDE_URL}" target="_blank" rel="noopener">Open Claude</a></li>
          <li class="ai-step-wrap">
            <div class="ai-step-row"><span class="ai-step-n">2.</span> <b>Paste the</b>
              <button class="cfg-copy" id="aiPromptCopy" type="button" aria-label="Copy the configuration prompt"><span class="cfg-words">configuration prompt</span><span class="cc-icon">${ICON_COPY}</span></button>
            </div>
            <p class="ai-steps-note">Send it to Claude, answer the questions, then fill the fields below.</p>
          </li>
        </ol>
      </div>
      <div class="block">
        <div class="field">
          <label for="aiModelName">1 · Model name</label>
          <div class="field-wrap"><input class="input" id="aiModelName" type="text" dir="ltr" spellcheck="false" autocomplete="off"
            placeholder="exact identifier the service expects (may be empty)"
            value="${esc(saved?.model_name || "")}" /></div>
          <span class="hint">The exact model identifier. May be empty when the server needs none.</span>
        </div>
        <div class="field">
          <label for="aiServerUrl">2 · Server URL</label>
          <div class="field-wrap"><input class="input" id="aiServerUrl" type="text" dir="ltr" spellcheck="false" autocomplete="off"
            placeholder="https://… or http://localhost:…"
            value="${esc(saved?.server_url || "")}" /></div>
          <span class="hint">Base address only — no endpoint path, no secret inside.</span>
        </div>
        <div class="field">
          <label for="aiApiKey">3 · API key / token (optional)</label>
          <div class="field-wrap"><input class="input" id="aiApiKey" type="password" autocomplete="off" placeholder="leave empty when the server needs none"
            value="${hasKey ? KEY_MASK : ""}" /></div>
          <span class="hint">${hasKey ? "Saved — shown as dots. Type a new key to replace it, or clear the field to remove it." : "Stored on this server only. Never displayed in full."}</span>
        </div>
        <div class="field">
          <label for="aiApiUrl">4 · API URL (endpoint path)</label>
          <div class="field-wrap"><input class="input" id="aiApiUrl" type="text" dir="ltr" spellcheck="false" autocomplete="off"
            placeholder="/v1/chat/completions"
            value="${esc(saved?.api_url || "")}" /></div>
          <span class="hint">Joined to the Server URL to make the full address. Leave empty only if the Server URL is already complete.</span>
        </div>
        <div class="field" style="margin-bottom:0;">
          <label for="aiConnJson">5 · Configuration Protocol</label>
          <div class="field-wrap ai-proto-wrap"><pre id="aiJsonPreview" dir="ltr" aria-hidden="true"></pre><textarea class="input ai-proto-input" id="aiConnJson" dir="ltr" spellcheck="false" rows="14" wrap="off"
            placeholder='{"language": "AIP/1", "version": 1, …}'>${esc(saved?.config_json ? JSON.stringify(saved.config_json, null, 2) : "")}</textarea></div>
        </div>
      </div>
      <div class="block" id="aiLogBlock" hidden>
        <div class="ai-log" id="aiLogWrap">
          <div class="ai-log-head">
            <span>Connection log</span>
            <button class="copy-chip" id="aiLogCopy" type="button" aria-label="Copy the log">${ICON_COPY}</button>
          </div>
          <pre class="ai-log-body" id="aiLog" dir="ltr"></pre>
        </div>
        <button class="ai-log-claude" id="aiLogClaude" type="button" hidden>Copy the log and paste it into Claude</button>
      </div>`,
    foot: `
      <button class="glass-btn" data-close type="button">Close</button>
      <button class="glass-btn accent" id="aiMainBtn" type="button">Connect</button>`,
  }));

  /* phase: idle → testing → ok | fail. Any edit returns to idle. */
  let phase = "idle";
  let logLines = [];

  const mainBtn = document.getElementById("aiMainBtn");
  const logBlock = document.getElementById("aiLogBlock");
  const logWrap = document.getElementById("aiLogWrap");
  const logPre = document.getElementById("aiLog");
  const logClaude = document.getElementById("aiLogClaude");
  const protoInput = document.getElementById("aiConnJson");
  const preview = document.getElementById("aiJsonPreview");

  document.getElementById("aiPromptCopy")?.addEventListener("click", (e) => copySetupPrompt(tpl.kind, e.currentTarget));

  function collect() {
    return {
      model_name: String(document.getElementById("aiModelName")?.value || ""),
      server_url: String(document.getElementById("aiServerUrl")?.value || "").trim(),
      api_url: String(document.getElementById("aiApiUrl")?.value || "").trim(),
      api_key: String(document.getElementById("aiApiKey")?.value || ""),
      conn_raw: String(protoInput?.value || ""),
    };
  }

  function payloadFor(f, parsed) {
    const rawKey = String(f.api_key || "");
    // Untouched dots mask with a saved key means keep it — never send dots.
    // An emptied field means remove the key.
    const keepKey = hasKey && isKeyMask(rawKey);
    return {
      kind: tpl.kind,
      model_name: f.model_name, server_url: f.server_url, api_url: f.api_url,
      api_key: keepKey ? "" : rawKey, keep_key: keepKey,
      config_json: parsed.value, parser_definition: parsed.value,
    };
  }

  function setMain(label, disabled) {
    if (!mainBtn) return;
    mainBtn.textContent = label;
    mainBtn.disabled = !!disabled;
  }

  function renderLog() {
    if (!logPre) return;
    logPre.textContent = logLines.join("\n");
  }

  function showLog(ok) {
    if (!logWrap || !logBlock) return;
    logWrap.classList.toggle("ok", !!ok);
    logWrap.classList.toggle("err", !ok);
    if (logClaude) logClaude.hidden = !!ok;
    revealAnimated(logBlock);
    replayPlay(logWrap);
  }

  function hideLog() {
    if (!logBlock || !logWrap) return;
    logBlock.hidden = true;
    logWrap.classList.remove("ok", "err");
    if (logClaude) logClaude.hidden = true;
    logLines = [];
    renderLog();
  }

  function markDirty() {
    if (phase === "idle") return;
    phase = "idle";
    hideLog();
    setMain("Connect", false);
  }

  function refreshPreview() {
    if (!preview || !protoInput) return;
    const raw = protoInput.value || "";
    if (!raw.trim()) { preview.innerHTML = ""; return; }
    /* Highlight the raw text as typed (works for partial JSON too);
       pretty-printing happens only on paste/blur so the caret never jumps. */
    preview.innerHTML = `${highlightJson(raw)}\n`;
    syncProtoScroll();
  }

  function prettifyProto() {
    if (!protoInput) return;
    const raw = protoInput.value || "";
    if (!raw.trim() || !raw.includes("{")) return;
    try {
      const pretty = `${JSON.stringify(JSON.parse(raw), null, 2)}\n`;
      if (pretty !== raw) {
        protoInput.value = pretty;
        refreshPreview();
      }
    } catch (_) { /* leave invalid JSON untouched while typing */ }
  }

  function syncProtoScroll() {
    if (!preview || !protoInput) return;
    preview.scrollTop = protoInput.scrollTop;
    preview.scrollLeft = protoInput.scrollLeft;
  }

  ["aiModelName", "aiServerUrl", "aiApiKey", "aiApiUrl"].forEach((id) => {
    document.getElementById(id)?.addEventListener("input", markDirty);
  });
  protoInput?.addEventListener("input", () => { refreshPreview(); markDirty(); });
  protoInput?.addEventListener("scroll", syncProtoScroll);
  protoInput?.addEventListener("paste", () => { setTimeout(prettifyProto, 0); });
  protoInput?.addEventListener("blur", prettifyProto);
  refreshPreview();

  document.getElementById("aiLogCopy")?.addEventListener("click", async (e) => {
    const ok = await copyText(logLines.join("\n"), true);
    if (ok) chipDone(e.currentTarget);
    else toast("Copy failed — long-press to copy manually");
  });
  logClaude?.addEventListener("click", async () => {
    const ok = await copyText(logLines.join("\n"), true);
    toast(ok ? "Log copied — paste it into Claude" : "Copy failed — long-press to copy manually");
  });

  mainBtn?.addEventListener("click", async () => {
    if (phase === "ok") {
      await doSave();
      return;
    }
    if (phase === "testing") return;
    await doConnect();
  });

  async function doConnect() {
    const f = collect();
    if (!f.server_url) {
      logLines = ["ERROR: Server URL is empty."];
      renderLog();
      showLog(false);
      phase = "fail";
      return;
    }
    const parsed = parseConnectionJson(f.conn_raw);
    if (!parsed.ok) {
      logLines = [`ERROR: ${parsed.error}`];
      renderLog();
      showLog(false);
      phase = "fail";
      return;
    }
    phase = "testing";
    setMain("Connecting…", true);
    logLines = [
      `POST ${f.server_url}${f.api_url || ""}`,
      `Model: ${f.model_name || "(none)"} · ${titleOf(tpl.kind)}`,
      "Sending test request…",
    ];
    renderLog();
    if (logBlock) logBlock.hidden = false;
    try {
      const tested = await fetchJson("/api/ai_models/test", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payloadFor(f, parsed)),
      });
      if (!tested.data?.ok) {
        const err = tested.data?.error || {};
        logLines = [
          `POST ${f.server_url}${f.api_url || ""}`,
          `Model: ${f.model_name || "(none)"} · ${titleOf(tpl.kind)}`,
          `Status: FAIL (HTTP ${tested.res.status})`,
          `Error: ${err.copyable || err.message || "unknown error"}`,
        ];
        renderLog();
        showLog(false);
        phase = "fail";
        setMain("Connect", false);
        toast("Connection failed");
        return;
      }
      logLines = [
        `POST ${f.server_url}${f.api_url || ""}`,
        `Model: ${f.model_name || "(none)"} · ${titleOf(tpl.kind)}`,
        `Status: OK · ${tested.data.latency_ms || 0} ms`,
        `Reply: ${(tested.data.reply_preview || "OK").slice(0, 500)}`,
      ];
      renderLog();
      showLog(true);
      phase = "ok";
      setMain("Save", false);
      toast("Connected — press Save");
    } catch (err) {
      const m = err instanceof Error ? err.message : "Connection failed";
      logLines = [`POST ${f.server_url}${f.api_url || ""}`, `Status: FAIL`, `Error: ${m.slice(0, 500)}`];
      renderLog();
      showLog(false);
      phase = "fail";
      setMain("Connect", false);
    }
  }

  async function doSave() {
    const f = collect();
    const parsed = parseConnectionJson(f.conn_raw);
    if (!parsed.ok) {
      phase = "idle";
      setMain("Connect", false);
      return;
    }
    setMain("Saving…", true);
    try {
      const res = await fetchJson("/api/ai_models/save", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ...payloadFor(f, parsed), force: false }),
      });
      if (res.data?.ok) {
        toast("Saved");
        closeModal();
        await refresh();
        window.dispatchEvent(new CustomEvent("x5:models-changed"));
      } else {
        const m = res.data?.error || `HTTP ${res.res.status}`;
        logLines = [...logLines, `Save: FAIL — ${String(m).slice(0, 300)}`];
        renderLog();
        showLog(false);
        phase = "fail";
        setMain("Connect", false);
        toast(m);
      }
    } catch (err) {
      const m = err instanceof Error ? err.message : "Save failed";
      logLines = [...logLines, `Save: FAIL — ${m.slice(0, 300)}`];
      renderLog();
      showLog(false);
      phase = "fail";
      setMain("Connect", false);
    }
  }
}

/* ---------------- cards grid ---------------- */

/* Short fingerprint of what the cards show — rebuild only on real change. */
function sigOf(state) {
  if (!state) return "null";
  return TEMPLATES.map((t) => {
    const s = state[t.kind] || {};
    return `${t.kind}:${s.configured ? 1 : 0}:${s.model_name || ""}`;
  }).join("|");
}

let lastSig = "";
let inFlight = null;

async function loadState() {
  try {
    const { res, data } = await fetchJson("/api/ai_models", { cache: "no-store" });
    if (!res.ok) throw new Error("bad status");
    return data || {};
  } catch (_) {
    return null;
  }
}

async function refresh() {
  // One fetch at a time: navigate() fires x5:page-open while an initial
  // refresh may still be in flight — joining it beats rebuilding twice.
  if (inFlight) {
    await inFlight.catch(() => {});
    return;
  }
  const grid = document.getElementById("aiModelsGrid");
  if (!grid) return;
  const job = (async () => {
    const state = await loadState();
    const sig = sigOf(state);
    // Same data + cards already on screen: leave the DOM alone so the
    // entrance animation started by navigate() is never torn down mid-flight.
    if (state !== null && sig === lastSig && grid.querySelector(".mini-card")) return;
    lastSig = sig;
    grid.innerHTML = "";
    if (state === null) {
      grid.innerHTML = `<div class="grid-empty">AI Models unavailable — check the server is running.</div>`;
      return;
    }
    for (const t of TEMPLATES) {
      const saved = state[t.kind] || null;
      const on = !!saved?.configured;
      const badge = on ? "Connected" : "Not configured";
      const el = document.createElement("button");
      el.className = "mini-card";
      el.type = "button";
      el.setAttribute("data-anim", "");
      el.innerHTML = `
      <div class="mini-top">
        <div class="mini-icon">${esc(t.tag)}</div>
        <svg class="mini-arrow" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round">
          <polyline points="9 18 15 12 9 6" />
        </svg>
      </div>
      <div class="mini-title">${esc(t.title)}</div>
      <div class="mini-desc">${esc(t.desc)}</div>
      <div class="mini-foot">
        <span class="badge glass ${on ? "green" : "muted"}">${esc(badge)}</span>
        <span class="mini-ver">${esc(on && saved?.model_name ? saved.model_name.slice(0, 24) : on ? "ready" : "setup needed")}</span>
      </div>`;
      el.addEventListener("click", () => openTemplateDialog(t, saved));
      grid.appendChild(el);
    }
    // Silent swap: the page entrance already played once at boot —
    // data appears with no re-animation. (Fresh cards carry data-anim but
    // never get .anim, so they render statically.)
  })();
  inFlight = job;
  try {
    await job;
  } finally {
    if (inFlight === job) inFlight = null;
  }
}

export async function initAiModels() {
  if (!document.getElementById("aiModelsGrid")) return;
  window.addEventListener("x5:page-open", (e) => {
    // Data refreshes silently — the entrance played once, never replayed.
    if (e.detail === "ai-models") refresh().catch(() => {});
  });
  // Deep-link from chat: {kind} opens that template dialog automatically.
  window.addEventListener("x5:open-model", (e) => {
    openAiModelDialog(String((e && e.detail && e.detail.kind) || "text"));
  });
  await refresh();
}

/* Open one template dialog by kind (used by chat glass buttons). Never throws. */
export async function openAiModelDialog(kind) {
  const k = String(kind || "text").toLowerCase();
  const tpl = TEMPLATES.find((t) => t.kind === k) || TEMPLATES[0];
  try {
    const state = await loadState();
    const saved = state ? state[tpl.kind] || null : null;
    openTemplateDialog(tpl, saved);
  } catch (_) {
    try { openTemplateDialog(tpl, null); } catch (_) {}
  }
}
try {
  window.openAiModelDialog = openAiModelDialog;
} catch (_) {}
