/**
 * events.js — single SSE push channel (replaces every client poller).
 *
 * One EventSource per open tab. The server fans out state changes only
 * (browser/live/chat/chat_done/dashboard/workspace); a `hello` snapshot on
 * every (re)connect resyncs late subscribers, so no last-event-id plumbing
 * is needed. Modules register with onPush(type, fn) — no import cycles.
 */
"use strict";

const handlers = new Map(); // type -> Set<fn>
const lastState = new Map(); // state types only (replayed to late subscribers)
const STATE_TYPES = new Set(["browser", "live", "chat", "dashboard", "workspace", "scheduler", "gateway"]);
let started = false;

export function onPush(type, fn) {
  if (typeof type !== "string" || typeof fn !== "function") return;
  if (!handlers.has(type)) handlers.set(type, new Set());
  handlers.get(type).add(fn);
  // Late subscriber (module init is async): replay the newest snapshot slice.
  try {
    if (STATE_TYPES.has(type) && lastState.has(type)) fn(lastState.get(type));
  } catch (_) {}
}

function dispatch(frame) {
  if (!frame || typeof frame.type !== "string") return;
  if (frame.type === "hello" && frame.data && typeof frame.data === "object") {
    const snap = frame.data;
    for (const key of ["browser", "chat", "workspace", "dashboard", "live", "scheduler", "gateway"]) {
      if (snap[key] !== undefined) {
        lastState.set(key === "live" ? "live" : key, snap[key]);
        emit(key === "live" ? "live" : key, snap[key]);
      }
    }
    return;
  }
  if (frame.type === "ping") return;
  if (STATE_TYPES.has(frame.type)) {
    try {
      lastState.set(frame.type, frame.data);
    } catch (_) {}
  }
  emit(frame.type, frame.data);
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

function connect() {
  let es = null;
  try {
    es = new EventSource("/api/events");
  } catch (_) {
    setTimeout(connect, 5000);
    return;
  }
  es.onmessage = (e) => {
    try {
      dispatch(JSON.parse(e.data));
    } catch (_) {}
  };
  es.onerror = () => {
    try {
      es.close();
    } catch (_) {}
    // Reconnect with backoff; the fresh hello resyncs everything.
    setTimeout(connect, 5000);
  };
}

export function startEvents() {
  if (started) return;
  started = true;
  connect();
}