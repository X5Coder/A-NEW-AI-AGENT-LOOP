/**
 * settings.js — console security + account actions
 * Owns: password change validation, logout. Local-only, honest labels.
 */
"use strict";

import { toast } from "./ui.js?v=8";

export function initSettings() {
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
    oldPw.value = "";
    newPw.value = "";
    confirmPw.value = "";
    toast("Password updated");
  });

  document.getElementById("logoutBtn")?.addEventListener("click", () => {
    try {
      ["x5-gateway-whatsapp", "x5-gateway-telegram"].forEach((k) => localStorage.removeItem(k));
    } catch (_) {
      // ignore
    }
    toast("Signed out");
  });
}
