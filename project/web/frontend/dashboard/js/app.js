/**
 * app.js — X5Agent entry point, wires all modules
 * Single responsibility: initialization orchestration.
 */
"use strict";

import { initModal, hideBootSplash, animateIn } from "./ui.js?v=13";
import { initNavigation } from "./navigation.js?v=13";
import { startEvents, whenHello } from "./events.js?v=5";
import { initWorkspace } from "./workspace.js?v=17";
import { initDashboard } from "./dashboard.js?v=14";
import { initCharts } from "./charts.js?v=14";
import { initChat } from "./chat.js?v=86";
import { initSchedule } from "./schedule.js?v=25";
import { initPlugins } from "./plugins.js?v=9";
import { initSkills } from "./skills.js?v=10";
import { initGateway } from "./gateway.js?v=30";
import { initAiModels } from "./ai_models.js?v=6";
import { initSettings } from "./settings.js?v=11";

// Keeps .app pinned to the real visible area when the mobile keyboard
// opens. iOS doesn't shrink window.innerHeight and 100dvh differs per
// browser, so CSS alone can't hold the layout: visualViewport.height is
// the actual visible height (shrinks with the keyboard).
function initKeyboardViewport() {
  const vv = window.visualViewport;
  const app = document.querySelector(".app");
  if (!vv || !app) return;
  if (!window.matchMedia("(pointer: coarse)").matches) return; // touch only
  let textFocused = false;
  const isTextEntry = (el) =>
    el instanceof HTMLElement &&
    (el.tagName === "TEXTAREA" ||
      (el.tagName === "INPUT" && /^(text|search|email|tel|url|password|number)$/i.test(el.type || "text")) ||
      el.isContentEditable);
  const reset = () => {
    app.style.position = "";
    app.style.top = "";
    app.style.height = "";
    app.style.left = "";
    app.style.right = "";
    app.style.bottom = "";
    app.style.width = "";
  };
  const sync = () => {
    const shrink = window.innerHeight - vv.height;
    if (shrink > 120 && textFocused) {
      app.style.position = "fixed";
      app.style.top = `${vv.offsetTop}px`;
      app.style.height = `${vv.height}px`;
      app.style.left = "0";
      app.style.right = "0";
      app.style.bottom = "auto";
      app.style.width = "auto";
    } else {
      reset();
    }
  };
  document.addEventListener("focusin", (e) => {
    textFocused = isTextEntry(e.target);
    sync();
  });
  document.addEventListener("focusout", () => {
    // Deferred: focus moves to another input without the keyboard closing.
    setTimeout(() => {
      textFocused = isTextEntry(document.activeElement);
      sync();
    }, 0);
  });
  vv.addEventListener("resize", sync);
  vv.addEventListener("scroll", sync);
}

function replayEntrance() {
  // The boot entrance played behind the black splash: replay it on the
  // revealed page so content animates into view exactly once.
  try {
    // Desktop only: the sidebar rail slides in from the left once, in sync
    // with the page reveal (phones keep the drawer pattern untouched).
    if (window.matchMedia("(min-width: 1025px)").matches) {
      const bar = document.getElementById("sidebar");
      if (bar && !bar.classList.contains("sidebar-enter")) {
        bar.classList.add("sidebar-enter");
        bar.addEventListener("animationend", () => {
          try {
            bar.classList.remove("sidebar-enter");
          } catch (_) {}
        }, { once: true });
      }
    }
    const active = document.querySelector(".page.active");
    if (!active) return;
    if (active.dataset.page === "agent") {
      const chat = active.querySelector(".chat");
      if (chat) {
        chat.classList.remove("chat-enter");
        void chat.offsetWidth;
        chat.classList.add("chat-enter");
      }
      return;
    }
    animateIn(active);
  } catch (_) {}
}

async function init() {
  initModal();
  initKeyboardViewport();
  initNavigation();
  startEvents();
  // Boot barrier: the black splash lifts only after EVERY boot payload
  // lands (SSE hello snapshot + each module's first load). allSettled so
  // one failed fetch can never trap the splash; a bounded watchdog
  // force-releases it when the backend is unreachable.
  const watchdog = new Promise((resolve) => setTimeout(resolve, 15000));
  try {
    await Promise.race([
      Promise.allSettled([
        whenHello(),
        initDashboard(),
        initCharts(),
        initChat(),
        initPlugins(),
        initSkills(),
        initAiModels(),
        initWorkspace(),
        initSchedule(),
        initGateway(),
        initSettings(),
      ]),
      watchdog,
    ]);
  } catch (_) {}
  hideBootSplash();
  replayEntrance();
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", init);
} else {
  init();
}
