/**
 * live-url.js — remote-safe live viewer URLs (pure, no DOM).
 *
 * The library mints viewer links on loopback (the gated viewer port, 8790 by
 * default). Opened from the same
 * machine that is fine; opened from a remote console page those links point
 * at the VIEWER'S own localhost ("refused to connect"). When this console
 * page itself is remote, rewrite only the host/scheme to this page's host,
 * keeping the viewer's own port and every query param (token, scale,
 * view_only) exactly as the library produced them. Anything else passes
 * through untouched.
 */
"use strict";

export function remoteLiveUrl(viewerUrl, pageLocation) {
  const raw = String(viewerUrl || "");
  if (!raw) return raw;
  // Default to the current page location when the caller omits it (all
  // chat.js callers pass a single arg). Without this the rewrite never
  // runs remotely and iframes keep pointing at the viewer's own localhost.
  let loc = pageLocation || null;
  if (!loc) {
    try {
      if (typeof window !== "undefined" && window.location) loc = window.location;
      else if (typeof globalThis !== "undefined" && globalThis.location) loc = globalThis.location;
    } catch (_) {
      loc = null;
    }
  }
  const pageHost = String((loc && loc.hostname) || "");
  if (!pageHost || pageHost === "127.0.0.1" || pageHost === "localhost" || pageHost === "::1") {
    return raw; // local page: loopback viewer works as-is
  }
  let u;
  try {
    u = new URL(raw);
  } catch (_) {
    return raw;
  }
  const uh = String(u.hostname || "");
  if (uh !== "127.0.0.1" && uh !== "localhost" && uh !== "::1") return raw;
  // Point the viewer at this machine's address, keeping its own port.
  u.protocol = String((loc && loc.protocol) || "http:");
  u.hostname = pageHost;
  return u.toString();
}
