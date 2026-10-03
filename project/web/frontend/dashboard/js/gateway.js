/**
 * gateway.js — WhatsApp + Telegram channel connections
 * Owns: connection badges, config modals (number/token),
 * local persistence. No secrets leave the browser except
 * when the user explicitly connects via their own gateway.
 */
"use strict";

import { openModal, modalShell, toast } from "./ui.js?v=8";

const WA_KEY = "x5-gateway-whatsapp";
const TG_KEY = "x5-gateway-telegram";

const WA_ICON = `<svg viewBox="0 0 24 24" fill="currentColor"><path d="M17.472 14.382c-.297-.149-1.758-.867-2.03-.967-.273-.099-.471-.148-.67.15-.197.297-.767.966-.94 1.164-.173.199-.347.223-.644.075-.297-.15-1.255-.463-2.39-1.475-.883-.788-1.48-1.761-1.653-2.059-.173-.297-.018-.458.13-.606.134-.133.298-.347.446-.52.149-.174.198-.298.298-.497.099-.198.05-.371-.025-.52-.075-.149-.669-1.612-.916-2.207-.242-.579-.487-.5-.669-.51a12.8 12.8 0 0 0-.57-.01c-.198 0-.52.074-.792.372-.272.297-1.04 1.016-1.04 2.479 0 1.462 1.065 2.875 1.213 3.074.149.198 2.096 3.2 5.077 4.487.709.306 1.262.489 1.694.625.712.227 1.36.195 1.872.118.571-.085 1.758-.719 2.006-1.413.248-.694.248-1.289.173-1.413-.074-.124-.272-.198-.57-.347m-5.421 7.403h-.004a9.87 9.87 0 0 1-5.031-1.378l-.361-.214-3.741.982.998-3.648-.235-.374a9.86 9.86 0 0 1-1.51-5.26c.001-5.45 4.436-9.884 9.888-9.884a9.82 9.82 0 0 1 6.988 2.896 9.825 9.825 0 0 1 2.893 6.994c-.003 5.45-4.437 9.884-9.885 9.884m8.413-18.297A11.815 11.815 0 0 0 12.05 0C5.495 0 .16 5.335.157 11.892c0 2.096.547 4.142 1.588 5.945L.057 24l6.305-1.654a11.882 11.882 0 0 0 5.683 1.448h.005c6.554 0 11.89-5.335 11.893-11.893a11.821 11.821 0 0 0-3.48-8.413Z" /></svg>`;
const TG_ICON = `<svg viewBox="0 0 24 24" fill="currentColor"><path d="M11.944 0A12 12 0 0 0 0 12a12 12 0 0 0 12 12 12 12 0 0 0 12-12A12 12 0 0 0 12 0a12 12 0 0 0-.056 0zm4.962 7.224c.1-.002.321.023.465.14a.506.506 0 0 1 .171.325c.016.093.036.306.02.472-.18 1.898-.962 6.502-1.36 8.627-.168.9-.499 1.201-.82 1.23-.696.065-1.225-.46-1.9-.902-1.056-.693-1.653-1.124-2.678-1.8-1.185-.78-.417-1.21.26-1.91.177-.184 3.247-2.977 3.307-3.23.007-.032.014-.15-.056-.212s-.174-.041-.249-.024c-.106.024-1.793 1.14-5.061 3.345-.48.33-.913.49-1.302.48-.428-.008-1.252-.241-1.865-.44-.752-.245-1.349-.374-1.297-.789.027-.216.325-.437.893-.663 3.498-1.524 5.83-2.529 6.998-3.014 3.332-1.386 4.025-1.627 4.476-1.635z" /></svg>`;

function load(key) {
  try {
    const raw = localStorage.getItem(key);
    return raw ? JSON.parse(raw) : null;
  } catch (_) {
    return null;
  }
}

function save(key, value) {
  try {
    localStorage.setItem(key, JSON.stringify(value));
  } catch (_) {
    // ignore
  }
}

function refreshBadges() {
  const wa = load(WA_KEY);
  const tg = load(TG_KEY);
  const waBadge = document.getElementById("waBadge");
  const tgBadge = document.getElementById("tgBadge");
  if (waBadge) {
    const on = Boolean(wa?.number);
    waBadge.textContent = on ? "Connected" : "Not connected";
    waBadge.classList.toggle("green", on);
    waBadge.classList.toggle("muted", !on);
  }
  if (tgBadge) {
    const on = Boolean(tg?.token);
    tgBadge.textContent = on ? "Connected" : "Not connected";
    tgBadge.classList.toggle("green", on);
    tgBadge.classList.toggle("muted", !on);
  }
}

function openWhatsAppModal() {
  const saved = load(WA_KEY) || {};
  const code = "X5-4821-WA";
  openModal(
    modalShell({
      icon: WA_ICON,
      title: "Connect WhatsApp",
      subtitle: saved.number ? "Connected · tap to update" : "Link a business number to your agent",
      body: `
        <div class="block">
          <div class="block-title">Connection code</div>
          <div class="code-box">
            <code>${code}</code>
            <button class="btn" style="height:34px;padding:0 14px;" id="waCopy" type="button">Copy</button>
          </div>
          <div class="field">
            <label for="waNumber">WhatsApp number</label>
            <input class="input" id="waNumber" type="tel" placeholder="+20 100 000 0000" value="${(saved.number || "").replace(/"/g, "")}" />
            <span class="hint">Use the number registered to your WhatsApp Business account.</span>
          </div>
        </div>
        <div class="block">
          <div class="block-title">How to link</div>
          <ol class="steps">
            <li><b>1</b><span>Open WhatsApp Business and go to <strong>Linked devices</strong>.</span></li>
            <li><b>2</b><span>Choose <strong>Link a device</strong> and enter the connection code above.</span></li>
            <li><b>3</b><span>Confirm the number, then finish the setup here.</span></li>
          </ol>
        </div>`,
      foot: `
        ${saved.number ? `<button class="btn btn-danger spread" id="waDisconnect" type="button">Disconnect</button>` : ""}
        <button class="btn" data-close type="button">Cancel</button>
        <button class="btn btn-primary" id="waConnect" type="button">Connect</button>`,
    })
  );

  document.getElementById("waCopy")?.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(code);
      toast("Code copied");
    } catch (_) {
      toast("Copy failed");
    }
  });
  document.getElementById("waConnect")?.addEventListener("click", () => {
    const input = document.getElementById("waNumber");
    const number = String(input?.value || "").trim().slice(0, 32);
    if (!number || number.replace(/\D/g, "").length < 7) {
      toast("Enter a valid number");
      return;
    }
    save(WA_KEY, { number });
    refreshBadges();
    toast("WhatsApp connected");
    document.querySelector("[data-close]")?.click();
  });
  document.getElementById("waDisconnect")?.addEventListener("click", () => {
    localStorage.removeItem(WA_KEY);
    refreshBadges();
    toast("WhatsApp disconnected");
    document.querySelector("[data-close]")?.click();
  });
}

function openTelegramModal() {
  const saved = load(TG_KEY) || {};
  openModal(
    modalShell({
      icon: TG_ICON,
      title: "Connect Telegram",
      subtitle: saved.token ? "Connected · tap to update" : "Link a bot to your agent",
      body: `
        <div class="block">
          <div class="block-title">Bot credentials</div>
          <div class="field">
            <label for="tgToken">Bot token</label>
            <input class="input" id="tgToken" type="password" placeholder="123456789:AAH-xxxx" value="" autocomplete="off" />
            <span class="hint">Issued by @BotFather. ${saved.token ? "A token is already saved — entering a new one replaces it." : ""}</span>
          </div>
          <div class="field" style="margin-bottom:0;">
            <label for="tgUser">Bot username (optional)</label>
            <input class="input" id="tgUser" type="text" placeholder="@my_agent_bot" value="${(saved.username || "").replace(/"/g, "")}" />
          </div>
        </div>
        <div class="block">
          <div class="block-title">How to link</div>
          <ol class="steps">
            <li><b>1</b><span>Open Telegram and start a chat with <strong>@BotFather</strong>.</span></li>
            <li><b>2</b><span>Send <strong>/newbot</strong> and follow the prompts to get a token.</span></li>
            <li><b>3</b><span>Paste the token above and connect your agent.</span></li>
          </ol>
        </div>`,
      foot: `
        ${saved.token ? `<button class="btn btn-danger spread" id="tgDisconnect" type="button">Disconnect</button>` : ""}
        <button class="btn" data-close type="button">Cancel</button>
        <button class="btn btn-primary" id="tgConnect" type="button">${saved.token ? "Save" : "Connect"}</button>`,
    })
  );

  document.getElementById("tgConnect")?.addEventListener("click", () => {
    const token = String(document.getElementById("tgToken")?.value || "").trim().slice(0, 128);
    const username = String(document.getElementById("tgUser")?.value || "").trim().slice(0, 64);
    if (!token && !saved.token) {
      toast("Enter the bot token");
      return;
    }
    if (token && !/^\d+:[\w-]{10,}$/.test(token)) {
      toast("Token looks invalid");
      return;
    }
    save(TG_KEY, { token: token || saved.token, username });
    refreshBadges();
    toast("Telegram connected");
    document.querySelector("[data-close]")?.click();
  });
  document.getElementById("tgDisconnect")?.addEventListener("click", () => {
    localStorage.removeItem(TG_KEY);
    refreshBadges();
    toast("Telegram disconnected");
    document.querySelector("[data-close]")?.click();
  });
}

export function initGateway() {
  refreshBadges();
  document.querySelectorAll("[data-gateway]").forEach((card) => {
    card.addEventListener("click", () => {
      if (card.dataset.gateway === "whatsapp") openWhatsAppModal();
      else openTelegramModal();
    });
  });
}
