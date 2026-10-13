/**
 * ui.js — shared interface helpers (X5Agent workshop)
 * Owns: entry animations, boot splash hide, modal shell, toast, chart tooltip.
 * No business logic, no API calls.
 */
"use strict";

export function animateIn(root, base = 0) {
  if (!root) return;
  const els = Array.from(root.querySelectorAll("[data-anim]"));
  els.forEach((el) => el.classList.remove("anim"));
  void root.offsetWidth;
  els.forEach((el, i) => {
    const d = el.dataset.delay !== undefined ? Number(el.dataset.delay) : base + i * 40;
    el.style.animationDelay = `${d}ms`;
    el.classList.add("anim");
  });
}

/* ---------- boot splash (black start screen) ----------
   Shown from first paint (static HTML in index.html). hideBootSplash()
   runs once the app.js boot barrier resolves (every boot payload landed):
   the splash fades out and is removed from the DOM. Idempotent, never
   throws, safe to call before the splash exists. */

let splashHidden = false;

export function hideBootSplash() {
  if (splashHidden) return;
  splashHidden = true;
  try {
    window.__x5bootDone = true;
  } catch (_) {}
  try {
    document.body.classList.remove("booting");
    const el = document.getElementById("bootSplash");
    if (!el) return;
    if (!el.classList.contains("show")) {
      // Data landed before the splash ever appeared: no flash at all.
      el.remove();
      return;
    }
    el.classList.add("done");
    setTimeout(() => {
      try {
        el.remove();
      } catch (_) {}
    }, 650);
  } catch (_) {}
}

/* ---------- modal ---------- */
const backdrop = () => document.getElementById("modalBackdrop");
const panel = () => document.getElementById("modalPanel");
let lastFocused = null;
let closeTimer = null;
let modalPushed = false;
let suppressPop = false;
let exitAnim = null;

function pushModalHistory() {
  // Lets the phone back button close the dialog instead of leaving the page.
  if (modalPushed) return;
  try {
    history.pushState({ x5modal: true }, "");
    modalPushed = true;
  } catch (_) {}
}

function popModalHistory() {
  if (!modalPushed) return;
  modalPushed = false;
  try {
    suppressPop = true;
    history.back();
  } catch (_) {
    suppressPop = false;
  }
}

export function openModal(html) {
  const b = backdrop();
  const p = panel();
  if (!b || !p) return;
  if (closeTimer) {
    clearTimeout(closeTimer);
    closeTimer = null;
  }
  if (exitAnim) {
    try { exitAnim.cancel(); } catch (_) {}
    exitAnim = null;
  }
  b.classList.remove("closing");
  lastFocused = document.activeElement;
  p.innerHTML = html;
  b.classList.add("open");
  document.body.style.overflow = "hidden";
  pushModalHistory();
  animateIn(p, 40);
  const focusable = p.querySelector("input, .btn-primary");
  if (focusable) setTimeout(() => focusable.focus(), 120);
}

function playExitAnimation(p) {
  // Web Animations API exit: runs on the compositor (transform + opacity
  // only) and is immune to stale stylesheets — the same poses as the CSS.
  try {
    if (!p || typeof p.animate !== "function") return null;
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return null;
    const mobile = window.matchMedia("(max-width: 640px)").matches;
    const to = mobile
      ? { transform: "translate3d(0, 32px, 0)", opacity: "0" }
      : { transform: "translate3d(0, 12px, 0) scale(0.96)", opacity: "0" };
    return p.animate(
      [
        { transform: "translate3d(0, 0, 0) scale(1)", opacity: "1" },
        to,
      ],
      { duration: 220, easing: "cubic-bezier(0.4, 0, 0.2, 1)", fill: "forwards" }
    );
  } catch (_) {
    return null;
  }
}

function requestClose() {
  const b = backdrop();
  const p = panel();
  if (!b || !p) return false;
  if (!b.classList.contains("open") || b.classList.contains("closing")) return false;
  // Unified transform exit: panel sinks, backdrop fades.
  // Every close path funnels here — outside, X, Escape, back button.
  b.classList.add("closing");
  if (exitAnim) {
    try { exitAnim.cancel(); } catch (_) {}
    exitAnim = null;
  }
  exitAnim = playExitAnimation(p);
  if (closeTimer) clearTimeout(closeTimer);
  closeTimer = setTimeout(() => {
    if (exitAnim) {
      try { exitAnim.cancel(); } catch (_) {}
      exitAnim = null;
    }
    b.classList.remove("open");
    b.classList.remove("closing");
    document.body.style.overflow = "";
    if (lastFocused && typeof lastFocused.focus === "function") lastFocused.focus();
    closeTimer = null;
  }, 240);
  return true;
}

export function closeModal() {
  if (requestClose()) popModalHistory();
}

export function modalShell({ icon, title, subtitle, body, foot }) {
  return `
    <div class="modal-head">
      <div class="tile">${icon}</div>
      <div class="modal-head-text">
        <h3>${title}</h3>
        <p>${subtitle}</p>
      </div>
      <button class="modal-close" data-close type="button" aria-label="Close">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round">
          <line x1="18" y1="6" x2="6" y2="18" /><line x1="6" y1="6" x2="18" y2="18" />
        </svg>
      </button>
    </div>
    <div class="modal-body">${body}</div>
    <div class="modal-foot">${foot}</div>`;
}

export function initModal() {
  const b = backdrop();
  const p = panel();
  if (!b || !p) return;
  // Drag-safe backdrop close: a text-selection drag that starts inside the
  // panel and ends outside must NOT close the dialog — only a true click
  // (down + up both on the backdrop) closes it.
  let downOnBackdrop = false;
  let upOnBackdrop = false;
  b.addEventListener("mousedown", (e) => {
    downOnBackdrop = e.target === b;
  });
  b.addEventListener("mouseup", (e) => {
    upOnBackdrop = e.target === b;
  });
  b.addEventListener("click", (e) => {
    const shouldClose = e.target === b && downOnBackdrop && upOnBackdrop;
    downOnBackdrop = false;
    upOnBackdrop = false;
    if (shouldClose) closeModal();
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && b.classList.contains("open")) closeModal();
  });
  window.addEventListener("popstate", () => {
    if (suppressPop) {
      suppressPop = false;
      return;
    }
    // Phone back button with an open dialog: consume the step as "close dialog".
    modalPushed = false;
    requestClose();
  });
  p.addEventListener("click", (e) => {
    if (e.target.closest("[data-close]")) closeModal();
  });
}

/* ---------- toast ---------- */
export function toast(message) {
  const wrap = document.getElementById("toastWrap");
  if (!wrap) return;
  while (wrap.children.length >= 3) wrap.firstChild.remove();
  const el = document.createElement("div");
  el.className = "toast";
  const text = document.createElement("span");
  text.textContent = String(message).slice(0, 80);
  el.innerHTML = `
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round">
      <polyline points="20 6 9 17 4 12" />
    </svg>`;
  el.appendChild(text);
  wrap.appendChild(el);
  setTimeout(() => {
    el.classList.add("out");
    setTimeout(() => el.remove(), 180);
  }, 2200);
}

/* ---------- chart tooltip (shared, bounded) ---------- */
export function attachChartTooltip(container) {
  const tip = document.getElementById("chartTip");
  if (!container || !tip) return;
  container.querySelectorAll(".bar, .flow-hit").forEach((bar) => {
    const show = () => {
      const rect = bar.getBoundingClientRect();
      const label = document.createElement("span");
      label.className = "tip-day";
      label.textContent = bar.dataset.label || "";
      const val = document.createElement("span");
      val.className = "tip-val";
      val.textContent = bar.dataset.value || "";
      tip.innerHTML = "";
      tip.appendChild(label);
      tip.appendChild(val);
      tip.style.left = `${rect.left + rect.width / 2}px`;
      tip.style.top = `${rect.top}px`;
      tip.classList.add("show");
    };
    bar.addEventListener("mouseenter", show);
    bar.addEventListener("mousemove", show);
    bar.addEventListener("mouseleave", () => tip.classList.remove("show"));
  });
}

export function hideChartTip() {
  document.getElementById("chartTip")?.classList.remove("show");
}

let pinTimer = null;

/* Pinned tooltip for touch screens (tap a bar to see the day info). */
export function pinChartTip(bar) {
  const tip = document.getElementById("chartTip");
  if (!bar || !tip) return;
  if (pinTimer) clearTimeout(pinTimer);
  const rect = bar.getBoundingClientRect();
  const label = document.createElement("span");
  label.className = "tip-day";
  label.textContent = bar.dataset.label || "";
  const val = document.createElement("span");
  val.className = "tip-val";
  val.textContent = bar.dataset.value || "";
  tip.innerHTML = "";
  tip.appendChild(label);
  tip.appendChild(val);
  tip.style.left = `${rect.left + rect.width / 2}px`;
  tip.style.top = `${rect.top}px`;
  tip.classList.add("show");
  pinTimer = setTimeout(() => tip.classList.remove("show"), 2600);
}