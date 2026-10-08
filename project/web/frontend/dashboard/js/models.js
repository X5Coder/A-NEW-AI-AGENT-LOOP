/**
 * models.js — Cloud providers only (one collapsible section)
 * Owns: fetching /api/models, section rendering, config/info modals.
 * - Cloud: provider vault. Keys masked (••••1234); a new key replaces the old.
 *   Only OpenRouter routes chat (model selectable); others are stored for future use.
 */
"use strict";

import { closeModal, openModal, toast } from "./ui.js?v=8";

const CHECK_SVG = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.8" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12" /></svg>`;

let cached = null;

function escapeHtml(s) {
  return String(s ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

function initials(name) {
  return String(name || "?")
    .split(/[\s_/-]+/)
    .map((w) => w[0])
    .join("")
    .slice(0, 2)
    .toUpperCase();
}

/* Provider marks (MIT, lobehub/lobe-icons static-png, trimmed + vendored
   under dashboard/img/providers — local files, no network at runtime). */
function iconFor(pid) {
  const img = (file, alt) => `<img class="prov" src="/img/providers/${file}.png" alt="${alt}" draggable="false" />`;
  switch (String(pid || "")) {
    case "openrouter":
      return img("openrouter", "OpenRouter");
    case "openai":
      return img("openai", "OpenAI");
    case "anthropic":
      return img("anthropic", "Anthropic");
    case "google":
      return img("gemini", "Gemini");
    case "deepseek":
      return img("deepseek", "DeepSeek");
    default:
      return "";
  }
}

async function postJson(url, body) {
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data?.error || `HTTP ${res.status}`);
  return data;
}

function cardHtml({ tag, title, desc, badge, badgeClass, version, icon }) {
  const el = document.createElement("button");
  el.className = "mini-card";
  el.type = "button";
  el.setAttribute("data-anim", "");
  el.innerHTML = `
    <div class="mini-top">
      <div class="mini-icon">${icon || escapeHtml(tag)}</div>
      <svg class="mini-arrow" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round">
        <polyline points="9 18 15 12 9 6" />
      </svg>
    </div>
    <div class="mini-title">${escapeHtml(title)}</div>
    <div class="mini-desc">${escapeHtml(desc)}</div>
    <div class="mini-foot">
      <span class="badge ${badgeClass}">${escapeHtml(badge)}</span>
      <span class="mini-ver">${escapeHtml(version)}</span>
    </div>`;
  return el;
}

function paintCloudCard(card, p) {
  // In-place badge sync — no re-render, no scroll jump.
  const on = p.active === true;
  const badge = card.querySelector(".badge");
  if (badge) {
    badge.textContent = !on ? "Off" : p.connected ? (p.routable ? "Connected" : "Saved") : "No key";
    badge.classList.toggle("green", on && p.connected);
    badge.classList.toggle("muted", !(on && p.connected));
  }
  const ver = card.querySelector(".mini-ver");
  if (ver) ver.textContent = (p.routable ? "Routes chat" : "Key vault") + (on ? " · Active" : "");
}

function modalHead(letters, title, subtitle, iconSvg) {
  return `
    <div class="modal-head">
      <div class="tile">${iconSvg || `<span class="tile-letters">${escapeHtml(letters)}</span>`}</div>
      <div class="modal-head-text">
        <h3>${escapeHtml(title)}</h3>
        <p>${escapeHtml(subtitle)}</p>
      </div>
      <button class="modal-close" data-close type="button" aria-label="Close">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round">
          <line x1="18" y1="6" x2="6" y2="18" /><line x1="6" y1="6" x2="18" y2="18" />
        </svg>
      </button>
    </div>`;
}

/* ---------------- cloud provider modal ---------------- */
function openProviderModal(provider, refresh) {
  const keyHint = provider.key_hint || "";
  const isActive = provider.active === true;
  openModal(`
    ${modalHead(provider.tag || initials(provider.name), provider.name, provider.connected ? "Connected" : "Enter your key to connect", iconFor(provider.id))}
    <div class="modal-body">
      <div class="block"><p class="modal-body-text">${escapeHtml(provider.description || "")}</p></div>
      <div class="block">
        <div class="block-title">Activation</div>
        <div class="toggle-row glass-panel" title="Enabling this provider disables the others automatically">
          <div class="t-text"><strong>Enable provider</strong><span>Activating one provider deactivates the others automatically</span></div>
          <label class="switch"><input type="checkbox" id="pActive" ${isActive ? "checked" : ""}></label>
        </div>
      </div>
      <div class="block">
        <div class="block-title">Info</div>
        <div class="info-row"><span class="k">Status</span><span class="v"><span class="badge glass ${isActive && provider.connected ? "green" : "muted"}" id="pStatusBadge">${!isActive ? "Inactive" : provider.connected ? "Connected" : "Not connected"}</span></span></div>
        <div class="info-row"><span class="k">Active</span><span class="v"><span class="badge glass ${isActive ? "green" : "muted"}" id="pActiveBadge">${isActive ? "Active" : "Inactive"}</span></span></div>
        <div class="info-row"><span class="k">Base URL</span><span class="v">${escapeHtml(provider.base_url || "—")}</span></div>
      </div>
      <div class="block" id="pCreds">
        <div class="block-title">Credentials</div>
        <div class="field">
          <label for="pKey">API key</label>
          <div class="field-wrap">
            <input class="input" id="pKey" type="password" placeholder="${escapeHtml(keyHint || "sk-••••••••••••")}" autocomplete="off" />
            <span class="field-check" id="pKeyCheck" hidden>${CHECK_SVG}</span>
            <button class="glass-btn" id="pConnect" type="button" hidden>Connect</button>
          </div>
          <span class="hint" id="pKeyHint">${provider.has_key ? `Saved as ${escapeHtml(keyHint)} — type a new key to replace it.` : "Stored on this server only. Never displayed in full."}</span>
          <div class="conn-result" id="pKeyResult" hidden></div>
        </div>
        <div class="field" style="margin-bottom:0;">
          <label for="pModel">Model</label>
          <div class="field-wrap">
            <input class="input" id="pModel" type="text" dir="ltr" spellcheck="false" autocomplete="off" placeholder="${escapeHtml((provider.models && provider.models[0]) || "model-name")}" value="${escapeHtml(provider.model || "")}" />
            <span class="field-check" id="pModelCheck" hidden>${CHECK_SVG}</span>
            <button class="glass-btn" id="pModelConnect" type="button" hidden>Connect</button>
          </div>
          <span class="hint">Saved on this server for future use. <b class="model-ok" id="pModelOk" hidden>OK</b></span>
          <div class="conn-result" id="pModelResult" hidden></div>
        </div>
      </div>
    </div>`);

  function syncModalBadges(on) {
    // Instant in-place update — the dialog never closes, nothing reloads.
    const sb = document.getElementById("pStatusBadge");
    if (sb) {
      const live = on && provider.connected;
      sb.textContent = !on ? "Inactive" : provider.connected ? "Connected" : "Not connected";
      sb.classList.toggle("green", live);
      sb.classList.toggle("muted", !live);
    }
    const ab = document.getElementById("pActiveBadge");
    if (ab) {
      ab.textContent = on ? "Active" : "Inactive";
      ab.classList.toggle("green", on);
      ab.classList.toggle("muted", !on);
    }
    const sw = document.getElementById("pActive");
    if (sw) sw.checked = on;
  }

  function syncGridCards() {
    // Exclusive activation can flip other cards — repaint them all in place.
    if (!cached || !Array.isArray(cached.cloud)) return;
    document.querySelectorAll("#modelsWrap [data-card-id]").forEach((card) => {
      const entry = cached.cloud.find((c) => c.id === card.dataset.cardId);
      if (entry) paintCloudCard(card, entry);
    });
  }

  function showConnResult(boxId, ok, title, detail) {
    // Per-field result template (key box / model box) — animated, dismissible.
    const box = document.getElementById(boxId);
    if (!box) return;
    const safeTitle = String(title || (ok ? "Connection OK" : "Connection failed")).slice(0, 120);
    const safeDetail = String(detail || "").slice(0, 2000);
    box.classList.toggle("ok", ok);
    box.classList.toggle("err", !ok);
    box.innerHTML = ok
      ? `<span class="conn-dot ok" aria-hidden="true"></span><span class="conn-text">${escapeHtml(safeTitle)}</span>
         <button type="button" class="conn-close" data-dismiss aria-label="Dismiss"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round"><line x1="18" y1="6" x2="6" y2="18" /><line x1="6" y1="6" x2="18" y2="18" /></svg></button>`
      : `<span class="conn-dot err" aria-hidden="true"></span>
         <span class="conn-text"><b>${escapeHtml(safeTitle)}</b>${safeDetail ? `<pre class="conn-detail" dir="ltr">${escapeHtml(safeDetail)}</pre>` : ""}</span>
         ${safeDetail ? `<button type="button" class="conn-copy" data-copy>Copy</button>` : ""}
         <button type="button" class="conn-close" data-dismiss aria-label="Dismiss"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round"><line x1="18" y1="6" x2="6" y2="18" /><line x1="6" y1="6" x2="18" y2="18" /></svg></button>`;
    box.hidden = false;
    box.classList.remove("play");
    void box.offsetWidth;
    box.classList.add("play");
    try {
      box.scrollIntoView({ behavior: "smooth", block: "nearest" });
    } catch (_) {}
    box.querySelector("[data-dismiss]")?.addEventListener("click", () => {
      box.hidden = true;
    });
    box.querySelector("[data-copy]")?.addEventListener("click", async () => {
      try {
        await navigator.clipboard.writeText(safeDetail);
        toast("Copied");
      } catch (_) {
        toast("Copy failed");
      }
    });
  }

  function showKeyResult(ok, title, detail) {
    showConnResult("pKeyResult", ok, title, detail);
  }

  function showModelResult(ok, title, detail) {
    showConnResult("pModelResult", ok, title, detail);
  }

  function hideKeyResult() {
    const keyBox = document.getElementById("pKeyResult");
    if (keyBox) keyBox.hidden = true;
    const modelBox = document.getElementById("pModelResult");
    if (modelBox) modelBox.hidden = true;
  }

  function hideModelOk() {
    document.getElementById("pModelOk")?.setAttribute("hidden", "");
  }

  function showModelOk() {
    const el = document.getElementById("pModelOk");
    if (!el) return;
    el.removeAttribute("hidden");
    el.classList.remove("play");
    void el.offsetWidth;
    el.classList.add("play");
  }

  function replayCheck(check) {
    // Force the success pop even if the badge was just unhidden.
    if (!check) return;
    check.classList.remove("play");
    void check.offsetWidth;
    check.classList.add("play");
  }

  const keyInput = document.getElementById("pKey");
  const keyBtn = document.getElementById("pConnect");
  const keyCheck = document.getElementById("pKeyCheck");
  const modelInput = document.getElementById("pModel");
  const modelBtn = document.getElementById("pModelConnect");
  const modelCheck = document.getElementById("pModelCheck");
  let initialModel = String(provider.model || "").trim();
  let testing = false;

  // Live background sync — badges, switch and cards follow the server state
  // while the dialog stays open. Never touches what the user is typing.
  // Self-cleans when the dialog is replaced or closed.
  const bgPoll = setInterval(async () => {
    if (testing) return;
    try {
      const panel = document.getElementById("modalPanel");
      const sw = document.getElementById("pActive");
      if (!panel || !sw || !panel.contains(sw)) {
        clearInterval(bgPoll);
        return;
      }
      const res = await fetch("/api/models", { cache: "no-store" });
      if (!res.ok) return;
      const data = await res.json();
      const fresh = Array.isArray(data?.cloud) ? data.cloud.find((c) => c.id === provider.id) : null;
      if (!fresh) return;
      provider.active = fresh.active === true;
      provider.connected = !!fresh.connected;
      provider.has_key = !!fresh.has_key;
      if (fresh.key_hint) provider.key_hint = fresh.key_hint;
      cached = data;
      syncModalBadges(provider.active);
      syncGridCards();
      syncKeyField();
      syncModelField();
      const hint = document.getElementById("pKeyHint");
      if (hint && !String(keyInput?.value || "").trim()) {
        hint.textContent = provider.has_key ? `Saved as ${provider.key_hint} — type a new key to replace it.` : "Stored on this server only. Never displayed in full.";
      }
    } catch (_) {}
  }, 6000);

  function syncKeyField() {
    // Glass Connect appears only while typing; a green check shows a saved key.
    const hasTyped = String(keyInput?.value || "").trim().length > 0;
    if (keyBtn) keyBtn.hidden = !hasTyped;
    if (keyCheck) keyCheck.hidden = !(provider.has_key && !hasTyped);
  }

  function syncModelField() {
    // Same rule for the model: Connect appears only when the text differs.
    const v = String(modelInput?.value || "").trim();
    const dirty = v.length > 0 && v !== initialModel;
    if (modelBtn) modelBtn.hidden = !dirty;
    if (modelCheck) modelCheck.hidden = !(provider.model && !dirty);
  }

  keyInput?.addEventListener("input", () => {
    hideKeyResult();
    hideModelOk();
    syncKeyField();
  });
  modelInput?.addEventListener("input", () => {
    hideModelOk();
    hideKeyResult();
    syncModelField();
  });
  syncKeyField();
  syncModelField();

  document.getElementById("pActive")?.addEventListener("change", async (e) => {
    const checked = e.target.checked;
    // Refuse activation without a saved key — point at the credentials block instead.
    if (checked && !provider.has_key && !provider.connected) {
      e.target.checked = false;
      const creds = document.getElementById("pCreds");
      creds?.classList.add("flash-error");
      creds?.scrollIntoView({ behavior: "smooth", block: "center" });
      setTimeout(() => creds?.classList.remove("flash-error"), 2400);
      toast("Save an API key first");
      return;
    }
    try {
      await postJson("/api/providers", { id: provider.id, active: checked });
      provider.active = checked;
      if (cached && Array.isArray(cached.cloud)) {
        cached.cloud.forEach((c) => {
          c.active = c.id === provider.id ? checked : checked ? false : c.active;
        });
      }
      syncModalBadges(checked);
      syncGridCards();
    } catch (err) {
      e.target.checked = !checked;
      toast(err instanceof Error ? err.message : "Save failed");
    }
  });

  document.getElementById("pConnect")?.addEventListener("click", async () => {
    const btn = document.getElementById("pConnect");
    const typedKey = String(keyInput?.value || "").trim();
    if (!typedKey && !provider.has_key) {
      toast("Enter the API key");
      showKeyResult(false, "Connection failed", "Enter the API key first.");
      return;
    }
    btn.disabled = true;
    const originalText = btn.textContent;
    btn.textContent = "Testing…";
    btn.classList.add("loading");
    testing = true;
    hideKeyResult();
    hideModelOk();
    if (keyCheck) keyCheck.hidden = true;
    try {
      // 1) Persist a newly typed key first (empty = keep the saved one).
      if (typedKey) {
        const saved = await postJson("/api/providers", { id: provider.id, api_key: typedKey });
        provider.has_key = !!saved.has_key;
        if (saved.key_hint) provider.key_hint = saved.key_hint;
        provider.connected = provider.has_key;
        if (keyInput) keyInput.value = "";
        const hint = document.getElementById("pKeyHint");
        if (hint) hint.textContent = provider.has_key ? `Saved as ${provider.key_hint} — type a new key to replace it.` : "Stored on this server only. Never displayed in full.";
      }
      // 2) Mandatory connection test against the saved key.
      let failure = null;
      try {
        const r = await postJson("/api/providers/test", { id: provider.id });
        if (!r || r.ok !== true) failure = (r && r.error) || "Unexpected response";
      } catch (err) {
        failure = err instanceof Error ? err.message : "Test failed";
      }
      if (failure) {
        // Never activate on failure — force the switch off.
        try {
          await postJson("/api/providers", { id: provider.id, active: false });
        } catch (_) {}
        provider.active = false;
        if (cached && Array.isArray(cached.cloud)) {
          cached.cloud.forEach((c) => {
            if (c.id === provider.id) {
              c.active = false;
              c.has_key = provider.has_key;
              c.connected = provider.connected;
            }
          });
        }
        syncModalBadges(false);
        syncGridCards();
        syncKeyField();
        showKeyResult(false, "Connection failed", failure);
        toast("Connection failed");
        return;
      }
      // 3) Success — activate the switch automatically, keep the dialog open.
      await postJson("/api/providers", { id: provider.id, active: true });
      provider.active = true;
      if (cached && Array.isArray(cached.cloud)) {
        cached.cloud.forEach((c) => {
          c.active = c.id === provider.id;
          if (c.id === provider.id) {
            c.has_key = provider.has_key;
            c.connected = provider.connected;
          }
        });
      }
      syncModalBadges(true);
      syncGridCards();
      syncKeyField();
      replayCheck(keyCheck);
      showKeyResult(true, "Connection OK — provider activated");
      toast("Connected");
    } catch (err) {
      const m = err instanceof Error ? err.message : "Save failed";
      syncKeyField();
      showKeyResult(false, "Connection failed", m);
      toast(m);
    } finally {
      btn.disabled = false;
      btn.textContent = originalText;
      btn.classList.remove("loading");
      testing = false;
    }
  });

  document.getElementById("pModelConnect")?.addEventListener("click", async () => {
    const btn = document.getElementById("pModelConnect");
    const typedModel = String(modelInput?.value || "").trim();
    if (!typedModel) {
      toast("Enter the model name");
      return;
    }
    btn.disabled = true;
    const originalText = btn.textContent;
    btn.textContent = "Testing…";
    btn.classList.add("loading");
    testing = true;
    hideModelOk();
    hideKeyResult();
    if (modelCheck) modelCheck.hidden = true;
    try {
      // 1) Persist the typed model.
      const saved = await postJson("/api/providers", { id: provider.id, model: typedModel });
      if (saved && typeof saved.model === "string" && saved.model) provider.model = saved.model;
      else provider.model = typedModel;
      initialModel = String(provider.model || "").trim();
      if (cached && Array.isArray(cached.cloud)) {
        cached.cloud.forEach((c) => {
          if (c.id === provider.id) c.model = provider.model;
        });
      }
      // 2) Verify with the provider test (saved key). Success shows a green OK only.
      let failure = null;
      try {
        const r = await postJson("/api/providers/test", { id: provider.id });
        if (!r || r.ok !== true) failure = (r && r.error) || "Unexpected response";
      } catch (err) {
        failure = err instanceof Error ? err.message : "Test failed";
      }
      if (failure) {
        syncModelField();
        showModelResult(false, "Model test failed", failure);
        toast("Model test failed");
        return;
      }
      showModelOk();
      syncModelField();
      replayCheck(modelCheck);
      toast("Model OK");
    } catch (err) {
      const m = err instanceof Error ? err.message : "Save failed";
      syncModelField();
      showModelResult(false, "Model test failed", m);
      toast(m);
    } finally {
      btn.disabled = false;
      btn.textContent = originalText;
      btn.classList.remove("loading");
      testing = false;
    }
  });
}

async function openProviderFresh(id, refresh) {
  // Always open the dialog with the latest server state (close + reopen is fresh).
  try {
    const res = await fetch("/api/models", { cache: "no-store" });
    if (res.ok) {
      const data = await res.json();
      if (data && Array.isArray(data.cloud)) {
        cached = data;
        const fresh = data.cloud.find((c) => c.id === id);
        if (fresh) {
          openProviderModal(fresh, refresh);
          return;
        }
      }
    }
  } catch (_) {}
  const stale = cached && Array.isArray(cached.cloud) ? cached.cloud.find((c) => c.id === id) : null;
  if (stale) openProviderModal(stale, refresh);
}

function render(data, refresh) {
  const wrap = document.getElementById("modelsWrap");
  if (!wrap) return;
  wrap.innerHTML = "";

  const cloud = Array.isArray(data?.cloud) ? data.cloud : [];

  const cloudCards = cloud.map((p) => {
    const on = p.active === true;
    const badge = !on ? "Off" : p.connected ? (p.routable ? "Connected" : "Saved") : "No key";
    const c = cardHtml({
      tag: p.tag || initials(p.name),
      icon: iconFor(p.id),
      title: p.name,
      desc: p.description || "",
      badge,
      badgeClass: "glass " + (on && p.connected ? "green" : "muted"),
      version: (p.routable ? "Routes chat" : "Key vault") + (on ? " · Active" : ""),
    });
    c.dataset.cardId = p.id;
    c.addEventListener("click", () => openProviderFresh(p.id, refresh));
    return c;
  });

  const connectedCount = cloud.filter((p) => p.connected).length;
  void connectedCount;
  const grid = document.createElement("div");
  grid.className = "grid-cards";
  if (!cloudCards.length) {
    grid.innerHTML = `<div class="grid-empty">Nothing here yet.</div>`;
  } else {
    cloudCards.forEach((c) => grid.appendChild(c));
  }
  wrap.appendChild(grid);

  // Entrance animation is owned by navigation — silent here (no double flash).
}

export async function initModels() {
  if (!document.getElementById("modelsWrap")) return;
  let lastFetch = 0;
  let dirty = true;

  async function refresh() {
    try {
      const res = await fetch("/api/models", { cache: "no-store" });
      if (!res.ok) throw new Error("bad status");
      cached = await res.json();
      render(cached, refresh);
      lastFetch = Date.now();
      dirty = false;
    } catch (_) {
      const wrap = document.getElementById("modelsWrap");
      if (wrap) wrap.innerHTML = `<div class="grid-empty">Models unavailable — check the server is running.</div>`;
    }
  }

  window.addEventListener("x5:page-open", (e) => {
    if (e.detail === "models") {
      if (dirty || Date.now() - lastFetch > 60000) refresh();
    }
  });
  window.addEventListener("x5:models-dirty", () => {
    dirty = true;
  });
  await refresh();
}