/**
 * chat.js — X5Agent agent chat
 * Owns: real history (/api/conversation), send (/api/chat),
 * attach menu (image/video metadata), voice input, thinking + typing.
 */
"use strict";

import { openModal, closeModal, toast } from "./ui.js?v=8";
import { navigate } from "./navigation.js?v=10";
import { onPush } from "./events.js?v=1";
import { onWsRaw, sendOp } from "./ws.js?v=3";
import { remoteLiveUrl } from "./live-url.js?v=1";

// WS answer/cancel helpers: same server ops as POST /api/chat_answer|cancel.
async function wsAnswer(body) {
  const r = await sendOp("chat_answer", body || {});
  const ok = Boolean(r && r.ok);
  return { ok, status: ok ? 200 : 400 };
}
async function wsCancel() {
  try {
    await sendOp("chat_cancel", {});
  } catch (_) {}
}

const MAX_MESSAGE = 4000;
/* no chat avatars — assistant messages are content-only */

let chatScroll = null;
let chatInner = null;
let composerInput = null;
let sendBtn = null;
let attachPreview = null;
let composerAttachBox = null;
let pendingAttachments = [];
let mentionMenu = null;

const MODE_KEY = "x5-agent-mode";
let agentMode = "restricted";
try {
  agentMode = localStorage.getItem(MODE_KEY) === "free" ? "free" : "restricted";
} catch (_) {
  agentMode = "restricted";
}
let pendingQuestion = null;
let activeThinking = null;
let lastUserText = "";
let lastUserEl = null;
let activeRun = null;
// Run generation: every stream turn owns a token so a superseded turn
// (STOP / fresh answer / newer message) exits quietly — no error text,
// no blocking fallback, no state clobbering.
let runGen = 0;

function scrollToBottom(smooth = true) {
  if (!chatScroll) return;
  try {
    chatScroll.scrollTo({ top: chatScroll.scrollHeight, behavior: smooth ? "smooth" : "auto" });
  } catch (_) {
    chatScroll.scrollTop = chatScroll.scrollHeight;
  }
}

function escapeHtml(text) {
  return String(text).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

function humanizeEvent(text) {
  // Never show raw storage IDs — friendly names only.
  const t = String(text || "");
  if (/^analyzing\s*·/i.test(t)) return "Analyzing · Your Image";
  if (/^analyzing video\s*·/i.test(t)) return "Analyzing video · Your Video";
  if (/^transcribing video\s*·/i.test(t)) return "Transcribing video · Your Video";
  if (/^checking (video job|transcribe job)\s*·/i.test(t)) return t.split("·")[0].trim();
  return t;
}

function isRtlStrong(ch) {
  // Numeric code-point ranges (no literal glyphs): Hebrew, Arabic blocks,
  // Arabic Supplement/Extended-A, Presentation Forms A/B.
  const c = String(ch || "").codePointAt(0) || 0;
  return (c >= 0x0590 && c <= 0x05ff) || (c >= 0x0600 && c <= 0x06ff) ||
    (c >= 0x0750 && c <= 0x077f) || (c >= 0x08a0 && c <= 0x08ff) ||
    (c >= 0xfb50 && c <= 0xfdff) || (c >= 0xfe70 && c <= 0xfeff);
}

function isLtrStrong(ch) {
  // Basic Latin + Latin/Greek/Cyrillic Extended (numeric ranges only).
  const c = String(ch || "").codePointAt(0) || 0;
  if ((c >= 65 && c <= 90) || (c >= 97 && c <= 122)) return true;
  return (c >= 0x00c0 && c <= 0x024f) || (c >= 0x0370 && c <= 0x03ff) ||
    (c >= 0x0400 && c <= 0x04ff) || (c >= 0x1e00 && c <= 0x1eff);
}

function autoDir(content) {
  // Explicit direction for one rich block (first-strong-char rule), so list
  // markers and table cells sit on the right side from the very first typed
  // frame — an empty dir=auto node would default to LTR (bullet on the
  // left, cell text jumping sides). Tags/entities are skipped so markup
  // never fakes the direction.
  try {
    const text = String(content || "")
      .replace(/<[^>]*>/g, "")
      .replace(/&[A-Za-z0-9#]+;/g, "");
    for (const ch of text) {
      if (isRtlStrong(ch)) return "rtl";
      if (isLtrStrong(ch)) return "ltr";
    }
  } catch (_) {}
  return "auto";
}

const CODE_LANG_NAMES = {
  js: "JavaScript", javascript: "JavaScript", ts: "TypeScript", typescript: "TypeScript",
  py: "Python", python: "Python", html: "HTML", xml: "XML", css: "CSS", json: "JSON",
  yaml: "YAML", yml: "YAML", sh: "Shell", bash: "Shell", shell: "Shell", sql: "SQL",
  java: "Java", c: "C", cpp: "C++", "c++": "C++", cs: "C#", "c#": "C#", go: "Go",
  rust: "Rust", php: "PHP", rb: "Ruby", ruby: "Ruby", swift: "Swift", kotlin: "Kotlin",
  md: "Markdown", markdown: "Markdown", txt: "Text", text: "Text",
};

function codeLangOf(tag) {
  // Display name for a fence tag (bounded). Unknown tags show as-is.
  try {
    const raw = String(tag || "").trim().slice(0, 16);
    const t = raw.toLowerCase();
    if (CODE_LANG_NAMES[t]) return { key: t, label: CODE_LANG_NAMES[t] };
    if (!t) return { key: "", label: "code" };
    return { key: t, label: raw };
  } catch (_) {
    return { key: "", label: "code" };
  }
}

function codeFamily(key) {
  // One small grammar per family; everything else shares the C-like one.
  if (!key) return "code";
  if (key === "py" || key === "python" || key === "rb" || key === "ruby") return "python";
  if (key === "html" || key === "xml") return "markup";
  if (key === "css") return "css";
  if (key === "sh" || key === "bash" || key === "shell") return "shell";
  if (key === "json" || key === "yaml" || key === "yml") return "data";
  if (key === "md" || key === "markdown" || key === "txt" || key === "text") return "plain";
  return "code";
}

const KW_CODE = new Set("break case catch class const continue debugger default delete do else export extends false finally for function if import in instanceof new null return super switch this throw true try typeof var void while with yield async await static get set of from as let enum implements interface package private protected public".split(" "));
const KW_PY = new Set("False None True and as assert async await break class continue def del elif else except finally for from global if import in is lambda nonlocal not or pass raise return try while with yield".split(" "));
const KW_SHELL = new Set("if then else elif fi for while until do done case esac function select time in".split(" "));

function runCodeTokens(s, re, classify) {
  // Walk one grammar over the source, escaping every piece. Never throws.
  const out = [];
  let last = 0;
  let m = null;
  try {
    re.lastIndex = 0;
    while ((m = re.exec(s)) !== null) {
      if (m.index > last) out.push(escapeHtml(s.slice(last, m.index)));
      let cls = "";
      try {
        cls = classify(m, s, re.lastIndex) || "";
      } catch (_) {
        cls = "";
      }
      out.push(cls ? `<span class="${cls}">${escapeHtml(m[0])}</span>` : escapeHtml(m[0]));
      last = re.lastIndex;
      if (!m[0].length) re.lastIndex += 1;
    }
  } catch (_) {}
  try {
    if (last < s.length) out.push(escapeHtml(s.slice(last)));
  } catch (_) {}
  return out.join("");
}

function highlightCode(src, family) {
  // Tiny dependency-free highlighter (comments/strings/numbers/keywords +
  // function names). Bounded: oversized blocks stay plain text. Never throws.
  try {
    const s = String(src ?? "");
    if (!s || s.length > 20000 || family === "plain") return escapeHtml(s);
    const isFnCall = (rest) => /^\s*\(/.test(rest);
    if (family === "python") {
      const re = /("""[\s\S]*?(?:"""|$)|'''[\s\S]*?(?:'''|$))|('(?:[^'\\\n]|\\.)*'|"(?:[^"\\\n]|\\.)*")|(#[^\n]*)|(\b\d+(?:\.\d+)?(?:[eE][+-]?\d+)?\b)|([A-Za-z_]\w*)/g;
      return runCodeTokens(s, re, (m, full, idx) => {
        if (m[1] || m[2]) return "tok-str";
        if (m[3]) return "tok-com";
        if (m[4]) return "tok-num";
        if (m[5]) return KW_PY.has(m[5]) ? "tok-kw" : (isFnCall(full.slice(idx)) ? "tok-fn" : "");
        return "";
      });
    }
    if (family === "shell") {
      const re = /(#[^\n]*)|('(?:[^'\\\n]|\\.)*'|"(?:[^"\\\n]|\\.)*"|`(?:[^`\\]|\\.)*`)|(--?[A-Za-z][\w-]*)|(\b\d+(?:\.\d+)?\b)|(\$[\w#?$!*]+)|([A-Za-z_]\w*)/g;
      return runCodeTokens(s, re, (m) => {
        if (m[1]) return "tok-com";
        if (m[2] || m[5]) return "tok-str";
        if (m[3]) return "tok-attr";
        if (m[4]) return "tok-num";
        if (m[6]) return KW_SHELL.has(m[6]) ? "tok-kw" : "";
        return "";
      });
    }
    if (family === "markup") {
      const re = /(<!--[\s\S]*?(?:-->|$))|(<\/?[A-Za-z][\w:.-]*|\/?>)|([A-Za-z_:][\w:.-]*(?=\s*=\s*["']))|('(?:[^'\\\n]|\\.)*'|"(?:[^"\\\n]|\\.)*")/g;
      return runCodeTokens(s, re, (m) => {
        if (m[1]) return "tok-com";
        if (m[2]) return "tok-tag";
        if (m[3]) return "tok-attr";
        if (m[4]) return "tok-str";
        return "";
      });
    }
    if (family === "css") {
      const re = /(\/\*[\s\S]*?(?:\*\/|$))|("(?:[^"\\\n]|\\.)*"|'(?:[^'\\\n]|\\.)*')|(@[\w-]+)|(#[0-9a-fA-F]{3,8}\b)|(\b\d+(?:\.\d+)?(?:px|em|rem|%|s|ms|vh|vw|ch|fr|deg)?\b)|([A-Za-z-]+(?=\s*:))/g;
      return runCodeTokens(s, re, (m) => {
        if (m[1]) return "tok-com";
        if (m[2]) return "tok-str";
        if (m[3]) return "tok-kw";
        if (m[4] || m[5]) return "tok-num";
        if (m[6]) return "tok-attr";
        return "";
      });
    }
    if (family === "data") {
      const re = /("(?:[^"\\\n]|\\.)*"(?=\s*:))|("(?:[^"\\\n]|\\.)*"|'(?:[^'\\\n]|\\.)*')|(\b\d+(?:\.\d+)?(?:[eE][+-]?\d+)?\b)|(\btrue\b|\bfalse\b|\bnull\b)/g;
      return runCodeTokens(s, re, (m) => {
        if (m[1]) return "tok-attr";
        if (m[2]) return "tok-str";
        if (m[3]) return "tok-num";
        if (m[4]) return "tok-kw";
        return "";
      });
    }
    const re = /(\/\*[\s\S]*?(?:\*\/|$))|(\/\/[^\n]*)|(`(?:[^`\\]|\\.)*`?|'(?:[^'\\\n]|\\.)*'|"(?:[^"\\\n]|\\.)*")|(\b0[xX][0-9a-fA-F]+\b|\b\d+(?:\.\d+)?(?:[eE][+-]?\d+)?\b)|([A-Za-z_$][\w$]*)/g;
    return runCodeTokens(s, re, (m, full, idx) => {
      if (m[1] || m[2]) return "tok-com";
      if (m[3]) return "tok-str";
      if (m[4]) return "tok-num";
      if (m[5]) return KW_CODE.has(m[5]) ? "tok-kw" : (isFnCall(full.slice(idx)) ? "tok-fn" : "");
      return "";
    });
  } catch (_) {
    try {
      return escapeHtml(String(src ?? ""));
    } catch (_) {
      return "";
    }
  }
}

const COPY_SVG = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2" /><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" /></svg>';

  // WhatsApp-style voice record bubble (agent TTS output)
const MV_PLAY_SVG = '<svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M8 5.5v13a1 1 0 0 0 1.53.85l10.2-6.5a1 1 0 0 0 0-1.7L9.53 4.65A1 1 0 0 0 8 5.5z"/></svg>';
const MV_PAUSE_SVG = '<svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><rect x="6.2" y="5" width="4" height="14" rx="1.4"/><rect x="13.8" y="5" width="4" height="14" rx="1.4"/></svg>';
const MV_BARS = 34;
let mvPlaying = null;

function mvStopOther(current) {
  // One voice record plays at a time - starting a new one pauses the rest.
  try {
    if (mvPlaying && mvPlaying !== current) mvPlaying.pause();
  } catch (_) {}
  try {
    document.querySelectorAll(".msg-voice").forEach((b) => {
      if (b !== current && typeof b._mvSetPlaying === "function") b._mvSetPlaying(false);
    });
  } catch (_) {}
}

function mvHashBars(seed, n) {
  // Deterministic bar heights per record so the shape is stable live and
  // after refresh (no real waveform fetch needed). Never throws.
  const out = [];
  try {
    let h = 2166136261;
    const s = String(seed || "v");
    for (let i = 0; i < s.length; i++) {
      h ^= s.charCodeAt(i);
      h = Math.imul(h, 16777619);
    }
    for (let i = 0; i < n; i++) {
      h ^= h << 13;
      h = Math.imul(h, 0x5bd1e995);
      h ^= h >>> 15;
      const v = (h >>> 0) / 4294967295;
      // Tall middle, tapered ends (voice-note look), min height visible.
      const taper = Math.sin((Math.PI * (i + 0.5)) / n) * 0.65 + 0.35;
      out.push(Math.max(0.14, Math.min(1, (0.25 + v * 0.75) * taper)));
    }
  } catch (_) {
    for (let i = 0; i < n; i++) out.push(0.4);
  }
  return out;
}

function mvFormat(sec) {
  try {
    const s = Math.max(0, Math.round(Number(sec) || 0));
    return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
  } catch (_) {
    return "0:00";
  }
}

function mvUrl(name) {
  return "/api/audio?path=" + encodeURIComponent(name);
}

function mvSetPlayingVisual(box, btn, playing) {
  try {
    box.classList.toggle("playing", !!playing);
    btn.innerHTML = playing ? MV_PAUSE_SVG : MV_PLAY_SVG;
    btn.setAttribute("aria-label", playing ? "Pause voice message" : "Play voice message");
    btn.setAttribute("aria-pressed", playing ? "true" : "false");
  } catch (_) {}
}

function createVoiceBubble(name, durationSec) {
  // Standalone record bubble: round play button + wave bars that fill as it
  // plays + duration label. Click/drag on the wave seeks. Never throws.
  const box = document.createElement("div");
  box.className = "msg-voice";
  box.setAttribute("dir", "ltr");
  try {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "mv-play";
    btn.setAttribute("aria-label", "Play voice message");
    btn.setAttribute("aria-pressed", "false");
    btn.innerHTML = MV_PLAY_SVG;

    const wave = document.createElement("div");
    wave.className = "mv-wave";
    wave.setAttribute("role", "slider");
    wave.setAttribute("aria-label", "Voice message progress");
    wave.setAttribute("aria-valuemin", "0");
    wave.setAttribute("aria-valuemax", "100");
    wave.setAttribute("aria-valuenow", "0");
    wave.tabIndex = 0;
    const heights = mvHashBars(name, MV_BARS);
    const bars = heights.map((hgt) => {
      const b = document.createElement("span");
      b.className = "mv-bar";
      b.style.transform = `scaleY(${hgt.toFixed(3)})`;
      wave.appendChild(b);
      return b;
    });

    const time = document.createElement("span");
    time.className = "mv-time";
    time.textContent = mvFormat(durationSec);

    const audioEl = document.createElement("audio");
    audioEl.preload = "metadata";
    audioEl.src = mvUrl(name);
    box.appendChild(audioEl);
    if (!audioEl.isConnected) {
      try {
        audioEl.load();
      } catch (_) {}
    }

    const setPlaying = (playing) => mvSetPlayingVisual(box, btn, playing);
    box._mvSetPlaying = setPlaying;

    const paint = (frac) => {
      try {
        const cut = Math.max(0, Math.min(1, Number(frac) || 0));
        const lit = Math.floor(cut * bars.length);
        bars.forEach((b, i) => b.classList.toggle("on", i < lit));
        wave.setAttribute("aria-valuenow", String(Math.round(cut * 100)));
      } catch (_) {}
    };

    const total = () => {
      try {
        const d = Number(audioEl.duration);
        if (Number.isFinite(d) && d > 0) return d;
      } catch (_) {}
      const f = Number(durationSec);
      return Number.isFinite(f) && f > 0 ? f : 0;
    };

    const tick = () => {
      try {
        const d = total();
        time.textContent = audioEl.paused && audioEl.currentTime <= 0
          ? mvFormat(d)
          : `${mvFormat(audioEl.currentTime)} / ${mvFormat(d)}`;
        paint(d > 0 ? audioEl.currentTime / d : 0);
      } catch (_) {}
    };

    const seekToFrac = (clientX) => {
      try {
        const r = wave.getBoundingClientRect();
        if (!r || r.width <= 0) return;
        const frac = Math.max(0, Math.min(1, (clientX - r.left) / r.width));
        const d = total();
        if (d > 0) audioEl.currentTime = frac * d;
        paint(frac);
      } catch (_) {}
    };

    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      try {
        if (audioEl.paused) {
          mvStopOther(box);
          audioEl.play().catch(() => setPlaying(false));
        } else audioEl.pause();
      } catch (_) {}
    });
    audioEl.addEventListener("play", () => {
      mvPlaying = audioEl;
      setPlaying(true);
      tick();
    });
    audioEl.addEventListener("pause", () => {
      if (mvPlaying === audioEl) mvPlaying = null;
      setPlaying(false);
      tick();
    });
    audioEl.addEventListener("timeupdate", tick);
    audioEl.addEventListener("loadedmetadata", tick);
    audioEl.addEventListener("ended", () => {
      try {
        audioEl.currentTime = 0;
      } catch (_) {}
      setPlaying(false);
      tick();
    });
    audioEl.addEventListener("error", () => {
      box.classList.add("broken");
      btn.disabled = true;
      time.textContent = "failed";
    }, { once: true });

    let dragging = false;
    wave.addEventListener("pointerdown", (e) => {
      dragging = true;
      try {
        wave.setPointerCapture(e.pointerId);
      } catch (_) {}
      seekToFrac(e.clientX);
    });
    wave.addEventListener("pointermove", (e) => {
      if (dragging) seekToFrac(e.clientX);
    });
    wave.addEventListener("pointerup", () => { dragging = false; });
    wave.addEventListener("pointercancel", () => { dragging = false; });
    wave.addEventListener("keydown", (e) => {
      try {
        const d = total();
        if (e.key === "ArrowRight" || e.key === "ArrowLeft") {
          e.preventDefault();
          const step = e.key === "ArrowRight" ? 2 : -2;
          if (d > 0) audioEl.currentTime = Math.max(0, Math.min(d, audioEl.currentTime + step));
          tick();
        } else if (e.key === " " || e.key === "Enter") {
          e.preventDefault();
          btn.click();
        }
      } catch (_) {}
    });

    box.appendChild(btn);
    box.appendChild(wave);
    box.appendChild(time);
    box.appendChild(audioEl);
  } catch (_) {}
  return box;
}

function appendFinalVoice(el, audioList) {
  // Voice records spoken via text_to_speech, shown as standalone bubbles
  // under the reply (live and after refresh). Accepts the engine's
  // [{audio, duration, voice, lang}] items (or bare filename strings).
  const list = (Array.isArray(audioList) ? audioList : [])
    .map((item) => {
      if (typeof item === "string") return { audio: item, duration: 0 };
      if (item && typeof item.audio === "string") {
        return { audio: item.audio, duration: Number(item.duration) || 0 };
      }
      return null;
    })
    .filter((v) => v && v.audio)
    .slice(0, 4);
  if (!el || !list.length) return;
  const content = el.querySelector(".msg-content");
  if (!content || content.querySelector(":scope > .msg-final-voice")) return;
  const wrap = document.createElement("div");
  wrap.className = "msg-final-voice";
  list.forEach((v) => wrap.appendChild(createVoiceBubble(v.audio, v.duration)));
  content.appendChild(wrap);
  scrollToBottom();
}

function codeblockShell(langTag, rawCode) {
  // Professional code card: fixed header (language + copy) over expanded,
  // highlighted, LTR code. Copy reads textContent (spans + entities resolve).
  try {
    const { key, label } = codeLangOf(langTag);
    const code = highlightCode(rawCode, codeFamily(key));
    return `<div class="codeblock" dir="ltr"><div class="code-head"><span class="code-lang">${escapeHtml(label)}</span><button type="button" class="code-copy" aria-label="Copy code">${COPY_SVG}<span>Copy</span></button></div><pre><code>${code}</code></pre></div>`;
  } catch (_) {
    try {
      return `<pre dir="ltr"><code>${escapeHtml(String(rawCode ?? ""))}</code></pre>`;
    } catch (_) {
      return "";
    }
  }
}

async function copyCodeBlock(btn) {
  // Copy one code card (clipboard API, textarea fallback for http).
  try {
    const block = btn && btn.closest ? btn.closest(".codeblock") : null;
    const codeEl = block ? block.querySelector("pre code") : null;
    const text = codeEl ? codeEl.textContent || "" : "";
    if (!text) {
      toast("Nothing to copy");
      return;
    }
    let ok = false;
    try {
      if (navigator.clipboard && navigator.clipboard.writeText) {
        await navigator.clipboard.writeText(text);
        ok = true;
      }
    } catch (_) {
      ok = false;
    }
    if (!ok) {
      const ta = document.createElement("textarea");
      ta.value = text;
      ta.setAttribute("readonly", "");
      ta.style.position = "fixed";
      ta.style.opacity = "0";
      document.body.appendChild(ta);
      ta.select();
      try {
        ok = document.execCommand("copy");
      } catch (_) {
        ok = false;
      }
      ta.remove();
    }
    const label = btn.querySelector("span");
    if (ok) {
      if (label) label.textContent = "Copied";
      btn.classList.add("copied");
      toast("Copied");
      setTimeout(() => {
        try {
          if (label) label.textContent = "Copy";
          btn.classList.remove("copied");
        } catch (_) {}
      }, 1400);
    } else {
      toast("Copy failed");
    }
  } catch (_) {
    try {
      toast("Copy failed");
    } catch (_) {}
  }
}

function formatRich(text) {
  // Global-AI style formatting: code blocks, bold, italic, code, lists. dir=auto per block.
  const chunks = [];
  // Fences are extracted from the RAW text first (placeholders survive the
  // later escaping); the code shell escapes + highlights internally.
  let src = String(text ?? "");
  src = src.replace(/```([^`\n]*)\n?([\s\S]*?)```/g, (m, lang, code) => {
    chunks.push(codeblockShell((lang || "").trim(), code.replace(/^\n+|\s+$/g, "")));
    return `%%X5CODE${chunks.length - 1}%%`;
  });
  let esc = escapeHtml(src);
  esc = esc.replace(/`([^`\n]+?)`/g, "<code>$1</code>");
  esc = esc.replace(/\*\*([^*]+?)\*\*/g, "<b>$1</b>");
  esc = esc.replace(/(^|[^*\w])\*([^*\n]+?)\*/g, "$1<i>$2</i>");
  esc = esc.replace(/~~([^~]+?)~~/g, "<del>$1</del>");
  const lines = esc.split("\n");
  let html = "";
  let list = null;
  const flush = () => {
    if (list) {
      html += `</${list}>`;
      list = null;
    }
  };
  const isPipeRow = (l) => /^\s*\|.*\|\s*$/.test(l);
  const isSepRow = (l) => /^\s*\|[\s:|-]+\|\s*$/.test(l);
  const splitCells = (l) =>
    l
      .trim()
      .replace(/^\||\|$/g, "")
      .split("|")
      .map((c) => c.trim());
  for (let li = 0; li < lines.length; li++) {
    const line = lines[li];
    const ul = line.match(/^\s*[-*]\s+(.*)$/);
    const ol = line.match(/^\s*\d+[.)]\s+(.*)$/);
    const h = line.match(/^\s*#{1,4}\s+(.*)$/);
    const quote = line.match(/^\s*>\s?(.*)$/);
    const codeRef = line.trim().match(/^%%X5CODE(\d+)%%$/);
    if (isPipeRow(line) && li + 1 < lines.length && isSepRow(lines[li + 1])) {
      flush();
      const head = splitCells(line);
      let rowsHtml = "";
      li += 2;
      while (li < lines.length && isPipeRow(lines[li])) {
        const cells = splitCells(lines[li]);
        // First body column is the row header (spec/label column): semantic
        // th + distinct styling + its own divider, so it never looks merged.
        rowsHtml += `<tr>${cells.map((c, idx) => (idx === 0 && cells.length > 1
          ? `<th scope="row" dir="${autoDir(c)}">${c}</th>`
          : `<td dir="${autoDir(c)}">${c}</td>`)).join("")}</tr>`;
        li++;
      }
      li--;
      // Explicit table direction from the header language (never auto): an
      // auto table can stay LTR while typing and strand an Arabic table
      // backwards — explicit dir pins the spec column right in RTL Arabic,
      // left in English, from the first frame.
      html += `<table dir="${autoDir(head.join(" "))}"><thead><tr>${head.map((c) => `<th dir="${autoDir(c)}">${c}</th>`).join("")}</tr></thead><tbody>${rowsHtml}</tbody></table>`;
    } else if (ul) {
      if (list !== "ul") {
        flush();
        html += "<ul>";
        list = "ul";
      }
      html += `<li dir="${autoDir(ul[1])}">${ul[1]}</li>`;
    } else if (ol) {
      if (list !== "ol") {
        flush();
        html += "<ol>";
        list = "ol";
      }
      html += `<li dir="${autoDir(ol[1])}">${ol[1]}</li>`;
    } else {
      flush();
      if (codeRef && chunks[Number(codeRef[1])] !== undefined) {
        html += chunks[Number(codeRef[1])];
      } else if (line.trim() === "") {
        continue;
      } else if (/^\s*(-{3,}|\*{3,})\s*$/.test(line)) {
        html += "<hr>";
      } else if (h) {
        html += `<p class="md-h" dir="auto"><b>${h[1]}</b></p>`;
      } else if (quote) {
        html += `<blockquote><p dir="auto">${quote[1]}</p></blockquote>`;
      } else {
        html += `<p dir="auto">${line}</p>`;
      }
    }
  }
  flush();
  // leftover inline placeholders (rare) restore as-is
  html = html.replace(/%%X5CODE(\d+)%%/g, (m, i) => chunks[Number(i)] || "");
  return html;
}

function hideWelcome() {
  document.getElementById("welcomeMsg")?.remove();
}

/* ---------- per-message direction: Arabic right, English left (both roles) ----------
   Frozen once at stream start so it never flips after finishing. ---------- */
function detectMsgDir(text) {
  const s = String(text || "").slice(0, 2000);
  if (!s.trim()) return null;
  const ar = (s.match(/[\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF\uFB50-\uFDFF\uFE70-\uFEFF]/g) || []).length;
  const lat = (s.match(/[A-Za-z]/g) || []).length;
  if (!ar && !lat) return null;
  return ar >= lat ? "rtl" : "ltr";
}

function applyMsgDir(el, text, fallback) {
  const d = detectMsgDir(text) || fallback || null;
  el.classList.remove("rtl", "ltr");
  if (!d) return;
  el.classList.add(d);
  const c = el.querySelector(".msg-content");
  if (c) c.setAttribute("dir", d);
}

function attachBadgeIcon(type) {
  if (type === "video")
    return '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><polygon points="23 7 16 12 23 17 23 7" /><rect x="1" y="5" width="15" height="14" rx="2" /></svg>';
  if (type === "document")
    return '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" /><polyline points="14 2 14 8 20 8" /><line x1="9" y1="13" x2="15" y2="13" /><line x1="9" y1="17" x2="13" y2="17" /></svg>';
  return '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="18" height="18" rx="2" /><circle cx="8.5" cy="8.5" r="1.5" /><polyline points="21 15 16 10 5 21" /></svg>';
}

function appendUserMessage(text, attachments) {
  const list = Array.isArray(attachments) ? attachments : attachments ? [attachments] : [];
  hideWelcome();
  const el = document.createElement("div");
  el.className = "msg user";
  applyMsgDir(el, text, "rtl");
  el.setAttribute("data-anim", "");
  list.forEach((attachment) => {
    if (!attachment) return;
    const name = document.createElement("span");
    name.className = "msg-attach-name";
    name.textContent = attachment.name;
    const badge = document.createElement("div");
    badge.className = "msg-attach";
    if (attachment.type === "document") badge.classList.add("ft-" + docTypeOf(attachment.name));
    badge.innerHTML = attachBadgeIcon(attachment.type);
    badge.appendChild(name);
    el.appendChild(badge);
  });
  if (text || list.length) {
    const content = document.createElement("div");
    content.className = "msg-content";
    el.querySelectorAll(".msg-attach").forEach((b) => content.appendChild(b));
    if (text) {
      const wrapper = document.createElement("div");
      wrapper.innerHTML = formatRich(text);
      content.appendChild(wrapper);
    }
    el.appendChild(content);
  }
  el.classList.add("anim");
  el.dataset.raw = text || "";
  chatInner.appendChild(el);
  scrollToBottom();
  return el;
}

function attachUserBar(el) {
  const old = el.querySelector(":scope > .msg-actions");
  if (old) old.remove();
  const bar = document.createElement("div");
  bar.className = "msg-actions below";
  const raw = el.dataset.raw || "";
  [
    ["copy", "Copy", () => copyText(raw)],
    ["edit", "Edit", () => editUserMessage(el)],
    ["trash", "Delete", () => confirmDeleteMessage(el)],
  ].forEach(([icon, label, fn], i) => {
    const b = makeActBtn(icon, label, fn, icon === "trash");
    b.style.animationDelay = `${i * 60}ms`;
    bar.appendChild(b);
  });
  el.appendChild(bar);
}

function appendAssistantShell() {
  const el = document.createElement("div");
  el.className = "msg assistant";
  el.innerHTML = `<div class="msg-content"><p dir="auto"></p></div>`;
  chatInner.appendChild(el);
  return { el, p: el.querySelector("p") };
}

/* ---------- thinking-block: Working header + expandable steps ----------
   Working header (dot-square 5x5 + wave text + arrow) stays open while the
   agent works; every step arrow shows the model reason only, plus one
   curated detail line per tool (todo steps, file names, short code/terminal
   preview) and/or image — raw tool output is never dumped.
   When done the wave stops (static white, 3x3 square) and the block
   collapses — the final reply lives below it. Header is English LTR;
   step content is dir=auto so Arabic reads RTL. ---------- */
const squareAnimations = new Map();

function startSquareAnimation(square) {
  stopSquareAnimation(square);
  const dots = [...square.querySelectorAll("span")];
  const rings = [[], [], []];
  dots.forEach((dot) => {
    rings[+dot.dataset.ring].push(dot);
  });
  let timers = [];
  let stopped = false;
  function runCycle() {
    if (stopped) return;
    dots.forEach((d) => d.classList.remove("on"));
    timers.push(setTimeout(() => {
      if (stopped) return;
      rings[0].forEach((d) => d.classList.add("on"));
    }, 60));
    timers.push(setTimeout(() => {
      if (stopped) return;
      rings[1].forEach((d) => d.classList.add("on"));
    }, 360));
    timers.push(setTimeout(() => {
      if (stopped) return;
      rings[2].forEach((d) => d.classList.add("on"));
    }, 660));
    timers.push(setTimeout(() => {
      if (stopped) return;
      dots.forEach((d) => d.classList.remove("on"));
    }, 1300));
    timers.push(setTimeout(runCycle, 1600));
  }
  squareAnimations.set(square, {
    stop: () => {
      stopped = true;
      timers.forEach((t) => clearTimeout(t));
      timers = [];
    },
  });
  runCycle();
}

function stopSquareAnimation(square) {
  const anim = squareAnimations.get(square);
  if (anim) {
    anim.stop();
    squareAnimations.delete(square);
  }
}

function createDotSquare() {
  const square = document.createElement("div");
  square.className = "dot-square";
  const SIZE = 5;
  const center = 2;
  for (let row = 0; row < SIZE; row++) {
    for (let col = 0; col < SIZE; col++) {
      const dot = document.createElement("span");
      const ring = Math.max(Math.abs(row - center), Math.abs(col - center));
      dot.dataset.ring = ring;
      const isWhite = row >= 1 && row <= 3 && col >= 1 && col <= 3;
      if (isWhite) dot.classList.add("lit-white");
      else dot.classList.add("lit-black");
      square.appendChild(dot);
    }
  }
  return square;
}

const THINK_ARROW_SVG = `<svg viewBox="0 0 12 12" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M4 2.5 L8 6 L4 9.5"/></svg>`;

function createThinkingBlock() {
  const block = document.createElement("div");
  block.className = "thinking-block open";

  const header = document.createElement("div");
  header.className = "thinking-header";
  header.setAttribute("role", "button");
  header.setAttribute("tabindex", "0");

  const dotSquare = createDotSquare();

  const workingText = document.createElement("span");
  workingText.className = "working-text";
  workingText.textContent = "Working";

  // No arrow yet: it appears only once the first step begins.
  header.appendChild(dotSquare);
  header.appendChild(workingText);

  const steps = document.createElement("div");
  steps.className = "steps";
  const stepsInner = document.createElement("div");
  stepsInner.className = "steps-inner";
  steps.appendChild(stepsInner);

  block.appendChild(header);
  block.appendChild(steps);

  startSquareAnimation(dotSquare);
  const toggle = () => block.classList.toggle("open");
  header.addEventListener("click", toggle);
  header.addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      toggle();
    }
  });

  block._dotSquare = dotSquare;
  block._stepsInner = stepsInner;
  block._t0 = Date.now();
  return block;
}

function ensureHeaderArrow(block) {
  // The header arrow exists only once steps began (never on a fresh
  // Working). Never throws, never duplicates.
  try {
    if (!block) return;
    const header = block.querySelector(":scope > .thinking-header");
    if (!header || header.querySelector(":scope > .arrow")) return;
    if (!block._stepsInner || !block._stepsInner.children.length) return;
    const arrow = document.createElement("span");
    arrow.className = "arrow";
    arrow.innerHTML = THINK_ARROW_SVG;
    header.appendChild(arrow);
  } catch (_) {}
}

function settleThinkingBlock(block, elapsedSec) {
  // Working stops: static white text + static 3x3 square, elapsed seconds
  // shown next to Working, block collapses.
  if (!block || block.dataset.settled === "1") return;
  block.dataset.settled = "1";
  ensureHeaderArrow(block);
  try {
    if (block._dotSquare) {
      stopSquareAnimation(block._dotSquare);
      block._dotSquare.querySelectorAll("span").forEach((d) => d.classList.remove("on"));
    }
  } catch (_) {}
  try {
    let secs = null;
    if (typeof elapsedSec === "number" && isFinite(elapsedSec) && elapsedSec >= 0) {
      secs = Math.round(elapsedSec);
    } else if (typeof block._t0 === "number") {
      secs = Math.max(0, Math.round((Date.now() - block._t0) / 1000));
    }
    if (secs !== null && !block.querySelector(":scope > .thinking-header > .working-time")) {
      const header = block.querySelector(":scope > .thinking-header");
      if (header) {
        const t = document.createElement("span");
        t.className = "working-time";
        t.textContent = `${secs}s`;
        const arrow = header.querySelector(":scope > .arrow");
        if (arrow) header.insertBefore(t, arrow);
        else header.appendChild(t);
      }
    }
  } catch (_) {}
  block.classList.add("done");
  block.classList.remove("open");
}

function stepReasonText(step) {
  // Optional Reason node: the model's own words only. Empty when the model
  // wrote none — the step then shows with no arrow (unless output/image).
  const r = step && typeof step.reason === "string" ? step.reason.trim() : "";
  return r ? r.slice(0, 500) : "";
}

function stepKey(step) {
  // One tool call = one step: keyed by the call id first, so a completion
  // whose label differs from its preview (question Answered, tool errors)
  // updates the SAME item instead of orphaning a forever-running row.
  try {
    const id = typeof step === "object" && step ? step.key || step.id || "" : "";
    if (id) return String(id);
  } catch (_) {}
  const t = typeof step === "string" ? step : (step && (step.event || "")) || "";
  return `::${t}`;
}


function ensureStepArrow(item) {
  // The arrow exists only while the step has expandable content
  // (reason text, output text, or an image). Never throws.
  try {
    if (!item || !item._head || item._head.querySelector(":scope > .step-arrow")) return;
    const inner = item._inner;
    const has = !!(
      (inner && inner.querySelector(":scope > .step-text, :scope > .step-img")) ||
      (typeof item._shot === "string" && item._shot)
    );
    if (!has) return;
    const arrow = document.createElement("span");
    arrow.className = "step-arrow";
    arrow.innerHTML = THINK_ARROW_SVG;
    item._head.appendChild(arrow);
  } catch (_) {}
}

function markStepRunning(item) {
  // The step was just requested: orange dot until it completes, then the
  // dot alone turns green — no check mark anywhere.
  try {
    if (!item) return;
    item._done = false;
    item.classList.remove("done");
    item.classList.add("running");
  } catch (_) {}
}

function createStepItem(step) {
  const title = humanizeEvent(typeof step === "string" ? step : step?.event || step?.text || "") || "Step";
  const reason = stepReasonText(typeof step === "object" ? step : null);
  const output = typeof step === "object" && step && typeof step.output === "string" ? step.output.slice(0, 500) : "";
  const media = typeof step === "object" && step ? step.media || null : null;
  const shot = typeof step === "object" && step
    ? (media && typeof media.shot === "string" && media.shot) || (typeof step.shot === "string" && step.shot) || ""
    : "";
  // Voice records render once as a WhatsApp-style bubble on the assistant
  // message (appendFinalVoice), never inside the step.
  // Voice records render once as a WhatsApp-style bubble on the assistant
  // message (appendFinalVoice), never inside the step.

  const item = document.createElement("div");
  item.className = "step-item";

  const head = document.createElement("div");
  head.className = "step-head";
  head.setAttribute("role", "button");
  head.setAttribute("tabindex", "0");
  const bullet = document.createElement("span");
  bullet.className = "step-bullet";
  const titleEl = document.createElement("span");
  titleEl.className = "step-title";
  titleEl.textContent = title;
  head.appendChild(bullet);
  head.appendChild(titleEl);

  const content = document.createElement("div");
  content.className = "step-content";
  const inner = document.createElement("div");
  inner.className = "step-content-inner";
  content.appendChild(inner);

  if (reason) {
    const reasonEl = document.createElement("div");
    reasonEl.className = "step-text step-reason";
    reasonEl.setAttribute("dir", "auto");
    reasonEl.textContent = reason;
    inner.appendChild(reasonEl);
  }

  item.appendChild(head);
  item.appendChild(content);
  item._head = head;
  item._inner = inner;
  item._done = false;
  item._shot = shot;

  const toggle = (e) => {
    if (e) e.stopPropagation();
    // No expandable content → plain line, nothing to open.
    if (!head.querySelector(":scope > .step-arrow")) return;
    item.classList.toggle("open");
    if (item.classList.contains("open")) {
      const img = inner.querySelector(".step-img img");
      if (img && !img.getAttribute("src") && img.dataset.shot) {
        img.src = "/api/shot?path=" + encodeURIComponent(img.dataset.shot);
      }
    }
  };
  head.addEventListener("click", toggle);
  head.addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      toggle();
    }
  });

  completeStepItem(item, { output, shot });
  return item;
}

function completeStepItem(item, { output = "", shot = "" } = {}) {
  // Fills a started step with its curated detail (under the reason) and/or
  // image, then marks it done (green bullet). The detail is never raw tool
  // output — the backend sends one short per-tool line only. Never throws.
  try {
    if (!item || !item._inner) return;
    const inner = item._inner;
    if (typeof output === "string" && output.trim() && !inner.querySelector(":scope > .step-out")) {
      const outEl = document.createElement("div");
      outEl.className = "step-text step-out";
      outEl.setAttribute("dir", "auto");
      outEl.textContent = output.trim().slice(0, 500);
      inner.appendChild(outEl);
    }
    if (typeof shot === "string" && shot && !inner.querySelector(":scope > .step-img")) {      const url = "/api/shot?path=" + encodeURIComponent(shot);
      const wrap = document.createElement("div");
      wrap.className = "step-img";
      const img = document.createElement("img");
      img.alt = "Step image";
      img.decoding = "async";
      img.loading = "lazy";
      img.dataset.shot = shot;
      img.addEventListener("error", () => wrap.remove(), { once: true });
      img.addEventListener("click", (e) => {
        e.stopPropagation();
        openShotLightbox(url);
      });
      wrap.appendChild(img);
      inner.appendChild(wrap);
      // Image-only expand still lazy-loads on first open.
      if (item.classList.contains("open") && !img.getAttribute("src")) {
        img.src = url;
      }
    }
    if (!item._done) {
      item._done = true;
      item.classList.remove("running");
      item.classList.add("done");
    }
    // Late content (output/image at completion) earns the arrow then.
    ensureStepArrow(item);
  } catch (_) {}
}

function addThinking(text) {
  const el = document.createElement("div");
  el.className = "msg assistant";
  el.id = "thinkingMsg";
  const block = createThinkingBlock();
  el.appendChild(block);
  el._block = block;
  // Direction from the very first frame (same language as the reply):
  // the Working line never jumps sides later.
  if (text) applyMsgDir(el, text, "ltr");
  chatInner.appendChild(el);
  scrollToBottom();
  return el;
}

function typeRich(container, fullText) {
  // Format FIRST, then animate writing the formatted result itself (fast).
  return new Promise((resolve) => {
    const template = document.createElement("div");
    template.innerHTML = formatRich(fullText);
    const live = template.cloneNode(true);
    const jobs = [];
    const walker = document.createTreeWalker(live, NodeFilter.SHOW_TEXT);
    const textNodes = [];
    let t;
    while ((t = walker.nextNode())) textNodes.push(t);
    textNodes.forEach((tn) => {
      jobs.push({ tn, full: tn.textContent });
      tn.textContent = "";
    });
    container.innerHTML = "";
    container.classList.add("typing-live");
    container.appendChild(live);
    let ji = 0;
    let ci = 0;
    const markItemText = () => {
      // A list item earns its marker the moment its first text arrives —
      // empty items stay markerless while typing (no stray dots).
      try {
        const job = jobs[ji];
        if (job && job.full.length && job.tn.parentElement) {
          const li = job.tn.parentElement.closest("li");
          if (li) li.classList.add("has-text");
        }
      } catch (_) {}
    };
    const advance = () => {
      while (ji < jobs.length && ci >= jobs[ji].full.length) {
        ji++;
        ci = 0;
      }
      markItemText();
    };
    advance();
    if (!jobs.some((j) => j.full.length)) {
      container.classList.remove("typing-live");
      resolve(live);
      return;
    }
    const cursor = document.createElement("span");
    cursor.className = "typing-cursor";
    const placeCursor = () => {
      cursor.remove();
      if (ji < jobs.length) {
        const tn = jobs[ji].tn;
        tn.parentNode.insertBefore(cursor, tn.nextSibling);
      } else {
        live.appendChild(cursor);
      }
    };
    placeCursor();
    scrollToBottom();
    (function tick() {
      if (ji >= jobs.length) {
        cursor.remove();
        scrollToBottom();
        container.classList.remove("typing-live");
        resolve(live);
        return;
      }
      const startJi = ji;
      const job = jobs[ji];
      const chunk = 2 + (Math.random() < 0.3 ? 1 : 0);
      job.tn.textContent += job.full.slice(ci, ci + chunk);
      ci += chunk;
      advance();
      // Blocks that just finished typing (tables / code) pop in with animation.
      for (let k = startJi; k < ji; k++) {
        try {
          const anc = jobs[k].tn.parentElement
            ? jobs[k].tn.parentElement.closest("table,pre")
            : null;
          if (anc && !anc.classList.contains("pop-in")) anc.classList.add("pop-in");
        } catch (_) {
          // ignore lookup failures mid-stream
        }
      }
      placeCursor();
      scrollToBottom();
      setTimeout(tick, 6 + Math.random() * 10);
    })();
  });
}

function typeMessage(text, steps = [], elapsed = null) {
  const { el, p } = appendAssistantShell();
  applyMsgDir(el, text, "ltr");
  // Reserve the action-bar height now: icons appearing later push nothing.
  const ph = document.createElement("div");
  ph.className = "msg-actions below msg-actions-ph";
  el.appendChild(ph);
  const list = Array.isArray(steps) ? steps : [];
  const seen = new Set();
  const items = [];
  if (list.length) {
    list.slice(0, 12).forEach((s, i) => {
      // Dedupe by unique call key — the same event label may repeat across calls.
      const key = typeof s === "string" ? `str:${i}:${s}` : (s?.key || `idx:${i}:${s?.event || ""}`);
      if (!key || seen.has(key)) return;
      seen.add(key);
      try {
        const item = createStepItem(s);
        completeStepItem(item, {});
        items.push(item);
      } catch (_) {}
    });
  }
  if (items.length) {
    // Settled block: Working stopped, elapsed seconds shown, collapsed,
    // steps expandable anytime.
    const block = createThinkingBlock();
    items.forEach((item) => block._stepsInner.appendChild(item));
    settleThinkingBlock(block, elapsed);
    p.before(block);
  }
  const holder = document.createElement("div");
  p.replaceWith(holder);
  return typeRich(holder, text).then(() => el);
}

function cleanHistoryText(raw) {
  // Strip the injected [Attached files] block on display; show file chips instead.
  const text = String(raw || "");
  const files = [];
  const nameRe = /name="([^"]+)"/g;
  let m;
  while ((m = nameRe.exec(text)) !== null) {
    if (files.length < 4) files.push(m[1].slice(0, 60));
  }
  const blockStart = text.indexOf("[Attached files");
  const clean = blockStart >= 0 ? text.slice(0, blockStart).trim() : text;
  return { text: clean.slice(0, MAX_MESSAGE), files };
}

async function loadHistory() {
  try {
    const res = await fetch("/api/conversation", { cache: "no-store" });
    if (!res.ok) return;
    const data = await res.json();
    const msgs = Array.isArray(data?.messages) ? data.messages : [];
    const valid = msgs.filter(
      (m) => m && (m.role === "user" || m.role === "assistant") && String(m.content || "").trim()
    );
    if (valid.length === 0) return;
    hideWelcome();
    // Refresh mid-run must never wipe in-flight UI: keep the thinking bubble
    // and any optimistic bubble/stream shell not persisted yet (no data-mid),
    // then re-append them after the persisted history renders.
    const keep = [];
    chatInner.querySelectorAll(".msg").forEach((m) => {
      try {
        if (m.id === "thinkingMsg" || !m.dataset.mid) {
          keep.push(m);
          m.remove();
        } else {
          m.remove();
        }
      } catch (_) {
        try {
          m.remove();
        } catch (_) {}
      }
    });
    // bounded render: last 100 to avoid DOM blowup
    let lastSeenUserText = "";
    valid.slice(-100).forEach((m) => {
      if (m.role === "user") {
        const cleaned = cleanHistoryText(m.content);
        if (!cleaned.text && !cleaned.files.length) return;
        const el = document.createElement("div");
        el.className = "msg user";
        applyMsgDir(el, cleaned.text, "rtl");
        const content = document.createElement("div");
        content.className = "msg-content";
        cleaned.files.forEach((fname) => {
          const badge = document.createElement("div");
          badge.className = "msg-attach";
          badge.innerHTML = attachBadgeIcon("image");
          const nm = document.createElement("span");
          nm.className = "msg-attach-name";
          nm.textContent = fname;
          badge.appendChild(nm);
          content.appendChild(badge);
        });
        if (cleaned.text) {
          const wrapper = document.createElement("div");
          wrapper.innerHTML = formatRich(cleaned.text);
          content.appendChild(wrapper);
        }
        el.appendChild(content);
        el.dataset.raw = cleaned.text;
        if (m.id) el.dataset.mid = String(m.id);
        if (cleaned.text) lastSeenUserText = cleaned.text;
        attachUserBar(el);
        chatInner.appendChild(el);
      } else {
        const { el, p } = appendAssistantShell();
        const rawText = String(m.content).slice(0, MAX_MESSAGE);
        applyMsgDir(el, rawText, "ltr");
        // After refresh: settled Working (stopped, collapsed) + reply below.
        // Steps stay expandable anytime — reason + output + image per step.
        try {
          const histSteps = Array.isArray(m.steps) ? m.steps.slice(0, 12) : [];
          const seenHist = new Set();
          const histItems = [];
          histSteps.forEach((s, i) => {
            const key = typeof s === "string" ? `str:${i}:${s}` : (s?.key || `idx:${i}:${s?.event || ""}`);
            if (!key || seenHist.has(key)) return;
            seenHist.add(key);
            try {
              const item = createStepItem(s);
              completeStepItem(item, {});
              histItems.push(item);
            } catch (_) {}
          });
          if (histItems.length) {
            const block = createThinkingBlock();
            histItems.forEach((item) => block._stepsInner.appendChild(item));
            settleThinkingBlock(block, m.elapsed);
            p.before(block);
          }
        } catch (_) {}
        // Screenshots of the screenshot tool are part of the final reply, so they stay.
        const rich = document.createElement("div");
        rich.innerHTML = formatRich(rawText);
        p.replaceWith(rich);
        appendFinalImages(el, m.images);
        appendFinalVoice(el, m.audio);
        el.dataset.raw = rawText;
        if (m.id) el.dataset.mid = String(m.id);
        if (lastSeenUserText) el.dataset.userText = lastSeenUserText;
        attachAssistantBar(el, rawText);
      }
    });
    keep.forEach((m) => {
      try {
        chatInner.appendChild(m);
      } catch (_) {}
    });
    scrollToBottom(false);
  } catch (_) {
    // keep welcome message on failure
  }
}

function readAsDataURL(file) {
  return new Promise((resolve, reject) => {
    const max = String(file.type || "").startsWith("video") ? 24 * 1024 * 1024 : 8 * 1024 * 1024;
    if (file.size > max || file.size === 0) {
      reject(new Error("too large"));
      return;
    }
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result || ""));
    reader.onerror = () => reject(new Error("read failed"));
    reader.readAsDataURL(file);
  });
}

async function maybeCompressImage(file) {
  // Phone photos are huge — downscale before upload (fixes 413s, speeds up vision).
  try {
    if (!file.type.startsWith("image/")) return file;
    if (file.size <= 1536 * 1024) return file;
    if (typeof createImageBitmap !== "function") return file;
    const bmp = await createImageBitmap(file);
    const maxDim = 1600;
    const scale = Math.min(1, maxDim / Math.max(bmp.width, bmp.height));
    if (scale >= 1) {
      if (bmp.close) bmp.close();
      return file;
    }
    const canvas = document.createElement("canvas");
    canvas.width = Math.round(bmp.width * scale);
    canvas.height = Math.round(bmp.height * scale);
    canvas.getContext("2d").drawImage(bmp, 0, 0, canvas.width, canvas.height);
    if (bmp.close) bmp.close();
    const blob = await new Promise((res) => canvas.toBlob(res, "image/jpeg", 0.85));
    if (!blob) return file;
    const name = file.name.replace(/\.[a-z0-9]+$/i, "") + ".jpg";
    return new File([blob], name, { type: "image/jpeg" });
  } catch (_) {
    return file;
  }
}

/* Deleted: old flow-step system replaced by the thinking-block above. */

/* Deleted: buildStepBox/bindStepToggle/toggleStepFlow/attachStepOutput —
   every step is now a step-item inside the thinking-block above. */

function finalShotUrl(shot) {
  return "/api/shot?path=" + encodeURIComponent(shot);
}

function appendFinalImages(el, images) {
  // Screenshots taken via the screenshot tool, shown as part of the final
  // reply (live and after refresh). Click any of them for zoom.
  const list = (Array.isArray(images) ? images : [])
    .map((item) => (typeof item === "string" ? item : item && item.shot))
    .filter((shot) => typeof shot === "string" && shot)
    .slice(0, 6);
  if (!el || !list.length) return;
  const content = el.querySelector(".msg-content");
  if (!content || content.querySelector(":scope > .msg-final-shots")) return;
  const wrap = document.createElement("div");
  wrap.className = "msg-final-shots";
  list.forEach((shot) => {
    const url = finalShotUrl(shot);
    const card = document.createElement("div");
    card.className = "final-shot";
    const img = document.createElement("img");
    img.alt = "Screenshot";
    img.decoding = "async";
    img.loading = "lazy";
    img.src = url;
    img.addEventListener("error", () => card.remove(), { once: true });
    img.addEventListener("click", () => openShotLightbox(url));
    card.appendChild(img);
    wrap.appendChild(card);
  });
  if (wrap.children.length) content.appendChild(wrap);
}

function escShotLightbox(e) {
  if (e && e.key === "Escape") closeShotLightbox();
}

function closeShotLightbox() {
  try {
    document.querySelector(".shotbox")?.remove();
    document.body.classList.remove("shotbox-open");
    document.removeEventListener("keydown", escShotLightbox);
  } catch (_) {}
}

function openShotLightbox(src) {
  // Fullscreen image viewer with zoom: buttons + wheel (desktop),
  // buttons + drag + double-tap (phone). Esc/backdrop closes.
  if (typeof src !== "string" || !src) return;
  closeShotLightbox();
  const back = document.createElement("div");
  back.className = "shotbox";
  back.innerHTML = `
    <div class="shotbox-stage"><img class="shotbox-img" alt="Screenshot enlarged" draggable="false"></div>
    <div class="shotbox-bar">
      <button type="button" data-zoom="out" aria-label="Zoom out">−</button>
      <button type="button" data-zoom="reset" aria-label="Reset zoom">1:1</button>
      <button type="button" data-zoom="in" aria-label="Zoom in">+</button>
      <button type="button" data-zoom="close" aria-label="Close">✕</button>
    </div>`;
  const img = back.querySelector(".shotbox-img");
  img.src = src;
  let scale = 1;
  let tx = 0;
  let ty = 0;
  const apply = () => {
    img.style.transform = `translate(${tx}px, ${ty}px) scale(${scale})`;
  };
  const zoomBy = (f) => {
    scale = Math.min(5, Math.max(1, scale * f));
    if (scale === 1) {
      tx = 0;
      ty = 0;
    }
    apply();
  };
  back.querySelector('[data-zoom="in"]').addEventListener("click", (e) => {
    e.stopPropagation();
    zoomBy(1.4);
  });
  back.querySelector('[data-zoom="out"]').addEventListener("click", (e) => {
    e.stopPropagation();
    zoomBy(1 / 1.4);
  });
  back.querySelector('[data-zoom="reset"]').addEventListener("click", (e) => {
    e.stopPropagation();
    scale = 1;
    tx = 0;
    ty = 0;
    apply();
  });
  back.querySelector('[data-zoom="close"]').addEventListener("click", (e) => {
    e.stopPropagation();
    closeShotLightbox();
  });
  back.addEventListener("click", (e) => {
    if (e.target === back || (e.target && e.target.classList && e.target.classList.contains("shotbox-stage"))) {
      closeShotLightbox();
    }
  });
  back.addEventListener("wheel", (e) => {
    e.preventDefault();
    zoomBy(e.deltaY < 0 ? 1.15 : 1 / 1.15);
  }, { passive: false });
  let drag = null;
  img.style.touchAction = "none";
  img.addEventListener("pointerdown", (e) => {
    if (scale <= 1) return;
    drag = { x: e.clientX - tx, y: e.clientY - ty };
    try {
      img.setPointerCapture(e.pointerId);
    } catch (_) {}
  });
  img.addEventListener("pointermove", (e) => {
    if (!drag) return;
    tx = e.clientX - drag.x;
    ty = e.clientY - drag.y;
    apply();
  });
  ["pointerup", "pointercancel"].forEach((ev) => img.addEventListener(ev, () => {
    drag = null;
  }));
  img.addEventListener("dblclick", (e) => {
    e.preventDefault();
    if (scale > 1) {
      scale = 1;
      tx = 0;
      ty = 0;
    } else {
      scale = 2.2;
    }
    apply();
  });
  document.addEventListener("keydown", escShotLightbox);
  document.body.appendChild(back);
  document.body.classList.add("shotbox-open");
}

async function sendStream(value, attachments, thinking) {
  // Live event streaming over the ONE ws.js socket — returns true when
  // fully handled, false to fall back. Frames arrive as raw WS messages
  // (same shapes as the old SSE frames), so rendering is unchanged.
  activeThinking = thinking;
  const myGen = ++runGen;
  activeRun = { cancelled: false, gen: myGen, thinking };
  let started;
  try {
    started = await sendOp("chat_stream", { message: value, attachments, mode: agentMode });
  } catch (err) {
    const wasCancelled = Boolean(activeRun && activeRun.cancelled);
    activeRun = null;
    if (err && (err.aborted || err.name === "AbortError" || wasCancelled)) {
      throw { aborted: true };
    }
    return false;
  }
  if (!started || !started.ok) {
    activeRun = null;
    return false;
  }

  // One thinking-block per turn: Working header + steps stay open above
  // while the answer streams below it. On done the block settles
  // (wave stops, collapsed) and the reply sits underneath.
  let shell = null;
  let shellP = null;
  const collected = [];
  const liveItems = new Map();
  // Streamed text buffered by loop step (never shown live): a step that
  // turns into tool events moves its text inside the event; the final
  // reply is typed once at done with the normal animation.
  const tokenSteps = new Map();

  function flushStepTokens(loopStep, item) {
    // Move this loop step's buffered text into its tool event (inside the
    // expandable content). Runs once per step. Never throws.
    try {
      if (typeof loopStep !== "number" || !item || !item._inner) return;
      if (item._flushedStep === loopStep) return;
      const text = (tokenSteps.get(loopStep) || "").trim().slice(0, 500);
      tokenSteps.delete(loopStep);
      if (!text) return;
      item._flushedStep = loopStep;
      const t = document.createElement("div");
      t.className = "step-text step-tokens";
      t.setAttribute("dir", "auto");
      t.textContent = text;
      item._inner.appendChild(t);
      ensureStepArrow(item);
      scrollToBottom();
    } catch (_) {}
  }
  function activeBlock() {
    try {
      if (thinking && thinking.isConnected && thinking._block) return thinking._block;
    } catch (_) {}
    return null;
  }

  function appendToken(step, delta) {
    // Buffer only — nothing is shown live. The reply is typed once at done
    // with the normal animation; tool-phase text moves inside its event.
    if (typeof delta !== "string" || !delta) return;
    try {
      if (typeof step === "number") {
        tokenSteps.set(step, (tokenSteps.get(step) || "") + delta);
      }
    } catch (_) {}
  }

  function ensureShell() {
    if (shell) return;
    const s = appendAssistantShell();
    shell = s.el;
    shellP = s.p;
    // The thinking bubble stays above; the answer shell goes right after it.
    try {
      if (thinking && thinking.isConnected) thinking.after(shell);
    } catch (_) {}
    // Freeze direction from the user's message at stream start — never
    // re-applied at done, so the bubble never flips after finishing.
    applyMsgDir(shell, value, "ltr");
    // Reserve the action-bar height now: icons appearing at done push nothing.
    const ph = document.createElement("div");
    ph.className = "msg-actions below msg-actions-ph";
    shell.appendChild(ph);
  }
  function appendStepStart(step) {
    // A tool call appears immediately: title + reason, open block,
    // bullet pending. Completion fills output/image and marks it done.
    const block = activeBlock();
    if (!block) {
      ensureShell();
      scrollToBottom();
      return;
    }
    const text = humanizeEvent(typeof step === "string" ? step : step?.event || "");
    if (!text) {
      scrollToBottom();
      return;
    }
    const key = stepKey(step);
    if (liveItems.has(key)) {
      scrollToBottom();
      return;
    }
    try {
      const item = createStepItem({
        event: text,
        reason: typeof step === "object" && step ? step.reason : "",
      });
      markStepRunning(item);
      block._stepsInner.appendChild(item);
      // Tool-phase text of this loop step moves inside the event.
      flushStepTokens(typeof step === "object" && step ? step.loopStep : undefined, item);
      // First step: the header arrow appears now.
      ensureHeaderArrow(block);
      liveItems.set(key, item);
      collected.push({ event: text, reason: stepReasonText(step), key });
    } catch (_) {}
    scrollToBottom();
  }
  function appendStep(step) {
    // Completion: the started step gains output/image + green bullet.
    // Never duplicates. A step never announced at start is created whole.
    const block = activeBlock();
    const text = humanizeEvent(typeof step === "string" ? step : step?.event || "");
    if (!text) {
      if (!block) ensureShell();
      scrollToBottom();
      return;
    }
    const key = stepKey(step);
    const output = typeof step === "object" && step ? step.output || step.out || null : null;
    const reason = typeof step === "object" && step ? step.reason || "" : "";
    const media = typeof step === "object" && step && step.media && typeof step.media === "object" ? step.media : null;
    const shot = media && typeof media.shot === "string" && media.shot
      ? media.shot
      : (typeof step === "object" && step && typeof step.shot === "string" ? step.shot : "");
    if (!block) {
      ensureShell();
      collected.push({ event: text, output, key, media, reason });
      scrollToBottom();
      return;
    }
    let item = liveItems.get(key);
    if (!item) {
      try {
        item = createStepItem({ event: text, reason, output, media, shot });
        block._stepsInner.appendChild(item);
        // First step: the header arrow appears now.
        ensureHeaderArrow(block);
        liveItems.set(key, item);
      } catch (_) {
        scrollToBottom();
        return;
      }
    } else {
      // The completion label may differ from the preview (Answered, errors):
      // the row keeps its place and takes the final title.
      try {
        const titleEl = item._head && item._head.querySelector(":scope > .step-title");
        if (titleEl && text && titleEl.textContent !== text) titleEl.textContent = text;
      } catch (_) {}
      completeStepItem(item, {
        output: typeof output === "string" ? output : "",
        shot: typeof shot === "string" ? shot : "",
      });
    }
    // Tool-phase text of this loop step moves inside the event.
    flushStepTokens(typeof step === "object" && step ? step.loopStep : undefined, item);
    // A completed call carrying a watch stream opens the player bar by itself
    // (e.g. browser start) — no pause, the loop keeps running.
    try {
      const st = media && media.stream;
      if (st && typeof st.url === "string" && st.url) {
        if (st.tool === "show_preview") showPreviewPlayer(st.url);
        else if (st.view_only !== false) {
          markLiveSeen();
          showEdgeTab(st.url);
        }
      }
    } catch (_) {}
    const idx = collected.findIndex((c) => stepKey(c) === key);
    const entry = { event: text, output, key, media, reason };
    if (idx >= 0) collected[idx] = entry;
    else collected.push(entry);
    scrollToBottom();
  }
  // WS frames arrive one message at a time on the same socket (same shapes
  // as the old SSE frames). Wait for done/error/cancelled of THIS turn.
  let doneReceived = false;
  let turnResult = null; // true = handled, false = fall back
  function onFrame(msg2) {
    if (turnResult !== null) return;
    try {
      if (!activeRun || activeRun.gen !== myGen) {
        turnResult = true;
        return;
      }
    } catch (_) {}
    try {
        if (msg2?.type === "event_start") appendStepStart({ event: msg2.event, key: msg2.key, reason: msg2.reason, loopStep: msg2.step });
        else if (msg2?.type === "event") appendStep({ event: msg2.event, output: msg2.output, key: msg2.key, media: msg2.media, reason: msg2.reason, shot: msg2.shot, loopStep: msg2.step });
        else if (msg2?.type === "token") appendToken(msg2.step, msg2.text);
        else if (msg2?.type === "live") {
          showLiveLink(msg2.live);
        }
        else if (msg2?.type === "live_end") clearLiveUI();
        else if (msg2?.type === "question") {
          if (msg2.kind === "live") {
            markLiveSeen();
            if (msg2.view_only) showEdgeTab(msg2.url, msg2.id);
            else showLiveControlCard(msg2);
          } else showQuestionCard(msg2);
        }
        else if (msg2?.type === "question-expired") {
          if (pendingQuestion && pendingQuestion.id === msg2.id) {
            hideQuestionCard();
            toast("Question expired — continued without answer");
          }
        } else if (msg2?.type === "cancelled") {
          // Stopped: a single line with the word Stopped in the middle only.
          try {
            thinking.remove();
          } catch (_) {}
          if (shell) {
            try {
              shell.remove();
            } catch (_) {}
            shell = null;
          }
          try {
            const line = document.createElement("div");
            line.className = "stopped-line";
            line.textContent = "Stopped";
            chatInner.appendChild(line);
            scrollToBottom();
          } catch (_) {}
          activeThinking = null;
          activeRun = null;
          turnResult = true;
          return;
        } else if (msg2?.type === "done") {
          (async () => {
            try {
          const replyText = String(msg2.reply || "");
          // Finalization: the thinking-block settles (Working stops,
          // collapsed) and stays above; the final reply is always last.
          let finalEl;
          const block = activeBlock();
          // Steps decide the block's fate: with steps it settles above the
          // reply; with none the Working artifact is dropped entirely.
          const liveHadSteps = !!(block && block._stepsInner && block._stepsInner.children.length);
          if (block && liveHadSteps) settleThinkingBlock(block);
          if (shell) {
            // Direction stays as frozen at stream start (never re-applied here).
            const content = shell.querySelector(".msg-content");
            try {
              shellP.remove();
            } catch (_) {}
            // The settled block moves to the top of the final message.
            // No steps: the Working artifact is dropped, reply only.
            try {
              if (block && block.isConnected && liveHadSteps) content?.prepend(block);
            } catch (_) {}
            try {
              thinking.remove();
            } catch (_) {}
            const holder = document.createElement("div");
            shellP = holder;
            try {
              content?.appendChild(holder);
            } catch (_) {
              shell.appendChild(holder);
            }
            await typeRich(holder, replyText);
            appendFinalImages(shell, msg2.images);
            appendFinalVoice(shell, msg2.audio);
            finalEl = shell;
            shell = null;
          } else if (liveHadSteps) {
            // No streamed text: the thinking bubble BECOMES the final message.
            try {
              thinking.removeAttribute("id");
            } catch (_) {}
            const holder = document.createElement("div");
            holder.className = "msg-content";
            thinking.appendChild(holder);
            await typeRich(holder, replyText);
            appendFinalImages(thinking, msg2.images);
            appendFinalVoice(thinking, msg2.audio);
            finalEl = thinking;
          } else {
            // Direct answer, no steps: Working artifact dropped, reply only.
            thinking.remove();
            finalEl = await typeMessage(replyText, collected);
            appendFinalImages(finalEl, msg2.images);
            appendFinalVoice(finalEl, msg2.audio);
          }
          const ids = msg2.ids || null;
          if (lastUserEl && ids && ids.user) lastUserEl.dataset.mid = ids.user;
          if (ids && ids.assistant) finalEl.dataset.mid = ids.assistant;
          finalEl.dataset.raw = replyText;
          finalEl.dataset.userText = value;
          attachAssistantBar(finalEl, replyText);
          if (msg2.needs_model) appendNeedsModelButton(finalEl, msg2.needs_kind);
          doneReceived = true;
          activeThinking = null;
          activeRun = null;
          turnResult = true;
            } catch (_) {
              turnResult = true;
            }
          })();
          return;
        } else if (msg2?.type === "error") {
          try {
            thinking.remove();
            if (shell) shell.remove();
          } catch (_) {}
          activeThinking = null;
          activeRun = null;
          const { el: errEl, p: perr } = appendAssistantShell();
          perr.textContent =
            `Error: ${[msg2.error, msg2.detail].filter(Boolean).join(" — ") || "request failed"}`;
          if (msg2.needs_model) appendNeedsModelButton(errEl, msg2.needs_kind);
          scrollToBottom();
          turnResult = true;
          return;
        }
    } catch (_) {}
  }
  onWsRaw(onFrame);
  try {
    for (;;) {
      if (turnResult !== null) break;
      try {
        if (!activeRun || activeRun.gen !== myGen) {
          turnResult = true;
          break;
        }
      } catch (_) {}
      await new Promise((r) => setTimeout(r, 50));
    }
  } finally {
    try {
      // Unsubscribe this turn (ws.js has no off; gen-guard makes it a no-op).
      turnResult = turnResult === null ? true : turnResult;
    } catch (_) {}
  }
  if (!doneReceived) {
    // Superseded turn (STOP / fresh answer / newer message took over):
    // exit quietly — no error text, no blocking fallback. Only a genuine
    // break of the OWNED stream still shows "Connection lost".
    if (!activeRun || activeRun.gen !== myGen) return true;
    if (!shell && !(thinking && thinking.isConnected && thinking._block)) {
      thinking.remove();
      return false;
    }
    const perr = document.createElement("p");
    perr.textContent = "Connection lost — please try again.";
    try {
      (shell || thinking).querySelector(".msg-content")?.appendChild(perr);
    } catch (_) {
      try {
        (shell || thinking).appendChild(perr);
      } catch (_) {}
    }
    scrollToBottom();
  }
  return turnResult === null ? true : turnResult;
}

const SEND_SVG = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><line x1="12" y1="19" x2="12" y2="5" /><polyline points="6 11 12 5 18 11" /></svg>`;
const STOP_SVG = `<svg viewBox="0 0 24 24" fill="currentColor"><rect x="6" y="6" width="12" height="12" rx="2" /></svg>`;

function setSending(sending) {
  if (!sendBtn) return;
  sendBtn.classList.toggle("stop", sending);
  sendBtn.innerHTML = sending ? STOP_SVG : SEND_SVG;
  sendBtn.setAttribute("aria-label", sending ? "Stop generation" : "Send message");
  sendBtn.title = sending ? "Stop generation" : "Send message";
  if (sending) {
    sendBtn.disabled = false;
  } else {
    updateSendEnabled();
  }
}

async function sendBlocking(value, attachments, thinking) {
  try {
    const res = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: value, attachments, mode: agentMode }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      // Fail clearly as an error state (never a canned reply): show the
      // server detail too so the real cause is visible, and carry the
      // no-model flag so the error bubble still offers the Models shortcut.
      const err = new Error(
        [data?.error, data?.detail].filter(Boolean).join(" — ") || `HTTP ${res.status}`
      );
      err.needsModel = Boolean(data?.needs_model);
      err.needsKind = String(data?.needs_kind || "text");
      throw err;
    }
    thinking.remove();
    activeThinking = null;
    const replyText = String(data?.reply || "");
    const finalEl = await typeMessage(replyText, data?.events, data?.elapsed);
    appendFinalImages(finalEl, data?.images);
    appendFinalVoice(finalEl, data?.audio);
    const ids = data?.ids || null;
    if (lastUserEl && ids && ids.user) lastUserEl.dataset.mid = ids.user;
    if (ids && ids.assistant) finalEl.dataset.mid = ids.assistant;
    finalEl.dataset.raw = replyText;
    finalEl.dataset.userText = value;
    attachAssistantBar(finalEl, replyText);
    if (data?.needs_model) appendNeedsModelButton(finalEl, data?.needs_kind);
  } catch (err) {
    thinking.remove();
    activeThinking = null;
    const { el: errEl, p } = appendAssistantShell();
    p.textContent = `Error: ${err instanceof Error ? err.message : String(err)}`;
    if (err && err.needsModel) appendNeedsModelButton(errEl, err.needsKind);
    scrollToBottom();
  }
}

async function handleSend() {
  // Editing mode: SEND saves the change into the SAME bubble (no new message).
  if (editingTarget) {
    await saveComposerEdit(composerInput.value);
    return;
  }
  // Open agent question: choice (or own reply) + SEND.
  if (pendingQuestion && pendingQuestion.kind === "ask") {
    await sendQuestionAnswer();
    return;
  }
  // Model-activation pause: the loop waits for Skip / Activate buttons only.
  if (pendingQuestion && pendingQuestion.kind === "needs_model") {
    toast("Pick Skip or Activate model first");
    return;
  }
  const value = composerInput.value.trim().slice(0, MAX_MESSAGE);
  const pending = pendingAttachments.slice();
  if (!value && !pending.length) return;
  if (isUploadingFiles()) {
    toast("Waiting for files to finish uploading…");
    return;
  }
  if (hasFailedFiles()) {
    toast("Remove failed files to send…");
    return;
  }

  // A new request supersedes any running generation.
  await abortActiveStream();

  lastUserEl = appendUserMessage(value, pending);
  attachUserBar(lastUserEl);
  if (value) lastUserText = value;
  hideMentionMenu();

  // Real file bytes go to the server (bounded, compressed); the agent loop analyzes them.
  // @image1.. labels in the text match the image order here and on the server.
  let attachments = [];
  try {
    for (const item of pending) {
      // Documents are already read at attach time (progress card); wait for
      // the read and reuse the bytes instead of reading twice.
      if (item._ready) {
        try {
          await item._ready;
        } catch (_) {}
      }
      const ready = item.type === "image" ? await maybeCompressImage(item.file) : item.file;
      const dataUrl = item.dataUrl || (await readAsDataURL(ready));
      if (item.type === "document" && !dataUrl) {
        toast("Remove failed files to send…");
        updateSendEnabled();
        return;
      }
      attachments.push({ type: item.type, name: ready.name || item.name, data: dataUrl });
    }
  } catch (_) {
    toast("File too large (image 8MB / video 24MB / document 100MB)");
    return;
  }

  composerInput.value = "";
  autoResize();
  clearAttachment();
  await executeAgentTurn(value, attachments);
}

async function executeAgentTurn(value, attachments) {
  // Runs one agent request WITHOUT appending a new user bubble.
  // Used by normal send (bubble already appended) and by resend/regenerate.
  // The composer stays enabled so the user can type during generation
  // (pressing SEND while generating stops the loop, typed text is kept).
  // A catch-up run from a pre-refresh generation no longer owns the UI.
  // A new local run takes over; completion still arrives via push.
  catchupResume = false;
  // A new agent turn may legitimately start the browser again.
  clearBrowserClosedFlag();
  document.getElementById("thinkingMsg")?.remove();
  activeThinking = null;
  setSending(true);

  const thinking = addThinking(value);
  try {
    let handled = false;
    try {
      handled = await sendStream(value, attachments, thinking);
    } catch (e) {
      if (e && e.aborted) return;
      handled = false;
    }
    if (!handled) await sendBlocking(value, attachments, thinking);
  } finally {
    // Only clear state this turn still owns — a newer turn may have taken
    // over while this one was winding down.
    try {
      if (!activeRun || activeRun.thinking === thinking) {
        activeRun = null;
        setSending(false);
      }
    } catch (_) {}
    composerInput.focus();
  }
}

function autoResize() {
  if (!composerInput) return;
  composerInput.style.height = "auto";
  composerInput.style.height = `${Math.min(composerInput.scrollHeight, 160)}px`;
  // Edge glow while the user is writing (simple, transform-free pulse in CSS).
  try {
    document.getElementById("composer")?.classList.toggle(
      "has-text", composerInput.value.trim().length > 0);
  } catch (_) {}
}

/* ---------- attachments (metadata only, never uploaded) ---------- */
function imageLabelFor(index) {
  return `image${index + 1}`;
}

function pendingImages() {
  return pendingAttachments.filter((a) => a.type === "image");
}

function formatSize(bytes) {
  const kb = bytes / 1024;
  return kb > 1024 ? `${(kb / 1024).toFixed(1)} MB` : `${Math.round(kb)} KB`;
}

function showAttachmentPreview(file, type) {
  if (pendingAttachments.length >= 2) {
    toast("Max 2 attachments");
    return;
  }
  const item = { type, name: file.name, file, thumb: null, uid: `doc${Date.now().toString(36)}${pendingAttachments.length}` };
  if (type !== "document") {
    try {
      item.thumb = URL.createObjectURL(file);
    } catch (_) {
      item.thumb = null;
    }
  }
  // Documents load async — send stays disabled until _done. Images/video are instant.
  item._done = type !== "document";
  pendingAttachments.push(item);
  renderAttachmentPreviews();
  updateSendEnabled();
  if (type === "document") startDocumentRead(item);
}

/* Document card: read once at attach time so the edge bar shows real progress.
   The bytes are reused at send — never read twice. */

/* Unified document icon: one glyph, tinted by file type only.
   Key always comes from this allowlist, so it is safe as a CSS class. */
const DOC_TYPE_EXTS = {
  pdf: ["pdf"],
  doc: ["doc", "docx"],
  xls: ["xls", "xlsx", "csv"],
  ppt: ["ppt", "pptx"],
};
function docTypeOf(name) {
  const ext =
    typeof name === "string" && name.includes(".") ? name.toLowerCase().split(".").pop() : "";
  for (const key of Object.keys(DOC_TYPE_EXTS)) {
    if (DOC_TYPE_EXTS[key].includes(ext)) return key;
  }
  return "txt";
}

/* Human label for the file type line (second row under the name). */
function docLabelFor(name) {
  const ext =
    typeof name === "string" && name.includes(".") ? name.toLowerCase().split(".").pop() : "";
  if (ext === "pdf") return "PDF";
  if (ext === "doc" || ext === "docx") return "Word";
  if (ext === "xls" || ext === "xlsx") return "Excel";
  if (ext === "csv") return "CSV";
  if (ext === "ppt" || ext === "pptx") return "PowerPoint";
  if (ext === "txt") return "Text";
  if (ext === "md") return "Markdown";
  if (ext === "json" || ext === "xml" || ext === "html") return ext.toUpperCase();
  return ext ? ext.toUpperCase() : "File";
}

function isUploadingFiles() {
  return pendingAttachments.some((a) => a && a.type === "document" && !a._done);
}

function hasFailedFiles() {
  return pendingAttachments.some((a) => a && a.type === "document" && a._done && !a.dataUrl);
}

function updateSendEnabled() {
  if (!sendBtn) return;
  // While generating the button is STOP and must stay clickable.
  if (sendBtn.classList.contains("stop")) return;
  // Send is enabled only when every attached file succeeded.
  const blocked = isUploadingFiles() || hasFailedFiles();
  sendBtn.disabled = blocked;
  sendBtn.classList.toggle("is-waiting", blocked);
  try {
    if (composerInput) {
      if (isUploadingFiles()) composerInput.placeholder = "Uploading files…";
      else if (hasFailedFiles()) composerInput.placeholder = "Remove failed files to send…";
      else composerInput.placeholder = "Message your agent…";
    }
  } catch (_) {}
}

function docRowFor(uid) {
  if (!composerAttachBox) return null;
  return composerAttachBox.querySelector(`[data-uid="${uid}"]`);
}

function updateDocProgress(uid, frac) {
  const row = docRowFor(uid);
  if (!row) return;
  const sizeEl = row.querySelector(".attach-doc-size");
  const p = Math.min(0.999, Math.max(0, frac || 0));
  try {
    row.style.setProperty("--p", p.toFixed(3));
  } catch (_) {}
  row.classList.add("is-loading");
  row.classList.remove("is-ready", "is-error");
  if (sizeEl) sizeEl.textContent = `Loading… ${Math.round(p * 100)}%`;
}

function finishDocRead(uid, ok, size) {
  const row = docRowFor(uid);
  const item = pendingAttachments.find((a) => a && a.uid === uid);
  if (item) {
    item._done = true;
    item._failed = !ok;
  }
  if (row) {
    const sizeEl = row.querySelector(".attach-doc-size");
    try {
      row.style.setProperty("--p", ok ? "1" : "0");
    } catch (_) {}
    row.classList.remove("is-loading");
    row.classList.add(ok ? "is-ready" : "is-error");
    if (sizeEl) sizeEl.textContent = ok ? formatSize(size) : "Failed to load";
  }
  updateSendEnabled();
}

function startDocumentRead(item) {
  item._done = false;
  item._failed = false;
  updateSendEnabled();
  updateDocProgress(item.uid, 0);
  item._ready = new Promise((resolve) => {
    const reader = new FileReader();
    reader.onprogress = (e) => {
      if (e.lengthComputable && e.total > 0) updateDocProgress(item.uid, e.loaded / e.total);
    };
    reader.onload = () => {
      item.dataUrl = typeof reader.result === "string" ? reader.result : null;
      finishDocRead(item.uid, !!item.dataUrl, item.file.size);
      resolve();
    };
    reader.onerror = () => {
      finishDocRead(item.uid, false, 0);
      resolve();
    };
    try {
      reader.readAsDataURL(item.file);
    } catch (_) {
      finishDocRead(item.uid, false, 0);
      resolve();
    }
  });
}

function removeAttachmentAt(index) {
  const item = pendingAttachments[index];
  const row = item ? docRowFor(item.uid) : null;
  const dropNow = () => {
    const at = item ? pendingAttachments.indexOf(item) : index;
    const gone = at >= 0 ? pendingAttachments.splice(at, 1)[0] : null;
    if (gone?.thumb) {
      try {
        URL.revokeObjectURL(gone.thumb);
      } catch (_) {}
    }
    renderAttachmentPreviews();
    updateSendEnabled();
  };
  // No row on screen (or reduced motion): remove instantly.
  if (!item || !row || row.classList.contains("leaving")) {
    if (!item) return;
    if (row && row.classList.contains("leaving")) return;
    dropNow();
    return;
  }
  try {
    if (window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      dropNow();
      return;
    }
  } catch (_) {}
  // Play the delete animation first. When it is the last card, collapse
  // the strip (while the fading card is still inside) so the composer
  // shrinks with animation instead of snapping, then clear the DOM.
  const isLastDoc = pendingAttachments.filter((a) => a && a.type === "document").length <= 1;
  row.classList.add("leaving");
  row.classList.remove("is-loading");
  if (isLastDoc && composerAttachBox) {
    setTimeout(() => {
      try {
        composerAttachBox.classList.add("collapsing");
      } catch (_) {}
    }, 120);
    setTimeout(() => {
      dropNow();
      try {
        composerAttachBox.classList.remove("collapsing");
      } catch (_) {}
    }, 470);
    return;
  }
  setTimeout(dropNow, 230);
}

function clearAttachment() {
  pendingAttachments.forEach((a) => {
    if (a.thumb) {
      try {
        URL.revokeObjectURL(a.thumb);
      } catch (_) {}
    }
  });
  pendingAttachments = [];
  renderAttachmentPreviews();
  updateSendEnabled();
  hideMentionMenu();
}

/* Fixed-size document card, rendered INSIDE the composer box (above writing):
   unified file icon, name / type / size rows, edge loading bar while reading,
   natural X (always on touch, hover on desktop). Send unlocks only on success. */
function appendDocumentCard(item, index, pos) {
  if (!composerAttachBox) return;
  const row = document.createElement("div");
  row.className = "attach-item attach-doc ft-" + docTypeOf(item.name);
  row.setAttribute("data-anim", "");
  row.setAttribute("data-uid", item.uid);
  // Entrance animation plays once per attached file — re-renders (second
  // file added, progress ticks) must not replay it. Staggered by position.
  if (!item._shown) {
    row.classList.add("anim");
    const slot = typeof pos === "number" ? pos : 0;
    row.style.animationDelay = `${Math.min(slot, 5) * 70}ms`;
    item._shown = true;
  }
  const loading = !item._done && !item.dataUrl;
  if (loading) row.classList.add("is-loading");
  else if (item.dataUrl) row.classList.add("is-ready");
  else if (item._done) row.classList.add("is-error");
  row.innerHTML =
    '<div class="attach-doc-icon" aria-hidden="true">' +
    '<svg class="attach-doc-file" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">' +
    '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>' +
    '<polyline points="14 2 14 8 20 8"/>' +
    '<line x1="9" y1="13" x2="15" y2="13"/>' +
    '<line x1="9" y1="17" x2="13" y2="17"/>' +
    "</svg>" +
    "</div>" +
    '<div class="attach-doc-meta">' +
    '<div class="attach-doc-top"><span class="attach-doc-name"></span>' +
    '<button type="button" class="attach-remove" aria-label="Remove attachment">×</button></div>' +
    '<div class="attach-doc-type"></div>' +
    '<div class="attach-doc-size">Loading… 0%</div>' +
    "</div>" +
    '<span class="attach-doc-bar" aria-hidden="true"></span>';
  row.querySelector(".attach-doc-name").textContent = item.name;
  row.querySelector(".attach-doc-name").title = item.name;
  row.querySelector(".attach-doc-type").textContent = docLabelFor(item.name);
  row.querySelector(".attach-remove").addEventListener("click", () => removeAttachmentAt(index));
  // Re-render after the read already finished (second attachment added):
  // restore the completed state instead of showing "uploading" again.
  if (item.dataUrl) {
    const doneSize = row.querySelector(".attach-doc-size");
    if (doneSize) doneSize.textContent = formatSize(item.file.size);
  } else if (item._done && !item.dataUrl) {
    row.classList.remove("is-loading");
    row.classList.add("is-error");
    const errSize = row.querySelector(".attach-doc-size");
    if (errSize) errSize.textContent = "Failed to load";
  }
  composerAttachBox.appendChild(row);
}

/* Document cards live inside the composer box. One render entry so the
   strip above (images/video/text) and the composer box never drift apart. */
function renderComposerDocuments() {
  if (!composerAttachBox) return;
  composerAttachBox.innerHTML = "";
  const docs = pendingAttachments.filter((a) => a.type === "document");
  if (!docs.length) {
    composerAttachBox.classList.remove("visible");
    return;
  }
  try {
    composerAttachBox.classList.remove("collapsing");
  } catch (_) {}
  composerAttachBox.classList.add("visible");
  docs.forEach((item, pos) => {
    appendDocumentCard(item, pendingAttachments.indexOf(item), pos);
  });
}

function renderAttachmentPreviews() {
  // Document cards render inside the composer box, not in the strip above.
  renderComposerDocuments();
  if (!attachPreview) return;
  attachPreview.innerHTML = "";
  // The strip above the composer is only for image/video/text — a lone
  // document must not leave an empty pill floating above the box.
  if (!pendingAttachments.some((a) => a.type !== "document")) {
    attachPreview.classList.remove("visible");
    return;
  }
  attachPreview.classList.add("visible");
  const images = pendingImages();
  pendingAttachments.forEach((item, index) => {
    if (item.type === "document") return;
    const row = document.createElement("div");
    row.className = "attach-item";
    const media =
      item.type === "text"
        ? `<span class="attach-kind">TXT</span>`
        : item.thumb && item.type === "image"
          ? `<img class="attach-thumb" src="${item.thumb}" alt="" />`
          : item.thumb
            ? `<video class="attach-thumb" src="${item.thumb}" muted preload="metadata"></video>`
            : `<span class="attach-kind">${item.type === "image" ? "Image" : "Video"}</span>`;
    const tag =
      item.type === "image" ? `<span class="attach-tag">@${imageLabelFor(images.indexOf(item))}</span>` : "";
    row.innerHTML = `${media}<span class="attach-name"></span>${tag}<span class="attach-size">${escapeHtml(formatSize(item.file.size))}</span><button type="button" class="attach-remove" aria-label="Remove">×</button>`;
    row.querySelector(".attach-name").textContent = item.name;
    row.querySelector(".attach-remove").addEventListener("click", () => removeAttachmentAt(index));
    attachPreview.appendChild(row);
  });
}

function initAttachments() {
  const attachBtn = document.getElementById("attachBtn");
  const attachMenu = document.getElementById("attachMenu");
  if (!attachBtn || !attachMenu) return;

  attachBtn.addEventListener("click", (e) => {
    e.stopPropagation();
    // Only one composer menu at a time — opening this one closes the mode menu.
    document.getElementById("modeMenu")?.classList.remove("open");
    const open = attachMenu.classList.toggle("open");
    attachBtn.classList.toggle("active", open);
  });
  document.addEventListener("click", (e) => {
    if (!attachMenu.classList.contains("open")) return;
    if (attachMenu.contains(e.target) || e.target === attachBtn) return;
    attachMenu.classList.remove("open");
    attachBtn.classList.remove("active");
  });

  attachMenu.querySelectorAll("button[data-attach]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const type = btn.dataset.attach;
      const input = document.getElementById(
        type === "image" ? "fileInputImage" : type === "video" ? "fileInputVideo" : "fileInputDocument"
      );
      attachMenu.classList.remove("open");
      attachBtn.classList.remove("active");
      if (!input) return;
      input.onchange = () => {
        if (input.files && input.files[0]) {
          const f = input.files[0];
          const limit = type === "document" ? 100 * 1024 * 1024 : 25 * 1024 * 1024;
          if (f.size > limit) {
            toast(type === "document" ? "Document too large (max 100MB)" : "File too large (max 25MB)");
          } else {
            showAttachmentPreview(f, type);
          }
        }
        input.value = "";
      };
      setTimeout(() => input.click(), 80);
    });
  });
}

/* ---------- voice: record -> local Whisper STT -> raw text into composer ---------- */
let mediaRecorder = null;
let audioChunks = [];
let isRecording = false;
let recStopTimer = null;
let audioCtx = null;
let voiceRaf = null;
let voiceTimerInt = null;
let voiceStartAt = 0;
let voiceSendAfter = false;
let voiceDiscarded = false;

function formatVoiceTime(ms) {
  const s = Math.floor(ms / 1000);
  const mm = String(Math.floor(s / 60)).padStart(2, "0");
  const ss = String(s % 60).padStart(2, "0");
  return `${mm}:${ss}`;
}

function setVoiceLabel(text) {
  const label = document.getElementById("voiceLabel");
  if (label) label.textContent = text;
}

/* Smooth height morph (FLIP): the container eases to the visible template's
   height while templates crossfade with a gentle rise. Bounded 260ms to
   match the CSS rhythm; cleanup always restores auto height. */
function morphSwitchHeight(switchEl, showEl, hideEl) {
  if (!switchEl || !showEl) return;
  try {
    const from = switchEl.offsetHeight;
    showEl.classList.remove("is-hidden");
    if (hideEl) hideEl.classList.add("is-hidden");
    const to = showEl.offsetHeight;
    if (!from || !to || from === to) return;
    switchEl.classList.add("animating-height");
    switchEl.style.height = `${from}px`;
    void switchEl.offsetHeight;
    switchEl.style.height = `${to}px`;
    setTimeout(() => {
      switchEl.style.height = "";
      switchEl.classList.remove("animating-height");
    }, 300);
  } catch (_) {
    switchEl.style.height = "";
    switchEl.classList.remove("animating-height");
  }
}

function showVoicePanel() {
  const panel = document.getElementById("voicePanel");
  const composer = document.getElementById("composer");
  if (!panel || !composer) return;
  clearVoiceLoading();
  panel.classList.remove("transcribing");
  // Instant swap to the shorter recording template (see morphSwitchHeight).
  const switchEl = panel?.closest(".composer-switch");
  morphSwitchHeight(switchEl, panel, composer);
  const timer = document.getElementById("voiceTimer");
  voiceStartAt = Date.now();
  if (timer) timer.textContent = "00:00";
  if (voiceTimerInt) clearInterval(voiceTimerInt);
  voiceTimerInt = setInterval(() => {
    if (timer) timer.textContent = formatVoiceTime(Date.now() - voiceStartAt);
  }, 250);
  setVoiceLabel("Recording…");
}

function hideVoicePanel() {
  const panel = document.getElementById("voicePanel");
  const composer = document.getElementById("composer");
  if (voiceTimerInt) {
    clearInterval(voiceTimerInt);
    voiceTimerInt = null;
  }
  clearVoiceLoading();
  // Swap back: the writing template returns instantly with the text inside.
  const switchEl = panel?.closest(".composer-switch");
  if (panel) panel.classList.remove("transcribing");
  morphSwitchHeight(switchEl, composer, panel);
}

function startVoiceBars(stream) {
  // True voice-level bars: time-domain RMS gates the overall height
  // (silence stays flat), per-band spectrum shapes each bar.
  // Fast attack + slow release keeps motion smooth and honest.
  try {
    stopVoiceBars();
    const AC = window.AudioContext || window.webkitAudioContext;
    if (!AC) return;
    audioCtx = new AC();
    const src = audioCtx.createMediaStreamSource(stream);
    const analyser = audioCtx.createAnalyser();
    analyser.fftSize = 256;
    analyser.smoothingTimeConstant = 0.55;
    src.connect(analyser);
    const wave = document.getElementById("voiceWave");
    if (!wave) return;
    wave.innerHTML = "";
    const COUNT = 32;
    for (let i = 0; i < COUNT; i++) {
      const b = document.createElement("span");
      b.className = "voice-bar";
      wave.appendChild(b);
    }
    const freq = new Uint8Array(analyser.frequencyBinCount);
    const time = new Uint8Array(analyser.fftSize);
    const spans = [...wave.children];
    const smooth = new Array(spans.length).fill(4);
    const loop = () => {
      if (!wave.isConnected || !wave.children.length) return;
      analyser.getByteTimeDomainData(time);
      let sum = 0;
      for (let i = 0; i < time.length; i++) {
        const v = (time[i] - 128) / 128;
        sum += v * v;
      }
      const rms = Math.sqrt(sum / time.length);
      const level = Math.min(1, Math.max(0, (rms - 0.015) / 0.32));
      analyser.getByteFrequencyData(freq);
      spans.forEach((sp, i) => {
        const band = freq[Math.floor((i / spans.length) * freq.length * 0.7)] || 0;
        const target = 4 + level * (6 + (band / 255) * 30);
        const cur = smooth[i];
        smooth[i] = target > cur ? target : cur + (target - cur) * 0.35;
        sp.style.height = `${Math.round(smooth[i])}px`;
      });
      voiceRaf = requestAnimationFrame(loop);
    };
    loop();
  } catch (_) {
    stopVoiceBars();
  }
}

function stopVoiceBars() {
  try {
    if (voiceRaf) cancelAnimationFrame(voiceRaf);
  } catch (_) {}
  voiceRaf = null;
  const wave = document.getElementById("voiceWave");
  if (wave) wave.innerHTML = "";
  document.getElementById("voiceBars")?.remove();
  if (audioCtx) {
    audioCtx.close().catch(() => {});
    audioCtx = null;
  }
}

function setMicState(state) {
  const micBtn = document.getElementById("micBtn");
  if (!micBtn) return;
  micBtn.classList.toggle("listening", state === "recording");
  micBtn.classList.toggle("loading", state === "loading");
}

async function startRecording() {
  if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === "undefined") {
    toast("Voice input not supported here");
    return;
  }
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    const mime =
      ["audio/webm", "audio/mp4", "audio/ogg"].find((m) => {
        try {
          return MediaRecorder.isTypeSupported(m);
        } catch (_) {
          return false;
        }
      }) || "";
    mediaRecorder = mime ? new MediaRecorder(stream, { mimeType: mime }) : new MediaRecorder(stream);
    audioChunks = [];
    mediaRecorder.ondataavailable = (e) => {
      if (e.data && e.data.size) audioChunks.push(e.data);
    };
    mediaRecorder.onstop = () => {
      stream.getTracks().forEach((t) => t.stop());
      finishRecording();
    };
    mediaRecorder.start();
    isRecording = true;
    voiceSendAfter = false;
    voiceDiscarded = false;
    setMicState("recording");
    showVoicePanel();
    startVoiceBars(stream);
    recStopTimer = setTimeout(stopRecording, 60000);
  } catch (_) {
    stopVoiceBars();
    hideVoicePanel();
    toast("Mic unavailable");
  }
}

function stopRecording() {
  if (recStopTimer) {
    clearTimeout(recStopTimer);
    recStopTimer = null;
  }
  if (mediaRecorder && isRecording) {
    isRecording = false;
    try {
      mediaRecorder.stop();
    } catch (_) {
      setMicState("idle");
    }
  }
}

function cancelVoiceRecording() {
  // Discard the take entirely — no transcription, back to the composer.
  voiceDiscarded = true;
  voiceSendAfter = false;
  if (recStopTimer) {
    clearTimeout(recStopTimer);
    recStopTimer = null;
  }
  if (mediaRecorder && isRecording) {
    isRecording = false;
    const rec = mediaRecorder;
    rec.onstop = () => {
      try {
        rec.stream?.getTracks().forEach((t) => t.stop());
      } catch (_) {}
    };
    try {
      rec.stop();
    } catch (_) {}
  }
  audioChunks = [];
  stopVoiceBars();
  setMicState("idle");
  hideVoicePanel();
  composerInput?.focus();
}

function stopVoiceToComposer() {
  // Stop -> transcribe -> text appears in the writing box (user sends manually).
  voiceSendAfter = false;
  setVoiceTranscribing("voiceStopBtn");
  stopRecording();
}

function sendVoiceDirectly() {
  // Stop -> transcribe -> send immediately without manual step.
  voiceSendAfter = true;
  setVoiceTranscribing("voiceSendBtn");
  stopRecording();
}

function setVoiceTranscribing(activeBtnId) {
  // Freeze the counter the moment transcription starts.
  if (voiceTimerInt) {
    clearInterval(voiceTimerInt);
    voiceTimerInt = null;
  }
  const panel = document.getElementById("voicePanel");
  if (panel) panel.classList.add("transcribing");
  // Loading spinner runs inside the pressed button until transcription finishes.
  ["voiceStopBtn", "voiceSendBtn"].forEach((id) => {
    document.getElementById(id)?.classList.toggle("loading", id === activeBtnId);
  });
  setVoiceLabel("Transcribing…");
}

function clearVoiceLoading() {
  ["voiceStopBtn", "voiceSendBtn"].forEach((id) => {
    document.getElementById(id)?.classList.remove("loading");
  });
}

async function finishRecording() {
  if (voiceDiscarded) {
    voiceDiscarded = false;
    voiceSendAfter = false;
    return;
  }
  setMicState("loading");
  setVoiceTranscribing();
  try {
    const blob = new Blob(audioChunks, { type: (mediaRecorder && mediaRecorder.mimeType) || "audio/webm" });
    audioChunks = [];
    if (!blob.size) throw new Error("Empty recording");
    const dataUrl = await new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(String(reader.result || ""));
      reader.onerror = () => reject(new Error("read failed"));
      reader.readAsDataURL(blob);
    });
    const res = await fetch("/api/stt", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ audio: dataUrl }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data?.error || `HTTP ${res.status}`);
    const text = String(data?.text || "").trim();
    if (!text) {
      hideVoicePanel();
      toast("No speech detected");
    } else {
      // Raw transcript first — the user edits freely, then sends to the agent normally.
      composerInput.value = `${composerInput.value} ${text}`.trim().slice(0, MAX_MESSAGE);
      autoResize();
      hideVoicePanel();
      if (voiceSendAfter) {
        voiceSendAfter = false;
        composerInput.focus();
        await handleSend();
        return;
      }
      composerInput.focus();
    }
  } catch (err) {
    hideVoicePanel();
    toast(err instanceof Error ? err.message : "Voice failed");
  } finally {
    voiceSendAfter = false;
    stopVoiceBars();
    setMicState("idle");
  }
}

function initMic() {
  const micBtn = document.getElementById("micBtn");
  if (micBtn) {
    micBtn.addEventListener("click", () => {
      if (micBtn.classList.contains("loading")) return;
      if (isRecording) stopVoiceToComposer();
      else startRecording();
    });
  }
  document.getElementById("voiceCancelBtn")?.addEventListener("click", cancelVoiceRecording);
  document.getElementById("voiceStopBtn")?.addEventListener("click", () => {
    if (isRecording) stopVoiceToComposer();
  });
  document.getElementById("voiceSendBtn")?.addEventListener("click", () => {
    if (isRecording) sendVoiceDirectly();
  });
}

/* ---------- message gestures: hover (desktop CSS) + long-press (touch) ---------- */
function hideAllUserBars() {
  chatInner?.querySelectorAll(".msg .msg-actions.show").forEach((b) => b.classList.remove("show"));
}

function initUserMessageGestures() {
  if (!chatInner) return;
  const LONG_MS = 550;
  let lpTimer = null;
  let startX = 0;
  let startY = 0;
  const clearLp = () => {
    if (lpTimer) clearTimeout(lpTimer);
    lpTimer = null;
  };
  chatInner.addEventListener(
    "touchstart",
    (e) => {
      if (e.touches.length !== 1) return;
      const msg = e.target.closest?.(".msg");
      const bar = msg?.querySelector(":scope > .msg-actions");
      if (!msg || !bar) return;
      const t = e.touches[0];
      startX = t.clientX;
      startY = t.clientY;
      clearLp();
      lpTimer = setTimeout(() => {
        hideAllUserBars();
        bar.classList.add("show");
        try {
          navigator.vibrate?.(10);
        } catch (_) {}
        lpTimer = null;
      }, LONG_MS);
    },
    { passive: true }
  );
  chatInner.addEventListener(
    "touchmove",
    (e) => {
      if (!lpTimer) return;
      const t = e.touches[0];
      if (!t) return;
      if (Math.abs(t.clientX - startX) > 10 || Math.abs(t.clientY - startY) > 10) clearLp();
    },
    { passive: true }
  );
  ["touchend", "touchcancel"].forEach((ev) => chatInner.addEventListener(ev, clearLp, { passive: true }));
  document.addEventListener(
    "touchstart",
    (e) => {
      if (!e.target.closest?.(".msg")) hideAllUserBars();
    },
    { passive: true }
  );
  chatScroll?.addEventListener("scroll", hideAllUserBars, { passive: true });
}

/* ---------- @mention image picker ---------- */
function hideMentionMenu() {
  if (mentionMenu) {
    mentionMenu.remove();
    mentionMenu = null;
  }
}

function showMentionMenu(query) {
  const images = pendingImages();
  const q = String(query || "").toLowerCase();
  const matches = images
    .map((item, i) => ({ item, label: `@${imageLabelFor(i)}` }))
    .filter((m) => m.label.toLowerCase().includes(q));
  if (!matches.length) {
    hideMentionMenu();
    return;
  }
  if (!mentionMenu) {
    mentionMenu = document.createElement("div");
    mentionMenu.className = "mention-menu";
    document.querySelector(".composer")?.appendChild(mentionMenu);
  }
  mentionMenu.innerHTML = "";
  const title = document.createElement("div");
  title.className = "mention-title";
  title.textContent = "Attach to message";
  mentionMenu.appendChild(title);
  matches.forEach((m) => {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "mention-item";
    btn.innerHTML = `<span class="mention-label"></span><span class="mention-name"></span>`;
    btn.querySelector(".mention-label").textContent = m.label;
    btn.querySelector(".mention-name").textContent = m.item.name;
    btn.addEventListener("click", () => insertMention(m.label));
    mentionMenu.appendChild(btn);
  });
}

function insertMention(label) {
  if (!composerInput) return;
  const pos = composerInput.selectionStart ?? composerInput.value.length;
  const before = composerInput.value.slice(0, pos);
  const after = composerInput.value.slice(pos);
  const at = before.lastIndexOf("@");
  const head = at >= 0 ? before.slice(0, at) : before;
  composerInput.value = `${head}${label} ${after}`;
  hideMentionMenu();
  composerInput.focus();
  autoResize();
}

function checkMention() {
  if (!composerInput) return;
  const pos = composerInput.selectionStart ?? composerInput.value.length;
  const before = composerInput.value.slice(0, pos);
  const m = before.match(/@(\w*)$/);
  if (m && pendingImages().length) showMentionMenu(m[1]);
  else hideMentionMenu();
}

/* ---------- agent mode (restricted default, full access with notice) ---------- */
function persistMode() {
  try {
    localStorage.setItem(MODE_KEY, agentMode);
  } catch (_) {}
}

function renderModeMenu() {
  const menu = document.getElementById("modeMenu");
  const btn = document.getElementById("modeBtn");
  if (!menu) return;
  if (btn) btn.classList.toggle("full", agentMode === "free");
  const modeLabel = document.getElementById("modeLabel");
  if (modeLabel) modeLabel.textContent = agentMode === "free" ? "Full access" : "Restricted";
  if (btn) btn.title = agentMode === "free" ? "Full access — terminal runs without asking" : "Restricted — terminal asks first";
  menu.innerHTML = "";
  const head = document.createElement("div");
  head.className = "mode-head";
  head.textContent = "Agent permission";
  menu.appendChild(head);
  [
    { val: "restricted", title: "Restricted", desc: "Ask before any terminal command" },
    { val: "free", title: "Full access", desc: "Terminal runs without asking" },
  ].forEach((opt) => {
    const b = document.createElement("button");
    b.type = "button";
    b.dataset.mode = opt.val;
    b.className = "mode-opt" + (agentMode === opt.val ? " active" : "");
    const radio = document.createElement("span");
    radio.className = "radio";
    radio.setAttribute("aria-hidden", "true");
    const icon = document.createElement("span");
    icon.className = "mode-opt-icon";
    icon.setAttribute("aria-hidden", "true");
    icon.innerHTML = opt.val === "restricted"
      ? `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 3.5 19 6v5.2c0 4.2-2.9 7.3-7 9.2-4.1-1.9-7-5-7-9.2V6l7-2.5Z"/><path d="m8.8 12 2 2 4.4-4.6"/></svg>`
      : `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M13 2.8 4.8 13.2h5.4L9 21.2l8.2-10.4h-5.4L13 2.8Z"/></svg>`;
    const txt = document.createElement("span");
    const title = document.createElement("b");
    title.textContent = opt.title;
    const desc = document.createElement("span");
    desc.className = "desc";
    desc.textContent = opt.desc + (opt.val === "restricted" ? " (default)" : "");
    txt.appendChild(title);
    txt.appendChild(desc);
    b.appendChild(icon);
    b.appendChild(txt);
    b.appendChild(radio);
    b.addEventListener("click", () => {
      menu.classList.remove("open");
      if (opt.val === "free" && agentMode !== "free") {
        showFullAccessWarning();
        return;
      }
      agentMode = opt.val;
      persistMode();
      renderModeMenu();
    });
    menu.appendChild(b);
  });
}

function initModeMenu() {
  const btn = document.getElementById("modeBtn");
  const menu = document.getElementById("modeMenu");
  if (!btn || !menu) return;
  renderModeMenu();
  btn.addEventListener("click", (e) => {
    e.stopPropagation();
    document.getElementById("attachMenu")?.classList.remove("open");
    document.getElementById("attachBtn")?.classList.remove("active");
    menu.classList.toggle("open");
  });
  document.addEventListener("click", (e) => {
    if (!menu.classList.contains("open")) return;
    if (menu.contains(e.target) || e.target === btn || btn.contains(e.target)) return;
    menu.classList.remove("open");
  });
}

/* ---------- full-access warning dialog with 5s countdown accept ---------- */
function showFullAccessWarning() {
  openModal(`
    <div class="modal-head">
      <div class="tile"><span class="tile-letters">!</span></div>
      <div class="modal-head-text"><h3>Enable Full access?</h3><p>Read carefully before accepting</p></div>
      <button class="modal-close" data-close type="button" aria-label="Close">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round">
          <line x1="18" y1="6" x2="6" y2="18" /><line x1="6" y1="6" x2="18" y2="18" />
        </svg>
      </button>
    </div>
    <div class="modal-body">
      <ul class="warn-list">
        <li>Terminal commands run <b>immediately</b> with no approval step.</li>
        <li>The agent can read, write, and delete files on this machine.</li>
        <li>Only use this on a machine you own and trust.</li>
        <li>You can switch back to <b>Restricted</b> anytime from the same menu.</li>
      </ul>
    </div>
    <div class="modal-foot">
      <button class="btn" id="fullAccessDecline" type="button">Decline</button>
      <button class="btn btn-primary" id="fullAccessAccept" type="button" disabled>Accept (5)</button>
    </div>`);
  const acceptBtn = document.getElementById("fullAccessAccept");
  const declineBtn = document.getElementById("fullAccessDecline");
  if (!acceptBtn) return;
  let left = 5;
  const timer = setInterval(() => {
    if (!acceptBtn.isConnected) {
      clearInterval(timer);
      return;
    }
    left -= 1;
    if (left <= 0) {
      clearInterval(timer);
      acceptBtn.disabled = false;
      acceptBtn.textContent = "Accept";
      acceptBtn.focus();
    } else {
      acceptBtn.textContent = `Accept (${left})`;
    }
  }, 1000);
  declineBtn?.addEventListener("click", () => {
    clearInterval(timer);
    closeModal();
  });
  acceptBtn.addEventListener("click", () => {
    if (acceptBtn.disabled) return;
    clearInterval(timer);
    agentMode = "free";
    persistMode();
    renderModeMenu();
    closeModal();
  });
}

/* ---------- question / approval cards ----------
   Ask = fused strip above the composer: lettered choice rows on top, the
   composer itself is the custom reply (essay = question only, no rows).
   Selection never sends — SEND posts the answer straight into the paused
   run, where it lands inside the question step's arrow (Q + A) and the loop
   continues from there (no user bubble, no restart). While asking, + / mode
   / mic hide (composer.asking), STOP flips to SEND, and Working freezes
   until the answer is sent. */
function pauseWorkingLoop() {
  // The loop is paused on this question (server blocks for the answer), so
  // the Working square freezes too — no motion while waiting. Never throws.
  try {
    const sq = activeThinking && activeThinking._block && activeThinking._block._dotSquare;
    if (sq) stopSquareAnimation(sq);
  } catch (_) {}
}

function resumeWorkingLoop() {
  // The answer is on its way — Working moves again (unless settled). Never throws.
  try {
    const blk = activeThinking && activeThinking._block;
    if (blk && blk.isConnected && blk.dataset.settled !== "1" && blk._dotSquare) {
      startSquareAnimation(blk._dotSquare);
    }
  } catch (_) {}
}

function hideQuestionCard() {
  const card = document.getElementById("questionCard");
  pendingQuestion = null;
  try {
    document.getElementById("composer")?.classList.remove("asking");
  } catch (_) {}
  if (!card) return;
  // Exit runs as keyframes (ask-out), never a transition: swapping the
  // animation property always moves, even with a retained enter fill.
  // (Never set inline animation:none here — inline wins over the stylesheet
  // and would cancel the exit itself.)
  card.classList.add("leaving");
  setTimeout(() => {
    try {
      card.remove();
    } catch (_) {}
    // The strip is gone — give the freed space back, unless a newer strip
    // already took its place (it owns the padding now).
    try {
      if (!document.getElementById("questionCard")) {
        const sc = document.getElementById("chatScroll");
        if (sc) sc.style.paddingBottom = "";
      }
    } catch (_) {}
  }, 220);
  resumeWorkingLoop();
  // The run is still generating after the strip leaves — back to STOP.
  try {
    const stillRunning = !!document.getElementById("thinkingMsg") || !!activeThinking;
    if (stillRunning && sendBtn && !sendBtn.classList.contains("stop")) setSending(true);
  } catch (_) {}
}

function showQuestionCard(q) {
  hideQuestionCard();
  pendingQuestion = { id: q.id, kind: q.kind || "ask", selected: null };
  const wrap = document.querySelector(".composer-wrap");
  if (!wrap) return;
  const card = document.createElement("div");
  card.className = "question-card";
  card.id = "questionCard";
  if (pendingQuestion.kind === "needs_model") {
    // Paused tool needs a model kind: Skip continues without it,
    // Activate jumps to AI Models and auto-opens the right dialog.
    const kind = String(q.needs_kind || "text").toLowerCase();
    const tool = String(q.tool || "");
    card.classList.add("needs-model");
    card.innerHTML = `
      <div class="question-kind">Model needed</div>
      <div class="question-text"></div>
      <div class="question-options">
        <button type="button" class="glass-btn" data-decide="skip">Skip</button>
        <button type="button" class="glass-btn accent" data-decide="activate">Activate model</button>
      </div>`;
    card.querySelector(".question-text").textContent =
      q.text || (tool ? `Tool ${tool} needs a model that is not connected.` : "This task needs a model that is not connected.");
    card.querySelectorAll("[data-decide]").forEach((btn) => {
      btn.addEventListener("click", () => decideNeedsModel(q.id, btn.dataset.decide, kind));
    });
    pauseWorkingLoop();
  } else if (pendingQuestion.kind === "approval") {
    card.innerHTML = `
      <div class="question-kind">Approval needed</div>
      <div class="question-command"></div>
      <div class="question-options">
        <button type="button" class="btn btn-primary" data-decide="continue">Continue</button>
        <button type="button" class="btn btn-danger" data-decide="stop">Stop</button>
      </div>`;
    card.querySelector(".question-command").textContent = q.command || q.text || "Run command?";
    card.querySelectorAll("[data-decide]").forEach((btn) => {
      btn.addEventListener("click", () => decideApproval(q.id, btn.dataset.decide));
    });
  } else {
    card.classList.add("ask");
    card.innerHTML = `
      <div class="question-kind">Agent question</div>
      <div class="question-text"></div>
      <div class="question-chips"></div>`;
    card.querySelector(".question-text").textContent = q.text || "";
    const box = card.querySelector(".question-chips");
    const choices = Array.isArray(q.choices) ? q.choices.slice(0, 8) : [];
    const LETTERS = ["A", "B", "C", "D", "E", "F", "G", "H"];
    choices.forEach((c, idx) => {
      const b = document.createElement("button");
      b.type = "button";
      b.className = "q-chip";
      b.style.animationDelay = `${80 + idx * 55}ms`;
      const key = document.createElement("span");
      key.className = "q-chip-key";
      key.textContent = LETTERS[idx] || String(idx + 1);
      key.setAttribute("aria-hidden", "true");
      b.appendChild(key);
      const label = document.createElement("span");
      label.className = "q-chip-label";
      label.textContent = String(c).slice(0, 120);
      label.setAttribute("dir", "auto");
      b.appendChild(label);
      b.setAttribute("aria-pressed", "false");
      b.addEventListener("click", () => {
        if (pendingQuestion) pendingQuestion.selected = String(c);
        box.querySelectorAll(".q-chip").forEach((x) => {
          const on = x === b;
          x.classList.toggle("selected", on);
          x.setAttribute("aria-pressed", on ? "true" : "false");
        });
        try {
          composerInput.focus();
        } catch (_) {}
      });
      box.appendChild(b);
    });
    try {
      document.getElementById("composer")?.classList.add("asking");
    } catch (_) {}
    // The loop is paused on this question: STOP becomes SEND so the answer
    // can actually be sent (choice or typed text via the normal SEND).
    try {
      setSending(false);
      if (sendBtn) {
        sendBtn.disabled = false;
        sendBtn.classList.remove("is-waiting");
      }
    } catch (_) {}
    pauseWorkingLoop();
  }
  const composerNode = document.getElementById("composer");
  if (composerNode) wrap.insertBefore(card, composerNode.closest(".composer-switch") || composerNode);
  else wrap.prepend(card);
  // The strip overlays the scroll: free its height + margin below the last
  // message so it never covers it while visible.
  try {
    const sc = document.getElementById("chatScroll");
    if (sc) sc.style.paddingBottom = `${200 + (card.offsetHeight || 160) + 24}px`;
  } catch (_) {}
  scrollToBottom();
}

async function decideApproval(qid, decision) {
  // Instant action — resumes the loop silently, no chat message.
  hideQuestionCard();
  try {
    await wsAnswer({ id: qid, decision });
  } catch (_) {
    toast("Answer failed");
  }
}

async function decideNeedsModel(qid, decision, kind) {
  // Skip resumes the loop without the tool; activate stops it and
  // deep-links to AI Models with that dialog auto-opened.
  hideQuestionCard();
  try {
    await wsAnswer({ id: qid, decision });
  } catch (_) {
    toast("Answer failed");
    return;
  }
  if (decision === "activate") openModelPage(kind);
}

/* ---------------- live browser: control card + floating preview ---------------- */
function liveFrame(url) {
  // Remote console (funnel): loopback viewer links are rewritten to the
  // funnel /live/ route on this page's host, or the iframe can never reach
  // them. Local pages keep the original URL untouched.
  let clean = remoteLiveUrl(url);
  if (!clean) clean = String(url || "");
  // Mixed-content guard: an http viewer inside an https console is blocked
  // outright by the browser. Best-effort upgrade to https so the live view
  // still opens when the relay serves TLS; otherwise the original URL stays.
  try {
    if (location.protocol === "https:" && clean.startsWith("http://")) {
      clean = "https://" + clean.slice("http://".length);
    }
  } catch (_) {}
  const frame = document.createElement("iframe");
  frame.className = "live-frame";
  frame.src = clean;
  frame.title = "Live browser";
  frame.setAttribute("allow", "clipboard-read; clipboard-write");
  frame.setAttribute("sandbox", "allow-scripts allow-same-origin allow-forms allow-pointer-lock");
  // Eager: the live view must stay connected while scrolling or tabbing away.
  frame.loading = "eager";
  return frame;
}

// The noVNC viewer (rfb.js inside the live iframe) warns
// "noVNC requires a secure context (TLS)" when the console is opened over
// plain http on a LAN IP. localhost and https are secure contexts and stay
// quiet. This explains it once instead of letting the console warn alone.
let insecureLiveWarned = false;
function warnInsecureLiveOnce() {
  if (insecureLiveWarned) return;
  let insecure = false;
  try {
    insecure = window.isSecureContext === false;
  } catch (_) {}
  if (!insecure) return;
  insecureLiveWarned = true;
  try {
    toast("Live view is on an insecure connection — open via localhost or HTTPS to silence the noVNC secure-context warning");
  } catch (_) {}
}

function showLiveControlCard(q) {
  // Big in-steps handoff card: the browser interactive inside, fullscreen +
  // confirm-and-continue only, no ×. No default sentences anywhere: the ask
  // line renders only when the handoff carries real text, otherwise it is gone.
  // Always the last window; closing animates out.
  hideQuestionCard();
  document.getElementById("liveCard")?.remove();
  pendingQuestion = { id: q.id, kind: "live", selected: null };
  const url = String(q.url || "");
  if (!url) return;
  // A handoff owns the screen: the floating preview window and the right-edge
  // tab close first (and stay hidden until the user continues), so only the
  // handoff card remains — never a preview lurking behind it.
  manualControlOpen = true;
  pendingLiveQid = q.id;
  stopLiveWatch();
  try {
    document.getElementById("liveMini")?.remove();
    document.getElementById("browserTab")?.classList.add("off");
  } catch (_) {}
  const ask = String(q.reason || "").trim().slice(0, 500);
  const host = chatInner || document.body;
  const card = document.createElement("div");
  card.className = "live-card";
  card.id = "liveCard";
  card.innerHTML = `
    ${ask ? `<div class="live-ask"></div>` : ""}
    <div class="live-body"></div>
    <div class="live-actions">
      <button type="button" class="live-full" data-act="full" aria-label="Open fullscreen in a new tab">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M15 3h6v6" /><path d="M9 21H3v-6" /><path d="M21 3l-7 7" /><path d="M3 21l7-7" /></svg>
      </button>
      <button type="button" class="glass-btn live-continue">Confirm &amp; Continue</button>
    </div>`;
  if (ask) {
    card.querySelector(".live-ask").textContent = ask;
    card.querySelector(".live-ask").setAttribute("dir", "auto");
  }
  card.querySelector(".live-body").appendChild(liveFrame(url));
  card.querySelector('[data-act="full"]').addEventListener("click", () => {
    try {
      window.open(remoteLiveUrl(url) || url, "_blank", "noopener");
    } catch (_) {}
  });
  card.querySelector(".live-continue").addEventListener("click", async (e) => {
    const btn = e.currentTarget;
    btn.disabled = true;
    btn.textContent = "…";
    try {
      const res = await wsAnswer({ id: q.id, decision: "continue" });
      if (!res.ok) throw new Error("bad status");
      pendingQuestion = null;
      // Handoff over: the preview may come back (the resumed loop re-attaches).
      manualControlOpen = false;
      pendingLiveQid = null;
      card.classList.add("leaving");
      setTimeout(() => card.remove(), 200);
      toast("Resumed");
    } catch (_) {
      btn.disabled = false;
      btn.textContent = "Confirm & Continue";
      toast("Answer failed");
    }
  });
  host.appendChild(card);
  scrollToBottom();
}

/* ---------- right-edge browser tab: fixed right, vertical drag only ---------- */
let browserStreamUrl = "";
let pendingLiveQid = null;
// Pinned to the right edge on desktop and phone alike. Only the vertical
// offset is remembered; there is no horizontal position and no shrinking.
let browserTabY = null;
// While the big manual control card is open, the preview template stays hidden.
let manualControlOpen = false;

function placeTab(x, y) {
  // Fixed-right rail: the tab never leaves the right edge. The first arg is
  // kept for caller compatibility but ignored — only the vertical position
  // moves, clamped on-screen. Left/right are pinned so no axis can fight
  // and stretch the tab.
  const tab = document.getElementById("browserTab");
  if (!tab) return;
  const h = tab.offsetHeight || 150;
  const top = (typeof y === "number" ? y : (typeof x === "number" ? x : 0)) || 0;
  const cy = Math.min(Math.max(60, top), Math.max(60, window.innerHeight - h - 8));
  browserTabY = cy;
  tab.style.left = "auto";
  tab.style.right = "0";
  tab.style.bottom = "auto";
  tab.style.top = `${cy}px`;
}

let tabClampBound = false;
function bindTabClampOnce() {
  // Rotation/resize must never strand a freely-placed tab off-screen.
  if (tabClampBound) return;
  tabClampBound = true;
  window.addEventListener("resize", () => {
    try {
      if (browserTabY !== null) placeTab(0, browserTabY);
    } catch (_) {}
  });
}

function refreshEdgeTab() {
  const tab = document.getElementById("browserTab");
  if (!tab) return;
  const badge = tab.querySelector(".tab-continue");
  if (badge) badge.hidden = !pendingLiveQid;
}

function showEdgeTab(url, continueQid) {
  // Partially-visible tab on the right edge while the browser runs.
  // Pull it out (or click it) to open the live player.
  const clean = typeof url === "string" ? url : "";
  if (!clean) return;
  browserStreamUrl = clean;
  if (continueQid) {
    pendingLiveQid = continueQid;
    pendingQuestion = { id: continueQid, kind: "live", selected: null };
  } else if (manualControlOpen) {
    // Manual control card owns the screen: keep the URL fresh but leave the
    // preview template hidden until the card closes.
    return;
  }
  let tab = document.getElementById("browserTab");
  if (!tab) {
    tab = document.createElement("div");
    tab.id = "browserTab";
    tab.setAttribute("role", "button");
    tab.setAttribute("tabindex", "0");
    tab.setAttribute("aria-label", "Open browser preview");
    tab.innerHTML = `
      <button type="button" class="tab-x" aria-label="Close browser">✕</button>
      <span class="tab-label">Browser Running</span>
      <span class="tab-continue" hidden aria-hidden="true"></span>`;
    tab.querySelector(".tab-x").addEventListener("click", (e) => {
      e.stopPropagation();
      showCloseBrowserDialog();
    });
    document.body.appendChild(tab);
    if (browserTabY !== null) placeTab(0, browserTabY);
    initTabDrag(tab);
    bindTabClampOnce();
  }
  warnInsecureLiveOnce();
  refreshEdgeTab();
  const player = document.getElementById("liveMini");
  if (player) {
    const frame = player.querySelector(".live-frame");
    const out = remoteLiveUrl(clean);
    if (frame && frame.getAttribute("src") !== out) frame.src = out;
    syncPlayerContinue(player);
  }
}

function showLiveLink(live) {
  // One place decides how a live link is shown: a control link stays the
  // right-edge tab so the user can drive it; the show_preview tool opens the
  // floating watch-only window (idempotent — no reopen when already on screen);
  // every other watch-only link keeps the existing right-edge tab.
  // Guard: the floating preview player NEVER renders a control URL. If a
  // control link ever arrives here (stale frame, mixed state), convert it to
  // the watch-only form instead of opening an interactive surface.
  try {
    let url = live && typeof live.url === "string" ? live.url : "";
    if (!url) return;
    markLiveSeen();
    if (live.view_only === false && live.tool !== "show_preview") {
      showEdgeTab(url);
      return;
    }
    if (live.tool === "show_preview") {
      showPreviewPlayer(toPreviewUrl(url));
      return;
    }
    showEdgeTab(url);
  } catch (_) {}
}

function toPreviewUrl(url) {
  // Force the watch-only form: the library mints a distinct preview token,
  // so a control URL must never be shown in the preview player. Best-effort
  // client-side guard (server already prefers the preview link): flip the
  // explicit view_only flag to true, preserving token/scale/path.
  try {
    const u = new URL(String(url || ""), location.href);
    u.searchParams.set("view_only", "true");
    return u.toString();
  } catch (_) {
    return String(url || "");
  }
}

function showCloseBrowserDialog() {
  // The × opens a real confirm dialog (closing stops the browser for real).
  openModal(`
    <div class="modal-head">
      <div class="tile"><span class="tile-letters">!</span></div>
      <div class="modal-head-text"><h3>Close browser?</h3><p>The live session stops for real</p></div>
      <button class="modal-close" data-close type="button" aria-label="Close">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round">
          <line x1="18" y1="6" x2="6" y2="18" /><line x1="6" y1="6" x2="18" y2="18" />
        </svg>
      </button>
    </div>
    <div class="modal-body"><p class="modal-body-text">Closing stops the browser session and the agent loses the live page. The tab and the preview will be removed.</p></div>
    <div class="modal-foot">
      <button class="btn" data-close type="button">Cancel</button>
      <button class="btn btn-danger" id="confirmCloseBrowserBtn" type="button">Close browser</button>
    </div>`);
  document.getElementById("confirmCloseBrowserBtn")?.addEventListener("click", async (e) => {
    // Close only clears the UI after the server confirms the browser stopped;
    // otherwise the poller truthfully brings the still-running tab back.
    const btn = e.currentTarget;
    btn.disabled = true;
    btn.classList.add("loading");
    try {
      const { res, data } = await postBrowserApi("close", 60000);
      if (!res.ok) {
        const why = String((data && (data.detail || data.error)) || "").slice(0, 160);
        throw new Error(why || "Close failed");
      }
      try {
        sessionStorage.setItem(BROWSER_CLOSED_KEY, "1");
      } catch (_) {}
      closeModal();
      clearLiveUI();
      toast("Browser closed");
    } catch (err) {
      btn.disabled = false;
      btn.classList.remove("loading");
      toast(err instanceof Error && err.message ? err.message : "Close failed");
    }
  });
}

/* ---------- floating live player (opens from the edge tab, fresh load) ---------- */
function syncPlayerContinue(player) {
  // Paused live handoff: the Continue button lives in the player header.
  // Preview mode: a Take-control button sits on the right instead.
  const spot = player ? player.querySelector(".live-mini-spot") : null;
  if (!spot) return;
  let btn = spot.querySelector('[data-act="continue"]');
  if (pendingLiveQid) {
    if (!btn) {
      btn = document.createElement("button");
      btn.type = "button";
      btn.className = "glass-btn live-mini-btn";
      btn.dataset.act = "continue";
      btn.textContent = "Continue";
      spot.prepend(btn);
    }
    const qid = pendingLiveQid;
    btn.hidden = false;
    btn.onclick = () => answerLiveContinue(qid, btn);
    spot.querySelector('[data-act="take-control"]')?.remove();
  } else {
    if (btn) btn.remove();
    let ctl = spot.querySelector('[data-act="take-control"]');
    if (!ctl) {
      ctl = document.createElement("button");
      ctl.type = "button";
      ctl.className = "live-full";
      ctl.dataset.act = "take-control";
      ctl.innerHTML = TAKE_CONTROL_SVG;
      ctl.setAttribute("aria-label", "Take control");
      ctl.title = "Take control — switch to the control live and open it big in chat";
      ctl.onclick = () => takeControlFromPreview(ctl);
      const reloadBtn = spot.querySelector('[data-act="reload"]');
      if (reloadBtn) spot.insertBefore(ctl, reloadBtn);
      else spot.prepend(ctl);
    }
  }
}

function showPreviewPlayer(url) {
  // show_preview tool: bring the floating preview window up (watch-only).
  // Already showing this exact link -> do nothing (no reopen, no flicker).
  // The player is watch-only only: force the preview form so a control URL
  // can never render interactive inside the floating template.
  try {
    const clean = toPreviewUrl(String(url || ""));
    if (!clean) return;
    markLiveSeen();
    const mini = document.getElementById("liveMini");
    const frame = mini && mini.querySelector(".live-frame");
    if (mini && frame && frame.getAttribute("src") === (remoteLiveUrl(clean) || clean)) return;
    browserStreamUrl = clean;
    openLivePlayer();
  } catch (_) {}
}

function openLivePlayer(atX, atY) {
  // Tap on the edge tab. If the remembered link was lost somehow, recover
  // it from the server instead of silently doing nothing.
  if (!browserStreamUrl) {
    fetch("/api/live_state", { cache: "no-store" })
      .then((r) => (r.ok ? r.json().catch(() => null) : null))
      .then((data) => {
        const u = data && data.active && data.active.url;
        if (typeof u === "string" && u.startsWith("http")) {
          browserStreamUrl = u;
          openLivePlayer(atX, atY);
        } else {
          try {
            toast("No live stream — start the browser first");
          } catch (_) {}
        }
      })
      .catch(() => {
        try {
          toast("No live stream — start the browser first");
        } catch (_) {}
      });
    return;
  }
  // Instant open with the remembered link (the tap must never wait on the
  // network); the persisted URL is adopted in the background when it differs.
  _buildLivePlayer(atX, atY);
  try {
    const ctl = new AbortController();
    const timer = setTimeout(() => {
      try {
        ctl.abort();
      } catch (_) {}
    }, 6000);
    fetch("/api/live_state", { cache: "no-store", signal: ctl.signal })
      .then((r) => (r.ok ? r.json().catch(() => null) : null))
      .then((data) => {
        try {
          clearTimeout(timer);
          const u = data && data.active && data.active.url;
          if (typeof u === "string" && u.startsWith("http") && u !== browserStreamUrl) {
            browserStreamUrl = u;
            const player = document.getElementById("liveMini");
            const frame = player ? player.querySelector(".live-frame") : null;
            const out = remoteLiveUrl(u) || u;
            if (frame && frame.getAttribute("src") !== out) frame.src = out;
          }
        } catch (_) {}
      })
      .catch(() => {
        try {
          clearTimeout(timer);
        } catch (_) {}
      });
  } catch (_) {}
}

function _buildLivePlayer(atX, atY) {
  if (!browserStreamUrl) return;
  document.getElementById("liveMini")?.remove();
  document.getElementById("browserTab")?.classList.add("off");
  const mini = document.createElement("div");
  mini.id = "liveMini";
  mini.innerHTML = `
    <div class="live-mini-head" data-drag>
      <span>Browser Running</span>
      <span class="live-mini-spot">
        <button type="button" class="live-full" data-act="reload" aria-label="Reload live view">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><polyline points="1 4 1 10 7 10" /><path d="M3.5 15a9 9 0 1 0 2.1-9.4L1 10" /></svg>
        </button>
        <button type="button" class="live-mini-x" data-act="close" aria-label="Minimize" title="Minimize">✕</button>
      </span>
    </div>
    <div class="live-mini-body"></div>
    <div class="live-resize" data-edge="right" aria-hidden="true"></div>
    <div class="live-resize" data-edge="bottom" aria-hidden="true"></div>
    <div class="live-resize" data-edge="corner" aria-hidden="true"></div>
    <div class="live-resize" data-edge="corner-left" aria-hidden="true"></div>`;
  // Fresh iframe every open => the live view reloads automatically.
  mini.querySelector(".live-mini-body").appendChild(liveFrame(browserStreamUrl));
  mini.querySelector('[data-act="reload"]').addEventListener("click", () => reloadLiveFrame());
  mini.querySelector('[data-act="close"]').addEventListener("click", () => {
    // Player × minimizes straight back to the edge tab (no dialog).
    // Real closing lives on the edge tab's × (with confirm).
    try {
      const r = mini.getBoundingClientRect();
      dockPlayerToTab(r.top);
    } catch (_) {
      dockPlayerToTab(0);
    }
  });
  syncPlayerContinue(mini);
  document.body.appendChild(mini);
  startLiveWatch();
  warnInsecureLiveOnce();
  // Free-floating window: opens around the tap point (desktop and phone),
  // then drags smoothly anywhere via makeDraggable below. Position is
  // clamped against the MEASURED size (CSS widens it on phones), so it can
  // never stick out past the viewport edges.
  mini.style.visibility = "hidden";
  const mw = mini.offsetWidth || 264;
  const mh = mini.offsetHeight || 160;
  const maxLeft = Math.max(8, window.innerWidth - mw - 8);
  const maxTop = Math.max(70, window.innerHeight - mh - 8);
  const left = typeof atX === "number"
    ? Math.min(Math.max(8, atX - mw / 2), maxLeft)
    : Math.min(Math.max(8, window.innerWidth - mw - 16), maxLeft);
  const top = typeof atY === "number"
    ? Math.min(Math.max(70, atY), maxTop)
    : Math.min(Math.max(70, Math.round(window.innerHeight * 0.3)), maxTop);
  mini.style.left = `${left}px`;
  mini.style.top = `${top}px`;
  mini.style.visibility = "";
  makeDraggable(mini, mini.querySelector("[data-drag]"));
  makeResizable(mini);
  bindMiniClampOnce();
}

const TAKE_CONTROL_SVG = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M5 3l7 17 2.5-7.5L22 10z" /><path d="M14.5 13.5L19 18" /></svg>`;

function reloadLiveFrame() {
  // Re-point the iframe at the freshest known stream URL (heals "Not Connected").
  // Rendered through the remote rewrite so reloads never regress to loopback.
  const player = document.getElementById("liveMini");
  const frame = player ? player.querySelector(".live-frame") : null;
  if (!frame) return;
  const fresh = remoteLiveUrl(browserStreamUrl || frame.getAttribute("src") || "");
  if (fresh) frame.src = fresh;
}

async function refreshLiveFrameFromServer() {
  // The minted viewer link can die server-side (WSL forwarding flap, expired
  // token after an x5-novnc restart) while the player keeps reloading the
  // same stale src forever. Ask the server for the persisted URL first and
  // adopt it when it differs; otherwise fall back to a plain reload.
  // Best-effort and bounded — failures simply reload the known URL.
  try {
    const ctl = new AbortController();
    const timer = setTimeout(() => {
      try {
        ctl.abort();
      } catch (_) {}
    }, 8000);
    const res = await fetch("/api/live_state", { cache: "no-store", signal: ctl.signal });
    clearTimeout(timer);
    const data = res.ok ? await res.json().catch(() => null) : null;
    const u = data && data.active && data.active.url;
    const fresh = typeof u === "string" && u.startsWith("http") ? u : "";
    const player = document.getElementById("liveMini");
    const frame = player ? player.querySelector(".live-frame") : null;
    if (!frame) return;
    if (fresh) {
      browserStreamUrl = fresh;
      const out = remoteLiveUrl(fresh);
      if (frame.getAttribute("src") !== out) frame.src = out;
      else reloadLiveFrame();
      return;
    }
  } catch (_) {}
  try {
    reloadLiveFrame();
  } catch (_) {}
}

let liveWatchTimer = 0;
let liveWatchMisses = 0;

function stopLiveWatch() {
  try {
    if (liveWatchTimer) clearInterval(liveWatchTimer);
  } catch (_) {}
  liveWatchTimer = 0;
  liveWatchMisses = 0;
}

function startLiveWatch() {
  // The VNC socket can die silently (forwarding flap, insecure-context drop,
  // WSL distro terminate cycle) while the page sits "loading" forever:
  // a cheap network probe of the exact frame URL reloads it back to life
  // after 2 consecutive misses. Runs only while the player is open and is
  // cleared with it, so it never spins alone.
  stopLiveWatch();
  try {
    liveWatchTimer = setInterval(async () => {
      try {
        const player = document.getElementById("liveMini");
        const frame = player ? player.querySelector(".live-frame") : null;
        const src = frame ? frame.getAttribute("src") : "";
        if (!src) {
          stopLiveWatch();
          return;
        }
        const ctl = new AbortController();
        const t = setTimeout(() => {
          try {
            ctl.abort();
          } catch (_) {}
        }, 6000);
        await fetch(src, { mode: "no-cors", cache: "no-store", signal: ctl.signal });
        clearTimeout(t);
        liveWatchMisses = 0;
      } catch (_) {
        liveWatchMisses += 1;
        if (liveWatchMisses >= 2) {
          liveWatchMisses = 0;
          // Adopt a fresh server URL when the stored one died (new host or
          // rotated token); plain reload otherwise.
          try {
            await refreshLiveFrameFromServer();
          } catch (_) {}
        }
      }
    }, 20000);
  } catch (_) {}
}

let liveHiddenAt = 0;

function reviveLiveFrames() {
  const frames = document.querySelectorAll("#liveMini .live-frame, .live-card .live-frame");
  if (!frames.length) {
    // No live UI at all while the tab may still run: a running browser with
    // no known link (server reboot cleared it, open never persisted one)
    // would otherwise leave no edge tab forever. Re-attach in background.
    fetch("/api/browser", { cache: "no-store" })
      .then((r) => (r.ok ? r.json().catch(() => null) : null))
      .then((bd) => {
        if (!bd || !bd.running) return;
        const su = bd.stream && bd.stream.url;
        if (typeof su === "string" && su) {
          markLiveSeen();
          showEdgeTab(su);
        } else {
          autoMintMissingPreview();
        }
      })
      .catch(() => {});
    return;
  }
  // Frames exist but sat through a long absence: one fresh connection, since
  // the viewer shows "Loading" on disconnect and never reconnects by itself.
  fetch("/api/live_state", { cache: "no-store" })
    .then((r) => (r.ok ? r.json().catch(() => null) : null))
    .then((data) => {
      const st = data && data.active;
      const u = st && typeof st.url === "string" ? st.url : "";
      if (!u) return;
      if (u !== browserStreamUrl) browserStreamUrl = u;
      const out = remoteLiveUrl(u) || u;
      frames.forEach((f) => {
        if (!f.isConnected) return;
        try {
          if (f.getAttribute("src") !== out) f.src = out;
        } catch (_) {}
      });
    })
    .catch(() => {});
}

document.addEventListener("visibilitychange", () => {
  try {
    if (document.hidden) {
      liveHiddenAt = Date.now();
    } else if (liveHiddenAt && Date.now() - liveHiddenAt > 20000) {
      liveHiddenAt = 0;
      reviveLiveFrames();
    }
  } catch (_) {}
});

function dockPlayerToTab(top) {
  // Back to the right edge with a smooth slide-out: the player glides off
  // screen first, then the peeking tab returns.
  const mini = document.getElementById("liveMini");
  const finish = () => {
    stopLiveWatch();
    document.getElementById("liveMini")?.remove();
    const tab = document.getElementById("browserTab");
    if (!tab && browserStreamUrl) {
      showEdgeTab(browserStreamUrl);
      return;
    }
    if (!tab) return;
    const y = Math.min(Math.max(70, top), Math.max(70, window.innerHeight - 140));
    if (browserTabY !== null) placeTab(0, y);
    else {
      tab.style.left = "auto";
      tab.style.right = "0";
      tab.style.top = `${y}px`;
      tab.style.bottom = "auto";
    }
    tab.classList.remove("off");
  };
  if (mini && !mini.dataset.docking) {
    mini.dataset.docking = "1";
    mini.classList.add("docking");
    setTimeout(finish, 300);
  } else {
    finish();
  }
}

function maybeDockPlayer(mini, wasMoved) {
  // Dock ONLY on a forceful shove to the right: the player must end up half
  // or more past the viewport's right edge — then it glides off with the
  // docking animation (CSS .docking) and the edge tab returns. A tap, a
  // leftward move, or a gentle drop never hides it: it snaps fully inside.
  // NOTE: `>=` is deliberate — the drag clamp parks left at exactly this
  // boundary at full shove, so strict `>` would never fire.
  try {
    const r = mini.getBoundingClientRect();
    const w = r.width || mini.offsetWidth || 264;
    const h = r.height || mini.offsetHeight || 160;
    if (wasMoved && r.left >= window.innerWidth - w / 2) {
      dockPlayerToTab(r.top);
      return;
    }
    const nx = Math.min(Math.max(0, r.left), Math.max(0, window.innerWidth - w - 8));
    const ny = Math.min(Math.max(0, r.top), Math.max(0, window.innerHeight - h - 8));
    mini.style.left = `${nx}px`;
    mini.style.top = `${ny}px`;
  } catch (_) {}
}

function initTabDrag(tab) {
  // Vertical-only rail (mouse + touch via Pointer Events): a tap opens the
  // player at the tap height, a drag slides the tab up/down along the right
  // edge and never leaves it. Presses starting on the × button are left
  // alone so closing keeps working.
  let sy = 0;
  let oy = 0;
  let moved = false;
  let dragging = false;
  let tapLimit = 6;
  // The tab owns its gestures (no native scroll starting on it): a finger
  // always jitters, so touch gets a wider tap threshold than the mouse.
  // Otherwise every phone tap reads as a drag and the player never opens.
  tab.style.touchAction = "none";
  tab.addEventListener("pointerdown", (e) => {
    if (e.target && e.target.closest && e.target.closest("button")) return;
    const r = tab.getBoundingClientRect();
    dragging = true;
    moved = false;
    tapLimit = e.pointerType === "touch" ? 14 : 6;
    sy = e.clientY;
    oy = r.top;
    tab.classList.add("dragging");
    try {
      tab.setPointerCapture?.(e.pointerId);
    } catch (_) {}
    e.preventDefault();
  });
  tab.addEventListener("pointermove", (e) => {
    if (!dragging) return;
    if (Math.abs(e.clientY - sy) > tapLimit) moved = true;
    if (moved) placeTab(0, oy + e.clientY - sy);
    e.preventDefault();
  });
  const end = (e) => {
    if (!dragging) return;
    dragging = false;
    tab.classList.remove("dragging");
    if (!moved) {
      const px = e && typeof e.clientX === "number" ? e.clientX : undefined;
      const py = e && typeof e.clientY === "number" ? e.clientY : undefined;
      openLivePlayer(px, py);
    }
  };
  tab.addEventListener("pointerup", end);
  tab.addEventListener("pointercancel", end);
  tab.addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      openLivePlayer();
    }
  });
}

function makeDraggable(el, handle) {
  // Free smooth drag anywhere (mouse + touch via Pointer Events).
  // translate-only motion. Presses starting on buttons are left alone
  // so clicks keep working.
  if (!el || !handle) return;
  let sx = 0;
  let sy = 0;
  let ox = 0;
  let oy = 0;
  let dragging = false;
  let moved = false;
  handle.style.touchAction = "none";
  handle.style.cursor = "grab";
  handle.addEventListener("pointerdown", (e) => {
    if (e.target && e.target.closest && e.target.closest("button")) return;
    if (el.classList.contains("docked")) return;
    dragging = true;
    moved = false;
    sx = e.clientX;
    sy = e.clientY;
    const r = el.getBoundingClientRect();
    ox = r.left;
    oy = r.top;
    try {
      handle.setPointerCapture?.(e.pointerId);
    } catch (_) {}
    handle.style.cursor = "grabbing";
    e.preventDefault();
  });
  handle.addEventListener("pointermove", (e) => {
    if (!dragging) return;
    if (Math.abs(e.clientX - sx) + Math.abs(e.clientY - sy) > 5) moved = true;
    // Clamp against the measured size, but allow pushing past the right edge
    // up to half out — only a drop there docks (see maybeDockPlayer).
    const elW = el.offsetWidth || 60;
    const elH = el.offsetHeight || 40;
    const nx = Math.min(Math.max(0, ox + e.clientX - sx), Math.max(0, window.innerWidth - elW / 2));
    const ny = Math.min(Math.max(0, oy + e.clientY - sy), Math.max(0, window.innerHeight - elH - 8));
    el.style.left = `${nx}px`;
    el.style.top = `${ny}px`;
    e.preventDefault();
  });
  const stop = () => {
    if (dragging && el.id === "liveMini") {
      try {
        maybeDockPlayer(el, moved);
      } catch (_) {}
    }
    dragging = false;
    handle.style.cursor = "grab";
  };
  handle.addEventListener("pointerup", stop);
  handle.addEventListener("pointercancel", stop);
}

function makeResizable(el) {
  // Drag edges/corners to resize (mouse + touch). Bounded to the viewport.
  if (!el) return;
  const MIN_W = 200;
  const MIN_H = 150;
  el.querySelectorAll(".live-resize").forEach((grip) => {
    const edge = grip.dataset.edge || "corner";
    grip.style.touchAction = "none";
    grip.addEventListener("pointerdown", (e) => {
      if (el.classList.contains("docked")) return;
      e.preventDefault();
      e.stopPropagation();
      const r = el.getBoundingClientRect();
      const sx = e.clientX;
      const sy = e.clientY;
      const ow = r.width;
      const oh = r.height;
      try {
        grip.setPointerCapture?.(e.pointerId);
      } catch (_) {}
      const move = (ev) => {
        const dx = ev.clientX - sx;
        const dy = ev.clientY - sy;
        const maxW = Math.max(MIN_W + 40, window.innerWidth - r.left - 8);
        if (edge === "right" || edge === "corner") {
          el.style.width = `${Math.min(Math.max(MIN_W, ow + dx), maxW)}px`;
        }
        if (edge === "corner-left") {
          // Grow leftward: the right side stays anchored.
          const maxWLeft = Math.max(MIN_W + 40, r.right - 8);
          const newW = Math.min(Math.max(MIN_W, ow - dx), maxWLeft);
          el.style.width = `${newW}px`;
          el.style.left = `${Math.max(0, r.left + (ow - newW))}px`;
        }
        if (edge === "bottom" || edge === "corner" || edge === "corner-left") {
          const frame = el.querySelector(".live-mini-body .live-frame");
          if (frame) {
            const h = Math.min(Math.max(110, oh - 44 + dy), Math.max(150, window.innerHeight - 140));
            frame.style.aspectRatio = "auto";
            frame.style.height = `${h}px`;
          }
        }
        ev.preventDefault();
      };
      const stop = () => {
        grip.removeEventListener("pointermove", move);
        grip.removeEventListener("pointerup", stop);
        grip.removeEventListener("pointercancel", stop);
      };
      grip.addEventListener("pointermove", move);
      grip.addEventListener("pointerup", stop);
      grip.addEventListener("pointercancel", stop);
    });
  });
}

let miniClampBound = false;
function bindMiniClampOnce() {
  // Rotation/resize must never strand the player off-screen (the phone case:
  // a 94vw window suddenly wider than the viewport). Re-clamp inside.
  if (miniClampBound) return;
  miniClampBound = true;
  window.addEventListener("resize", () => {
    try {
      const mini = document.getElementById("liveMini");
      if (!mini) return;
      const w = mini.offsetWidth || 264;
      const h = mini.offsetHeight || 160;
      const r = mini.getBoundingClientRect();
      const nx = Math.min(Math.max(0, r.left), Math.max(0, window.innerWidth - w - 8));
      const ny = Math.min(Math.max(0, r.top), Math.max(0, window.innerHeight - h - 8));
      mini.style.left = `${nx}px`;
      mini.style.top = `${ny}px`;
    } catch (_) {}
  });
}

function clearLiveUI(persist) {
  stopLiveWatch();
  try {
    document.getElementById("browserTab")?.remove();
    document.getElementById("liveMini")?.remove();
  } catch (_) {}
  pendingLiveQid = null;
  if (persist === true) {
    // Dismissing the preview forgets it server-side too (no zombie restore).
    fetch("/api/live_state", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ clear: true }),
    }).catch(() => {});
  }
}

async function answerLiveContinue(qid, btn) {
  // Resume a paused live handoff — silent, no chat message is sent.
  if (btn) {
    btn.disabled = true;
    btn.textContent = "…";
  }
  try {
    const res = await wsAnswer({ id: qid, decision: "continue" });
    if (!res.ok) throw new Error("bad status");
    pendingQuestion = null;
    pendingLiveQid = null;
    if (btn) btn.remove();
    refreshEdgeTab();
    toast("Resumed");
  } catch (_) {
    if (btn) {
      btn.disabled = false;
      btn.textContent = "Continue";
    }
    toast("Answer failed");
  }
}

async function takeControlFromPreview(btn) {
  // Take-over stops the agent loop first, then mints the CONTROL live URL
  // and opens the big in-chat control card. A spinner runs inside the icon
  // until control lands, so the wait is visible.
  if (btn) {
    btn.disabled = true;
    btn.classList.add("loading");
  }
  try {
    try {
      await abortActiveStream();
    } catch (_) {}
    const { res, data } = await postBrowserApi("control");
    const url = data && typeof data.url === "string" ? data.url : "";
    if (!res.ok || !url) {
      const why = String((data && (data.detail || data.error)) || "").slice(0, 160);
      throw new Error(staleServerHint(why) || why || "control failed");
    }
    try {
      sessionStorage.removeItem(BROWSER_CLOSED_KEY);
    } catch (_) {}
    markLiveSeen();
    browserStreamUrl = url;
    // Hide the preview template right away: the player and the edge tab go
    // away, and only the big chat control card stays on screen.
    try {
      document.getElementById("liveMini")?.remove();
      document.getElementById("browserTab")?.classList.add("off");
    } catch (_) {}
    showControlCard(url);
  } catch (err) {
    toast(err instanceof Error && err.message ? err.message : "Control failed");
  } finally {
    if (btn && btn.isConnected) {
      btn.disabled = false;
      btn.classList.remove("loading");
    }
  }
}

function showControlCard(url) {
  // Big centered in-chat card for manual control: status dot + X only
  // (no default title sentence anywhere), X closes it with the leaving animation.
  const clean = String(url || "");
  if (!clean) return;
  hideQuestionCard();
  manualControlOpen = true;
  stopLiveWatch();
  document.getElementById("liveControlManual")?.remove();
  document.getElementById("liveMini")?.remove();
  document.getElementById("browserTab")?.classList.add("off");
  const host = chatInner || document.body;
  const card = document.createElement("div");
  card.className = "live-card live-control-manual";
  card.id = "liveControlManual";
  card.innerHTML = `
    <div class="live-head">
      <button type="button" class="live-mini-x" data-act="dismiss" aria-label="Close">✕</button>
    </div>
    <div class="live-body"></div>`;
  card.querySelector(".live-body").appendChild(liveFrame(clean));
  card.querySelector('[data-act="dismiss"]').addEventListener("click", () => {
    // X only: forget it server-side too, so a later refresh never brings
    // this dismissed card back. (Paused-loop handoffs have no X on purpose.)
    try {
      fetch("/api/live_state", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ clear: true }),
      }).catch(() => {});
    } catch (_) {}
    manualControlOpen = false;
    document.getElementById("browserTab")?.classList.remove("off");
    showEdgeTab(browserStreamUrl || clean);
    card.classList.add("leaving");
    setTimeout(() => card.remove(), 200);
  });
  host.appendChild(card);
  scrollToBottom();
}

async function isLiveUrlAlive(url, timeoutMs = 6000) {
  // Bounded reachability probe for a persisted stream link: a no-cors fetch
  // resolves (opaque) on any answer and rejects only when nothing listens
  // (dead relay, browser closed while the library still reports running).
  // Probes the remote-rewritten URL so a funnel page checks the server's
  // /live/ route instead of the viewer's own localhost. Never throws.
  // Same pattern as the live frame watcher.
  try {
    const u = remoteLiveUrl(String(url || "")) || String(url || "");
    if (!u.startsWith("http")) return false;
    const ctl = new AbortController();
    const timer = setTimeout(() => {
      try {
        ctl.abort();
      } catch (_) {}
    }, timeoutMs);
    try {
      await fetch(u, { mode: "no-cors", cache: "no-store", signal: ctl.signal });
      return true;
    } catch (_) {
      return false;
    } finally {
      clearTimeout(timer);
    }
  } catch (_) {
    return false;
  }
}

async function restoreLive() {
  // After a refresh: bring back the stream card / preview bar so the running
  // task keeps all its visible data. Returns true while something is live.
  // A user-confirmed close stays closed across refresh (no resurrection).
  try {
    if (userClosedBrowser()) return false;
    const res = await fetch("/api/live_state", { cache: "no-store" });
    if (!res.ok) return false;
    const data = await res.json();
    const st = data && data.active;
    if (!st || !st.url) return false;
    if (st.mode === "control" && data.pending && st.qid) {
      showLiveControlCard({ id: st.qid, kind: "live", url: st.url, tool: st.tool, reason: st.reason });
      return true;
    }
    if (st.mode === "control" && st.url) {
      // Browser runs with no paused loop: show the right-edge tab (interactive),
      // never a blocking card the user did not ask for. The persisted link is
      // probed first: the library can still report a session whose stream is
      // already dead, and refresh must never resurrect a dead tab.
      if (!(await isLiveUrlAlive(st.url))) return false;
      browserStreamUrl = String(st.url);
      showEdgeTab(st.url, data.pending ? st.qid : null);
      return true;
    }
    if (st.mode === "preview") {
      // Same dead-link guard as above: no probe, no tab. Opening the player
      // always re-reads the freshest persisted URL first.
      if (!st.url || !(await isLiveUrlAlive(st.url))) return false;
      showEdgeTab(st.url, data.pending ? st.qid : null);
      return true;
    }
    return false;
  } catch (_) {
    return false;
  }
}

let browserDownStreak = 0;
let lastAutoPreviewAt = 0;

async function autoMintMissingPreview() {
  // The browser runs but no stream link is known anywhere (server reboot
  // cleared it, or the open never persisted one): mint the stable cached
  // preview link in the background so the edge tab can appear. Bounded:
  // at most once per 60s, never while a control/handoff UI owns the screen,
  // and never boots anything (the preview action fails clearly while down).
  try {
    if (manualControlOpen || pendingLiveQid) return;
    if (document.getElementById("liveMini") || document.getElementById("browserTab")) return;
    if (document.getElementById("liveControlManual") || document.getElementById("liveCard")) return;
    const now = Date.now();
    if (now - lastAutoPreviewAt < 60000) return;
    lastAutoPreviewAt = now;
    const { res, data } = await postBrowserApi("preview", 30000);
    const url = data && typeof data.url === "string" ? data.url : "";
    if (res.ok && url) {
      clearBrowserClosedFlag();
      markLiveSeen();
      showEdgeTab(url);
    }
  } catch (_) {}
}

async function autoCheckBrowserOnAgentOpen() {
  // Auto-check every time the agent page opens: browser running -> fetch the
  // live link and show the right-edge template automatically (no click needed).
  // Skips when any live UI already owns the screen, a handoff is pending, or
  // the user explicitly closed the browser. Dead links are probed first so a
  // stale session never pops a broken player; a running browser with no link
  // yet falls back to the bounded background mint (at most once per 60s).
  try {
    if (manualControlOpen || pendingLiveQid) return;
    if (document.getElementById("liveMini") || document.getElementById("browserTab")) return;
    if (document.getElementById("liveControlManual") || document.getElementById("liveCard")) return;
    if (userClosedBrowser()) return;
    const res = await fetch("/api/browser", { cache: "no-store" });
    if (!res.ok) return;
    const data = await res.json().catch(() => null);
    if (!data || !data.running) return;
    const url = data.stream && typeof data.stream.url === "string" ? data.stream.url : "";
    if (url) {
      if (!(await isLiveUrlAlive(url))) return;
      clearBrowserClosedFlag();
      markLiveSeen();
      showEdgeTab(url);
    } else {
      autoMintMissingPreview();
    }
  } catch (_) {}
}


function onBrowserPush(data) {
  // Push version of the old poller: reflects reality only — never starts
  // the browser by itself. Down-hysteresis (4 misses) keeps relay flaps
  // from flickering the tab or wiping a just-opened player: explicit close
  // paths clear the UI immediately, so waiting here only affects surprises.
  if (document.hidden) return;
  const page = document.querySelector('.page[data-page="agent"]');
  if (!page || !page.classList.contains("active")) return;
  try {
    const running = Boolean(data && data.running);
    const tab = document.getElementById("browserTab");
    const player = document.getElementById("liveMini");
    if (running) {
      browserDownStreak = 0;
      const url = data.stream && typeof data.stream.url === "string" ? data.stream.url : "";
      if (url) {
        markLiveSeen();
        showEdgeTab(url);
      } else {
        // Browser runs but no link is known: mint the missing preview in the
        // background so the edge tab appears (bounded inside).
        autoMintMissingPreview();
      }
    } else if (tab || player) {
      browserDownStreak += 1;
      if (browserDownStreak >= 4) {
        // Browser reads down: hide the tab/player LOCALLY only. The server
        // keeps the persisted stream (a flap must never delete the recovery
        // link), so refresh restores it; explicit close paths clear it.
        clearLiveUI();
      }
    } else {
      browserDownStreak = 0;
    }
  } catch (_) {
  }
}

function isChatIdle() {
  return !(activeRun || activeThinking
    || document.getElementById("thinkingMsg")
    || (chatInner && chatInner.querySelector(".typing-cursor")));
}

function onLivePush(data) {
  // Push version of the old live poller: when the server stream clears and
  // the chat is idle, reload history once (the loop finished).
  // Never wipes a running chat: the just-sent message is not persisted yet.
  try {
    if (data && typeof data.url === "string" && data.url) {
      showLiveLink(data);
      return;
    }
    if (isChatIdle()) {
      clearLiveUI();
      loadHistory().catch(() => {});
    }
  } catch (_) {}
}

async function onChatDonePush(data) {
  // A loop finished somewhere (this tab or another): if idle, the reply is
  // persisted now — reload once. Replaces the old catchup poller.
  try {
    if (data && data.gateway) {
      activeThinking = null;
      catchupResume = false;
      // Remember replies already on screen so only the fresh gateway reply
      // gets the real SSE-style typing animation below.
      let known = null;
      try {
        known = new Set();
        chatInner?.querySelectorAll(".msg.assistant").forEach((m) => {
          if (m.dataset.mid) known.add(m.dataset.mid);
        });
      } catch (_) { known = null; }
      try {
        document.getElementById("thinkingMsg")?.remove();
        chatInner?.querySelectorAll(".msg.gateway-pending").forEach((item) => item.remove());
      } catch (_) {}
      try { await loadHistory(); } catch (_) {}
      // Gateway reply: same typing animation as a local run (typeRich on the
      // reply text only — steps/images/voice stay as rendered). Best-effort.
      try {
        const assistants = chatInner ? Array.from(chatInner.querySelectorAll(".msg.assistant")) : [];
        let target = null;
        for (let i = assistants.length - 1; i >= 0; i--) {
          const m = assistants[i];
          if (known && m.dataset.mid && known.has(m.dataset.mid)) continue;
          if (!String(m.dataset.raw || "").trim()) continue;
          target = m;
          break;
        }
        if (target) {
          const raw = String(target.dataset.raw || "").slice(0, MAX_MESSAGE);
          const content = target.querySelector(":scope > .msg-content");
          const rich = content
            ? content.querySelector(":scope > div:not(.thinking-block):not(.msg-final-shots):not(.msg-voice)")
            : null;
          if (raw && rich) await typeRich(rich, raw);
          scrollToBottom();
        }
      } catch (_) {}
      return;
    }
    if (!isChatIdle()) return;
    if (catchupResume) {
      // A resumed pre-refresh run just landed: drop the mid-less resume
      // bubble + thinking so history renders a single copy of each.
      catchupResume = false;
      activeThinking = null;
      setSending(false);
      try {
        document.getElementById("thinkingMsg")?.remove();
        if (lastUserEl && !lastUserEl.dataset.mid) lastUserEl.remove();
        lastUserEl = null;
      } catch (_) {}
    }
    loadHistory().catch(() => {});
  } catch (_) {}
}

function onChatPush(data) {
  // A resumed run died (e.g. server restarted): stop spinning, no polling.
  try {
    if (data && data.gateway && data.active) {
      if (!document.getElementById("thinkingMsg")) {
        const preview = String(data.input || "").slice(0, MAX_MESSAGE);
        hideWelcome();
        if (preview) {
          const pending = appendUserMessage(preview, []);
          pending.classList.add("gateway-pending");
        }
        activeThinking = addThinking(preview);
        // Follow this channel-driven run live: same Working block + steps
        // as a local run, settled by the gateway chat_done frame.
        catchupLiveItems = new Map();
        catchupResume = true;
      }
      return;
    }
    if (data && !data.active && activeThinking && !activeRun) {
      activeThinking = null;
      setSending(false);
      try {
        document.getElementById("thinkingMsg")?.remove();
      } catch (_) {}
      toast("Run stopped — send again");
    }
  } catch (_) {}
}

let catchupResume = false;

// A tab that refreshed mid-run owns no stream: rebuild the running task's
// Working block from the server's buffered progress frames, then keep it live
// as new "run" pushes arrive. Rendering is idempotent (keyed step items), so a
// frame seen twice (replay + push) never duplicates a step.
let catchupLiveItems = new Map();

function renderRunFrame(frame) {
  try {
    if (!frame || typeof frame !== "object") return;
    const block = activeThinking && activeThinking._block;
    const type = frame.type;
    if (type === "event_start") {
      if (!block) return;
      const key = stepKey({ key: frame.key, event: frame.event });
      if (catchupLiveItems.has(key)) return;
      const item = createStepItem({ event: frame.event, reason: frame.reason || "", output: null, media: null, shot: "" });
      markStepRunning(item);
      block._stepsInner.appendChild(item);
      ensureHeaderArrow(block);
      catchupLiveItems.set(key, item);
      scrollToBottom();
      return;
    }
    if (type === "event") {
      if (!block) return;
      const key = stepKey({ key: frame.key, event: frame.event });
      let item = catchupLiveItems.get(key);
      if (!item) {
        item = createStepItem({ event: frame.event, reason: frame.reason || "", output: null, media: frame.media || null, shot: "" });
        block._stepsInner.appendChild(item);
        ensureHeaderArrow(block);
        catchupLiveItems.set(key, item);
      } else {
        try {
          const titleEl = item._head && item._head.querySelector(":scope > .step-title");
          const finalTitle = humanizeEvent(frame.event || "");
          if (titleEl && finalTitle && titleEl.textContent !== finalTitle) {
            titleEl.textContent = finalTitle;
          }
        } catch (_) {}
        completeStepItem(item, {
          output: typeof frame.output === "string" ? frame.output : "",
          shot: typeof frame.shot === "string" ? frame.shot : "",
        });
      }
      try {
        const st = frame.media && frame.media.stream;
        if (st && typeof st.url === "string" && st.url) {
          if (st.tool === "show_preview") showPreviewPlayer(toPreviewUrl(st.url));
          else if (st.view_only !== false) {
            markLiveSeen();
            showEdgeTab(st.url);
          }
        }
      } catch (_) {}
      scrollToBottom();
      return;
    }
    if (type === "question") {
      if (frame.kind === "live") {
        markLiveSeen();
        if (frame.view_only) showEdgeTab(frame.url, frame.id);
        else showLiveControlCard(frame);
      } else showQuestionCard(frame);
      return;
    }
    if (type === "live") {
      showLiveLink(frame.live);
      return;
    }
    if (type === "live_end") clearLiveUI();
  } catch (_) {}
}

function replayRunFrames(frames) {
  catchupLiveItems = new Map();
  if (Array.isArray(frames)) {
    for (const frame of frames) renderRunFrame(frame);
  }
}

function onRunPush(frame) {
  // Only a re-attached viewer renders pushed frames: the tab that owns the
  // POST stream already renders them from its own response body.
  if (!catchupResume) return;
  renderRunFrame(frame);
}

async function resumeActiveRun() {
  // Refresh while the agent runs: re-show the pending request as running and
  // pick up its reply when it lands. Never wipes anything (no history reload
  // while busy — the running message is not persisted yet).
  let st = null;
  try {
    const r = await sendOp("chat_active", {});
    if (!r || !r.ok) return;
    st = (r && r.data) || {};
  } catch (_) {
    return;
  }
  if (!st || !st.active) return;
  hideWelcome();
  const preview = String(st.input || "").slice(0, 200);
  if (preview) {
    // No duplicate resume bubble: reuse the tail user bubble when it already
    // shows this pending input (double refresh of the same run).
    const cleaned = cleanHistoryText(preview);
    const want = String(cleaned.text || preview);
    let tail = null;
    try {
      const users = chatInner.querySelectorAll(".msg.user");
      tail = users.length ? users[users.length - 1] : null;
    } catch (_) {
      tail = null;
    }
    if (tail && String(tail.dataset.raw || "") === want) {
      lastUserEl = tail;
    } else {
      lastUserEl = appendUserMessage(cleaned.text || preview, []);
      attachUserBar(lastUserEl);
    }
    if (cleaned.text) lastUserText = cleaned.text;
  }
  activeThinking = addThinking(preview);
  // The button shows STOP while the resumed run lives — even though this
  // page owns no stream for it. Completion (or death) arrives via push
  // (chat_done / chat events) — no polling.
  setSending(true);
  catchupResume = true;
  // Rebuild the steps already streamed for this run (they are not in the
  // persisted history until the loop ends), then keep following live pushes.
  try {
    const r = await sendOp("chat_frames", {});
    const fd = (r && r.ok && r.data) || null;
    if (fd && Array.isArray(fd.frames)) replayRunFrames(fd.frames);
  } catch (_) {}
}

async function sendQuestionAnswer() {
  // The answer resumes the paused run — it is never posted as a user
  // message. It lands inside the question step's arrow (Q + A, from the
  // loop's own completion event) and the loop continues from there.
  // Nothing auto-sends: chip choice or typed text, both go through SEND.
  const q = pendingQuestion;
  if (!q) return;
  const typed = composerInput.value.trim().slice(0, 2000);
  const chosen = typeof q.selected === "string" && q.selected.trim() ? q.selected.trim().slice(0, 2000) : "";
  const answer = typed || chosen;
  if (!answer) {
    toast("Select a choice or type a reply");
    return;
  }
  hideQuestionCard();
  composerInput.value = "";
  autoResize();
  sendBtn.disabled = true;
  composerInput.disabled = true;
  try {
    const res = await wsAnswer({ id: q.id, answer });
    if (!res.ok) toast("Answer failed");
    // The loop resumes on the still-open stream; its completion event
    // carries the Q + A into the step arrow, then work continues.
  } catch (_) {
    toast("Answer failed");
  } finally {
    sendBtn.disabled = false;
    composerInput.disabled = false;
    composerInput.focus();
  }
}

/* ---------- message actions: copy / share / edit / regenerate / delete ---------- */
const ICONS = {
  copy: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2" /><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" /></svg>',
  edit: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17 3a2.8 2.8 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5L17 3z" /></svg>',
  refresh: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="1 4 1 10 7 10" /><path d="M3.5 15a9 9 0 1 0 2.1-9.4L1 10" /></svg>',
  trash: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6" /><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" /></svg>',
};

async function postMessageApi(body) {
  const res = await fetch("/api/message", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data?.error || `HTTP ${res.status}`);
  return data;
}

/* ---------- browser API with bounded wait (never hangs the button) ---------- */
const BROWSER_CLOSED_KEY = "x5-browser-closed";

function userClosedBrowser() {
  try {
    return sessionStorage.getItem(BROWSER_CLOSED_KEY) === "1";
  } catch (_) {
    return false;
  }
}

const LIVE_SEEN_KEY = "x5-live-seen";

function markLiveSeen() {
  // The browser was legitimately started (agent or explicit user action).
  try {
    sessionStorage.setItem(LIVE_SEEN_KEY, "1");
  } catch (_) {}
}

function liveSeen() {
  try {
    return sessionStorage.getItem(LIVE_SEEN_KEY) === "1";
  } catch (_) {
    return false;
  }
}

function clearBrowserClosedFlag() {
  try {
    sessionStorage.removeItem(BROWSER_CLOSED_KEY);
  } catch (_) {}
}

async function postBrowserApi(action, timeoutMs = 45000) {
  const c = new AbortController();
  const t = setTimeout(() => {
    try {
      c.abort();
    } catch (_) {}
  }, timeoutMs);
  try {
    const res = await fetch("/api/browser", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action }),
      signal: c.signal,
    });
    const data = await res.json().catch(() => ({}));
    return { res, data };
  } catch (err) {
    if (err && (err.name === "AbortError" || err.aborted)) {
      throw new Error("request timed out");
    }
    throw err;
  } finally {
    clearTimeout(t);
  }
}

function staleServerHint(text) {
  // Old server builds reject the control action with the legacy message.
  return /preview\|close/.test(String(text || ""))
    ? "Server is old — restart it to enable control"
    : "";
}

function makeActBtn(icon, label, fn, danger) {
  const b = document.createElement("button");
  b.type = "button";
  b.className = "msg-act" + (danger ? " danger" : "");
  b.title = label;
  b.setAttribute("aria-label", label);
  b.innerHTML = ICONS[icon] || "";
  b.addEventListener("click", (e) => {
    e.stopPropagation();
    fn();
  });
  return b;
}

function openModelPage(kind) {
  // Glass-button path: go to AI Models and auto-open that template dialog.
  const k = String(kind || "text").toLowerCase();
  try { navigate("ai-models"); } catch (_) {}
  try {
    setTimeout(() => {
      try {
        if (typeof window.openAiModelDialog === "function") window.openAiModelDialog(k);
        else window.dispatchEvent(new CustomEvent("x5:open-model", { detail: { kind: k } }));
      } catch (_) {}
    }, 120);
  } catch (_) {}
}

function appendNeedsModelButton(shell, kind) {
  // Glass shortcut under the no-model state — message + glass button that
  // jumps to AI Models and auto-opens the right dialog.
  const k = String(kind || "text").toLowerCase();
  const content = shell.querySelector(".msg-content");
  if (!content || content.querySelector(".needs-model-wrap")) return;
  const wrap = document.createElement("div");
  wrap.className = "needs-model-wrap";
  const note = document.createElement("div");
  note.className = "needs-model-note";
  note.textContent = "Connect a model to continue. One click opens the setup page.";
  note.setAttribute("dir", "auto");
  const b = document.createElement("button");
  b.type = "button";
  b.className = "needs-model-btn glass";
  b.textContent = "Connect model";
  b.addEventListener("click", () => openModelPage(k));
  wrap.appendChild(note);
  wrap.appendChild(b);
  content.appendChild(wrap);
  scrollToBottom();
}

function attachAssistantBar(shell, raw) {
  const old = shell.querySelector(":scope > .msg-actions");
  if (old) old.remove();
  const bar = document.createElement("div");
  bar.className = "msg-actions below";
  [
    ["copy", "Copy", () => copyText(raw)],
    ["edit", "Edit", () => editAssistantMessage(shell, raw)],
    ["refresh", "Regenerate", () => regenerateReply(shell)],
    ["trash", "Delete", () => confirmDeleteMessage(shell)],
  ].forEach(([icon, label, fn], i) => {
    const b = makeActBtn(icon, label, fn, icon === "trash");
    b.style.animationDelay = `${i * 60}ms`;
    bar.appendChild(b);
  });
  shell.appendChild(bar);
}

async function copyText(text) {
  try {
    await navigator.clipboard.writeText(String(text || ""));
    toast("Copied");
  } catch (_) {
    toast("Copy failed");
  }
}

function confirmDeleteMessage(shell) {
  const isUser = shell.classList.contains("user");
  openModal(`
    <div class="modal-head">
      <div class="tile"><span class="tile-letters">!</span></div>
      <div class="modal-head-text"><h3>Delete message?</h3><p>Removes it from context permanently</p></div>
      <button class="modal-close" data-close type="button" aria-label="Close">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round">
          <line x1="18" y1="6" x2="6" y2="18" /><line x1="6" y1="6" x2="18" y2="18" />
        </svg>
      </button>
    </div>
    <div class="modal-body"><p class="modal-body-text">${
      isUser
        ? "This user message will be deleted from history, so the agent will no longer see it in context."
        : "This assistant reply will be deleted from history."
    }</p></div>
    <div class="modal-foot">
      <button class="btn" data-close type="button">Cancel</button>
      <button class="btn btn-danger" id="confirmDelBtn" type="button">Delete</button>
    </div>`);
  document.getElementById("confirmDelBtn")?.addEventListener("click", async () => {
    closeModal();
    await deleteMessage(shell);
  });
}

/* ---------- edit in composer: message -> composer box -> SEND saves it ---------- */
let editingTarget = null;

function ensureEditBanner() {
  let banner = document.getElementById("editBanner");
  const composer = document.getElementById("composer");
  if (banner) {
    // The strip lives INSIDE the composer (top row of the send template).
    if (composer && banner.parentNode !== composer) composer.prepend(banner);
    return banner;
  }
  if (!composer) return null;
  banner = document.createElement("div");
  banner.id = "editBanner";
  banner.className = "edit-banner";
  banner.hidden = true;
  banner.innerHTML = `<span class="edit-strip-icon" aria-hidden="true"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17 3a2.8 2.8 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5L17 3z" /></svg></span><span class="edit-banner-text">Editing message — SEND saves</span><button type="button" class="edit-strip-x" id="editCancelBtn" aria-label="Cancel editing">✕</button>`;
  composer.prepend(banner);
  banner.querySelector("#editCancelBtn")?.addEventListener("click", cancelComposerEdit);
  return banner;
}

function showEditBanner() {
  const banner = ensureEditBanner();
  if (!banner) return;
  banner.classList.remove("closing");
  banner.hidden = false;
}

function hideEditBanner() {
  const banner = document.getElementById("editBanner");
  if (!banner || banner.hidden) return;
  // Exit animation first, then detach.
  banner.classList.add("closing");
  setTimeout(() => {
    banner.hidden = true;
    banner.classList.remove("closing");
  }, 180);
}

function updateEditSendState() {
  // SEND stays off until the text actually differs (and is not empty).
  if (!editingTarget || !sendBtn || !composerInput) return;
  if (sendBtn.classList.contains("stop")) return;
  const cur = String(composerInput.value || "").trim().slice(0, MAX_MESSAGE);
  const orig = String(editingTarget.original || "").trim().slice(0, MAX_MESSAGE);
  sendBtn.disabled = !(cur.length > 0 && cur !== orig);
}

function startComposerEdit(el) {
  const raw = el.dataset.raw || "";
  if (!raw) {
    toast("Nothing to edit");
    return;
  }
  abortActiveStream();
  hideMentionMenu();
  clearAttachment();
  editingTarget = { el, mid: el.dataset.mid || null, original: raw };
  composerInput.value = raw;
  autoResize();
  showEditBanner();
  updateEditSendState();
  composerInput.focus();
}

function cancelComposerEdit() {
  editingTarget = null;
  composerInput.value = "";
  autoResize();
  hideEditBanner();
  if (sendBtn) sendBtn.disabled = false;
  composerInput.focus();
}

async function saveComposerEdit(value) {
  const target = editingTarget;
  if (!target) return false;
  const v = String(value || "").trim().slice(0, MAX_MESSAGE);
  if (!v) {
    toast("Message is empty");
    return true;
  }
  const orig = String(target.original || "").trim().slice(0, MAX_MESSAGE);
  if (v === orig) {
    toast("No changes yet");
    return true;
  }
  const { el, mid } = target;
  const isUserEdit = el.classList.contains("user");
  if (mid) {
    try {
      await postMessageApi({ action: "edit", id: mid, content: v });
    } catch (err) {
      toast(err instanceof Error ? err.message : "Save failed");
      return true;
    }
  }
  // Old embedding replaced server-side; re-render the SAME bubble in place.
  const content = el.querySelector(".msg-content");
  if (content) {
    content.innerHTML = "";
    const div = document.createElement("div");
    div.innerHTML = formatRich(v);
    content.appendChild(div);
  }
  el.dataset.raw = v;
  if (el.classList.contains("assistant")) attachAssistantBar(el, v);
  else attachUserBar(el);
  editingTarget = null;
  composerInput.value = "";
  autoResize();
  hideEditBanner();
  if (sendBtn) sendBtn.disabled = false;
  if (isUserEdit) {
    // Editing what you sent restarts the answer: drop every assistant
    // reply after this bubble (server + DOM), then run the turn again.
    await abortActiveStream();
    const trailing = [];
    let node = el.nextElementSibling;
    while (node) {
      const next = node.nextElementSibling;
      if (node.classList && node.classList.contains("assistant")) trailing.push(node);
      node = next;
    }
    for (const reply of trailing) {
      const rid = reply.dataset.mid;
      if (rid) {
        try {
          await postMessageApi({ action: "delete", id: rid });
        } catch (_) {}
      }
      reply.remove();
    }
    lastUserEl = el;
    lastUserText = v;
    composerInput.focus();
    await executeAgentTurn(v, []);
    return true;
  }
  composerInput.focus();
  return true;
}

async function editAssistantMessage(shell, raw) {
  void raw;
  startComposerEdit(shell);
}

async function deleteMessage(shell) {
  const mid = shell.dataset.mid;
  if (mid) {
    try {
      await postMessageApi({ action: "delete", id: mid });
    } catch (err) {
      toast(err instanceof Error ? err.message : "Delete failed");
      return;
    }
  }
  shell.remove();
}

function previousUserText(shell) {
  // Walk back to the nearest user bubble for a reliable regenerate prompt.
  let prev = shell.previousElementSibling;
  while (prev) {
    if (prev.classList && prev.classList.contains("user") && String(prev.dataset.raw || "").trim()) {
      return String(prev.dataset.raw);
    }
    prev = prev.previousElementSibling;
  }
  return "";
}

async function regenerateReply(shell) {
  // Delete the old reply (+embedding), then generate a fresh one for the same prompt.
  // No duplicate user bubble is appended.
  if (editingTarget) cancelComposerEdit();
  await abortActiveStream();
  const userText = shell.dataset.userText || previousUserText(shell) || lastUserText;
  if (!userText) {
    toast("Nothing to regenerate");
    return;
  }
  const mid = shell.dataset.mid;
  if (mid) {
    try {
      await postMessageApi({ action: "delete", id: mid });
    } catch (err) {
      toast(err instanceof Error ? err.message : "Delete failed");
      return;
    }
  }
  shell.remove();
  lastUserEl = null;
  if (userText) lastUserText = userText;
  await executeAgentTurn(userText, []);
}

async function editUserMessage(el) {
  startComposerEdit(el);
}

async function abortActiveStream() {
  if (!activeRun) return;
  activeRun.cancelled = true;
  await wsCancel();
  activeRun = null;
  setSending(false);
}

async function stopGeneration() {
  // The send button is a stop button while anything generates — including a
  // pre-refresh run this page never owned (no local stream to abort).
  try {
    if (activeRun) {
      activeRun.cancelled = true;
      activeRun = null;
    }
    await wsCancel();
  } catch (_) {}
  catchupResume = false;
  try {
    document.getElementById("thinkingMsg")?.remove();
  } catch (_) {}
  activeThinking = null;
  setSending(false);
  try {
    composerInput.focus();
  } catch (_) {}
}

export async function initChat() {
  chatScroll = document.getElementById("chatScroll");
  chatInner = document.getElementById("chatInner");
  composerInput = document.getElementById("composerInput");
  sendBtn = document.getElementById("sendBtn");
  attachPreview = document.getElementById("attachmentPreview");
  composerAttachBox = document.getElementById("composerAttachments");
  const composer = document.getElementById("composer");
  if (!chatScroll || !chatInner || !composerInput || !sendBtn || !composer) return;

  // History first: resumeActiveRun appends after it, so nothing wipes it.
  await loadHistory().catch(() => {});
  initAttachments();
  initMic();
  initUserMessageGestures();
  initModeMenu();
  ensureEditBanner();
  // Restored live sessions survive refresh — the task keeps all its data.
  // One-shot restore; afterwards everything arrives via push (no polling).
  restoreLive().catch(() => {});
  onPush("browser", onBrowserPush);
  onPush("live", onLivePush);
  onPush("chat", onChatPush);
  onPush("chat_done", onChatDonePush);
  onPush("run", onRunPush);
  // Returning to the agent page re-checks the browser automatically, so a
  // session started while another page was open still shows its template.
  window.addEventListener("x5:agent-open", () => {
    autoCheckBrowserOnAgentOpen().catch(() => {});
  });
  // Returning to the agent page re-checks the browser automatically, so a
  // session started while another page was open still shows its template.
  window.addEventListener("x5:agent-open", () => {
    autoCheckBrowserOnAgentOpen().catch(() => {});
  });
  // A refresh mid-generation resumes the running state instead of losing it.
  resumeActiveRun().catch(() => {});
  // Manual copy is off on agent messages (the copy button still works,
  // the composer stays fully editable).
  chatScroll.addEventListener("copy", (e) => {
    // Copy stays off on agent messages, but the live template is copyable.
    try {
      if (e.target && e.target.closest && e.target.closest(".live-card")) return;
    } catch (_) {}
    e.preventDefault();
  });
  // Code-card copy buttons (all replies, live + history): one delegated
  // listener — cards are rendered dynamically by formatRich.
  chatScroll.addEventListener("click", (e) => {
    try {
      const btn = e.target && e.target.closest ? e.target.closest(".code-copy") : null;
      if (!btn || !chatScroll.contains(btn)) return;
      e.stopPropagation();
      e.preventDefault();
      copyCodeBlock(btn);
    } catch (_) {}
  });
  // Code-card copy buttons (all replies, live + history): one delegated
  // listener — cards are rendered dynamically by formatRich.
  chatScroll.addEventListener("click", (e) => {
    try {
      const btn = e.target && e.target.closest ? e.target.closest(".code-copy") : null;
      if (!btn || !chatScroll.contains(btn)) return;
      e.stopPropagation();
      e.preventDefault();
      copyCodeBlock(btn);
    } catch (_) {}
  });

  setSending(false);
  sendBtn.addEventListener("click", (e) => {
    // While generating, the send button is a stop button (works even for a
    // pre-refresh run: the cancel reaches the server either way).
    if (sendBtn.classList.contains("stop")) {
      e.preventDefault();
      e.stopPropagation();
      stopGeneration();
      toast("Stopped");
    }
  }, true);
  composer.addEventListener("submit", (e) => {
    e.preventDefault();
    if (sendBtn.classList.contains("stop")) {
      stopGeneration();
      return;
    }
    handleSend();
  });
  composerInput.addEventListener("input", autoResize);
  composerInput.addEventListener("input", checkMention);
  composerInput.addEventListener("input", updateEditSendState);
  composerInput.addEventListener("paste", (e) => {
    // Oversize paste becomes a TXT file automatically (safe limit respected).
    try {
      const text = (e.clipboardData && e.clipboardData.getData("text/plain")) || "";
      if (!text) return;
      const cur = composerInput.value || "";
      const start = composerInput.selectionStart ?? cur.length;
      const end = composerInput.selectionEnd ?? cur.length;
      const full = cur.slice(0, start) + text + cur.slice(end);
      if (full.length <= MAX_MESSAGE) return;
      e.preventDefault();
      if (pendingAttachments.length >= 2) {
        const room = Math.max(0, MAX_MESSAGE - cur.length);
        composerInput.value = (cur + text.slice(0, room)).slice(0, MAX_MESSAGE);
        autoResize();
        toast("احذف مرفقاً أولاً (بحد أقصى 2)");
        return;
      }
      const fname = `pasted-${Date.now().toString(36)}.txt`;
      showAttachmentPreview(new File([full], fname, { type: "text/plain" }), "text");
      composerInput.value = "";
      autoResize();
      toast("النص الطويل تحول لملف TXT تلقائياً");
    } catch (_) {}
  });
  composerInput.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && editingTarget) {
      e.preventDefault();
      cancelComposerEdit();
      return;
    }
    if (e.key === "Escape" && mentionMenu) {
      e.preventDefault();
      hideMentionMenu();
      return;
    }
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      composer.requestSubmit();
    }
  });
  window.addEventListener("x5:agent-open", () => scrollToBottom(false));

  // Scroll-to-latest glass button: appears when the user scrolls up.
  const scrollBtn = document.getElementById("chatScrollBtn");
  const updateScrollBtn = () => {
    if (!chatScroll || !scrollBtn) return;
    const far = chatScroll.scrollHeight - chatScroll.scrollTop - chatScroll.clientHeight > 320;
    scrollBtn.classList.toggle("show", far);
  };
  chatScroll.addEventListener("scroll", updateScrollBtn, { passive: true });
  scrollBtn?.addEventListener("click", () => scrollToBottom());
  updateScrollBtn();
}
