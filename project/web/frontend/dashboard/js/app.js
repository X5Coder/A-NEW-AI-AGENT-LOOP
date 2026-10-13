/**
 * app.js — X5Agent entry point, wires all modules
 * Single responsibility: initialization orchestration.
 */
"use strict";

import { initModal, paintSkeletons } from "./ui.js?v=10";
import { initNavigation } from "./navigation.js?v=12";
import { startEvents } from "./events.js?v=4";
import { initWorkspace } from "./workspace.js?v=16";
import { initDashboard } from "./dashboard.js?v=11";
import { initCharts } from "./charts.js?v=13";
import { initChat } from "./chat.js?v=85";
import { initSchedule } from "./schedule.js?v=24";
import { initPlugins } from "./plugins.js?v=8";
import { initSkills } from "./skills.js?v=9";
import { initGateway } from "./gateway.js?v=29";
import { initAiModels } from "./ai_models.js?v=5";
import { initSettings } from "./settings.js?v=10";

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

function init() {
  initModal();
  // Skeletons first: templates (with shimmer) exist before the first
  // navigate(), so the entrance animation plays on them exactly once —
  // every module then swaps real data in with no re-animation.
  paintSkeletons();
  initKeyboardViewport();
  initNavigation();
  startEvents();
  initWorkspace();
  initDashboard();
  initCharts();
  initChat();
  initSchedule();
  initPlugins();
  initSkills();
  initGateway();
  initAiModels();
  initSettings();
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", init);
} else {
  init();
}