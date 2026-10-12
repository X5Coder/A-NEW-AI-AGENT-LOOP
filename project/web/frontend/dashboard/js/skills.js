/**
 * skills.js — user-authored skills (same home skills_search reads from)
 * Owns: fetching /api/skills, card grid (icon copies whole skill), view modal
 * (+ Copy full text, + Edit), + Add skill glass dialog.
 * Create/edit delegate to skill_create (same validation: name 50, content 10000,
 * description 500, no path traversal; same name overwrites = edit).
 * Invalid fields turn red in place until truly fixed (no toast for validation).
 * Create keeps a local draft across closes (cleared on save); edit loads the
 * full skill text and never touches the draft. One unified icon for every skill.
 * New skills are searchable immediately because skills_search embeds
 * descriptions on the fly — no extra step.
 */
"use strict";

import { openModal, closeModal, modalShell, toast } from "./ui.js?v=8";

/* Unified mark for every skill (same layers glyph as the sidebar entry). */
const SKILL_ICON = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 2 2 7l10 5 10-5-10-5Z" /><path d="m2 17 10 5 10-5" /><path d="m2 12 10 5 10-5" /></svg>`;

/* Trash glyph for the per-card delete button (inside the skill card only). */
const TRASH_ICON = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 6h18" /><path d="M8 6V4a1 1 0 0 1 1-1h6a1 1 0 0 1 1 1v2" /><path d="M19 6 18 20a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6" /><line x1="10" y1="11" x2="10" y2="17" /><line x1="14" y1="11" x2="14" y2="17" /></svg>`;

const MAX_NAME = 50;
const MAX_CONTENT = 10000;
const MAX_DESC = 500;
// Last writings in the create dialog (local-only, cleared after a real save).
const DRAFT_KEY = "x5_skill_draft";

function escapeHtml(s) {
  return String(s ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

function validateField(kind, value) {
  const v = String(value ?? "").trim();
  if (kind === "name") {
    if (!v) return "Name is required";
    if (v.length > MAX_NAME) return `Name too long (max ${MAX_NAME})`;
    if (v.includes("..") || v.includes("/") || v.includes("\\")) return "Name cannot contain .. / \\";
    if (!/^[\w\-\s]+$/u.test(v)) return "Name: letters, numbers, space, - _ only";
  } else if (kind === "content") {
    if (!v) return "Instructions are required";
    if (v.length > MAX_CONTENT) return `Instructions too long (max ${MAX_CONTENT})`;
  } else if (kind === "description") {
    if (v.length > MAX_DESC) return `Description too long (max ${MAX_DESC})`;
  }
  return null;
}

function validateInputs(name, content, description) {
  const values = { name, content, description };
  for (const kind of ["name", "content", "description"]) {
    const message = validateField(kind, values[kind]);
    if (message) return { field: kind, message };
  }
  return null;
}

async function fetchSkills() {
  const res = await fetch("/api/skills", { cache: "no-store" });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data?.error || `HTTP ${res.status}`);
  return Array.isArray(data?.skills) ? data.skills : [];
}

async function fetchSkillDetail(name) {
  const res = await fetch(`/api/skills?name=${encodeURIComponent(name)}`, { cache: "no-store" });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data?.error || `HTTP ${res.status}`);
  return data?.skill || null;
}

async function copySkillText(name, fallbackText) {
  // Always copies the COMPLETE text (list previews are cut) — same pattern as models.js.
  let text = String(fallbackText || "");
  try {
    const detail = await fetchSkillDetail(name);
    if (detail) text = detail.full_instructions || detail.instructions || text;
  } catch (_) {}
  if (!text.trim()) {
    toast("Nothing to copy");
    return;
  }
  try {
    await navigator.clipboard.writeText(text);
    toast("Copied");
  } catch (_) {
    toast("Copy failed");
  }
}

function openSkillModal(skill) {
  const body = (skill.preview || "").trim() || skill.description || "";
  openModal(modalShell({
    icon: SKILL_ICON,
    title: escapeHtml(skill.name),
    subtitle: escapeHtml(skill.source || "skill"),
    body: `
      <div class="block"><p class="modal-body-text">${escapeHtml(skill.description || "")}</p></div>
      <div class="block">
        <div class="block-title">Instructions</div>
        <div class="conn-detail" style="max-height:320px;">${escapeHtml(body.slice(0, 5000))}</div>
      </div>`,
    foot: `
      <button class="glass-btn" data-close type="button">Close</button>
      <button class="glass-btn" id="skillCopyBtn" type="button">Copy</button>
      <button class="glass-btn" id="skillEditBtn" type="button">Edit</button>`,
  }));
  // Copy takes the full text from the server; Edit loads it into the dialog.
  document.getElementById("skillCopyBtn")?.addEventListener("click", () => copySkillText(skill.name, body));
  // Edit always loads the COMPLETE text from the server (list previews are cut).
  document.getElementById("skillEditBtn")?.addEventListener("click", () => openEditDialog(skill.name));
}

async function openEditDialog(name) {
  let detail = null;
  try {
    detail = await fetchSkillDetail(name);
  } catch (err) {
    toast(err instanceof Error ? err.message : "Load failed");
    return;
  }
  if (!detail) {
    toast("Skill not found");
    return;
  }
  openCreateDialog(
    () => window.dispatchEvent(new CustomEvent("x5:skills-dirty")),
    {
      mode: "edit",
      initial: {
        name: detail.skill_name || name,
        description: detail.description || "",
        content: detail.full_instructions || detail.instructions || "",
      },
    },
  );
}

async function openSkillDetailByName(name) {
  try {
    const detail = await fetchSkillDetail(name);
    if (!detail) {
      toast("Skill not found");
      return;
    }
    openSkillModal({
      id: detail.skill_name || name,
      name: detail.skill_name || name,
      description: "",
      preview: detail.instructions || "",
      source: "skill",
      created_at: "",
    });
  } catch (err) {
    toast(err instanceof Error ? err.message : "Load failed");
  }
}

function openCreateDialog(onSaved, opts = {}) {
  const isEdit = opts.mode === "edit";
  const initial = (opts.initial && typeof opts.initial === "object") ? opts.initial : {};
  openModal(modalShell({
    icon: SKILL_ICON,
    title: isEdit ? "Edit skill" : "Add skill",
    subtitle: isEdit ? String(initial.name || "") : "Saved where skills_search looks",
    body: `
      <div class="field">
        <label for="skillName">Name *</label>
        <div class="field-wrap">
          <input class="input" id="skillName" maxlength="${MAX_NAME}" placeholder="e.g. weekly-report" autocomplete="off" />
        </div>
        <span class="hint">${isEdit ? "Name can't change when editing" : `Letters, numbers, space, - _ · max ${MAX_NAME}`}</span>
      </div>
      <div class="field">
        <label for="skillDesc">Description (optional)</label>
        <div class="field-wrap">
          <input class="input" id="skillDesc" maxlength="${MAX_DESC}" placeholder="One line so search can find it" autocomplete="off" />
        </div>
      </div>
      <div class="field">
        <label for="skillContent">Instructions * (Markdown)</label>
        <div class="field-wrap">
          <textarea class="input" id="skillContent" rows="8" maxlength="${MAX_CONTENT}"
            style="height:auto;min-height:160px;padding:10px 12px;resize:vertical;"
            placeholder="Step-by-step instructions your agent should follow…"></textarea>
        </div>
        <span class="hint" id="skillCount">0 / ${MAX_CONTENT}</span>
      </div>`,
    foot: `
      <button class="glass-btn" data-close type="button">Cancel</button>
      <button class="glass-btn" id="skillSaveBtn" type="button">Save skill</button>`,
  }));

  const nameEl = document.getElementById("skillName");
  const descEl = document.getElementById("skillDesc");
  const contentEl = document.getElementById("skillContent");
  const countEl = document.getElementById("skillCount");
  const saveBtn = document.getElementById("skillSaveBtn");

  const fieldEls = { name: nameEl, description: descEl, content: contentEl };
  function clearFieldErrors() {
    Object.values(fieldEls).forEach((el) => el?.classList.remove("field-error"));
  }
  // Draft: last writings survive closing the dialog (local-only).
  function saveDraft() {
    try {
      localStorage.setItem(DRAFT_KEY, JSON.stringify({
        name: String(nameEl?.value || "").slice(0, MAX_NAME),
        description: String(descEl?.value || "").slice(0, MAX_DESC),
        content: String(contentEl?.value || "").slice(0, MAX_CONTENT),
      }));
    } catch (_) {}
  }
  function clearDraft() {
    try {
      localStorage.removeItem(DRAFT_KEY);
    } catch (_) {}
  }
  // Restore last writings, if any (create mode only — edit loads the skill itself).
  if (!isEdit) {
    try {
      const raw = localStorage.getItem(DRAFT_KEY);
      const draft = raw ? JSON.parse(raw) : null;
      if (draft && typeof draft === "object") {
        if (nameEl && draft.name) nameEl.value = String(draft.name).slice(0, MAX_NAME);
        if (descEl && draft.description) descEl.value = String(draft.description).slice(0, MAX_DESC);
        if (contentEl && draft.content) contentEl.value = String(draft.content).slice(0, MAX_CONTENT);
      }
    } catch (_) {}
  } else {
    // Prefill from the skill (safe .value assignment, name locked to avoid orphans).
    if (nameEl) {
      nameEl.value = String(initial.name || "").slice(0, MAX_NAME);
      nameEl.disabled = true;
    }
    if (descEl) descEl.value = String(initial.description || "").slice(0, MAX_DESC);
    if (contentEl) contentEl.value = String(initial.content || "").slice(0, MAX_CONTENT);
  }
  // Red is persistent: it clears only when the field actually becomes valid.
  // Every keystroke also refreshes the saved draft (create mode only).
  Object.entries(fieldEls).forEach(([kind, el]) => {
    el?.addEventListener("input", () => {
      if (el.classList.contains("field-error") && !validateField(kind, el.value)) {
        el.classList.remove("field-error");
      }
      if (countEl && el === contentEl) countEl.textContent = `${el.value.length} / ${MAX_CONTENT}`;
      if (!isEdit) saveDraft();
    });
  });
  if (countEl && contentEl) countEl.textContent = `${contentEl.value.length} / ${MAX_CONTENT}`;

  saveBtn?.addEventListener("click", async () => {
    clearFieldErrors();
    if (!isEdit) saveDraft();
    const bad = validateInputs(nameEl?.value, contentEl?.value, descEl?.value);
    if (bad) {
      // In-place persistent red on the offending field instead of a toast.
      const el = fieldEls[bad.field];
      el?.classList.add("field-error");
      el?.focus();
      return;
    }
    saveBtn.disabled = true;
    const originalText = saveBtn.textContent;
    saveBtn.textContent = "Saving…";
    saveBtn.classList.add("loading");
    try {
      const res = await fetch("/api/skills", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name: nameEl.value.trim(),
          content: contentEl.value.trim(),
          description: descEl.value.trim(),
        }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data?.error || `HTTP ${res.status}`);
      if (!isEdit) clearDraft();
      closeModal();
      toast(isEdit ? "Skill updated" : "Skill saved");
      window.dispatchEvent(new CustomEvent("x5:skills-dirty"));
      if (typeof onSaved === "function") onSaved();
    } catch (e) {
      toast(e instanceof Error ? e.message : "Save failed");
    } finally {
      saveBtn.disabled = false;
      saveBtn.textContent = originalText;
      saveBtn.classList.remove("loading");
    }
  });
}

async function deleteSkill(name) {
  const res = await fetch("/api/skills/delete", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name }),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data?.error || `HTTP ${res.status}`);
  return data;
}

function openDeleteConfirm(name) {
  openModal(modalShell({
    icon: TRASH_ICON,
    title: "Delete skill?",
    subtitle: escapeHtml(name),
    body: `<div class="block"><p class="modal-body-text">This removes the skill permanently. This can't be undone.</p></div>`,
    foot: `
      <button class="glass-btn" data-close type="button">Cancel</button>
      <button class="glass-btn danger" id="skillDeleteBtn" type="button">Delete</button>`,
  }));
  document.getElementById("skillDeleteBtn")?.addEventListener("click", async (e) => {
    const btn = e.currentTarget;
    btn.disabled = true;
    btn.textContent = "Deleting…";
    try {
      await deleteSkill(name);
      closeModal();
      toast("Skill deleted");
      window.dispatchEvent(new CustomEvent("x5:skills-dirty"));
    } catch (err) {
      toast(err instanceof Error ? err.message : "Delete failed");
      btn.disabled = false;
      btn.textContent = "Delete";
    }
  });
}

function renderSkills(skills) {
  const grid = document.getElementById("skillsGrid");
  const label = document.getElementById("skillsCountLabel");
  if (!grid) return;
  if (label) {
    label.textContent = skills.length
      ? `${skills.length} available · searchable via skills_search`
      : "No skills yet";
  }
  grid.innerHTML = "";
  if (!skills.length) {
    grid.innerHTML = `<div class="grid-empty">No skills yet — press <b>+ Add skill</b> to create the first one.</div>`;
    return;
  }
  skills.forEach((s) => {
    const card = document.createElement("button");
    card.className = "mini-card";
    card.type = "button";
    card.setAttribute("data-anim", "");
    card.innerHTML = `
      <div class="mini-top">
        <div class="mini-icon skill-copy" title="Copy skill" data-copy>${SKILL_ICON}</div>
        <div class="mini-actions">
          <button class="skill-del" title="Delete skill" aria-label="Delete skill" data-del type="button">${TRASH_ICON}</button>
          <svg class="mini-arrow" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round">
            <polyline points="9 18 15 12 9 6" />
          </svg>
        </div>
      </div>
      <div class="mini-title">${escapeHtml(s.name)}</div>
      <div class="mini-desc">${escapeHtml(s.description || "")}</div>
      <div class="mini-foot">
        <span class="mini-ver">${escapeHtml(s.source || "")}</span>
      </div>`;
    card.addEventListener("click", () => {
      // Full instructions come from skills_view via the server (path-validated).
      if (s.preview) openSkillModal(s);
      else openSkillDetailByName(s.name);
    });
    // The card icon copies the whole skill without opening it.
    card.querySelector("[data-copy]")?.addEventListener("click", (e) => {
      e.stopPropagation();
      e.preventDefault();
      copySkillText(s.name, s.preview);
    });
    // Delete lives inside the card only: confirm first, never opens the skill.
    card.querySelector("[data-del]")?.addEventListener("click", (e) => {
      e.stopPropagation();
      e.preventDefault();
      openDeleteConfirm(s.name);
    });
    grid.appendChild(card);
  });
}

let lastFetch = 0;
let dirty = true;

export async function initSkills() {
  const grid = document.getElementById("skillsGrid");
  if (!grid) return;

  async function refresh() {
    try {
      renderSkills(await fetchSkills());
      lastFetch = Date.now();
      dirty = false;
    } catch (_) {
      grid.innerHTML = `<div class="grid-empty">Skills unavailable — check the server is running.</div>`;
    }
  }

  document.getElementById("addSkillBtn")?.addEventListener("click", () => openCreateDialog(refresh));

  window.addEventListener("x5:skills-dirty", () => {
    dirty = true;
    refresh();
  });
  window.addEventListener("x5:page-open", (e) => {
    if (e.detail === "skills" && (dirty || Date.now() - lastFetch > 60000)) refresh();
  });
  // Chat blue-token click: jump here and open that skill's detail.
  window.addEventListener("x5:reveal-skill", (e) => {
    try {
      const name = e && e.detail && typeof e.detail.name === "string" ? e.detail.name.slice(0, 100) : "";
      if (name.trim()) openSkillDetailByName(name);
    } catch (_) {}
  });
  await refresh();
}