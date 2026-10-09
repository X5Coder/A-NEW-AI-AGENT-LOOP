/**
 * charts.js — X5Agent consumption charts (tokens + requests + est. cost)
 * Owns: fetching /api/cost, range switching (7D / 30D / months),
 * professional workshop bars, tooltips. Real data only; honest empty state.
 * Silent refresh: unchanged data never touches the DOM.
 */
"use strict";

import { attachChartTooltip, animateIn, pinChartTip } from "./ui.js?v=9";
import { onPush } from "./events.js?v=1";

const MONTH_NAMES = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const RANGE_LABELS = { "7d": "Last 7 days", "30d": "Last 30 days", "months": "By month" };

function takeLast(arr, n) {
  const list = Array.isArray(arr) ? arr.slice(-n) : [];
  while (list.length < n) list.unshift(typeof list[0] === "string" ? "—" : 0);
  return list;
}

function sliceRange(data, range) {
  const counts = Array.isArray(data?.counts) ? data.counts : [];
  const labels = Array.isArray(data?.labels) ? data.labels : [];
  const daily = Array.isArray(data?.daily) ? data.daily : [];
  const rawTokens = Array.isArray(data?.tokens) ? data.tokens : null;
  const n = Math.max(counts.length, labels.length, daily.length, rawTokens ? rawTokens.length : 0);
  const pad = (arr) => {
    const l = arr.slice(-n);
    while (l.length < n) l.unshift(0);
    return l;
  };
  const lab = labels.slice(-n);
  while (lab.length < n) lab.unshift("—");
  const c = pad(counts.map((v) => Number(v) || 0));
  const d = pad(daily.map((v) => Number(v) || 0));
  // Measured tokens when present; otherwise derive nothing (no fabricated estimates).
  const t = rawTokens ? pad(rawTokens.map((v) => Number(v) || 0)) : c.map(() => 0);
  if (range === "30d") return { labels: lab, counts: c, costs: d, tokens: t };
  if (range === "months") {
    // Aggregate the available days into calendar months (only real data, never padded).
    const buckets = new Map();
    for (let i = 0; i < n; i++) {
      const m = /^(\d{1,2})\/\d{1,2}$/.exec(String(lab[i] || ""));
      if (!m) continue;
      const mi = Math.min(12, Math.max(1, parseInt(m[1], 10)));
      if (!buckets.has(mi)) buckets.set(mi, { c: 0, d: 0, t: 0 });
      buckets.get(mi).c += c[i];
      buckets.get(mi).d += d[i];
      buckets.get(mi).t += t[i];
    }
    const keys = [...buckets.keys()].sort((a, b) => a - b);
    return {
      labels: keys.map((k) => MONTH_NAMES[k - 1]),
      counts: keys.map((k) => buckets.get(k).c),
      costs: keys.map((k) => Math.round(buckets.get(k).d * 100) / 100),
      tokens: keys.map((k) => buckets.get(k).t),
    };
  }
  return { labels: takeLast(lab, 7), counts: takeLast(c, 7), costs: takeLast(d, 7), tokens: takeLast(t, 7) };
}

function formatCompact(n) {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(2)}M`;
  if (n >= 1000) return `${(n / 1000).toFixed(1)}K`;
  return String(Math.round(n));
}

function escHtml(s) {
  return String(s == null ? "" : s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

function smoothLine(pts) {
  if (pts.length < 2) return pts.length ? `M ${pts[0][0]} ${pts[0][1]}` : "";
  let d = `M ${pts[0][0]} ${pts[0][1]}`;
  for (let i = 0; i < pts.length - 1; i++) {
    const p0 = pts[Math.max(0, i - 1)];
    const p1 = pts[i];
    const p2 = pts[i + 1];
    const p3 = pts[Math.min(pts.length - 1, i + 2)];
    const c1x = p1[0] + (p2[0] - p0[0]) / 6;
    const c1y = p1[1] + (p2[1] - p0[1]) / 6;
    const c2x = p2[0] - (p3[0] - p1[0]) / 6;
    const c2y = p2[1] - (p3[1] - p1[1]) / 6;
    d += ` C ${c1x.toFixed(1)} ${c1y.toFixed(1)}, ${c2x.toFixed(1)} ${c2y.toFixed(1)}, ${p2[0].toFixed(1)} ${p2[1].toFixed(1)}`;
  }
  return d;
}

let flowUid = 0;

function renderChart(el, labels, values, unit, animate) {
  if (!el) return;
  const n = Math.max(labels.length, values.length);
  const vals = Array.from({ length: n }, (_, i) => Number(values[i]) || 0);
  const labs = Array.from({ length: n }, (_, i) => String(labels[i] ?? "—"));
  const total = vals.reduce((a, b) => a + b, 0);
  const hasData = total > 0;
  const uid = `f${++flowUid}`;

  if (!hasData) {
    el.innerHTML = `
      <div class="flow" data-anim>
        <svg class="flow-svg" viewBox="0 0 600 190" preserveAspectRatio="none" aria-hidden="true">
          <line x1="0" y1="47" x2="600" y2="47" class="flow-grid" />
          <line x1="0" y1="95" x2="600" y2="95" class="flow-grid" />
          <line x1="0" y1="142" x2="600" y2="142" class="flow-grid" />
          <line x1="0" y1="178" x2="600" y2="178" class="flow-grid" stroke-dasharray="3 6" />
        </svg>
        <div class="chart-empty">No activity in this range — send a message to the agent.</div>
      </div>`;
    if (animate) animateIn(el);
    return;
  }

  const W = 600;
  const H = 190;
  const padL = 10;
  const padR = 10;
  const padT = 14;
  const padB = 12;
  const max = Math.max(...vals, 1);
  const step = n > 1 ? (W - padL - padR) / (n - 1) : 0;
  const pts = vals.map((v, i) => {
    const x = n > 1 ? padL + i * step : W / 2;
    const y = padT + (1 - Math.max(0, v) / max) * (H - padT - padB);
    return [x, y];
  });
  const line = smoothLine(pts);
  const area = `${line} L ${pts[pts.length - 1][0].toFixed(1)} ${H - padB} L ${pts[0][0].toFixed(1)} ${H - padB} Z`;
  const peakIdx = vals.indexOf(max);
  const lastIdx = n - 1;

  // Thin X labels so phones never crowd: keep ≤6 readable ticks, spacing preserved.
  const maxTicks = n > 12 ? 5 : 7;
  const keep = new Set();
  if (n <= maxTicks) {
    for (let i = 0; i < n; i++) keep.add(i);
  } else {
    for (let k = 0; k < maxTicks; k++) keep.add(Math.round((k * (n - 1)) / (maxTicks - 1)));
  }

  el.innerHTML = `
    <div class="flow" data-anim>
      <svg class="flow-svg" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" role="img" aria-label="Usage flow chart">
        <defs>
          <linearGradient id="x5FlowFill-${uid}" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0" stop-color="var(--chart-fill-top)" stop-opacity="1" />
            <stop offset="1" stop-color="var(--chart-fill-bot)" stop-opacity="1" />
          </linearGradient>
          <linearGradient id="x5FlowStroke-${uid}" x1="0" y1="0" x2="1" y2="0">
            <stop offset="0" stop-color="var(--chart-line)" />
            <stop offset="1" stop-color="var(--chart-line-hi)" />
          </linearGradient>
        </defs>
        ${[0.25, 0.5, 0.75].map((f) => `<line x1="0" y1="${(padT + f * (H - padT - padB)).toFixed(1)}" x2="${W}" y2="${(padT + f * (H - padT - padB)).toFixed(1)}" class="flow-grid" />`).join("")}
        <path class="flow-area" d="${area}" fill="url(#x5FlowFill-${uid})" />
        <path class="flow-line" d="${line}" stroke="url(#x5FlowStroke-${uid})" />
        ${[peakIdx, lastIdx].filter((v, i, a) => a.indexOf(v) === i).map((i) => `<circle class="flow-dot${i === peakIdx ? " peak" : ""}" cx="${pts[i][0].toFixed(1)}" cy="${pts[i][1].toFixed(1)}" r="${i === peakIdx ? 5 : 4}" />`).join("")}
        ${pts.map((p, i) => `<rect class="flow-hit" x="${Math.max(0, p[0] - step / 2).toFixed(1)}" y="0" width="${(n > 1 ? step : W).toFixed(1)}" height="${H}" data-label="${escHtml(labs[i])}" data-value="${escHtml(`${formatCompact(vals[i])}${unit}`)}" tabindex="0" role="img" aria-label="${escHtml(`${labs[i]}: ${formatCompact(vals[i])}${unit}`)}" />`).join("")}
      </svg>
      <div class="flow-x">${labs.map((l, i) => `<span>${keep.has(i) ? escHtml(l) : ""}</span>`).join("")}</div>
    </div>`;

  // Fix gradient refs to the per-chart uid (keeps multi-chart pages collision-free).
  try {
    el.querySelector(".flow-area")?.setAttribute("fill", `url(#x5FlowFill-${uid})`);
    el.querySelector(".flow-line")?.setAttribute("stroke", `url(#x5FlowStroke-${uid})`);
  } catch (_) {}

  if (animate) animateIn(el);
  attachChartTooltip(el);
  // Touch screens have no hover: tapping a point pins the day info instead.
  try {
    if (window.matchMedia("(hover: none)").matches) {
      el.querySelectorAll(".flow-hit").forEach((hit) => {
        hit.addEventListener("click", () => pinChartTip(hit));
      });
    }
  } catch (_) {
    // matchMedia unavailable — hover tooltips still work
  }
}

function setBadge(id, text) {
  const el = document.getElementById(id);
  if (el) el.textContent = text;
}

function setRangeLabel(id, range) {
  const el = document.getElementById(id);
  if (el) el.textContent = RANGE_LABELS[range] || RANGE_LABELS["7d"];
}

export async function initCharts() {
  const tokensEl = document.getElementById("chartTokens");
  const reqEl = document.getElementById("chartRequests");
  if (!tokensEl || !reqEl) return;

  // Silent background refresh: skip DOM updates when data is unchanged
  // (no flicker, open tooltips stay), animate only on first paint,
  // and keep the last good chart if a tick fails instead of wiping it.
  const seen = { tokens: null, req: null };
  let firstPaint = true;
  let range = "7d";
  let cached = null;

  function sig(r, labels, values) {
    return r + "|" + labels.join("|") + "#" + values.join(",");
  }

  function paint(animate) {
    if (!cached) return;
    const { labels, counts, tokens } = sliceRange(cached, range);

    const sTok = sig(range, labels, tokens);
    const sReq = sig(range, labels, counts);
    if (animate || sTok !== seen.tokens) {
      seen.tokens = sTok;
      renderChart(tokensEl, labels, tokens, "", animate);
    }
    if (animate || sReq !== seen.req) {
      seen.req = sReq;
      renderChart(reqEl, labels, counts, "", animate);
    }
    firstPaint = false;

    const totalReq = counts.reduce((a, b) => a + b, 0);
    const totalTok = tokens.reduce((a, b) => a + b, 0);
    setBadge("tokensBadge", `${formatCompact(totalTok)} total`);
    setBadge("requestsBadge", `${formatCompact(totalReq)} total`);
    setRangeLabel("tokensRangeLabel", range);
    setRangeLabel("requestsRangeLabel", range);
  }

  function paintEmpty() {
    renderChart(tokensEl, [], [], "", true);
    renderChart(reqEl, [], [], "", true);
    setBadge("tokensBadge", "unavailable");
    setBadge("requestsBadge", "unavailable");
    firstPaint = false;
  }

  async function load() {
    let data = null;
    try {
      const res = await fetch("/api/cost", { cache: "no-store" });
      if (!res.ok) throw new Error("bad status");
      data = await res.json();
    } catch (_) {
      data = null;
    }
    if (!data) {
      // Transient failure: keep showing the last good chart (or empty on very first paint).
      if (firstPaint) paintEmpty();
      return;
    }
    cached = data;
    paint(firstPaint);
  }

  document.querySelectorAll("[data-chart-range]").forEach((box) => {
    box.addEventListener("click", (e) => {
      const btn = e.target.closest("button[data-range]");
      if (!btn || btn.dataset.range === range) return;
      range = btn.dataset.range;
      document.querySelectorAll("[data-chart-range] button").forEach((b) => {
        b.classList.toggle("active", b.dataset.range === range);
      });
      paint(true);
    });
  });

  await load();
  // Push replaces the old 30s poll: reload only when the server fans a new
  // dashboard snapshot (same freshness, O(1) total).
  onPush("dashboard", () => {
    load().catch(() => {});
  });
}