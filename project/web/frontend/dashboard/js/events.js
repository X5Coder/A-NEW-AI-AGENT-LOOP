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
 *
 * Proxy note: a reverse proxy / idle timeout resets long SSE streams (502 /
 * connection reset) while the session is still valid. A closed stream is NOT
 * proof of logout: login redirect happens ONLY after /api/auth/status
 * provably answers 401, otherwise the single stream redials with backoff.
 */
"use strict";

import { loadBootCache, saveBootCache } from "./boot_cache.js?v=1";

const handlers = new Map(); // type -> Set<fn>
const lastState = new Map(); // state types only (replayed to late subscribers)
const lastHash = new Map(); // section -> last seen server hash
const STATE_TYPES = new Set(["browser", "live", "chat", "dashboard", "workspace", "scheduler", "gateway", "chat_last50"]);
let started = false;
let sseSource = null; // the ONE stream: created once, never duplicated
let reconnectTimer = null;
let reconnectAttempt = 0;

// Persistent hash-cache: restore BEFORE any module subscribes, so the
// first onPush replay paints cached data instantly on refresh. The hello
// snapshot then reconciles: unchanged hash = cached render stays, broken
// hash = re-render + cache rewritten with the new hash.
let cacheLoaded = false;
try {
  const cached = loadBootCache();
  if (cached) {
    for (const [k, v] of Object.entries(cached.hashes || {})) {
      if (typeof k === "string" && typeof v === "string") lastHash.set(k, v);
    }
    for (const [k, v] of Object.entries(cached.states || {})) {
      if (typeof k === "string" && STATE_TYPES.has(k)) lastState.set(k, v);
    }
    cacheLoaded = lastState.size > 0;
  }
} catch (_) {}

export function hasBootCache() {
  return cacheLoaded;
}

function persistBootCache() {
  // Best-effort: writes ALWAYS go through here, so a data update mints a
  // new stored hash in the same step.
  try {
    const hashes = {};
    const states = {};
    for (const [k, v] of lastHash) {
      if (typeof v === "string") hashes[k] = v;
    }
    for (const [k, v] of lastState) {
      if (STATE_TYPES.has(k)) states[k] = v;
    }
    saveBootCache(hashes, states);
  } catch (_) {}
}
// Boot barrier: resolves once the first hello snapshot (chat + dashboard +
// everything else) is applied. The app.js boot splash waits on this plus
// every module's first load — or lifts early on the fast path when a
// hash-cache already painted the shell.
let helloSeen = false;
let helloResolve = null;
const helloPromise = new Promise((resolve) => {
  helloResolve = resolve;
});
export function whenHello() {
  return helloPromise;
}

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
  // valid — cached render stays). A broken hash re-renders AND rewrites
  // the stored hash in the same step. Returns true when data was applied.
  if (!section || typeof section !== "object") return false;
  try {
    const h = section.hash;
    if (typeof h === "string" && lastHash.get(name) === h) {
      // Hash match but first boot after refresh: the cached state may hold
      // the same data without ever emitting to the fresh subscribers.
      // Replay it once so modules paint, without rewriting the cache.
      if (lastState.has(name)) emit(name, lastState.get(name));
      return false;
    }
    if (typeof h === "string") lastHash.set(name, h);
    lastState.set(name, section.data);
    emit(name, section.data);
    persistBootCache();
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
  persistBootCache();
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
      persistBootCache();
    } catch (_) {}
    emit("chat_done", data);
    return;
  }
  if (channel === "dashboard") {
    // Dashboard changed: drop its hash so the next hello re-renders it.
    try {
      lastHash.delete("dashboard");
      persistBootCache();
    } catch (_) {}
  }
  if (STATE_TYPES.has(channel)) {
    try {
      lastState.set(channel, data);
    } catch (_) {}
  }
  emit(channel, data);
  // Live state changed: keep the stored snapshot fresh so the next
  // refresh paints the newest data instantly.
  try {
    if (STATE_TYPES.has(channel)) persistBootCache();
  } catch (_) {}
}

function reconnectDelay() {
  // Capped backoff: 1s -> 2s -> 5s -> 15s steady. This is a persistent
  // subscription (same as native EventSource redial), not an operation
  // retry, so it keeps redialling instead of giving up on the dashboard.
  reconnectAttempt += 1;
  if (reconnectAttempt <= 1) return 1000;
  if (reconnectAttempt === 2) return 2000;
  if (reconnectAttempt === 3) return 5000;
  return 15000;
}

function closeStream() {
  try {
    if (sseSource) sseSource.close();
  } catch (_) {}
  sseSource = null;
}

function scheduleReconnect() {
  // Never stack timers: one pending redial at most (single-stream rule).
  if (reconnectTimer) return;
  const wait = reconnectDelay();
  reconnectTimer = setTimeout(() => {
    reconnectTimer = null;
    sseSource = null;
    connectStream();
  }, wait);
}

async function handleStreamError() {
  // A closed stream is NOT proof of logout: a proxy/idle timeout answers 502 /
  // resets long SSE streams while the session is still valid. Redirect to
  // login ONLY when the session is provably dead; otherwise redial.
  try {
    const res = await fetch("/api/auth/status", { cache: "no-store" });
    if (res.status === 401) {
      try {
        location.replace("/login.html");
      } catch (_) {}
      return;
    }
    if (res.ok) {
      reconnectAttempt = 0; // session alive: fast redial on a stream blip
      closeStream();
      connectStream();
      return;
    }
  } catch (_) {
    // Status itself unreachable (proxy 502 / offline): never redirect.
  }
  closeStream();
  scheduleReconnect();
}

function connectStream() {
  if (sseSource) return;
  let src;
  try {
    src = new EventSource("/api/events");
  } catch (_) {
    scheduleReconnect();
    return;
  }
  sseSource = src;
  src.onmessage = (e) => {
    let frame;
    try {
      frame = JSON.parse(e.data);
    } catch (_) {
      return;
    }
    if (!frame || typeof frame !== "object") return;
    reconnectAttempt = 0; // healthy stream: reset the redial backoff
    if (frame.type === "hello" && frame.data) {
      onHello(frame.data);
      // First snapshot applied: release the app.js boot barrier.
      if (!helloSeen) {
        helloSeen = true;
        try {
          helloResolve();
        } catch (_) {}
      }
      return;
    }
    if (typeof frame.type === "string") onServerPush(frame.type, frame.data || {});
  };
  src.onerror = () => {
    // Native EventSource redials by itself while CONNECTING/OPEN; act only
    // when it gave up (CLOSED, e.g. proxy 502): verify session, then redial.
    try {
      if (src.readyState !== 2) return;
    } catch (_) {}
    handleStreamError();
  };
}

export function startEvents() {
  // Single stream: second calls are no-ops, so multi-module init can never
  // open a second SSE connection.
  if (started) return;
  started = true;
  try {
    connectStream();
  } catch (_) {}
}
