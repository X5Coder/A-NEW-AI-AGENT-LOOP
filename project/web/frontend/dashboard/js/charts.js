/**
 * charts.js — X5Agent consumption charts (tokens + requests + est. cost)
 * Owns: fetching /api/cost, range switching (7D / 30D / months),
 * professional workshop bars, tooltips. Real data only; honest empty state.
 * Silent refresh: unchanged data never touches the DOM.
 */
"use strict";

import { attachChartTooltip, animateIn, pinChartTip } from "./ui.js?v=8";
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

function renderChart(el, labels, values, unit, animate) {
  if (!el) return;
  const max = Math.max(...values, 1);
  const total = values.reduce((a, b) => a + b, 0);
  const hasData = total > 0;
  const peakIdx = hasData ? values.indexOf(max) : -1;

  el.innerHTML = `
    <div class="chart-track">
      ${values
        .map(
          (v, i) => `
        <div class="bar" data-label="${labels[i]}" data-value="${hasData ? `${formatCompact(v)}${unit}` : "no activity"}">
          <div class="bar-fill${i === peakIdx ? " peak" : ""}"${animate ? ` data-anim="bar" data-delay="${i * 60}"` : ""} style="--h:${hasData ? Math.max(4, Math.round((v / max) * 100)) : 2}%"></div>
        </div>`
        )
        .join("")}
    </div>
    <div class="chart-x">
      ${labels.map((l) => `<span>${l}</span>`).join("")}
    </div>
    ${hasData ? "" : `<div class="chart-empty">No activity in this range — send a message to the agent.</div>`}`;

  if (animate) animateIn(el);
  attachChartTooltip(el);
  // Touch screens have no hover: tapping a bar pins the day info instead.
  try {
    if (window.matchMedia("(hover: none)").matches) {
      el.querySelectorAll(".bar").forEach((bar) => {
        bar.addEventListener("click", () => pinChartTip(bar));
      });
    }
  } catch (_) {
    // matchMedia unavailable — hover tooltips still work
  }
}

function formatMoney(n) {
  const v = typeof n === "number" && isFinite(n) ? n : 0;
  return `$${v.toFixed(2)}`;
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
  const costEl = document.getElementById("chartCost");
  if (!tokensEl || !reqEl) return;

  // Silent background refresh: skip DOM updates when data is unchanged
  // (no flicker, open tooltips stay), animate only on first paint,
  // and keep the last good chart if a tick fails instead of wiping it.
  const seen = { tokens: null, req: null, cost: null };
  let firstPaint = true;
  let range = "7d";
  let cached = null;

  function sig(r, labels, values) {
    return r + "|" + labels.join("|") + "#" + values.join(",");
  }

  function paint(animate) {
    if (!cached) return;
    const { labels, counts, costs, tokens } = sliceRange(cached, range);

    const sTok = sig(range, labels, tokens);
    const sReq = sig(range, labels, counts);
    const sCost = sig(range, labels, costs);
    if (animate || sTok !== seen.tokens) {
      seen.tokens = sTok;
      renderChart(tokensEl, labels, tokens, "", animate);
    }
    if (animate || sReq !== seen.req) {
      seen.req = sReq;
      renderChart(reqEl, labels, counts, "", animate);
    }
    if (costEl && (animate || sCost !== seen.cost)) {
      seen.cost = sCost;
      renderChart(costEl, labels, costs.map((v) => Math.round(v * 100)), "¢", animate);
    }
    firstPaint = false;

    const totalReq = counts.reduce((a, b) => a + b, 0);
    const totalTok = tokens.reduce((a, b) => a + b, 0);
    const totalCost = costs.reduce((a, b) => a + b, 0);
    setBadge("tokensBadge", `${formatCompact(totalTok)} total`);
    setBadge("requestsBadge", `${formatCompact(totalReq)} total`);
    setBadge("costBadge", `${formatMoney(totalCost)} total`);
    setRangeLabel("tokensRangeLabel", range);
    setRangeLabel("requestsRangeLabel", range);
    setRangeLabel("costRangeLabel", range);
  }

  function paintEmpty() {
    renderChart(tokensEl, [], [], "", true);
    renderChart(reqEl, [], [], "", true);
    if (costEl) renderChart(costEl, [], [], "¢", true);
    setBadge("tokensBadge", "unavailable");
    setBadge("requestsBadge", "unavailable");
    setBadge("costBadge", "unavailable");
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
