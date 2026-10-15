/**
 * boot_cache.js — persistent hash-cache for the boot snapshot.
 *
 * What: stores each hello section as {hash, data} in localStorage.
 * Why: on refresh the shell paints cached data instantly, so the boot
 *   splash lifts without waiting for the network; a section re-renders
 *   only when its server hash breaks (data actually changed).
 * Owner: events.js restores + persists; modules render via onPush replay.
 * Limits: one key, best-effort (quota errors drop the largest section
 *   first, never throw). No secrets stored (snapshot holds no tokens).
 */
"use strict";

const KEY = "x5_boot_cache_v1";
const MAX_RAW = 1_500_000; // ~1.5MB: refuse oversized snapshots, keep shell fast

export function loadBootCache() {
  // Returns {hashes: {section: hash}, states: {section: data}} or null.
  try {
    const raw = localStorage.getItem(KEY);
    if (!raw || raw.length < 4 || raw.length > MAX_RAW) return null;
    const parsed = JSON.parse(raw);
    if (!parsed || typeof parsed !== "object") return null;
    const hashes = parsed.hashes;
    const states = parsed.states;
    if (!hashes || typeof hashes !== "object" || !states || typeof states !== "object") return null;
    return { hashes, states };
  } catch (_) {
    return null;
  }
}

export function saveBootCache(hashes, states) {
  // Persists the current maps. Never throws; shrinks on quota pressure.
  try {
    if (!hashes || !states) return;
    const payload = { v: 1, savedAt: Date.now(), hashes, states };
    try {
      localStorage.setItem(KEY, JSON.stringify(payload));
      return;
    } catch (_) {
      // Quota hit: retry without the largest section (usually chat_last50).
      try {
        const slimStates = { ...states };
        const slimHashes = { ...hashes };
        let biggest = "";
        let biggestLen = 0;
        for (const k of Object.keys(slimStates)) {
          let len = 0;
          try {
            len = JSON.stringify(slimStates[k]).length;
          } catch (_) {
            len = 0;
          }
          if (len > biggestLen) {
            biggestLen = len;
            biggest = k;
          }
        }
        if (biggest) {
          delete slimStates[biggest];
          delete slimHashes[biggest];
        }
        localStorage.setItem(KEY, JSON.stringify({ v: 1, savedAt: Date.now(), hashes: slimHashes, states: slimStates }));
      } catch (_) {}
    }
  } catch (_) {}
}

export function clearBootCache() {
  try {
    localStorage.removeItem(KEY);
  } catch (_) {}
}
