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
const statusHandlers = new Set(); // fn(online:boolean) — transport state
let closedByUs = false;
let wsOnline = false; // true only after auth_ok on the socket
let httpFallback = false; // true when the WS upgrade never completes (proxy/funnel drops it)
let everAuthed = false; // any auth_ok on this page load (token is valid)
let lastActivity = 0; // last inbound frame: freshly-opened sockets skip heartbeat
let heartbeatTimer = null;
// Bounded reconnect: an endless 3s retry loop floods the console and the
// server (Safari/iOS may refuse wss upgrades the server cannot fix — e.g.
// extension negotiation or browser policy). After MAX_ATTEMPTS consecutive
// failures we park quietly on HTTP/SSE fallback and retry rarely.
let failCount = 0;
let parked = false; // true when reconnects are parked (quiet fallback mode)
let parkedTimer = null;
const MAX_ATTEMPTS = 5;
const PARK_MS = 60000;

function retryDelay() {
  // Exponential backoff: 3s, 6s, 12s, 24s, then park.
  const step = Math.min(failCount, 4);
  return 3000 * Math.pow(2, step);
}

function noteFailure() {
  failCount += 1;
  return failCount;
}

function noteSuccess() {
  failCount = 0;
  parked = false;
  try {
    if (parkedTimer) clearTimeout(parkedTimer);
  } catch (_) {}
  parkedTimer = null;
}

function scheduleReconnect() {
  // Returns the delay used (0 = parked, no reconnect scheduled).
  if (failCount >= MAX_ATTEMPTS) {
    // Park: stay quiet on HTTP/SSE fallback, probe rarely in background.
    parked = true;
    httpFallback = true;
    setOnline(false);
    try {
      if (parkedTimer) clearTimeout(parkedTimer);
    } catch (_) {}
    try {
      parkedTimer = setTimeout(() => {
        parked = false;
        failCount = 0;
        connect();
      }, PARK_MS);
    } catch (_) {}
    return 0;
  }
  const delay = retryDelay();
  setTimeout(connect, delay);
  return delay;
}

export function isWsParked() {
  return parked;
}

export function retryWsNow() {
  // Manual retry (e.g. from a status pill): reset the budget and reconnect.
  try {
    if (parkedTimer) clearTimeout(parkedTimer);
  } catch (_) {}
  parkedTimer = null;
  parked = false;
  failCount = 0;
  connect();
}

function noteActivity() {
  try {
    lastActivity = Date.now();
  } catch (_) {}
}

function stopHeartbeat() {
  try {
    if (heartbeatTimer) clearInterval(heartbeatTimer);
  } catch (_) {}
  heartbeatTimer = null;
}

function startHeartbeat() {
  // Client heartbeat: a ping op every 25s keeps the funnel/proxy route and
  // the server idle-watchdog (75s) from killing a quiet-but-healthy socket.
  // No response = a dead socket: close it so the normal reconnect path runs.
  stopHeartbeat();
  noteActivity();
  try {
    heartbeatTimer = setInterval(() => {
      try {
        if (!sock || sock.readyState !== 1) return;
        if (!wsOnline) return;
        if (Date.now() - lastActivity < 25000) {
          sendOp("ping", {}).catch(() => {});
          return;
        }
        // Silent >25s on an "open" socket: ask once, drop when unanswered.
        let answered = false;
        const probe = sendOp("ping", {});
        probe.then(() => { answered = true; }, () => {});
        setTimeout(() => {
          if (!answered) {
            try { sock.close(4000, "heartbeat timeout"); } catch (_) {}
          }
        }, 10000);
      } catch (_) {}
    }, 25000);
  } catch (_) {}
}

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

export function onWsStatus(fn) {
  if (typeof fn === "function") statusHandlers.add(fn);
}

export function isWsOnline() {
  return wsOnline;
}

export function isHttpFallback() {
  return httpFallback;
}

function setOnline(on) {
  if (wsOnline === on) return;
  wsOnline = on;
  for (const fn of statusHandlers) {
    try {
      fn(on);
    } catch (_) {}
  }
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
      noteFailure();
      scheduleReconnect();
      resolve(null);
      return;
    }
    sock = ws;
    let authed = false;
    let opened = false;
    // Upgrade watchdog: some proxies (funnel included) answer HTTP fine but
    // never complete the WS upgrade — onerror/onclose fire without onopen.
    // That is a transport block, NOT a bad token: never log out for it.
    const upgradeTimer = setTimeout(() => {
      if (!opened && !authed) {
        // NOTE: no httpFallback/close here — dead()/onclose below own the
        // single failure path (bounded retry + park). Closing here would
        // double-count one failure as two.
        try {
          ws.close();
        } catch (_) {}
      }
    }, 8000);
    const clearUpgradeTimer = () => {
      try {
        clearTimeout(upgradeTimer);
      } catch (_) {}
    };
    ws.onopen = () => {
      opened = true;
      try {
        ws.send(JSON.stringify({ op: "auth", token: getWsToken() }));
      } catch (_) {}
    };
    ws.onmessage = (e) => {
      try {
        if (typeof e.data === "string") noteActivity();
      } catch (_) {}
      let m;
      try {
        m = JSON.parse(e.data);
      } catch (_) {
        return;
      }
      if (!m || typeof m !== "object") return;
      if (m.type === "auth_ok") {
        authed = true;
        everAuthed = true;
        httpFallback = false;
        clearUpgradeTimer();
        noteSuccess();
        setOnline(true);
        startHeartbeat();
        dispatchHello(m.hello || {});
        resolve(ws);
        return;
      }
      if (m.type === "auth_error") {
        // Server explicitly refused the token (expired/logout) — that and
        // only that means back to login.
        clearUpgradeTimer();
        try {
          setWsToken("");
        } catch (_) {}
        try {
          if (location.pathname !== "/login.html") location.href = "/login.html";
        } catch (_) {}
        resolve(null);
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
      clearUpgradeTimer();
      if (!opened && !authed) {
        // Upgrade never completed (proxy/funnel/browser drops WS): transport
        // block, NOT a bad token. Bounded retry with backoff, then park
        // quietly on HTTP/SSE fallback (same page still works).
        noteFailure();
        httpFallback = true;
        setOnline(false);
        try {
          ws.close();
        } catch (_) {}
        sock = null;
        scheduleReconnect();
        resolve(null);
        return;
      }
      if (!authed) {
        // Opened but no auth_ok: only log out when the token itself is
        // known-bad (server sent auth_error, handled above) or when a
        // previous auth already proved it on this page load. Otherwise it
        // is a dropped connection mid-handshake — retry quietly.
        if (everAuthed) {
          try {
            ws.close();
          } catch (_) {}
          sock = null;
          noteFailure();
          scheduleReconnect();
          resolve(null);
          return;
        }
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
      setOnline(false);
      noteFailure();
      scheduleReconnect();
      resolve(null);
    };
    ws.onerror = dead;
    ws.onclose = () => {
      clearUpgradeTimer();
      stopHeartbeat();
      if (closedByUs) {
        setOnline(false);
        resolve(null);
        return;
      }
      if (!opened && !authed) {
        // Same transport-block case as dead(): bounded retry, then park.
        // Guard: dead() above may have already counted this failure
        // (onerror fires before onclose for the same drop) — count once.
        if (sock === ws) {
          noteFailure();
          httpFallback = true;
          setOnline(false);
          sock = null;
          for (const [, p] of pending) {
            try {
              p.reject(new Error("ws closed"));
            } catch (_) {}
          }
          pending.clear();
          scheduleReconnect();
        }
        resolve(null);
        return;
      }
      // Reconnect with fresh hello resync (same as old SSE behavior).
      sock = null;
      setOnline(false);
      for (const [, p] of pending) {
        try {
          p.reject(new Error("ws closed"));
        } catch (_) {}
      }
      pending.clear();
      noteFailure();
      scheduleReconnect();
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

// HTTP fallback map: when the WS upgrade is blocked (proxy/funnel), the
// same ops ride plain POSTs so the console keeps working. chat_stream has
// no POST-streaming fallback here (SSE endpoint still exists server-side);
// its WS-only frames are awaited via onWsRaw when the socket is up.
const HTTP_OPS = {
  chat_answer: { method: "POST", url: "/api/chat_answer" },
  chat_cancel: { method: "POST", url: "/api/chat_cancel" },
  chat_active: { method: "GET", url: "/api/chat_active" },
  chat_frames: { method: "GET", url: "/api/chat_frames" },
  ping: null, // local-only, no HTTP equivalent needed
};

function httpFallbackOp(op, params) {
  const route = HTTP_OPS[op];
  if (!route) return Promise.reject(new Error("ws not connected"));
  const url = route.url;
  const init =
    route.method === "GET" ? { cache: "no-store" } : {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(params || {}),
    };
  return fetch(url, init).then((r) =>
    r.json().then((d) => ({ id: 0, ok: r.ok, data: d }))
  );
}

export function sendOp(op, params) {
  return ensureWs().then(
    (ws) =>
      new Promise((resolve, reject) => {
        if (!ws || ws.readyState !== 1) {
          // WS blocked (proxy/funnel): same op over HTTP, no logout.
          httpFallbackOp(op, params).then(resolve, reject);
          return;
        }
        const id = nextId++;
        pending.set(id, { resolve, reject });
        try {
          ws.send(JSON.stringify({ id, op, params: params || {} }));
        } catch (e) {
          pending.delete(id);
          httpFallbackOp(op, params).then(resolve, () => reject(e));
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
  stopHeartbeat();
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
