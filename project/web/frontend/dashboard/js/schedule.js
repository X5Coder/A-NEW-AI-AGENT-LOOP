/**
 * schedule.js — left-edge scheduler panel (mirror of the browser right-edge tab).
 *
 * Single responsibility: show scheduled jobs pushed over SSE (`scheduler`
 * frames) in a left-edge tab + floating list. Each job card shows the task
 * name only, a live countdown to next_run, and a (?) button for the full
 * detail dialog (task data + "تعليمات المهمة" skill view/edit + run
 * history with per-run preview chat). No polling anywhere: SSE push +
 * one local 1s tick for the countdown text only.
 */
"use strict";

import { onPush } from "./events.js?v=1";
import { openModal, closeModal, toast } from "./ui.js?v=9";

let jobs = []; // summarized jobs from the server (executing flag included)
let miniOpen = false;
let tabY = null;
let tickBound = false;

function nowMs() {
  return Date.now();
}

function parseRunMs(iso) {
  if (!iso) return null;
  const t = Date.parse(iso);
  return Number.isFinite(t) ? t : null;
}

function lastRun(job) {
  const h = Array.isArray(job.run_history) ? job.run_history : [];
  return h.length ? h[h.length - 1] : null;
}

function statusOf(job) {
  if (job.executing) return "running";
  const lr = lastRun(job);
  if (!lr) return job.status === "paused" ? "paused" : "waiting";
  return lr.success ? "ok" : "fail";
}

/** Executing/due-first ordering: running jobs float to the top automatically. */
function sortedJobs() {
  const arr = jobs.slice();
  arr.sort((a, b) => {
    const ae = a.executing ? 1 : 0;
    const be = b.executing ? 1 : 0;
    if (ae !== be) return be - ae;
    const an = parseRunMs(a.next_run);
    const bn = parseRunMs(b.next_run);
    if (an !== null && bn !== null) return an - bn;
    if (an !== null) return -1;
    if (bn !== null) return 1;
    return String(a.task).localeCompare(String(b.task));
  });
  return arr;
}

function countdownText(job) {
  if (job.executing) return "يتم التنفيذ…";
  const ms = parseRunMs(job.next_run);
  if (ms === null) {
    const lr = lastRun(job);
    if (lr) return lr.success ? "تم التنفيذ" : "فشل التنفيذ";
    return job.status === "paused" ? "متوقفة" : "مكتملة";
  }
  let s = Math.max(0, Math.floor((ms - nowMs()) / 1000));
  if (s <= 0) return "حان الوقت…";
  const d = Math.floor(s / 86400);
  const h = Math.floor((s % 86400) / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  if (d > 0) return `بعد ${d} يوم ${h} س`;
  if (h > 0) return `بعد ${h} س ${m} د`;
  if (m > 0) return `بعد ${m} د ${sec} ث`;
  return `بعد ${sec} ث`;
}

function esc(s) {
  return String(s == null ? "" : s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

/* ---------- left-edge tab (mirrors #browserTab, pinned left) ---------- */

function placeSchedTab(y) {
  // Fixed-right rail like the browser tab: only the vertical position moves.
  const tab = document.getElementById("scheduleTab");
  if (!tab) return;
  const h = tab.offsetHeight || 150;
  const top = typeof y === "number" ? y : 0;
  const cy = Math.min(Math.max(60, top), Math.max(60, window.innerHeight - h - 8));
  tabY = cy;
  tab.style.left = "auto";
  tab.style.right = "0";
  tab.style.bottom = "auto";
  tab.style.top = `${cy}px`;
}

function bindTabClampOnce() {
  if (bindTabClampOnce.done) return;
  bindTabClampOnce.done = true;
  window.addEventListener("resize", () => {
    try {
      if (tabY !== null) placeSchedTab(tabY);
    } catch (_) {}
  });
}

function initSchedTabDrag(tab) {
  let startY = 0;
  let baseY = 0;
  let dragging = false;
  let moved = false;
  const onMove = (e) => {
    if (!dragging) return;
    const y = e.clientY != null ? e.clientY : (e.touches && e.touches[0] ? e.touches[0].clientY : null);
    if (y === null) return;
    if (Math.abs(y - startY) > 4) moved = true;
    if (moved) {
      tab.classList.add("dragging");
      placeSchedTab(baseY + (y - startY));
    }
  };
  const onUp = () => {
    dragging = false;
    tab.classList.remove("dragging");
    window.removeEventListener("pointermove", onMove);
    window.removeEventListener("pointerup", onUp);
    if (!moved) openMini();
  };
  tab.addEventListener("pointerdown", (e) => {
    if (e.target.closest(".tab-x")) return;
    dragging = true;
    moved = false;
    startY = e.clientY;
    baseY = tabY !== null ? tabY : tab.getBoundingClientRect().top;
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
  });
}

function ensureTab() {
  // Visible while at least one job exists, hidden when none.
  let tab = document.getElementById("scheduleTab");
  if (!jobs.length) {
    if (tab) tab.remove();
    const mini = document.getElementById("scheduleMini");
    if (mini) mini.remove();
    miniOpen = false;
    return;
  }
  if (!tab) {
    tab = document.createElement("div");
    tab.id = "scheduleTab";
    tab.setAttribute("role", "button");
    tab.setAttribute("tabindex", "0");
    tab.setAttribute("aria-label", "Open scheduled tasks");
    tab.innerHTML = `
      <span class="tab-count" id="schedTabCount"></span>
      <span class="tab-label">Scheduled Tasks</span>`;
    tab.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        openMini();
      }
    });
    document.body.appendChild(tab);
    // Right side is shared with the browser tab: default below it so the
    // two tabs never cover each other (both stay freely draggable).
    if (tabY !== null) placeSchedTab(tabY);
    else {
      let defY = Math.floor(window.innerHeight * 0.62);
      try {
        const bt = document.getElementById("browserTab");
        if (bt) {
          const r = bt.getBoundingClientRect();
          defY = Math.min(window.innerHeight - 170, r.bottom + 12);
        }
      } catch (_) {}
      placeSchedTab(defY);
    }
    initSchedTabDrag(tab);
    bindTabClampOnce();
  }
  const badge = tab.querySelector("#schedTabCount");
  if (badge) badge.textContent = String(jobs.length);
  const running = jobs.some((j) => j.executing);
  tab.classList.toggle("has-running", running);
}

/* ---------- floating list (mirrors #liveMini, pinned left) ---------- */

function cardClass(job) {
  const st = statusOf(job);
  if (st === "running") return "is-running";
  if (st === "ok") return "is-ok";
  if (st === "fail") return "is-fail";
  return "";
}

function renderMini() {
  const mini = document.getElementById("scheduleMini");
  if (!mini) return;
  const list = mini.querySelector(".sched-list");
  if (!list) return;
  const arr = sortedJobs();
  if (!arr.length) {
    list.innerHTML = `<div class="sched-empty">لا توجد مهام مجدولة</div>`;
  } else {
    list.innerHTML = "";
    for (const job of arr) {
      const card = document.createElement("div");
      card.className = `sched-card ${cardClass(job)}`;
      card.dataset.job = job.job_id;
      const icon = document.createElement("span");
      icon.className = "sched-icon";
      icon.setAttribute("aria-hidden", "true");
      icon.innerHTML = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9" /><polyline points="12 7 12 12 15.5 14" /></svg>`;
      const mid = document.createElement("div");
      mid.className = "sched-mid";
      const name = document.createElement("div");
      name.className = "sched-name";
      name.dir = "auto";
      name.textContent = job.task || job.job_id;
      const cd = document.createElement("div");
      cd.className = "sched-count";
      cd.dataset.cd = job.job_id;
      cd.textContent = countdownText(job);
      mid.appendChild(name);
      mid.appendChild(cd);
      const q = document.createElement("button");
      q.type = "button";
      q.className = "sched-q";
      q.setAttribute("aria-label", "Task details");
      q.textContent = "?";
      q.addEventListener("click", (e) => {
        e.stopPropagation();
        openDetail(job.job_id);
      });
      card.appendChild(icon);
      card.appendChild(mid);
      card.appendChild(q);
      list.appendChild(card);
    }
  }
  const title = mini.querySelector(".sched-mini-count");
  if (title) title.textContent = `${arr.length} مهمة`;
}

function tickCountdowns() {
  // Local 1s tick rewrites countdown text only — no network, no re-render.
  const nodes = document.querySelectorAll("#scheduleMini [data-cd]");
  if (!nodes.length) return;
  const byId = new Map(jobs.map((j) => [j.job_id, j]));
  for (const el of nodes) {
    const job = byId.get(el.getAttribute("data-cd"));
    if (!job) continue;
    const t = countdownText(job);
    if (el.textContent !== t) el.textContent = t;
  }
}

function initMiniDrag(mini) {
  const head = mini.querySelector(".sched-mini-head");
  if (!head) return;
  let sx = 0;
  let sy = 0;
  let ox = 0;
  let oy = 0;
  let dragging = false;
  head.addEventListener("pointerdown", (e) => {
    if (e.target.closest("button")) return;
    dragging = true;
    sx = e.clientX;
    sy = e.clientY;
    const r = mini.getBoundingClientRect();
    ox = r.left;
    oy = r.top;
    head.setPointerCapture(e.pointerId);
  });
  head.addEventListener("pointermove", (e) => {
    if (!dragging) return;
    const nx = Math.min(Math.max(0, ox + (e.clientX - sx)), window.innerWidth - 120);
    const ny = Math.min(Math.max(0, oy + (e.clientY - sy)), window.innerHeight - 80);
    mini.style.left = `${nx}px`;
    mini.style.right = "auto";
    mini.style.top = `${ny}px`;
  });
  head.addEventListener("pointerup", () => {
    if (!dragging) return;
    dragging = false;
    // Dock like the live browser player: pushed into the right edge, the
    // panel glides off and collapses back to the edge tab (which never hides).
    try {
      const r = mini.getBoundingClientRect();
      if (r.right >= window.innerWidth - 30) {
        mini.classList.add("docking");
        setTimeout(() => {
          mini.remove();
          miniOpen = false;
        }, 320);
      }
    } catch (_) {}
  });
}

function openMini() {
  let mini = document.getElementById("scheduleMini");
  if (mini) {
    renderMini();
    return;
  }
  mini = document.createElement("div");
  mini.id = "scheduleMini";
  mini.innerHTML = `
    <div class="sched-mini-head">
      <span class="sched-mini-title">المهام المجدولة</span>
      <span class="sched-mini-count"></span>
    </div>
    <div class="sched-list"></div>`;
  document.body.appendChild(mini);
  // Default dock: right side under the tab (same rail as the browser tab).
  mini.style.right = "44px";
  mini.style.top = "120px";
  initMiniDrag(mini);
  miniOpen = true;
  renderMini();
}

/* ---------- detail dialog: full task + تعليمات المهمة + runs ---------- */

async function fetchDetail(jobId) {
  const res = await fetch(`/api/scheduler?job_id=${encodeURIComponent(jobId)}`);
  if (!res.ok) throw new Error("load failed");
  const data = await res.json();
  if (!data || !data.job) throw new Error("job not found");
  return data.job;
}

function runRow(run, idx, jobId, skillText) {
  // Uniform single-line row: icon + name + run time (all same size) + preview.
  const ok = !!run.success;
  const when = run.at ? new Date(run.at).toLocaleString() : "—";
  return `
    <div class="sched-run ${ok ? "is-ok" : "is-fail"}">
      <span class="sched-run-dot"></span>
      <span class="sched-run-title" dir="auto">${ok ? "نجاح" : "فشل"}</span>
      <span class="sched-run-time" dir="ltr">${esc(when)}</span>
      <button type="button" class="sched-preview-btn" data-run="${idx}" title="معاينة" aria-label="Preview run">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z" /><circle cx="12" cy="12" r="3" /></svg>
      </button>
    </div>`;
}

async function openDetail(jobId) {
  let job;
  try {
    job = await fetchDetail(jobId);
  } catch (_) {
    toast("تعذر تحميل المهمة");
    return;
  }
  const runs = Array.isArray(job.run_history) ? job.run_history.slice().reverse() : [];
  openModal(`
    <div class="modal-head">
      <div class="tile"><span class="tile-letters">؟</span></div>
      <div class="modal-head-text"><h3 dir="auto">${esc(job.task || job.job_id)}</h3></div>
      <button class="modal-close" data-close type="button" aria-label="Close">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round">
          <line x1="18" y1="6" x2="6" y2="18" /><line x1="6" y1="6" x2="18" y2="18" />
        </svg>
      </button>
    </div>
    <div class="modal-body sched-detail">
      <div class="sched-meta">
        <div><b>الحالة:</b> <span dir="auto">${esc(job.status || "—")}</span></div>
        <div><b>التكرار:</b> <span dir="auto">${esc(job.repeat || "مرة واحدة")}</span></div>
        <div><b>التشغيل القادم:</b> <span dir="ltr">${esc(job.next_run || "—")}</span></div>
        <div><b>آخر تشغيل:</b> <span dir="ltr">${esc(job.last_run || "—")}</span></div>
        <div><b>عدد التنفيذات:</b> <span dir="ltr">${esc(job.run_count || 0)}${job.repeat_count ? " / " + esc(job.repeat_count) : ""}</span></div>
      </div>
      <div class="sched-skill-card">
        <div class="sched-skill-head">
          <h4>تعليمات المهمة</h4>
          <span class="sched-skill-actions">
            <button type="button" class="sched-icon-btn" id="schedSkillEditBtn" title="تعديل" aria-label="Edit instructions">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17 3a2.85 2.83 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5Z" /></svg>
            </button>
            <button type="button" class="sched-icon-btn sched-save" id="schedSkillSaveBtn" title="حفظ" aria-label="Save instructions" hidden>
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12" /></svg>
            </button>
            <button type="button" class="sched-icon-btn" id="schedSkillCancelBtn" title="إلغاء" aria-label="Cancel editing" hidden>
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"><line x1="18" y1="6" x2="6" y2="18" /><line x1="6" y1="6" x2="18" y2="18" /></svg>
            </button>
          </span>
        </div>
        <pre class="sched-skill" dir="auto" id="schedSkillPre">${esc(job.full_instructions || job.skill_preview || "—")}</pre>
        <textarea class="input sched-edit" id="schedSkillEdit" rows="8" dir="auto" hidden></textarea>
      </div>
      <div class="modal-foot sched-detail-foot">
        <button class="btn sched-delete-btn" id="schedDeleteBtn" type="button">حذف المهمة</button>
        <button class="btn" data-close type="button">إغلاق</button>
      </div>
      <h4>حالات التنفيذ (${runs.length})</h4>
      <div class="sched-runs">
        ${runs.length ? runs.map((r, i) => runRow(r, runs.length - 1 - i, job.job_id, "")).join("") : '<div class="sched-empty">لم تُنفذ بعد</div>'}
      </div>
    </div>`);
  const editBtn = document.getElementById("schedSkillEditBtn");
  const saveBtn = document.getElementById("schedSkillSaveBtn");
  const cancelBtn = document.getElementById("schedSkillCancelBtn");
  const pre = document.getElementById("schedSkillPre");
  const area = document.getElementById("schedSkillEdit");
  if (area) area.value = job.full_instructions || job.skill_preview || "";
  const setEditing = (on) => {
    if (!pre || !area || !editBtn || !saveBtn || !cancelBtn) return;
    pre.hidden = on;
    area.hidden = !on;
    editBtn.hidden = on;
    saveBtn.hidden = !on;
    cancelBtn.hidden = !on;
    if (on) area.focus();
  };
  editBtn?.addEventListener("click", () => setEditing(true));
  cancelBtn?.addEventListener("click", () => {
    if (area) area.value = pre ? pre.textContent : "";
    setEditing(false);
  });
  saveBtn?.addEventListener("click", async () => {
    const text = (area?.value || "").trim();
    if (!text) {
      toast("التعليمات فارغة");
      return;
    }
    saveBtn.disabled = true;
    try {
      const res = await fetch("/api/scheduler", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action: "update_skill", job_id: job.job_id, instructions: text }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.error || "save failed");
      toast("تم حفظ التعليمات");
      if (pre) pre.textContent = text;
      setEditing(false);
    } catch (err) {
      toast("فشل الحفظ");
    } finally {
      saveBtn.disabled = false;
    }
  });
  document.querySelectorAll(".sched-preview-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      const idx = Number(btn.getAttribute("data-run"));
      try {
        closeModal();
      } catch (_) {}
      openRunPage(job.job_id, idx);
    });
  });
  // Two-step delete: arm → confirm. Removes job + skill + branch session.
  const delBtn = document.getElementById("schedDeleteBtn");
  let armTimer = null;
  delBtn?.addEventListener("click", async () => {
    if (!delBtn.classList.contains("armed")) {
      delBtn.classList.add("armed");
      delBtn.textContent = "تأكيد الحذف؟";
      armTimer = setTimeout(() => {
        delBtn.classList.remove("armed");
        delBtn.textContent = "حذف المهمة";
      }, 4000);
      return;
    }
    if (armTimer) clearTimeout(armTimer);
    delBtn.disabled = true;
    try {
      const res = await fetch("/api/scheduler", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action: "delete", job_id: job.job_id }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.error || "delete failed");
      try {
        closeModal();
      } catch (_) {}
      toast("تم حذف المهمة");
    } catch (_) {
      toast("فشل الحذف");
      delBtn.disabled = false;
      delBtn.classList.remove("armed");
      delBtn.textContent = "حذف المهمة";
    }
  });
}

/* ---------- run page: a FULL agent-like section per run, hash-routed ----------
 * Preview opens /#sched-<jobid>-<runIdx> (a real session URL, not a dialog):
 * skill as the user message + execution output as the agent reply + a working
 * follow-up composer. Refresh/deep-link keeps the same session. Back returns
 * to the agent page. */

const HASH_RE = /^#sched-([A-Za-z0-9_-]{1,64})-(\d{1,3})$/;

let runView = null; // {jobId, runIdx, skill, task} while the run page is open

function bubble(who, text, mid) {
  // Same message shape as the main agent chat: .msg.user / .msg.assistant.
  const el = document.createElement("div");
  el.className = who === "user" ? "msg user" : "msg assistant";
  if (who === "pending") el.classList.add("is-pending");
  if (mid) el.dataset.mid = String(mid).slice(0, 16);
  const content = document.createElement("div");
  content.className = "msg-content";
  content.dir = "auto";
  content.textContent = text;
  el.appendChild(content);
  return el;
}

function updateSchedVisibility() {
  // The scheduler tab + list live in the Agent section only (agent page and
  // its full run sessions — never dashboard/workspace/models/...).
  let on = false;
  try {
    on = !!document.querySelector('.page[data-page="agent"].active')
      || !!document.querySelector('.page[data-page="schedrun"].active');
  } catch (_) {}
  try {
    document.body.classList.toggle("sched-visible", on);
  } catch (_) {}
}

function showPage(name) {
  // Same activation the router uses (pages + titles + scroll mode).
  const titles = {
    agent: ["Agent", "Chat with your AI assistant"],
    schedrun: ["Scheduled Task", "Follow-up chat for one scheduled run"],
  };
  document.querySelectorAll(".nav-item").forEach((b) => {
    b.classList.toggle("active", b.dataset.nav === (name === "schedrun" ? "agent" : name));
  });
  document.querySelectorAll(".page").forEach((p) => {
    p.classList.toggle("active", p.dataset.page === name);
  });
  const t = titles[name] || titles.agent;
  const h = document.getElementById("pageTitle");
  const s = document.getElementById("pageSub");
  if (h) h.textContent = t[0];
  if (s) s.textContent = t[1];
  const content = document.getElementById("content");
  if (content) {
    content.classList.toggle("no-scroll", name === "agent" || name === "schedrun");
    content.scrollTop = 0;
  }
  updateSchedVisibility();
}

function hideRunPage() {
  if (!runView) return;
  runView = null;
  showPage("agent");
}

async function renderRunPage(jobId, runIdx) {
  let detail;
  try {
    detail = await fetchDetail(jobId);
  } catch (_) {
    toast("تعذر تحميل المهمة");
    hideRunPage();
    try {
      history.replaceState(null, "", "/");
    } catch (_) {}
    return;
  }
  const hist = Array.isArray(detail.run_history) ? detail.run_history : [];
  const run = hist[runIdx] || hist[hist.length - 1] || null;
  const skill = detail.full_instructions || detail.skill_preview || detail.task || "";
  runView = { jobId, runIdx, skill, task: detail.task || detail.job_id };
  showPage("schedrun");
  const title = document.getElementById("schedRunTitle");
  if (title) title.textContent = detail.task || detail.job_id;
  const log = document.getElementById("schedRunLog");
  const scroll = document.getElementById("schedRunScroll");
  if (log) {
    log.innerHTML = "";
    const sess = Array.isArray(detail.session) ? detail.session : [];
    if (sess.length) {
      // The job's own branch session: every execution + follow-up lives here.
      for (const m of sess) {
        log.appendChild(bubble(m.role === "assistant" ? "agent" : "user", m.content || "—", m.id));
      }
      // Jump to the previewed run's own reply (fallback: bottom).
      const anchor = run && run.assistant_msg
        ? log.querySelector(`[data-mid="${CSS.escape(run.assistant_msg)}"]`)
        : null;
      requestAnimationFrame(() => {
        try {
          if (anchor) {
            anchor.scrollIntoView({ block: "center" });
            anchor.classList.add("flash");
            setTimeout(() => anchor.classList.remove("flash"), 1600);
          } else {
            scroll?.scrollTo(0, scroll.scrollHeight);
          }
        } catch (_) {}
      });
    } else {
      // Legacy fallback: jobs executed before branch sessions existed.
      log.appendChild(bubble("user", skill || "—"));
      log.appendChild(bubble("agent", (run && run.output) || (detail.executing ? "يتم التنفيذ الآن…" : "لا يوجد ناتج بعد")));
    }
  }
}

function openRunPage(jobId, runIdx) {
  const idx = Number.isFinite(Number(runIdx)) ? Number(runIdx) : 0;
  try {
    history.pushState({ schedrun: true }, "", `/#sched-${jobId}-${idx}`);
  } catch (_) {}
  renderRunPage(jobId, idx);
}

function handleHash() {
  // Deep-link / refresh on a run session re-opens the same full section.
  let m = null;
  try {
    m = HASH_RE.exec(location.hash || "");
  } catch (_) {}
  if (m) {
    renderRunPage(m[1], Number(m[2]));
  } else if (runView) {
    hideRunPage();
  }
}



/* ---------- push wiring ---------- */

function onSchedPush(data) {
  try {
    const list = data && Array.isArray(data.jobs) ? data.jobs : [];
    jobs = list.filter((j) => j && typeof j.job_id === "string");
    ensureTab();
    if (miniOpen || document.getElementById("scheduleMini")) {
      miniOpen = true;
      if (!document.getElementById("scheduleMini")) openMini();
      else renderMini();
    }
    // A newly created job pops its list open once (like the browser preview).
    if (jobs.length && !document.getElementById("scheduleMini") && onSchedPush.lastCount !== undefined && jobs.length > onSchedPush.lastCount) {
      openMini();
    }
    onSchedPush.lastCount = jobs.length;
  } catch (_) {}
}

async function fetchInitial() {
  try {
    const res = await fetch("/api/scheduler");
    if (!res.ok) return;
    const data = await res.json();
    onSchedPush(data);
    onSchedPush.lastCount = jobs.length; // no auto-pop on first load
    const mini = document.getElementById("scheduleMini");
    if (mini) mini.remove();
    miniOpen = false;
  } catch (_) {}
}

let runMode = "restricted"; // permission mode of the run-page composer
let runPending = []; // [{type, name, dataUrl}] max 2, cleared after each send
let runRecorder = null;
let runRecChunks = [];
let runRecStream = null;

function readFileAsDataURL(file) {
  return new Promise((resolve, reject) => {
    try {
      const reader = new FileReader();
      reader.onload = () => resolve(reader.result);
      reader.onerror = () => reject(new Error("read failed"));
      reader.readAsDataURL(file);
    } catch (e) {
      reject(e);
    }
  });
}

function renderRunChips() {
  const box = document.getElementById("schedRunAttachments");
  if (!box) return;
  box.innerHTML = "";
  runPending.forEach((att, i) => {
    const chip = document.createElement("div");
    chip.className = "sched-att-chip";
    const label = document.createElement("span");
    label.textContent = att.name;
    label.dir = "auto";
    const x = document.createElement("button");
    x.type = "button";
    x.textContent = "✕";
    x.setAttribute("aria-label", "Remove attachment");
    x.addEventListener("click", () => {
      runPending.splice(i, 1);
      renderRunChips();
    });
    chip.appendChild(label);
    chip.appendChild(x);
    box.appendChild(chip);
  });
}

function bindRunFormOnce() {
  if (bindRunFormOnce.done) return;
  bindRunFormOnce.done = true;
  const form = document.getElementById("schedRunForm");
  const input = document.getElementById("schedRunInput");
  if (!form || !input) return;

  // Textarea autogrow (same feel as the main composer).
  input.addEventListener("input", () => {
    try {
      input.style.height = "auto";
      input.style.height = Math.min(input.scrollHeight, 120) + "px";
    } catch (_) {}
  });

  // Attach menu (same template as the main composer).
  const attachBtn = document.getElementById("schedRunAttachBtn");
  const attachMenu = document.getElementById("schedRunAttachMenu");
  attachBtn?.addEventListener("click", (e) => {
    e.stopPropagation();
    document.getElementById("schedRunModeMenu")?.classList.remove("open");
    attachMenu?.classList.toggle("open");
  });
  document.addEventListener("click", (e) => {
    try {
      if (attachMenu?.classList.contains("open")
        && !attachMenu.contains(e.target) && e.target !== attachBtn
        && !attachBtn?.contains(e.target)) attachMenu.classList.remove("open");
      const mm = document.getElementById("schedRunModeMenu");
      const mb = document.getElementById("schedRunModeBtn");
      if (mm?.classList.contains("open") && !mm.contains(e.target)
        && !mb?.contains(e.target)) mm.classList.remove("open");
    } catch (_) {}
  });
  const pickers = {
    image: document.getElementById("schedRunFileImage"),
    video: document.getElementById("schedRunFileVideo"),
    document: document.getElementById("schedRunFileDocument"),
  };
  attachMenu?.querySelectorAll("button[data-attach]").forEach((btn) => {
    btn.addEventListener("click", () => {
      attachMenu.classList.remove("open");
      const kind = btn.getAttribute("data-attach");
      try {
        pickers[kind]?.click();
      } catch (_) {}
    });
  });
  Object.entries(pickers).forEach(([kind, picker]) => {
    picker?.addEventListener("change", async () => {
      const files = picker.files ? Array.from(picker.files).slice(0, 2) : [];
      picker.value = "";
      for (const file of files) {
        if (runPending.length >= 2) {
          toast("حد أقصى مرفقين");
          break;
        }
        const limit = kind === "image" ? 8 * 1024 * 1024 : kind === "video" ? 24 * 1024 * 1024 : 100 * 1024 * 1024;
        if (file.size > limit) {
          toast("ملف كبير جداً");
          continue;
        }
        try {
          const dataUrl = await readFileAsDataURL(file);
          runPending.push({ type: kind, name: (file.name || "file").slice(0, 200), data: dataUrl });
        } catch (_) {
          toast("تعذر قراءة الملف");
        }
      }
      renderRunChips();
    });
  });

  // Mode pill (same template: Restricted / Free).
  const modeBtn = document.getElementById("schedRunModeBtn");
  const modeMenu = document.getElementById("schedRunModeMenu");
  const modeLabel = document.getElementById("schedRunModeLabel");
  const paintMode = () => {
    if (modeLabel) modeLabel.textContent = runMode === "free" ? "Full access" : "Restricted";
    modeMenu?.querySelectorAll("button").forEach((b) => {
      b.classList.toggle("active", b.dataset.mode === runMode);
    });
  };
  if (modeMenu) {
    const ICONS = {
      restricted: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 3.5 19 6v5.2c0 4.2-2.9 7.3-7 9.2-4.1-1.9-7-5-7-9.2V6l7-2.5Z"/><path d="m8.8 12 2 2 4.4-4.6"/></svg>`,
      free: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M13 2.8 4.8 13.2h5.4L9 21.2l8.2-10.4h-5.4L13 2.8Z"/></svg>`,
    };
    modeMenu.innerHTML = `<div class="mode-head">Agent permission</div>`;
    [
      { val: "restricted", title: "Restricted", desc: "Ask before any terminal command (default)" },
      { val: "free", title: "Full access", desc: "Terminal runs without asking" },
    ].forEach((opt) => {
      const b = document.createElement("button");
      b.type = "button";
      b.dataset.mode = opt.val;
      b.className = "mode-opt";
      b.innerHTML = `<span class="mode-opt-icon" aria-hidden="true">${ICONS[opt.val]}</span>`
        + `<span><b></b><span class="desc"></span></span><span class="radio" aria-hidden="true"></span>`;
      b.querySelector("b").textContent = opt.title;
      b.querySelector(".desc").textContent = opt.desc;
      b.addEventListener("click", () => {
        runMode = b.dataset.mode === "free" ? "free" : "restricted";
        paintMode();
        modeMenu.classList.remove("open");
      });
      modeMenu.appendChild(b);
    });
  }
  paintMode();
  modeBtn?.addEventListener("click", (e) => {
    e.stopPropagation();
    attachMenu?.classList.remove("open");
    modeMenu?.classList.toggle("open");
  });

  // Mic: record → /api/stt → insert transcript (same backend as main chat).
  const micBtn = document.getElementById("schedRunMicBtn");
  micBtn?.addEventListener("click", async () => {
    try {
      if (runRecorder) {
        runRecorder.stop();
        return;
      }
      if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === "undefined") {
        toast("التسجيل غير مدعوم هنا");
        return;
      }
      runRecStream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const mime = ["audio/webm", "audio/mp4"].find((m) => { try { return MediaRecorder.isTypeSupported(m); } catch (_) { return false; } });
      runRecChunks = [];
      runRecorder = mime ? new MediaRecorder(runRecStream, { mimeType: mime }) : new MediaRecorder(runRecStream);
      runRecorder.ondataavailable = (e) => {
        if (e.data && e.data.size) runRecChunks.push(e.data);
      };
      runRecorder.onstop = async () => {
        const rec = runRecorder;
        runRecorder = null;
        micBtn.classList.remove("recording");
        try {
          runRecStream?.getTracks().forEach((t) => t.stop());
        } catch (_) {}
        runRecStream = null;
        const blob = new Blob(runRecChunks, { type: rec.mimeType || "audio/webm" });
        runRecChunks = [];
        if (!blob.size) return;
        micBtn.disabled = true;
        try {
          const dataUrl = await new Promise((resolve, reject) => {
            const r = new FileReader();
            r.onload = () => resolve(r.result);
            r.onerror = () => reject(new Error("read failed"));
            r.readAsDataURL(blob);
          });
          const res = await fetch("/api/stt", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ audio: dataUrl }),
          });
          const data = await res.json().catch(() => ({}));
          const text = res.ok ? String(data.text || "").trim() : "";
          if (text) {
            input.value = (input.value ? input.value + " " : "") + text;
            input.dispatchEvent(new Event("input"));
            input.focus();
          } else {
            toast("لم يُسمع صوت واضح");
          }
        } catch (_) {
          toast("تعذر التفريغ الصوتي");
        } finally {
          micBtn.disabled = false;
        }
      };
      runRecorder.start();
      micBtn.classList.add("recording");
      toast("يسجل… دوس مجدداً للإيقاف");
    } catch (_) {
      toast("تعذر بدء التسجيل");
      runRecorder = null;
      micBtn.classList.remove("recording");
    }
  });

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    if (!runView) return;
    const msg = (input.value || "").trim();
    if (!msg && !runPending.length) return;
    input.value = "";
    input.style.height = "auto";
    const atts = runPending.slice(0, 2);
    runPending = [];
    renderRunChips();
    const log = document.getElementById("schedRunLog");
    log?.appendChild(bubble("user", msg || "مرفقات"));
    log?.appendChild(bubble("pending", "…"));
    const scroll = document.getElementById("schedRunScroll");
    scroll?.scrollTo(0, scroll.scrollHeight);
    try {
      const res = await fetch("/api/scheduler", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          action: "chat",
          job_id: runView.jobId,
          message: msg,
          attachments: atts,
          mode: runMode,
        }),
      });
      const data = await res.json().catch(() => ({}));
      log?.querySelector(".msg.is-pending")?.remove();
      log?.appendChild(bubble("agent", res.ok ? data.reply || "—" : `تعذر الإرسال: ${data.error || res.status}`));
    } catch (_) {
      log?.querySelector(".msg.is-pending")?.remove();
      log?.appendChild(bubble("agent", "تعذر الإرسال (انقطع الاتصال)"));
    }
    scroll?.scrollTo(0, scroll.scrollHeight);
  });
  document.getElementById("schedRunBack")?.addEventListener("click", () => {
    try {
      history.back();
    } catch (_) {
      try {
        history.replaceState(null, "", "/");
      } catch (_) {}
      hideRunPage();
    }
  });
}

export function initSchedule() {
  onPush("scheduler", onSchedPush);
  fetchInitial();
  bindRunFormOnce();
  // Hash session routing: open run page on deep-link/refresh, close on back.
  window.addEventListener("hashchange", handleHash);
  // Browser back/forward lands here too (after the router): keep views in sync.
  window.addEventListener("popstate", () => {
    try {
      if (!HASH_RE.exec(location.hash || "")) hideRunPage();
    } catch (_) {}
  });
  // Sidebar clicks while a run page is open: strip the session hash so the
  // URL never points at a hidden session (router owns the page switch).
  document.querySelectorAll(".nav-item").forEach((btn) => {
    btn.addEventListener("click", () => {
      if (!runView) return;
      // The router early-returns on same-page clicks, so leaving the run
      // page for Agent is done here; other pages are handled by x5:page-open.
      if (btn.dataset.nav === "agent") {
        try {
          history.replaceState(null, "", location.pathname);
        } catch (_) {}
        hideRunPage();
      }
    });
  });
  // Router switches (sidebar/back-forward) re-gate Agent-only visibility.
  // The router owns the page; here we only drop any stale session hash.
  window.addEventListener("x5:page-open", () => {
    try {
      if (HASH_RE.exec(location.hash || "")) {
        history.replaceState(null, "", location.pathname);
      }
    } catch (_) {}
    runView = null;
    updateSchedVisibility();
  });
  handleHash();
  updateSchedVisibility();
  if (!tickBound) {
    tickBound = true;
    setInterval(tickCountdowns, 1000);
  }
}
