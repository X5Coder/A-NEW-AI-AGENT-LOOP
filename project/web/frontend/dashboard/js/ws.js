/**
 * ws.js — the ONE socket for chat + pushes (replaces chat SSE + events SSE).
 *
 * Opens when the UI loads, closes when the tab closes. First message is
 * always {"op":"auth","token": localStorage x5_ws_token} (minted at login,
 * saved by login.html). wss:// is used automatically when the page itself
 * is https (funnel public links terminate TLS above the server).
 * Traffic is instant + bidirectional: ops up, replies + pushes down.
 */
"use strict";

const TOKEN_KEY = "x5_ws_token";
let sock = null;
let ready = null;
let nextId = 1;
const pending = new Map(); // id -> {resolve, reject}
const pushHandlers = new Set(); // fn(channel, data)
const rawHandlers = new Set(); // fn(msg) — chat progress frames
const helloHandlers = new Set(); // fn(hello)
let closedByUs = false;

export function getWsToken() {
  try {
    return localStorage.getItem(TOKEN_KEY) || "";
  } catch (_) {
    return "";
  }
}

export function setWsToken(t) {
  try {
    if (t) localStorage.setItem(TOKEN_KEY, t);
    else localStorage.removeItem(TOKEN_KEY);
  } catch (_) {}
}

function wsUrl() {
  try {
    const proto = location.protocol === "https:" ? "wss://" : "ws://";
    return proto + location.host + "/ws";
  } catch (_) {
    return "ws://127.0.0.1:7777/ws";
  }
}

export function onWsPush(fn) {
  if (typeof fn === "function") pushHandlers.add(fn);
}

export function onWsRaw(fn) {
  if (typeof fn === "function") rawHandlers.add(fn);
}

export function onWsHello(fn) {
  if (typeof fn === "function") helloHandlers.add(fn);
}

function dispatchHello(hello) {
  for (const fn of helloHandlers) {
    try {
      fn(hello || {});
    } catch (_) {}
  }
}

function dispatchPush(channel, data) {
  for (const fn of pushHandlers) {
    try {
      fn(channel, data);
    } catch (_) {}
  }
}

function dispatchRaw(msg) {
  for (const fn of rawHandlers) {
    try {
      fn(msg);
    } catch (_) {}
  }
}

function connect() {
  closedByUs = false;
  ready = new Promise((resolve) => {
    let ws;
    try {
      ws = new WebSocket(wsUrl());
    } catch (_) {
      setTimeout(connect, 3000);
      resolve(null);
      return;
    }
    sock = ws;
    let authed = false;
    ws.onopen = () => {
      try {
        ws.send(JSON.stringify({ op: "auth", token: getWsToken() }));
      } catch (_) {}
    };
    ws.onmessage = (e) => {
      let m;
      try {
        m = JSON.parse(e.data);
      } catch (_) {
        return;
      }
      if (!m || typeof m !== "object") return;
      if (m.type === "auth_ok") {
        authed = true;
        dispatchHello(m.hello || {});
        resolve(ws);
        return;
      }
      if (m.type === "push") {
        dispatchPush(m.channel, m.data);
        return;
      }
      if (m.id !== undefined && pending.has(m.id)) {
        const p = pending.get(m.id);
        pending.delete(m.id);
        try {
          p.resolve(m);
        } catch (_) {}
        return;
      }
      // Chat progress frames (event/event_start/token/question/live/done...)
      if (typeof m.type === "string") dispatchRaw(m);
    };
    const dead = () => {
      if (!authed) {
        // Bad/expired token — back to login instead of retrying forever.
        try {
          setWsToken("");
        } catch (_) {}
        try {
          if (location.pathname !== "/login.html") location.href = "/login.html";
        } catch (_) {}
        resolve(null);
        return;
      }
      try {
        ws.close();
      } catch (_) {}
      sock = null;
      setTimeout(connect, 3000);
      resolve(null);
    };
    ws.onerror = dead;
    ws.onclose = () => {
      if (closedByUs) {
        resolve(null);
        return;
      }
      // Reconnect with fresh hello resync (same as old SSE behavior).
      sock = null;
      for (const [, p] of pending) {
        try {
          p.reject(new Error("ws closed"));
        } catch (_) {}
      }
      pending.clear();
      setTimeout(connect, 3000);
      resolve(null);
    };
  });
  return ready;
}

export function ensureWs() {
  if (sock && sock.readyState === 1) return Promise.resolve(sock);
  if (ready) return ready.then(() => (sock && sock.readyState === 1 ? sock : null));
  return connect();
}

export function sendOp(op, params) {
  return ensureWs().then(
    (ws) =>
      new Promise((resolve, reject) => {
        if (!ws || ws.readyState !== 1) {
          reject(new Error("ws not connected"));
          return;
        }
        const id = nextId++;
        pending.set(id, { resolve, reject });
        try {
          ws.send(JSON.stringify({ id, op, params: params || {} }));
        } catch (e) {
          pending.delete(id);
          reject(e);
          return;
        }
        setTimeout(() => {
          if (pending.has(id)) {
            pending.delete(id);
            reject(new Error("ws timeout"));
          }
        }, 60000);
      })
  );
}

export function closeWs() {
  closedByUs = true;
  try {
    if (sock) sock.close(1000, "tab closed");
  } catch (_) {}
  sock = null;
}

try {
  window.addEventListener("beforeunload", closeWs);
} catch (_) {}

// Open on UI load (import side-effect, like old startEvents).
connect();
