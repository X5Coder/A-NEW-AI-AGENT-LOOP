/**
 * settings.js — project info, check-then-confirm updates, security + account.
 * Owns: project version line, check-then-confirm updates (check only:
 * compares updates.json (repo root) with GitHub and shows its features,
 * with a separate "Update now" button that runs only after your approval —
 * code only, so no data is ever lost — plus live server-side progress while
 * updating), password change validation, logout (behind a transform-animated confirm dialog via
 * the shared modal shell). Local-only, honest labels.
 */
"use strict";

import { closeModal, modalShell, openModal, toast } from "./ui.js?v=13";

async function _getJson(url, opts) {
  const r = await fetch(url, opts);
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.error || ("Request failed (" + r.status + ")"));
  return data;
}

function _setStatus(text, ok) {
  const el = document.getElementById("updateStatus");
  if (!el) return;
  el.textContent = text;
  el.classList.toggle("ok", ok === true);
  el.classList.toggle("warn", ok === false);
}

function _showProgress(show, done) {
  const prog = document.getElementById("updateProg");
  const bar = document.getElementById("updateBar");
  const log = document.getElementById("updateLog");
  if (prog) prog.hidden = !show;
  if (log) log.hidden = !show;
  if (bar) {
    bar.classList.toggle("update-bar-running", show && !done);
    bar.style.transform = show && done ? "scaleX(1)" : "";
  }
  if (log && show) log.textContent = "";
}

function _hideNotes() {
  const wrap = document.getElementById("updateNotesWrap");
  if (wrap) wrap.hidden = true;
}

function _showNotes(title, body) {
  const wrap = document.getElementById("updateNotesWrap");
  const titleEl = document.getElementById("updateNotesTitle");
  const bodyEl = document.getElementById("updateNotes");
  if (!wrap || !titleEl || !bodyEl) return;
  if (!body) {
    wrap.hidden = true;
    return;
  }
  titleEl.textContent = title || "What's new";
  bodyEl.textContent = body;
  wrap.hidden = false;
}

function _setUpdateButtons(mode) {
  // One glass button visible at a time: "check" | "update" | "none" (while busy).
  const checkBtn = document.getElementById("checkUpdateBtn");
  const runBtn = document.getElementById("updateRunBtn");
  if (checkBtn) {
    checkBtn.hidden = mode !== "check";
    checkBtn.disabled = false;
  }
  if (runBtn) {
    runBtn.hidden = mode !== "update";
    runBtn.disabled = false;
  }
}

function _pollUpdateProgress(btn) {
  let shown = 0;
  const tick = async () => {
    let snap;
    try {
      snap = await _getJson("/api/update_progress");
    } catch (_) {
      setTimeout(tick, 2000);
      return;
    }
    const log = document.getElementById("updateLog");
    const lines = Array.isArray(snap.lines) ? snap.lines : [];
    if (log && lines.length > shown) {
      log.textContent += lines.slice(shown).join("\n") + "\n";
      log.scrollTop = log.scrollHeight;
      shown = lines.length;
    }
    if (snap.done) {
      _showProgress(true, true);
      if (snap.ok) {
        _setStatus("Updated to the latest release. Restart the console to apply it.", true);
        toast("Updated — restart the console");
        _setUpdateButtons("check");
      } else {
        _setStatus("Update failed — see the log above.", false);
        _setUpdateButtons("update");
      }
      return;
    }
    setTimeout(tick, 1500);
  };
  setTimeout(tick, 1200);
}

async function _onCheckUpdates() {
  _setUpdateButtons("none");
  _setStatus("Checking updates.json on GitHub for a new version…");
  _showProgress(false);
  _hideNotes();
  let info;
  try {
    info = await _getJson("/api/update_check");
  } catch (e) {
    _setStatus("Check failed: " + e.message, false);
    _setUpdateButtons("check");
    return;
  }
  const local = info.local || "unknown";
  const localTag = "local v" + local;
  if (info.error && !info.latest && info.up_to_date !== false) {
    _setStatus("Check failed: " + info.error + " (" + localTag + ")", false);
    _setUpdateButtons("check");
    return;
  }
  if (info.up_to_date) {
    _setStatus("You are up to date — " + localTag + ".", true);
    _setUpdateButtons("check");
    return;
  }
  if (info.up_to_date === false) {
    // Update found: tell first, show the features, reveal the Update button.
    const remoteTag = info.latest ? "v" + info.latest : "a newer version";
    _setStatus("New version " + remoteTag + " available (" + localTag + ") — read what's new, then press Update now. No data will be lost: the update refreshes code only.");
    _showNotes("What's new in v" + (info.latest || ""), info.notes);
    _setUpdateButtons("update");
    return;
  }
  _setStatus("Check failed: " + (info.error || "nothing to compare yet.") + " (" + localTag + ")", false);
  _setUpdateButtons("check");
}

async function _onRunUpdate() {
  _setUpdateButtons("none");
  _setStatus("Updating now — code only, your data is never touched. Watch the progress below.");
  _showProgress(true, false);
  try {
    await _getJson("/api/update_run", { method: "POST" });
  } catch (e) {
    _setStatus("Update could not start: " + e.message, false);
    _setUpdateButtons("update");
    return;
  }
  _pollUpdateProgress();
}

const LOGOUT_ICON = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4" /><polyline points="16 17 21 12 16 7" /><line x1="21" y1="12" x2="9" y2="12" /></svg>`;

function openLogoutConfirm(onConfirm) {
  // Shared modal shell: transform rise on open, transform sink on close
  // (openModal/closeModal own both animations; data-close + Escape + outside
  // all funnel through the same animated close path).
  openModal(modalShell({
    icon: LOGOUT_ICON,
    title: "Sign out?",
    subtitle: "End this console session",
    body: `<div class="block"><p class="modal-body-text">You will be signed out on this device and local preferences will be cleared.</p></div>`,
    foot: `
      <button class="glass-btn" data-close type="button">Cancel</button>
      <button class="glass-btn danger" id="logoutConfirmBtn" type="button">Sign out</button>`,
  }));
  document.getElementById("logoutConfirmBtn")?.addEventListener("click", () => {
    closeModal();
    if (typeof onConfirm === "function") onConfirm();
  });
}

async function _refreshChatCount() {
  // Live message count for the main chat (conversation.json only).
  const badge = document.getElementById("chatCountBadge");
  if (!badge) return;
  try {
    const res = await fetch("/api/conversation", { cache: "no-store" });
    const data = await res.json().catch(() => ({}));
    const msgs = data && Array.isArray(data.messages) ? data.messages : null;
    if (!res.ok || !msgs) throw new Error("count failed");
    badge.textContent = msgs.length === 0
      ? "No messages"
      : `${msgs.length} message${msgs.length === 1 ? "" : "s"}`;
  } catch (_) {
    badge.textContent = "—";
  }
}

export function initSettings() {
  document.getElementById("checkUpdateBtn")?.addEventListener("click", (e) => {
    _onCheckUpdates(e.currentTarget);
  });

  document.getElementById("updateRunBtn")?.addEventListener("click", (e) => {
    _onRunUpdate(e.currentTarget);
  });

  document.getElementById("changePwBtn")?.addEventListener("click", () => {
    const oldPw = document.getElementById("pwOld");
    const newPw = document.getElementById("pwNew");
    const confirmPw = document.getElementById("pwConfirm");
    if (!oldPw?.value || !newPw?.value || !confirmPw?.value) {
      toast("Fill in all password fields");
      return;
    }
    if (newPw.value.length < 8) {
      toast("New password too short");
      return;
    }
    if (newPw.value !== confirmPw.value) {
      toast("Passwords do not match");
      return;
    }
    fetch("/api/auth/password", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ old_password: oldPw.value, new_password: newPw.value }),
    }).then((r) => r.json().then((d) => ({ s: r.status, d }))).then(({ s, d }) => {
      if (s === 200 && d && d.ok) {
        toast("Password updated");
      } else if (s === 401) {
        location.href = "/login.html";
      } else {
        toast((d && d.error) || "Password change failed");
      }
    }).catch(() => toast("Password change failed"));
    oldPw.value = "";
    newPw.value = "";
    confirmPw.value = "";
  });

  document.getElementById("clearChatBtn")?.addEventListener("click", () => {
    // Destructive: clears the MAIN chat history + its embeddings only.
    // Saved memories are never touched — the confirm says so explicitly.
    openModal(modalShell({
      icon: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6" /><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" /></svg>`,
      title: "Delete chat history?",
      subtitle: "Main chat only",
      body: `<div class="block"><p class="modal-body-text">This deletes the main agent chat and its search index completely. Your saved memories will NOT be deleted — only the chat.</p></div>`,
      foot: `
      <button class="glass-btn" data-close type="button">Cancel</button>
      <button class="glass-btn danger" id="clearChatConfirmBtn" type="button">Delete chat only</button>`,
    }));
    document.getElementById("clearChatConfirmBtn")?.addEventListener("click", async (e) => {
      const btn = e.currentTarget;
      // Loading spinner until the server confirms BOTH files are wiped
      // (conversation.json + chat embeddings) — the response only arrives
      // after the full delete, so closing here means fully deleted.
      btn.disabled = true;
      btn.classList.add("loading");
      try {
        const data = await _getJson("/api/conversation/clear", { method: "POST" });
        closeModal();
        // Reset the Agent view to the empty welcome state (same as fresh history).
        try {
          const inner = document.getElementById("chatInner");
          if (inner) {
            inner.querySelectorAll(".msg").forEach((m) => m.remove());
            if (!document.getElementById("welcomeMsg")) {
              const w = document.createElement("div");
              w.className = "chat-welcome";
              w.id = "welcomeMsg";
              w.innerHTML = "<h2>How may I assist you, sir?</h2><p>I&#39;m X5Agent, your personal AI agent, here to save you time and handle the work for you</p>";
              inner.prepend(w);
            }
          }
        } catch (_) {}
        _refreshChatCount();
        // No task exists anymore: disarm a stale Continue pill (same key
        // the chat uses) and tell the Agent view to recompute its button.
        try { localStorage.removeItem("x5:stopped-task"); } catch (_) {}
        try { window.dispatchEvent(new CustomEvent("x5:chat-cleared")); } catch (_) {}
        toast(data && typeof data.removed === "number" && data.removed
          ? `Chat + search index deleted (${data.removed} messages)`
          : "Chat + search index deleted");
      } catch (err) {
        btn.disabled = false;
        btn.classList.remove("loading");
        toast((err && err.message) || "Delete failed");
      }
    });
  });

  document.getElementById("logoutBtn")?.addEventListener("click", () => {
    openLogoutConfirm(() => {
      fetch("/api/auth/logout", { method: "POST" }).catch(() => {}).finally(() => {
        try {
          ["x5-gateway-whatsapp", "x5-gateway-telegram"].forEach((k) => localStorage.removeItem(k));
        } catch (_) {
          // ignore
        }
        location.href = "/login.html";
      });
    });
  });

  // Returned for the app.js boot barrier (black splash waits on both).
  // NOTE: keep this return LAST - everything above wires buttons, and an
  // earlier return would leave them dead (unreachable code).
  return Promise.allSettled([
    fetch("/api/dashboard").then((r) => r.json()).then((d) => {
      const el = document.getElementById("projVersion");
      if (el && d && d.version) el.textContent = "v" + d.version;
    }).catch(() => {}),
    _refreshChatCount(),
  ]);
}
