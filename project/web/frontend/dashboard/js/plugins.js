/**
 * plugins.js — real discoverable plugins (behind tool_search)
 * Owns: fetching /api/plugins, card grid, detail modal.
 * Permission = the whole tool (title + desc + switch). No actions shown.
 * Core tools are hidden (only discoverable tools are listed here).
 * Switches persist silently in the background — no page refresh.
 */
"use strict";

import { openModal, toast } from "./ui.js?v=13";

function escapeHtml(s) {
  return String(s ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

/* Fixed mark for every plugin (MIT, lobehub/lobe-icons static-png,
   trimmed + vendored under dashboard/img/icons). */
const PLUGIN_ICON = `<img class="prov" src="/img/icons/mcp.png" alt="Plugin" draggable="false" />`;

async function postState(body) {
  const res = await fetch("/api/plugin_state", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data?.error || `HTTP ${res.status}`);
  return data;
}

function badgeFor(p) {
  if (p.effective === false) {
    return { text: p.off_reason === "model" ? "Stopped · model" : "Disabled", cls: "muted" };
  }
  return { text: "Connected", cls: "green" };
}

function openPluginModal(plugin) {
  // Permission = whole action. Multi-action tools list every action;
  // single-permission tools list one row bound to the plugin switch.
  const actions = Array.isArray(plugin.actions) ? plugin.actions : [];
  const rowsHtml = actions.length
    ? actions
        .map((a, i) => {
          const on = a.enabled !== false && plugin.effective !== false;
          return `<div class="toggle-row glass-panel" title="${escapeHtml(a.desc || a.title || a.name)}">
              <div class="t-text"><strong>${escapeHtml(a.title || a.name)}</strong><span>${escapeHtml(a.desc || "")}</span></div>
              <label class="switch"><input type="checkbox" data-action="${i}" ${on ? "checked" : ""}></label>
            </div>`;
        })
        .join("")
    : `<div class="perm perm-name"><span>No permissions</span></div>`;

  const stoppedByModel = plugin.effective === false && plugin.off_reason === "model";
  const modelName = plugin.model ? plugin.model.replace(/_/g, " ") : "";

  openModal(`
    <div class="modal-head">
      <div class="tile">${PLUGIN_ICON}</div>
      <div class="modal-head-text">
        <h3>${escapeHtml(plugin.title)}</h3>
        <p>${plugin.effective === false ? "Stopped" : "Connected"} · ${escapeHtml(plugin.version)}</p>
      </div>
      <button class="modal-close" data-close type="button" aria-label="Close">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round">
          <line x1="18" y1="6" x2="6" y2="18" /><line x1="6" y1="6" x2="18" y2="18" />
        </svg>
      </button>
    </div>
    <div class="modal-body">
      <div class="block"><p class="modal-body-text">${escapeHtml(plugin.long_description || plugin.description)}</p></div>
      <div class="block">
        <div class="block-title">Permissions</div>
        ${rowsHtml}
        ${stoppedByModel ? `<p class="modal-body-text" style="margin-top:8px;">Stopped because the ${escapeHtml(modelName)} model is off.</p>` : ""}
      </div>
      <div class="block">
        <div class="block-title">Info</div>
        <div class="info-row"><span class="k">Tool id</span><span class="v">${escapeHtml(plugin.id)}</span></div>
        <div class="info-row"><span class="k">Source</span><span class="v">Local · tool_search</span></div>
        ${plugin.model ? `<div class="info-row"><span class="k">Model</span><span class="v">${escapeHtml(modelName)}</span></div>` : ""}
      </div>
    </div>`);

  // Silent background persist — no refresh, modal stays open.
  document.querySelectorAll("[data-action]").forEach((input) => {
    input.addEventListener("change", async (e) => {
      const action = actions[Number(e.target.dataset.action)];
      if (!action) return;
      const checked = e.target.checked;
      try {
        if (action.scope === "plugin") {
          await postState({ plugin: plugin.id, enabled: checked });
        } else {
          await postState({ plugin: plugin.id, permissions: { [action.name]: checked } });
        }
        dirty = true;
      } catch (err) {
        e.target.checked = !checked;
        toast(err instanceof Error ? err.message : "Save failed");
      }
    });
  });
}

function renderPlugins(plugins) {
  const grid = document.getElementById("pluginsGrid");
  if (!grid) return;

  grid.innerHTML = "";
  if (!plugins.length) {
    grid.innerHTML = `<div class="grid-empty">No plugins Available</div>`;
    return;
  }
  plugins.forEach((p) => {
    const badge = badgeFor(p);
    const card = document.createElement("button");
    card.className = "mini-card";
    card.type = "button";
    card.setAttribute("data-anim", "");
    card.innerHTML = `
      <div class="mini-top">
        <div class="mini-icon">${PLUGIN_ICON}</div>
        <svg class="mini-arrow" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round">
          <polyline points="9 18 15 12 9 6" />
        </svg>
      </div>
      <div class="mini-title">${escapeHtml(p.title)}</div>
      <div class="mini-desc">${escapeHtml(p.description)}</div>
      <div class="mini-foot">
        <span class="badge ${badge.cls}">${escapeHtml(badge.text)}</span>
        <span class="mini-ver">${escapeHtml(p.version)}</span>
      </div>`;

    card.addEventListener("click", () => openPluginModal(p));
    grid.appendChild(card);
  });
}

export async function fetchPlugins() {
  const res = await fetch("/api/plugins", { cache: "no-store" });
  if (!res.ok) throw new Error("bad status");
  const data = await res.json();
  const plugins = Array.isArray(data?.plugins) ? data.plugins : [];
  return plugins;
}

let lastFetch = 0;
let dirty = true;

export async function initPlugins() {
  const grid = document.getElementById("pluginsGrid");
  if (!grid) return;

  async function refresh() {
    try {
      renderPlugins(await fetchPlugins());
      lastFetch = Date.now();
      dirty = false;
    } catch (_) {
      grid.innerHTML = `<div class="grid-empty">Plugins unavailable — check the server is running.</div>`;
    }
  }

  // Fresh data when something changed elsewhere; never double-load on navigation.
  window.addEventListener("x5:plugins-dirty", () => {
    dirty = true;
    refresh();
  });
  window.addEventListener("x5:page-open", (e) => {
    if (e.detail === "plugins" && (dirty || Date.now() - lastFetch > 60000)) refresh();
  });
  await refresh();
}