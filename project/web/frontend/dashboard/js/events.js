/**
 * events.js — push fan-in over the ONE ws.js socket (replaces SSE).
 *
 * Same contract as before: modules register with onPush(type, fn), hello
 * snapshots resync late subscribers. Transport is ws.js now: pushes arrive
 * on the same socket that carries chat (instant + bidirectional).
 */
"use strict";

import { onWsPush, onWsHello, onWsStatus, ensureWs, isHttpFallback } from "./ws.js?v=2";

const handlers = new Map(); // type -> Set<fn>
const lastState = new Map(); // state types only (replayed to late subscribers)
const STATE_TYPES = new Set(["browser", "live", "chat", "dashboard", "workspace", "scheduler", "gateway"]);
let started = false;
let sseSource = null;
let sseWanted = false;

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

function onHello(snap) {
  if (!snap || typeof snap !== "object") return;
  for (const key of ["browser", "chat", "workspace", "dashboard", "live", "scheduler", "gateway"]) {
    if (snap[key] !== undefined) {
      try {
        lastState.set(key, snap[key]);
      } catch (_) {}
      emit(key, snap[key]);
    }
  }
}

function onServerPush(channel, data) {
  if (typeof channel !== "string") return;
  // Chat run frames also ride pushes: "run" replays to refreshed tabs,
  // "chat_run" is the live progress (chat.js listens via onWsRaw too).
  if (channel === "run" || channel === "chat_run") {
    emit(channel, data);
    return;
  }
  if (channel === "chat_done") {
    emit("chat_done", data);
    return;
  }
  if (STATE_TYPES.has(channel)) {
    try {
      lastState.set(channel, data);
    } catch (_) {}
  }
  emit(channel, data);
}

export function startEvents() {
  if (started) return;
  started = true;
  onWsHello(onHello);
  onWsPush(onServerPush);
  onWsStatus(onTransportStatus);
  // Owner gate: a dead session surfaces as WS auth failure (ws.js sends
  // the tab to login) — this probe only catches the cookie-less case early.
  try {
    ensureWs().catch(() => {});
  } catch (_) {}
  // If the WS upgrade is already known-blocked (proxy/funnel), start SSE
  // right away instead of waiting for the status callback.
  try {
    if (isHttpFallback()) startSseFallback();
  } catch (_) {}
}

function onTransportStatus(online) {
  try {
    if (!online && isHttpFallback()) startSseFallback();
    else if (online) stopSseFallback();
  } catch (_) {}
}

function startSseFallback() {
  // SSE fallback: same onPush contract over /api/events (plain HTTPS GET,
  // passes proxies that drop the WS upgrade). One source per page.
  if (sseWanted) return;
  sseWanted = true;
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
      // EventSource retries natively; a hard failure just stays quiet —
      // the WS background retry may still recover the socket.
    };
  } catch (_) {}
}

function stopSseFallback() {
  sseWanted = false;
  try {
    if (sseSource) sseSource.close();
  } catch (_) {}
  sseSource = null;
}
