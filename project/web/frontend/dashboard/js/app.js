/**
 * app.js — X5Agent entry point, wires all modules
 * Single responsibility: initialization orchestration.
 */
"use strict";

import { initModal } from "./ui.js?v=8";
import { initNavigation } from "./navigation.js?v=10";
import { startEvents } from "./events.js?v=1";
import { initWorkspace } from "./workspace.js?v=4";
import { initDashboard } from "./dashboard.js?v=9";
import { initCharts } from "./charts.js?v=9";
import { initChat } from "./chat.js?v=49";
import { initPlugins } from "./plugins.js?v=8";
import { initSkills } from "./skills.js?v=8";
import { initGateway } from "./gateway.js?v=8";
import { initModels } from "./models.js?v=8";
import { initSettings } from "./settings.js?v=8";

function init() {
  initModal();
  initNavigation();
  startEvents();
  initWorkspace();
  initDashboard();
  initCharts();
  initChat();
  initPlugins();
  initSkills();
  initGateway();
  initModels();
  initSettings();
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", init);
} else {
  init();
}
