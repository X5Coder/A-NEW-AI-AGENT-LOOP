/**
 * bridge.js — X5Agent WhatsApp loopback bridge (Baileys).
 *
 * Owns: one Baileys socket + loopback HTTP surface for Python.
 * Binds 127.0.0.1 ONLY (never exposed). Auth state lives in AUTH_DIR.
 * States: idle | qr | pairing | reconnecting | connected | failed
 *
 * Endpoints:
 *   GET  /health                    -> { ok: true }
 *   GET  /status                    -> { state, phone, qr, qr_at, pairing, pairing_at, error }
 *   POST /connect { phone? }        -> boot socket (pairing code requested only via /pair)
 *   POST /pair    { phone }         -> request 8-char link code for the agent number
 *   GET  /inbox?take=N              -> drain collected inbound messages (max 100)
 *   POST /send   { to, text }       -> send a text message (linked only), returns { ok, id }
 *   POST /edit   { to, text, editId } -> edit our own sent message by id (15-min window)
 *   POST /read   { messageId }       -> mark one received message as read
 *   POST /typing { to, action }     -> composing|paused presence (typing indicator)
 *   POST /resolve { numbers }       -> resolve allowed phone numbers to LIDs
 *   POST /disconnect                -> logout, wipe auth, back to idle
 */
"use strict";

const http = require("http");
const {
  default: makeWASocket,
  useMultiFileAuthState,
  DisconnectReason,
  fetchLatestBaileysVersion,
  downloadMediaMessage,
} = require("@whiskeysockets/baileys");
const pino = require("pino");

/* Push channel to Python: wakeup on new messages, status on link changes.
   Fire-and-forget over loopback (5s cap, never throws, never blocks the
   socket loop). Python drains /inbox on wakeup, so nothing polls anymore. */
const PUSH_URL = process.env.BRIDGE_PUSH_URL || "";
const PUSH_SECRET = process.env.BRIDGE_PUSH_SECRET || "";
function pushToPython(payload) {
  if (!PUSH_URL || !PUSH_SECRET) return;
  try {
    const body = Buffer.from(JSON.stringify({ secret: PUSH_SECRET, ...(payload || {}) }));
    const u = new URL(PUSH_URL);
    const req = http.request(
      { host: "127.0.0.1", port: Number(u.port) || 80, path: u.pathname,
        method: "POST", headers: { "Content-Type": "application/json", "Content-Length": body.length },
        timeout: 5000 },
      (res) => { try { res.resume(); } catch (_) {} }
    );
    req.on("error", () => {});
    req.on("timeout", () => { try { req.destroy(); } catch (_) {} });
    req.end(body);
  } catch (_) {}
}
const pushWakeup = () => pushToPython({ kind: "wakeup" });
const pushStatus = () => pushToPython({ kind: "status" });

async function resolveLids(numbers) {
  if (!sock || status.state !== "connected" || !Array.isArray(numbers)) return [];
  const phones = [...new Set(numbers.map(digitsOnly).filter((n) => n.length >= 7))].slice(0, 50);
  if (!phones.length) return [];
  try {
    const entries = await sock.onWhatsApp(...phones.map((phone) => `${phone}@s.whatsapp.net`));
    if (!Array.isArray(entries)) return [];
    return entries.map((entry) => ({
      phone: jidUser(entry && entry.jid),
      lid: jidUser(entry && entry.lid),
    })).filter((entry) => /^\d{7,15}$/.test(entry.phone) && /^\d{7,30}$/.test(entry.lid));
  } catch (_) {
    return [];
  }
}

function rememberMessage(m) {
  const id = String(m?.key?.id || "");
  if (!id) return;
  recentMessages.set(id, m);
  while (recentMessages.size > INBOX_MAX) {
    recentMessages.delete(recentMessages.keys().next().value);
  }
}

/* Inbound inbox: collected from messages.upsert, drained by Python via /inbox. */
const inbox = [];
const INBOX_MAX = 200;
const MEDIA_MAX = 12 * 1024 * 1024;
const recentMessages = new Map();

function jidUser(jid) {
  return String(jid || "").split("@")[0].split(":")[0];
}

function msgText(m) {
  const msg = m.message || {};
  return (
    msg.conversation ||
    (msg.extendedTextMessage && msg.extendedTextMessage.text) ||
    (msg.imageMessage && msg.imageMessage.caption) ||
    (msg.videoMessage && msg.videoMessage.caption) ||
    ""
  );
}

function msgMedia(m) {
  const msg = m.message || {};
  if (msg.imageMessage) return { kind: "image", node: msg.imageMessage };
  if (msg.videoMessage) return { kind: "file", node: msg.videoMessage };
  if (msg.audioMessage) return { kind: "voice", node: msg.audioMessage };
  if (msg.documentMessage) return { kind: "file", node: msg.documentMessage };
  return null;
}

async function packMessage(m) {
  const key = m.key || {};
  const remote = String(key.remoteJid || "");
  if (remote === "status@broadcast") return null;
  const media = msgMedia(m);
  const item = {
    id: String(key.id || ""),
    from: jidUser(remote),
    chatJid: remote,
    group: remote.endsWith("@g.us") ? jidUser(remote) : "",
    participant: key.participant ? jidUser(key.participant) : "",
    senderPn: String(key.participantPn || key.senderPn || ""),
    remotePn: String(key.remoteJidAlt || ""),
    fromMe: Boolean(key.fromMe),
    name: String(m.pushName || ""),
    ts: Number(m.messageTimestamp || 0),
    kind: media ? media.kind : "text",
    text: String(msgText(m) || ""),
    fileName: "",
    mime: "",
    data: "",
  };
  if (!media) return item.text ? item : null;
  item.fileName = String(media.node.fileName || media.node.title || "");
  item.mime = String(media.node.mimetype || "");
  try {
    const buf = await downloadMediaMessage(m, "buffer", {});
    if (!buf || !buf.length) {
      item.kind = "text";
      item.text = item.text || "[attachment could not be downloaded]";
      return item;
    }
    if (buf.length > MEDIA_MAX) {
      item.kind = "text";
      item.text = item.text || "[file too large, max 12MB]";
      return item;
    }
    item.data = buf.toString("base64");
  } catch (_) {
    item.kind = "text";
    item.text = item.text || "[attachment could not be downloaded]";
  }
  return item;
}

const PORT = Number(process.env.BRIDGE_PORT || 0);
const AUTH_DIR = process.env.AUTH_DIR || "./wa_auth";

const status = {
  state: "idle",
  phone: "",
  linked: "",
  qr: "",
  qr_at: 0,
  pairing: "",
  pairing_at: 0,
  error: "",
};

let sock = null;
let starting = null;

function scheduleReconnect() {
  if (reconnectTimer || reconnectAttempts >= MAX_RECONNECT_ATTEMPTS) {
    if (reconnectAttempts >= MAX_RECONNECT_ATTEMPTS) {
      status.state = "failed";
      status.error = "Could not restore the WhatsApp session. Generate a new QR code to link again.";
    }
    return;
  }
  reconnectAttempts += 1;
  status.state = "reconnecting";
  status.qr = "";
  status.pairing = "";
  reconnectTimer = setTimeout(() => {
    reconnectTimer = null;
    bootSocket().catch(() => scheduleReconnect());
  }, 1500 * reconnectAttempts);
}
let reconnectTimer = null;
let reconnectAttempts = 0;
const MAX_RECONNECT_ATTEMPTS = 3;



async function requestPairingCodeWhenReady(socket, phone) {
  /* The websocket becomes usable shortly after socket creation. A QR update
     may arrive earlier, so retry the pairing request a bounded three times. */
  let lastError = null;
  for (let attempt = 0; attempt < 3; attempt += 1) {
    await new Promise((resolve) => setTimeout(resolve, 1200));
    try {
      const code = await socket.requestPairingCode(phone);
      if (code) return code;
    } catch (error) {
      lastError = error;
    }
  }
  throw lastError || new Error("Pairing code could not be created");
}

function digitsOnly(v) {
  return String(v || "").replace(/\D/g, "").slice(0, 15);
}

function recipientJid(v) {
  const raw = String(v || "").trim();
  if (/^\d{7,30}@(s\.whatsapp\.net|lid)$/.test(raw)) return raw;
  const digits = digitsOnly(raw);
  return digits.length >= 7 ? `${digits}@s.whatsapp.net` : "";
}

async function bootSocket() {
  if (sock) return sock;
  if (starting) return starting;
  starting = (async () => {
    const { state: authState, saveCreds } = await useMultiFileAuthState(AUTH_DIR);
    let version = undefined;
    try {
      // Bounded: a stalled fetch must never hang the boot (bundled fallback).
      const v = await Promise.race([
        fetchLatestBaileysVersion(),
        new Promise((_, reject) => setTimeout(() => reject(new Error("version timeout")), 8000)),
      ]);
      version = v.version;
    } catch (_) {
      // Offline/slow/blocked: Baileys falls back to its bundled version.
    }
    const s = makeWASocket({
      auth: authState,
      version,
      printQRInTerminal: false,
      logger: pino({ level: "silent" }),
      browser: ["X5Agent", "Chrome", "1.0"],
    });
    s.ev.on("creds.update", saveCreds);
    s.ev.on("messages.upsert", async ({ messages: incoming, type }) => {
      if (type !== "notify" || !Array.isArray(incoming)) return;
      let got = false;
      for (const m of incoming) {
        try {
          rememberMessage(m);
          const item = await packMessage(m);
          if (!item) continue;
          inbox.push(item);
          if (inbox.length > INBOX_MAX) inbox.splice(0, inbox.length - INBOX_MAX);
          got = true;
        } catch (_) {}
      }
      if (got) pushWakeup();
    });
    s.ev.on("connection.update", (u) => {
      if (u.qr) {
        status.qr = String(u.qr);
        status.qr_at = Date.now();
        status.state = "qr";
      }
      if (u.connection === "open") {
        reconnectAttempts = 0;
        status.state = "connected";
        status.qr = "";
        status.pairing = "";
        status.error = "";
        try {
          status.linked = jidUser(s.user && s.user.id);
        } catch (_) {
          status.linked = "";
        }
      } else if (u.connection === "close") {
        const code = u.lastDisconnect?.error?.output?.statusCode;
        const loggedOut = code === DisconnectReason.loggedOut;
        status.qr = "";
        status.pairing = "";
        sock = null;
        if (loggedOut) {
          status.state = "idle";
          status.phone = "";
          status.linked = "";
        } else {
          // A normal transport restart must reuse the stored credentials.
          // QR is only shown when WhatsApp explicitly requests one.
          status.error = "";
          scheduleReconnect();
        }
      } else if (u.connection === "connecting") {
        if (status.state === "idle") status.state = "reconnecting";
      }
      try { pushStatus(); } catch (_) {}
    });
    sock = s;
    starting = null;
    return s;
  })();
  try {
    return await starting;
  } finally {
    starting = null;
  }
}

function readBody(req) {
  return new Promise((resolve) => {
    let raw = "";
    req.on("data", (c) => {
      raw += c;
      if (raw.length > 4096) {
        try {
          req.destroy();
        } catch (_) {}
        resolve(null);
      }
    });
    req.on("end", () => {
      if (!raw) return resolve({});
      try {
        resolve(JSON.parse(raw));
      } catch (_) {
        resolve(null);
      }
    });
  });
}

function send(res, code, obj) {
  const body = JSON.stringify(obj);
  res.writeHead(code, { "Content-Type": "application/json", "Content-Length": Buffer.byteLength(body) });
  res.end(body);
}

const server = http.createServer(async (req, res) => {
  try {
    if (req.method === "GET" && req.url === "/health") return send(res, 200, { ok: true });
    if (req.method === "GET" && req.url === "/status") return send(res, 200, status);

    if (req.method === "POST" && req.url === "/connect") {
      const body = await readBody(req);
      if (body === null) return send(res, 400, { error: "bad json" });
      const phone = digitsOnly(body.phone);
      if (phone) status.phone = phone;
      try {
        await bootSocket();
      } catch (e) {
        status.state = "failed";
        status.error = String((e && e.message) || e || "boot failed").slice(0, 200);
        return send(res, 500, { error: status.error });
      }
      return send(res, 200, status);
    }

    if (req.method === "POST" && req.url === "/pair") {
      const body = await readBody(req);
      if (body === null) return send(res, 400, { error: "bad json" });
      const phone = digitsOnly(body.phone);
      if (phone.length < 7) return send(res, 400, { error: "agent number required (7-15 digits)" });
      status.phone = phone;
      try {
        const s = await bootSocket();
        if (!s.requestPairingCode) return send(res, 500, { error: "pairing unsupported by engine" });
        const code = await requestPairingCodeWhenReady(s, phone);
        status.pairing = String(code || "").replace(/[^A-Za-z0-9]/g, "");
        status.pairing_at = Date.now();
        status.state = "pairing";
        try { pushStatus(); } catch (_) {}
        return send(res, 200, status);
      } catch (e) {
        status.error = String((e && e.message) || e || "pair failed").slice(0, 200);
        return send(res, 500, { error: status.error });
      }
    }

    if (req.method === "GET" && req.url.startsWith("/inbox")) {
      let take = 50;
      try {
        const u = new URL(req.url, "http://x");
        take = Math.min(100, Math.max(1, parseInt(u.searchParams.get("take") || "50", 10) || 50));
      } catch (_) {}
      return send(res, 200, { messages: inbox.splice(0, take) });
    }

    if (req.method === "POST" && req.url === "/resolve") {
      const body = await readBody(req);
      if (body === null) return send(res, 400, { error: "bad json" });
      return send(res, 200, { mappings: await resolveLids(body.numbers) });
    }

    if (req.method === "POST" && req.url === "/send") {
      const body = await readBody(req);
      if (body === null) return send(res, 400, { error: "bad json" });
      const jid = recipientJid(body.to);
      const text = String(body.text || "").slice(0, 8000);
      if (!jid || !text) return send(res, 400, { error: "to + text required" });
      if (!sock) return send(res, 400, { error: "not linked" });
      try {
        const replyTo = String(body.replyTo || "");
        const quoted = /^[A-Za-z0-9_-]{1,128}$/.test(replyTo) ? recentMessages.get(replyTo) : null;
        const sent = await sock.sendMessage(jid, { text }, quoted ? { quoted } : undefined);
        return send(res, 200, { ok: true, id: String((sent && sent.key && sent.key.id) || "") });
      } catch (e) {
        return send(res, 500, { error: String((e && e.message) || e || "send failed").slice(0, 200) });
      }
    }

    if (req.method === "POST" && req.url === "/edit") {
      const body = await readBody(req);
      if (body === null) return send(res, 400, { error: "bad json" });
      const jid = recipientJid(body.to);
      const text = String(body.text || "").slice(0, 8000);
      const editId = String(body.editId || "");
      if (!jid || !text) return send(res, 400, { error: "to + text required" });
      if (!/^[A-Za-z0-9_-]{1,128}$/.test(editId)) return send(res, 400, { error: "editId required" });
      if (!sock) return send(res, 400, { error: "not linked" });
      try {
        await sock.sendMessage(jid, { text, edit: { remoteJid: jid, fromMe: true, id: editId } });
        return send(res, 200, { ok: true });
      } catch (e) {
        return send(res, 500, { error: String((e && e.message) || e || "edit failed").slice(0, 200) });
      }
    }

    if (req.method === "POST" && req.url === "/typing") {
      const body = await readBody(req);
      if (body === null) return send(res, 400, { error: "bad json" });
      const jid = recipientJid(body.to);
      const action = body.action === "paused" ? "paused" : "composing";
      if (!jid) return send(res, 400, { error: "to required" });
      if (!sock) return send(res, 400, { error: "not linked" });
      try {
        try {
          await sock.presenceSubscribe(jid);
        } catch (_) {}
        await sock.sendPresenceUpdate(action, jid);
        return send(res, 200, { ok: true });
      } catch (e) {
        return send(res, 500, { error: String((e && e.message) || e || "typing failed").slice(0, 200) });
      }
    }

        if (req.method === "POST" && req.url === "/read") {
      const body = await readBody(req);
      if (body === null) return send(res, 400, { error: "bad json" });
      const messageId = String(body.messageId || "");
      const received = /^[A-Za-z0-9_-]{1,128}$/.test(messageId) ? recentMessages.get(messageId) : null;
      if (!received?.key) return send(res, 404, { error: "message not available" });
      if (!sock) return send(res, 400, { error: "not linked" });
      try {
        await sock.readMessages([received.key]);
        return send(res, 200, { ok: true });
      } catch (e) {
        return send(res, 500, { error: String((e && e.message) || e || "read failed").slice(0, 200) });
      }
    }

    if (req.method === "POST" && req.url === "/disconnect") {
      if (reconnectTimer) clearTimeout(reconnectTimer);
      reconnectTimer = null;
      reconnectAttempts = 0;
      try {
        await sock?.logout().catch(() => {});
      } catch (_) {}
      sock = null;
      status.state = "idle";
      status.phone = "";
      status.linked = "";
      status.qr = "";
      status.pairing = "";
      status.error = "";
      try {
        const fs = require("fs");
        fs.rmSync(AUTH_DIR, { recursive: true, force: true });
      } catch (_) {}
      try { pushStatus(); } catch (_) {}
      return send(res, 200, status);
    }

    return send(res, 404, { error: "not found" });
  } catch (e) {
    return send(res, 500, { error: String((e && e.message) || e || "bridge error").slice(0, 200) });
  }
});

server.listen(PORT, "127.0.0.1", () => {
  const addr = server.address();
  console.log(`READY port=${addr && addr.port}`);
});
