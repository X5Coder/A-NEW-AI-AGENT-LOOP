/**
 * live-url.js — remote-safe live viewer URLs (pure, no DOM).
 *
 * The library mints viewer links on 127.0.0.1:6080. Opened from the same
 * machine that is fine; opened through the public funnel it points at the
 * VIEWER'S own localhost ("refused to connect"). When this console page
 * itself is remote, rewrite those links to the funnel /live/ route on this
 * page's own host (same hostname, same TLS), preserving every query param
 * (token, scale, view_only) and pointing the viewer websocket at the
 * routed path. Anything else passes through untouched.
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
  const proto = String((loc && loc.protocol) || "https:") === "http:" ? "http:" : "https:";
  const pagePort = String((loc && loc.port) || "");
  const host = pagePort ? `${pageHost}:${pagePort}` : pageHost;
  const file = String(u.pathname || "").split("/").pop() || "x5-live.html";
  const params = new URLSearchParams(u.search || "");
  if (!params.get("path")) params.set("path", "live/websockify");
  return `${proto}//${host}/live/${file}?${params.toString()}`;
}
