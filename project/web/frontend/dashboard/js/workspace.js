/**
 * workspace.js — Agent Workspace file browser
 * Owns: folder listing (one level), breadcrumb, upload, new folder, download.
 * Event-driven (push): reloads on workspace-version pushes + focus/visibility.
 */
"use strict";

import { toast, openModal, closeModal, modalShell } from "./ui.js?v=8";
import { onPush } from "./events.js?v=1";

let wsPath = "";
let wsVersion = 0;
let wsBusy = false;

function fmtSize(bytes) {
  const n = Number(bytes) || 0;
  if (n < 1024) return `${n} B`;
  const kb = n / 1024;
  if (kb < 1024) return `${Math.round(kb)} KB`;
  return `${(kb / 1024).toFixed(1)} MB`;
}

function fmtDate(ts) {
  try {
    const d = new Date(Number(ts) * 1000);
    if (Number.isNaN(d.getTime())) return "";
    return d.toLocaleDateString() + " " + d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  } catch (_) {
    return "";
  }
}

function fmtCount(entry) {
  if (entry.type !== "folder") return "";
  const n = Number(entry.items) || 0;
  const plus = entry.items_truncated ? "+" : "";
  if (n === 0) return "empty";
  if (n === 1) return "1 item";
  return `${n}${plus} items`;
}

function isImageName(name) {
  return /\.(png|jpe?g|gif|webp|bmp|svg|avif)$/i.test(String(name || ""));
}

function escName(s) {
  return String(s || "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

function joinPath(base, name) {
  return base ? `${base}/${name}` : name;
}

let lastSig = "";
let lastPath = "\0";

function sigFor(path, entries) {
  try {
    return path + "|" + entries.map((e) => `${e?.type}:${e?.name}:${e?.size ?? e?.items ?? ""}`).join(",");
  } catch (_) {
    return "";
  }
}

async function loadWorkspace() {
  const list = document.getElementById("wsList");
  const bar = document.getElementById("wsPathBar");
  if (!list) return;
  if (wsBusy) return;
  wsBusy = true;
  try {
    const res = await fetch("/api/workspace?path=" + encodeURIComponent(wsPath), { cache: "no-store" });
    if (!res.ok) throw new Error("load failed");
    const data = await res.json();
    const entries = Array.isArray(data?.entries) ? data.entries : [];
    wsPath = typeof data?.path === "string" ? data.path : wsPath;

    // Silent background refresh: skip re-render when nothing changed,
    // so polling never flickers in front of the user.
    const sig = sigFor(wsPath, entries);
    if (wsPath === lastPath && sig && sig === lastSig) return;
    lastPath = wsPath;
    lastSig = sig;

    if (bar) {
      // Full path from the workspace root inside the project.
      const fullPath = wsPath ? `X5Agent/Workspace/${wsPath}` : "X5Agent/Workspace";
      const parts = wsPath ? wsPath.split("/") : [];
      let html = `<span class="ws-path-ico" aria-hidden="true">${folderSvg()}</span>`;
      html += `<span class="ws-crumbs"><span class="ws-root">X5Agent</span><span class="ws-crumb-sep">/</span><button type="button" data-ws-nav="" class="ws-crumb-btn">Workspace</button>`;
      let acc = "";
      parts.forEach((p) => {
        acc = acc ? `${acc}/${p}` : p;
        html += `<span class="ws-crumb-sep">/</span><button type="button" data-ws-nav="${escName(acc)}" class="ws-crumb-btn">${escName(p)}</button>`;
      });
      html += `</span><button type="button" class="ws-copy-btn" id="wsCopyPath" title="Copy full path" aria-label="Copy full path">${copySvg()}</button>`;
      bar.innerHTML = html;
      bar.querySelectorAll("[data-ws-nav]").forEach((b) => {
        b.addEventListener("click", () => {
          wsPath = b.dataset.wsNav || "";
          loadWorkspace();
        });
      });
      // Long paths: keep the deepest segment in view.
      try {
        const crumbs = bar.querySelector(".ws-crumbs");
        if (crumbs) crumbs.scrollLeft = crumbs.scrollWidth;
      } catch (_) {}
      bar.querySelector("#wsCopyPath")?.addEventListener("click", async (e) => {
        const btn = e.currentTarget;
        try {
          if (navigator.clipboard && navigator.clipboard.writeText) {
            await navigator.clipboard.writeText(fullPath);
          } else {
            const ta = document.createElement("textarea");
            ta.value = fullPath;
            document.body.appendChild(ta);
            ta.select();
            document.execCommand("copy");
            ta.remove();
          }
          btn.classList.add("copied");
          setTimeout(() => btn.classList.remove("copied"), 1200);
          toast("Path copied");
        } catch (_) {
          toast("Copy failed");
        }
      });
    }

    list.innerHTML = "";
    list.classList.toggle("ws-grid", entries.length > 0);
    if (!entries.length) {
      const empty = document.createElement("div");
      empty.className = "ws-empty";
      empty.innerHTML = `
        <div class="ws-empty-word">Workspace is empty</div>
        <button type="button" class="glass-btn" id="wsEmptyUpload">Upload your first file</button>`;
      list.appendChild(empty);
      empty.querySelector("#wsEmptyUpload")?.addEventListener("click", () => {
        document.getElementById("wsFileInput")?.click();
      });
      return;
    }
    entries.forEach((e) => {
      const btn = document.createElement("button");
      btn.className = "ws-card";
      btn.type = "button";
      const isFolder = e?.type === "folder";
      const full = joinPath(wsPath, e?.name || "");
      const meta = isFolder ? fmtCount(e) : [fmtSize(e?.size), fmtDate(e?.mtime)].filter(Boolean).join(" · ");
      const thumb = isFolder
        ? `<span class="ws-thumb">${folderSvg()}</span>`
        : isImageName(e?.name)
          ? `<img class="ws-thumb-img" loading="lazy" alt="" src="/api/workspace/download?path=${encodeURIComponent(full)}" />`
          : `<span class="ws-thumb">${fileSvg()}</span>`;
      btn.innerHTML = `
        ${thumb}
        <span class="ws-info">
          <span class="ws-name"></span>
          <span class="ws-meta">${escName(meta)}</span>
        </span>`;
      btn.querySelector(".ws-name").textContent = e?.name || "";
      btn.title = isFolder ? `Open ${e?.name}` : `Download ${e?.name}`;
      btn.addEventListener("click", () => {
        if (isFolder) {
          wsPath = full;
          loadWorkspace();
        } else {
          const a = document.createElement("a");
          a.href = "/api/workspace/download?path=" + encodeURIComponent(full);
          a.download = e.name;
          document.body.appendChild(a);
          a.click();
          a.remove();
        }
      });
      list.appendChild(btn);
    });
  } catch (_) {
    // Invalidate the signature so the next successful poll always re-renders
    // (otherwise a stale error could stick when data is unchanged).
    lastSig = "";
    if (!list.children.length) {
      list.innerHTML = `<div class="grid-empty" data-ws-error><span>Could not load Workspace.</span><button type="button" class="ws-retry-btn" id="wsRetryBtn">Retry</button></div>`;
      document.getElementById("wsRetryBtn")?.addEventListener("click", () => {
        lastSig = "";
        loadWorkspace();
      });
    }
  } finally {
    wsBusy = false;
  }
}

function folderSvg() {
  return `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M20 20a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.9a2 2 0 0 1-1.69-.9L9.6 3.9A2 2 0 0 0 7.93 3H4a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2Z" /></svg>`;
}

function copySvg() {
  return `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect width="14" height="14" x="8" y="8" rx="2" /><path d="M4 16c-1.1 0-2-.9-2-2V4c0-1.1.9-2 2-2h10c1.1 0 2 .9 2 2" /></svg>`;
}

function fileSvg() {
  return `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7Z" /><path d="M14 2v4a2 2 0 0 0 2 2h4" /></svg>`;
}

function arrowSvg() {
  return `<svg class="row-arrow" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><polyline points="9 18 15 12 9 6" /></svg>`;
}

function downloadSvg() {
  return `<svg class="row-arrow" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" /><polyline points="7 10 12 15 17 10" /><line x1="12" y1="15" x2="12" y2="3" /></svg>`;
}

function readAsDataURL(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result || ""));
    reader.onerror = () => reject(new Error("read failed"));
    reader.readAsDataURL(file);
  });
}

async function uploadPicked(file) {
  if (!file || file.size === 0) {
    toast("Empty file");
    return;
  }
  if (file.size > 10 * 1024 * 1024) {
    toast("File too large (max 10MB)");
    return;
  }
  try {
    const data = await readAsDataURL(file);
    const res = await fetch("/api/workspace", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action: "upload", path: wsPath, name: file.name, data }),
    });
    const out = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(out?.error || "upload failed");
    toast("Uploaded to Workspace");
    loadWorkspace();
  } catch (err) {
    toast(err instanceof Error && err.message ? err.message : "Upload failed");
  }
}

function validFolderName(v) {
  const s = String(v || "").trim();
  if (!s) return "Name required";
  if (s.length > 100) return "Too long (max 100)";
  if (s.includes("/") || s.includes("\\") || s.includes("..")) return "Single name only, no paths";
  return "";
}

async function createFolder() {
  openModal(modalShell({
    icon: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" width="20" height="20"><path d="M20 20a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.9a2 2 0 0 1-1.69-.9L9.6 3.9A2 2 0 0 0 7.93 3H4a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2Z" /></svg>`,
    title: "New folder",
    subtitle: wsPath ? `Inside Workspace / ${wsPath}` : "Inside Workspace",
    body: `
      <div class="field">
        <label for="wsFolderName">Folder name</label>
        <div class="field-wrap">
          <input class="input" id="wsFolderName" maxlength="100" placeholder="e.g. project-live" autocomplete="off" />
        </div>
        <span class="hint" id="wsFolderErr" style="color: var(--red-hi); display: none;"></span>
      </div>`,
    foot: `
      <button class="glass-btn" data-close type="button">Cancel</button>
      <button class="glass-btn" id="wsFolderSave" type="button">Create</button>`,
  }));
  const input = document.getElementById("wsFolderName");
  const err = document.getElementById("wsFolderErr");
  const save = document.getElementById("wsFolderSave");
  const showErr = (msg) => {
    if (!err) return;
    if (msg) {
      err.textContent = msg;
      err.style.display = "";
    } else {
      err.textContent = "";
      err.style.display = "none";
    }
  };
  input?.addEventListener("input", () => {
    if (err && err.style.display !== "none" && !validFolderName(input.value)) showErr("");
  });
  const submit = async () => {
    const bad = validFolderName(input?.value);
    if (bad) {
      showErr(bad);
      return;
    }
    save.disabled = true;
    try {
      const res = await fetch("/api/workspace", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action: "mkdir", path: wsPath, name: String(input.value).trim() }),
      });
      const out = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(out?.error || "create failed");
      closeModal();
      toast("Folder created");
      loadWorkspace();
    } catch (e) {
      showErr(e instanceof Error && e.message ? e.message : "Create failed");
      save.disabled = false;
    }
  };
  save?.addEventListener("click", submit);
  input?.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      submit();
    }
  });
}

function workspaceVisible() {
  try {
    const page = document.querySelector('.page[data-page="workspace"]');
    return !!page && page.classList.contains("active");
  } catch (_) {
    return false;
  }
}

export function initWorkspace() {
  document.getElementById("wsUploadBtn")?.addEventListener("click", () => {
    document.getElementById("wsFileInput")?.click();
  });
  document.getElementById("wsMkdirBtn")?.addEventListener("click", createFolder);
  document.getElementById("wsFileInput")?.addEventListener("change", (e) => {
    const f = e.target && e.target.files && e.target.files[0];
    e.target.value = "";
    if (f) uploadPicked(f);
  });
  window.addEventListener("x5:page-open", (e) => {
    if (e && e.detail === "workspace") loadWorkspace();
  });
  // Push replaces the old 3s poll: reload only when the server reports a
  // new workspace version (agent-side writes included via its sweep).
  // Focus/visibility reloads stay (event-driven, not polling).
  onPush("workspace", (v) => {
    try {
      const next = v && typeof v.version === "number" ? v.version : 0;
      if (next && next !== wsVersion && workspaceVisible() && !document.hidden) {
        wsVersion = next;
        loadWorkspace();
      } else if (next) {
        wsVersion = Math.max(wsVersion, next);
      }
    } catch (_) {}
  });
  window.addEventListener("focus", () => {
    if (workspaceVisible()) loadWorkspace();
  });
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden && workspaceVisible()) loadWorkspace();
  });
  loadWorkspace();
}
