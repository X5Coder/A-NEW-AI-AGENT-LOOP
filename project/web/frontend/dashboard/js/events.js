/**
 * events.js — THE ONE server -> browser stream (single SSE connection).
 *
 * Transport rule: server -> browser is ONE SSE stream (GET /api/events
 * below, opened once when the UI loads). Browser -> server is plain POST
 * only. No WebSocket anywhere. No polling anywhere.
 *
 * hello order (most important first): last-50 chat messages -> dashboard
 * bundle -> everything else. Each section carries a server hash: the client
 * keeps the last hash per section and re-renders a section ONLY when its
 * hash broke (data changed) — otherwise the cached render stays.
 */
"use strict";

const handlers = new Map(); // type -> Set<fn>
const lastState = new Map(); // state types only (replayed to late subscribers)
const lastHash = new Map(); // section -> last seen server hash
const STATE_TYPES = new Set(["browser", "live", "chat", "dashboard", "workspace", "scheduler", "gateway", "chat_last50"]);
let started = false;
let sseSource = null; // the ONE stream: created once, never duplicated

export function onPush(type, fn) {
  if (typeof type !== "string" || typeof fn !== "function") return;
  if (!handlers.has(type)) handlers.set(type, new Set());
  handlers.get(type).add(fn);
  // Late subscriber (module init is async): replay the newest snapshot slice.
  try {
    if (STATE_TYPES.has(type) && lastState.has(type)) fn(lastState.get(type));
  } catch (_) {}
}

function emit(type, data) {
  const set = handlers.get(type);
  if (!set) return;
  for (const fn of set) {
    try {
      fn(data);
    } catch (_) {}
  }
}

function applySection(name, section) {
  // {hash, data} sections: skip when the hash is unchanged (cache still
  // valid). Returns true when the data was actually applied.
  if (!section || typeof section !== "object") return false;
  try {
    const h = section.hash;
    if (typeof h === "string" && lastHash.get(name) === h) return false;
    if (typeof h === "string") lastHash.set(name, h);
    lastState.set(name, section.data);
    emit(name, section.data);
    return true;
  } catch (_) {
    return false;
  }
}

function onHello(snap) {
  if (!snap || typeof snap !== "object") return;
  // 1. Most important FIRST: last 50 chat messages render immediately.
  if (snap.chat_last50 !== undefined) applySection("chat_last50", snap.chat_last50);
  // 2. Dashboard bundle second.
  if (snap.dashboard !== undefined) applySection("dashboard", snap.dashboard);
  // 3. Everything else (browser/live/chat/workspace/scheduler/gateway).
  const rest = snap.rest && typeof snap.rest === "object" ? snap.rest.data : null;
  const flat = rest && typeof rest === "object" ? rest : snap;
  for (const key of ["browser", "chat", "workspace", "scheduler", "gateway"]) {
    if (flat[key] !== undefined) {
      try {
        lastState.set(key, flat[key]);
      } catch (_) {}
      emit(key, flat[key]);
    }
  }
  if (rest && typeof rest === "object" && typeof snap.rest.hash === "string") {
    try {
      lastHash.set("rest", snap.rest.hash);
    } catch (_) {}
  }
}

function onServerPush(channel, data) {
  if (typeof channel !== "string") return;
  // Chat run frames also ride pushes: "run" replays to refreshed tabs,
  // "chat_run" is the live progress.
  if (channel === "run" || channel === "chat_run") {
    emit(channel, data);
    return;
  }
  if (channel === "chat_done") {
    // A run persisted: hashes broke server-side; next hello re-renders.
    try {
      lastHash.delete("chat_last50");
      lastHash.delete("rest");
    } catch (_) {}
    emit("chat_done", data);
    return;
  }
  if (channel === "dashboard") {
    // Dashboard changed: drop its hash so the next hello re-renders it.
    try {
      lastHash.delete("dashboard");
    } catch (_) {}
  }
  if (STATE_TYPES.has(channel)) {
    try {
      lastState.set(channel, data);
    } catch (_) {}
  }
  emit(channel, data);
}

export function startEvents() {
  // Single stream: second calls are no-ops, so multi-module init can never
  // open a second SSE connection.
  if (started) return;
  started = true;
  try {
    if (sseSource) return;
    const src = new EventSource("/api/events");
    sseSource = src;
    src.onmessage = (e) => {
      let frame;
      try {
        frame = JSON.parse(e.data);
      } catch (_) {
        return;
      }
      if (!frame || typeof frame !== "object") return;
      if (frame.type === "hello" && frame.data) {
        onHello(frame.data);
        return;
      }
      if (typeof frame.type === "string") onServerPush(frame.type, frame.data || {});
    };
    src.onerror = () => {
      // EventSource retries natively on the SAME single stream.
      // A dead session (401) sends the tab one-way to login.
      try {
        if (src.readyState === 2) location.replace("/login.html");
      } catch (_) {}
    };
  } catch (_) {}
}
