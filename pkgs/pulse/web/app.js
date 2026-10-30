// Pulse's page: renders /api/snapshot every minute. Everything from the
// server goes into the page as text nodes (h() below), never as HTML.
"use strict";

const $ = (id) => document.getElementById(id);
let snap = null;
let sparks = [];

// Elements

function h(tag, attrs, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") el.className = v;
    else if (k === "style") Object.assign(el.style, v);
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else el.setAttribute(k, v === true ? "" : v);
  }
  add(el, kids);
  return el;
}

function add(el, kids) {
  for (const kid of kids.flat(Infinity)) {
    if (kid === null || kid === undefined || kid === false) continue;
    el.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  }
  return el;
}

const SVG = "http://www.w3.org/2000/svg";
function s(tag, attrs, ...kids) {
  const el = document.createElementNS(SVG, tag);
  for (const [k, v] of Object.entries(attrs || {})) el.setAttribute(k, v);
  for (const kid of kids) if (kid) el.append(kid);
  return el;
}

const ICONS = {
  ok: ["M5 8.3l2.1 2.1L11 6", "OK"],
  warn: ["M8 4.6v4.2M8 11.3v.1", "Warning"],
  bad: ["M5.6 5.6l4.8 4.8M10.4 5.6l-4.8 4.8", "Problem"],
  info: ["M8 7.4v4.1M8 4.8v.1", "Note"],
  idle: ["", "Off"],
};

function icon(level, label) {
  const [d, name] = ICONS[level] || ICONS.info;
  const el = s("svg", { class: `st ${level}`, viewBox: "0 0 16 16",
                        role: "img", "aria-label": label || name },
               s("circle", { cx: 8, cy: 8, r: 7.25 }),
               d ? s("path", { d }) : null);
  el.append(s("title", {}, document.createTextNode(label || name)));
  return el;
}

function card(id, title, sub, ...body) {
  const el = $(id);
  el.replaceChildren(h("header", {}, h("h2", {}, title),
                       sub ? h("div", { class: "sub" }, sub) : null),
                     ...body.flat(Infinity).filter(Boolean));
  return el;
}

function rows(items) {
  return h("div", { class: "rows" }, items);
}

function row(level, main, right, label) {
  return h("div", { class: "row" }, level ? icon(level, label) : null,
           h("div", { class: "grow" }, main),
           right ? h("div", { class: "right" }, right) : null);
}

function link(text, url) {
  return url ? h("a", { href: url, target: "_blank", rel: "noopener" }, text)
             : text;
}

const empty = (text) => h("p", { class: "empty" }, text);

// Formatting

function when(v) {
  if (v === null || v === undefined) return null;
  return typeof v === "number" ? new Date(v * 1000) : new Date(v);
}

function span(seconds) {
  const s = Math.abs(seconds);
  if (s < 90) return `${Math.round(s)} s`;
  if (s < 5400) return `${Math.round(s / 60)} min`;
  if (s < 36 * 3600) return `${Math.round(s / 3600)} h`;
  return `${Math.round(s / 86400)} days`;
}

function ago(v) {
  const d = when(v);
  if (!d) return "never";
  const s = (Date.now() - d) / 1000;
  return s < 0 ? `in ${span(s)}` : s < 45 ? "just now" : `${span(s)} ago`;
}

const clockFmt = new Intl.DateTimeFormat("en-GB", { hour: "2-digit", minute: "2-digit" });
const dayFmt = new Intl.DateTimeFormat("en-GB", { weekday: "short", day: "numeric", month: "short" });
const longDate = new Intl.DateTimeFormat("en-GB", { weekday: "long", day: "numeric", month: "long", year: "numeric" });
const clock = (v) => clockFmt.format(when(v));

function dayKey(d) {
  return `${d.getFullYear()}-${d.getMonth()}-${d.getDate()}`;
}

function dayLabel(v) {
  const d = when(v);
  const today = new Date();
  const tomorrow = new Date(today.getFullYear(), today.getMonth(), today.getDate() + 1);
  const yesterday = new Date(today.getFullYear(), today.getMonth(), today.getDate() - 1);
  if (dayKey(d) === dayKey(today)) return "Today";
  if (dayKey(d) === dayKey(tomorrow)) return "Tomorrow";
  if (dayKey(d) === dayKey(yesterday)) return "Yesterday";
  return dayFmt.format(d);
}

function bytes(n) {
  if (n === null || n === undefined) return "–";
  const a = Math.abs(n);
  if (a < 1e3) return `${Math.round(n)} B`;
  if (a < 1e6) return `${(n / 1e3).toFixed(1)} kB`;
  if (a < 1e9) return `${(n / 1e6).toFixed(1)} MB`;
  if (a < 1e12) return `${(n / 1e9).toFixed(a < 1e10 ? 2 : 1)} GB`;
  return `${(n / 1e12).toFixed(2)} TB`;
}

const pct = (x) => x === null || x === undefined ? "–" : `${Math.round(x * 100)}%`;
const plural = (n, w, ws) => `${n} ${n === 1 ? w : ws || w + "s"}`;
function count(n) {
  if (n >= 1e9) return `${(n / 1e9).toFixed(1)} G`;
  if (n >= 1e6) return `${(n / 1e6).toFixed(1)} M`;
  if (n >= 1e5) return `${Math.round(n / 1e3)} k`;
  return new Intl.NumberFormat("en-GB").format(n);
}

function section(name) {
  return (snap && snap.sections[name]) || { pending: true };
}

// A section's state for a card's subtitle
function freshness(sec, prefix) {
  if (sec.error) return h("span", { title: sec.error }, "couldn't read it: " + sec.error.slice(0, 80));
  if (sec.pending && !sec.updated) return "loading…";
  if (!sec.updated) return prefix ? prefix + "not yet" : null;
  return (prefix || "") + ago(sec.updated);
}

// Markdown, for the brief and the answers: escaped text only, with bold,
// italics, code, links, lists and small headings
function inline(text) {
  const out = [];
  const re = /(\*\*([^*]+)\*\*|`([^`]+)`|\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)|(?<![\w*])\*([^*\s][^*]*)\*(?!\w)|(?<!\w)_([^_]+)_(?!\w))/g;
  let last = 0;
  for (const m of text.matchAll(re)) {
    if (m.index > last) out.push(text.slice(last, m.index));
    if (m[2]) out.push(h("strong", {}, m[2]));
    else if (m[3]) out.push(h("code", {}, m[3]));
    else if (m[4]) out.push(link(m[4], m[5]));
    else out.push(h("em", {}, m[6] || m[7]));
    last = m.index + m[0].length;
  }
  out.push(text.slice(last));
  return out;
}

function markdown(text) {
  const blocks = [];
  let list = null, para = [];
  const flush = () => {
    if (para.length) blocks.push(h("p", {}, inline(para.join(" "))));
    para = [];
  };
  for (const raw of text.split("\n")) {
    const line = raw.trimEnd();
    const item = line.match(/^\s*(?:[-*•]|(\d+)[.)])\s+(.*)$/);
    const head = line.match(/^(#{1,6})\s+(.*)$/);
    if (item) {
      flush();
      const kind = item[1] ? "ol" : "ul";
      if (!list || list.tagName.toLowerCase() !== kind) {
        list = h(kind);
        blocks.push(list);
      }
      list.append(h("li", {}, inline(item[2])));
      continue;
    }
    if (head) {
      flush();
      list = null;
      blocks.push(h(head[1].length <= 3 ? "h3" : "h4", {}, inline(head[2])));
    } else if (!line.trim()) {
      flush();
      list = null;
    } else if (list && /^\s{2,}/.test(raw)) {
      list.lastChild.append(" ", ...inline(line.trim()));
    } else {
      list = null;
      para.push(line.trim());
    }
  }
  flush();
  return blocks;
}

// Sparklines: one series, 2 px line, a 10% wash, the latest value marked,
// and a crosshair with the value under the pointer

function spark(points, { format, min, max, label }) {
  const el = h("div", { class: "spark", tabindex: 0 });
  if (!points || points.length < 2) {
    el.append(h("div", { class: "small muted", style: { paddingTop: "14px" } }, "no history yet"));
    return el;
  }
  const values = points.map((p) => p[1]);
  const lo = min !== undefined ? min : Math.min(...values);
  let hi = max !== undefined ? max : Math.max(...values);
  if (hi <= lo) hi = lo + 1;
  el.setAttribute("aria-label", `${label} over 24 hours: low ${format(Math.min(...values))}, high ${format(Math.max(...values))}, now ${format(values[values.length - 1])}`);
  sparks.push(() => draw(el, points, lo, hi, format));
  return el;
}

function draw(el, points, lo, hi, format) {
  const w = el.clientWidth || 200, ht = el.clientHeight || 44, pad = 4;
  const t0 = points[0][0], t1 = points[points.length - 1][0];
  const x = (t) => ((t - t0) / (t1 - t0 || 1)) * w;
  const y = (v) => ht - pad - ((v - lo) / (hi - lo)) * (ht - pad * 2);
  const path = points.map((p, i) => `${i ? "L" : "M"}${x(p[0]).toFixed(1)},${y(p[1]).toFixed(1)}`).join("");
  const last = points[points.length - 1];
  const svg = s("svg", { viewBox: `0 0 ${w} ${ht}`, "aria-hidden": "true" },
    s("line", { class: "base", x1: 0, x2: w, y1: ht - 0.5, y2: ht - 0.5 }),
    s("path", { class: "area", d: `${path}L${x(last[0])},${ht}L0,${ht}Z` }),
    s("path", { class: "line", d: path }),
    s("circle", { class: "end", cx: x(last[0]), cy: y(last[1]), r: 4 }));
  const cross = s("line", { class: "cross", y1: 0, y2: ht, visibility: "hidden" });
  const dot = s("circle", { class: "end", r: 4, visibility: "hidden" });
  svg.append(cross, dot);
  const tip = h("div", { class: "tip", hidden: true });
  el.replaceChildren(svg, tip);
  const show = (i) => {
    const p = points[i];
    cross.setAttribute("x1", x(p[0]));
    cross.setAttribute("x2", x(p[0]));
    dot.setAttribute("cx", x(p[0]));
    dot.setAttribute("cy", y(p[1]));
    cross.setAttribute("visibility", "visible");
    dot.setAttribute("visibility", "visible");
    tip.replaceChildren(h("b", {}, format(p[1])), h("span", {}, clock(p[0])));
    tip.hidden = false;
    tip.style.left = `${Math.min(Math.max(x(p[0]), 40), w - 40)}px`;
    tip.style.top = `${y(p[1]) - 8}px`;
  };
  const hide = () => {
    cross.setAttribute("visibility", "hidden");
    dot.setAttribute("visibility", "hidden");
    tip.hidden = true;
  };
  el.onpointermove = (e) => {
    const r = el.getBoundingClientRect();
    const t = t0 + ((e.clientX - r.left) / r.width) * (t1 - t0);
    let best = 0;
    points.forEach((p, i) => { if (Math.abs(p[0] - t) < Math.abs(points[best][0] - t)) best = i; });
    show(best);
  };
  el.onpointerleave = hide;
  el.onfocus = () => show(points.length - 1);
  el.onblur = hide;
}

// The page

function header() {
  const now = new Date();
  const hour = now.getHours();
  const part = hour < 5 ? "Good night" : hour < 12 ? "Good morning" : hour < 19 ? "Good afternoon" : "Good evening";
  $("greeting").textContent = snap.name ? `${part}, ${snap.name}` : part;
  $("date").textContent = longDate.format(now);
  const bad = snap.attention.filter((i) => i.level === "bad").length;
  const warn = snap.attention.filter((i) => i.level === "warn").length;
  const text = snap.status === "ok" ? "All good"
    : [bad && plural(bad, "problem"), warn && plural(warn, "warning")].filter(Boolean).join(" · ");
  $("status").replaceChildren(icon(snap.status), text);
  document.title = snap.status === "ok" ? "Pulse" : `(${bad + warn}) Pulse`;
}

function brief() {
  const b = snap.brief;
  const meta = $("brief-meta");
  meta.replaceChildren();
  if (b) {
    $("brief-text").replaceChildren(...markdown(b.text));
    meta.append(`${clock(b.created)} · ${b.source}${b.model ? " · " + b.model : ""}`);
  } else {
    const how = snap.ai.server
      ? "aurea writes it after 06:00."
      : "Your laptop writes it with Claude Code the first time its agent runs after 06:00 (pulse-agent --brief writes one now).";
    $("brief-text").replaceChildren(empty("No brief yet today. " + how));
  }
  if (snap.ai.server) {
    meta.append(h("button", { onclick: rewrite, title: "Write today's brief again from the current data" }, b ? "Rewrite" : "Write it now"));
  }
  const ask = !!snap.ai.server;
  $("question").disabled = !ask;
  $("ask-send").disabled = !ask;
  $("ask-note").textContent = ask ? ""
    : "Questions need a Claude API key on aurea (the \"anthropic\" credential in profiles/pulse.nix).";
}

async function rewrite(e) {
  const button = e.target;
  button.disabled = true;
  button.textContent = "Writing…";
  try {
    const reply = await fetch("/api/brief/generate", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
    if (!reply.ok) throw new Error((await reply.json()).error || reply.statusText);
    await load();
  } catch (err) {
    button.disabled = false;
    button.textContent = "Rewrite";
    $("brief-meta").append(h("span", { class: "small", style: { color: "var(--critical)" } }, String(err.message || err)));
  }
}

function attentionCard() {
  const items = snap.attention;
  const body = items.length
    ? h("ul", { class: "items" }, items.map((i) => h("li", {},
        icon(i.level),
        h("div", { class: "what" },
          h("div", { class: "title" }, link(i.title, i.url)),
          i.detail ? h("div", { class: "detail" }, i.detail) : null,
          h("div", { class: "area" }, i.area)))))
    : h("div", { class: "all-clear" }, icon("ok"), "Nothing needs you right now.");
  card("attention", "Needs attention", items.length ? plural(items.length, "item") : null, body);
}

function todayCard() {
  const sec = section("calendar");
  const data = sec.data;
  if (!data) {
    card("today", "Calendar", freshness(sec), empty(snap.sections.calendar ? "Waiting for the calendar…" : "No calendar configured."));
    return;
  }
  const keys = {};
  const now = Date.now();
  const byDay = new Map();
  for (const e of data.events) {
    const k = dayLabel(e.start);
    if (!byDay.has(k)) byDay.set(k, []);
    byDay.get(k).push(e);
  }
  const body = [];
  if (!byDay.has("Today")) body.push(h("h3", {}, "Today"), empty("Nothing on the calendar."));
  let shown = 0;
  for (const [label, events] of byDay) {
    if (shown > 14) break;
    body.push(h("h3", {}, label));
    for (const e of events) {
      shown++;
      if (!(e.calendar in keys)) keys[e.calendar] = { n: Object.keys(keys).length % 4 + 1, label: e.label };
      const start = when(e.start), end = when(e.end);
      const cls = ["event", end < now ? "past" : start <= now ? "now" : ""].join(" ");
      body.push(h("div", { class: cls },
        h("div", { class: "when num" }, e.all_day ? "all day" : `${clock(start)}–${clock(end)}`),
        h("i", { class: `key c${keys[e.calendar].n}`, "aria-hidden": "true" }),
        h("div", {},
          h("div", { class: "title" }, e.title, e.kind === "evaluation" ? h("span", { class: "badge" }, "evaluation") : null),
          h("div", { class: "where" }, [e.label, e.location].filter(Boolean).join(" · ")))));
    }
  }
  if (Object.keys(keys).length > 1) {
    body.push(h("div", { class: "legend" }, Object.values(keys).map((k) =>
      h("span", {}, h("i", { style: { background: `var(--cal-${k.n})` } }), k.label))));
  }
  card("today", "Calendar", freshness(sec), body);
}

function ciLevel(state) {
  return { SUCCESS: "ok", FAILURE: "bad", ERROR: "bad", PENDING: "warn", EXPECTED: "warn" }[state] || "idle";
}

function projectsCard() {
  const sec = section("github");
  const local = section("repos").data || [];
  const byRemote = {};
  for (const r of local) if (r.remote) byRemote[r.remote.toLowerCase()] = r;
  const g = sec.data;
  if (!g) {
    card("projects", "Projects", freshness(sec), empty(sec.via === "laptop"
      ? "GitHub comes from the laptop's agent; it hasn't reported yet."
      : "Waiting for GitHub…"));
    return;
  }
  const repos = g.repos.slice().sort((a, b) => (b.pushed || "").localeCompare(a.pushed || "")).slice(0, 9);
  const left = repos.map((r) => {
    const mine = byRemote[r.name.toLowerCase()];
    const bots = r.prs.filter((p) => p.bot).length;
    const facts = [
      r.language,
      r.open_prs ? h("span", {}, h("b", {}, plural(r.open_prs, "PR")), bots ? ` (${bots} from bots)` : "") : null,
      r.issues ? plural(r.issues, "issue") : null,
      r.release ? `release ${r.release}` : null,
      mine ? h("span", { title: mine.path }, "local: ", h("b", {}, mine.branch || "detached"),
        mine.changed ? ` · ${mine.changed} changed` : "", mine.ahead ? ` · ${mine.ahead} unpushed` : "",
        mine.behind ? ` · ${mine.behind} behind` : "") : null,
    ].filter(Boolean);
    const milestones = r.milestones.map((m) => h("div", { class: "row", style: { borderTop: 0, padding: "2px 0" } },
      h("div", { class: "grow small" }, link(m.title, m.url), h("span", { class: "muted" }, ` · ${m.closed}/${m.open + m.closed}${m.due ? " · due " + dayLabel(m.due) : ""}`)),
      h("div", { class: "meter", title: `${Math.round(m.progress || 0)}% done` }, h("i", { style: { width: `${m.progress || 0}%` } }))));
    return h("div", { class: "repo" },
      icon(ciLevel(r.ci), r.ci ? `CI on ${r.branch}: ${r.ci.toLowerCase()}` : "No CI"),
      h("div", { class: "name" }, link(r.name.replace(/^JoaoTRoxo\//, ""), r.url), r.private ? h("span", { class: "badge" }, "private") : null),
      h("div", { class: "right muted small" }, "pushed ", ago(r.pushed)),
      h("div", { class: "facts" }, facts.map((f) => h("span", {}, f))),
      milestones.length ? h("div", { style: { gridColumn: "2 / -1" } }, milestones) : null);
  });
  const pr = (p) => row(ciLevel(p.ci), [link(p.title, p.url), h("span", { class: "muted small" }, ` ${p.repo.split("/")[1]}#${p.number}`)], ago(p.updated), p.ci ? `checks: ${p.ci.toLowerCase()}` : "no checks");
  const right = [h("h3", {}, "Your pull requests"), g.my_prs.length ? rows(g.my_prs.map(pr)) : empty("None open.")];
  if (g.review_requests.length) right.push(h("h3", {}, "Waiting for your review"), rows(g.review_requests.map(pr)));
  if (g.assigned.length) {
    right.push(h("h3", {}, "Assigned to you"), rows(g.assigned.map((i) =>
      row(null, [link(i.title, i.url), h("span", { class: "muted small" }, ` ${i.repo.split("/")[1]}#${i.number}`)], ago(i.updated)))));
  }
  const n = g.notifications;
  if (n) {
    right.push(h("h3", {}, link(`Notifications · ${n.unread} unread`, "https://github.com/notifications")));
    right.push(n.latest.length ? rows(n.latest.slice(0, 5).map((x) =>
      row(/fail/i.test(x.title) ? "bad" : "info", [x.title, h("span", { class: "muted small" }, " " + x.repo.split("/")[1])], ago(x.updated)))) : empty("All read."));
  }
  card("projects", "Projects", freshness(sec, sec.via === "laptop" ? "GitHub via the laptop, " : "GitHub, "),
       h("div", { class: "cols" }, h("div", {}, left), h("div", {}, right)));
}

function tile(label, value, note, chart, extra) {
  return h("article", { class: "card tile" },
    h("div", { class: "label" }, label, extra || null),
    h("div", { class: "value" }, value),
    h("div", { class: "note" }, note || ""),
    chart || null);
}

function tilesCard() {
  const sec = section("machine");
  const m = sec.data;
  const el = $("tiles");
  if (!m) {
    el.replaceChildren(h("article", { class: "card tile", style: { gridColumn: "1 / -1" } },
      h("div", { class: "label" }, "aurea"), h("div", { class: "note" }, freshness(sec) || "waiting…")));
    return;
  }
  const smartTemps = (m.smart || []).filter((d) => d.temperature).map((d) => `${d.name} ${Math.round(d.temperature)} °C`);
  const net = m.network || {};
  const tiles = [
    tile("CPU", pct(m.cpu.now), `load ${m.load[0]?.toFixed(2)} · up ${span(m.uptime)}`,
         spark(m.cpu.series, { format: pct, min: 0, label: "CPU" })),
    tile("Memory", pct(m.memory.now), `of ${bytes(m.memory.total)}${m.memory.swap > 0.01 ? " · swap " + pct(m.memory.swap) : ""}`,
         spark(m.memory.series, { format: pct, min: 0, max: 1, label: "Memory" })),
    tile("CPU temperature", m.temperature.now !== null ? `${Math.round(m.temperature.now)} °C` : "–", smartTemps.length ? "disks " + smartTemps.join(", ") : "",
         spark(m.temperature.series, { format: (v) => `${Math.round(v)} °C`, label: "CPU temperature" })),
    tile("Network in", `${bytes(net.rx)}/s`, `${bytes(net.tx)}/s out`,
         spark(net.series, { format: (v) => `${bytes(v)}/s`, min: 0, label: "Network in" })),
  ];
  for (const d of m.disks) {
    if (!d.mounted) {
      tiles.push(tile(d.name, "–", "not mounted", null, icon("bad", "Not mounted")));
      continue;
    }
    const grew = d.grew ? ` · ${d.grew > 0 ? "+" : "−"}${bytes(Math.abs(d.grew))} in 24 h` : "";
    tiles.push(tile(d.name, pct(d.used), `${bytes(d.free)} free${grew}`,
      spark(d.series, { format: pct, min: 0, max: 1, label: d.name })));
  }
  el.replaceChildren(...tiles);
}

function servicesCard() {
  const sec = section("units");
  const u = sec.data;
  if (!u) {
    card("services", "Services", freshness(sec), empty("Waiting…"));
    return;
  }
  const groups = new Map();
  for (const sv of u.services) {
    const g = sv.group || "Other";
    if (!groups.has(g)) groups.set(g, []);
    groups.get(g).push(sv);
  }
  const body = [];
  for (const [g, list] of groups) {
    body.push(h("h3", {}, g), rows(list.map((sv) => {
      const up = sv.active === "active";
      const level = up ? (sv.restarts > 0 ? "warn" : "ok") : sv.active === "activating" ? "warn" : "bad";
      const right = up ? (sv.since ? `up ${span(Date.now() / 1000 - sv.since)}` : "up") : `${sv.active} (${sv.sub})`;
      return row(level, link(sv.name, sv.url), sv.restarts ? `${right} · ${plural(sv.restarts, "restart")}` : right, `${sv.name}: ${sv.active}`);
    })));
  }
  const reach = section("reach").data;
  if (reach && reach.endpoints.length) {
    body.push(h("h3", {}, "From the internet"), rows(reach.endpoints.map((e) =>
      row(e.ok ? "ok" : "bad", link(e.name, e.url), e.ok ? `${e.ms} ms` : e.code ? `HTTP ${e.code}` : "unreachable", e.error || null))));
  }
  card("services", "Services", freshness(sec), body);
}

function jobsCard() {
  const sec = section("units");
  const u = sec.data;
  if (!u) {
    card("jobs", "Jobs & alerts", null, empty("Waiting…"));
    return;
  }
  const body = [];
  if (u.alerts.length || u.failed.length) {
    body.push(h("h3", {}, "Open alerts"), rows([
      ...u.alerts.map((a) => row("bad", a.message.split("\n")[0], ago(a.since))),
      ...u.failed.filter((f) => !u.alerts.some((a) => a.key === `failed-${f}`)).map((f) => row("bad", `${f} failed`)),
    ]));
  }
  body.push(h("h3", {}, "Scheduled jobs"), rows(u.jobs.map((j) => {
    const level = j.running ? "info" : j.result === "success" ? "ok" : j.result ? "bad" : "idle";
    const last = j.finished || j.started || j.triggered;
    const right = [j.running ? "running" : last ? `ran ${ago(last)}` : "never ran", j.next ? `next ${ago(j.next)}` : null].filter(Boolean).join(" · ");
    return row(level, j.name, right, j.result ? `last run: ${j.result}` : null);
  })));
  const t = section("tailnet").data;
  if (t) {
    const recent = t.peers.filter((p) => p.online || (p.seen && Date.now() - when(p.seen) < 60 * 86400e3));
    body.push(h("h3", {}, "Tailnet"), rows(recent.slice(0, 6).map((p) =>
      row(p.online ? "ok" : "idle", [p.name, h("span", { class: "muted small" }, ` ${p.os}`)], p.online ? "online" : p.seen ? `seen ${ago(p.seen)}` : "offline", p.online ? "Online" : "Offline"))));
  }
  card("jobs", "Jobs & alerts", freshness(sec), body);
}

function recorderCard() {
  const sec = section("recordings");
  const r = sec.data;
  if (!r) {
    card("recorder", "Market-data recorder", freshness(sec), empty(sec.pending ? "Waiting…" : "jrxd isn't configured."));
    return;
  }
  const gaps = section("gaps");
  const today = (gaps.data && gaps.data.today && gaps.data.today.streams) || {};
  const days = r.days.slice().reverse();
  const top = Math.max(1, ...days.map((d) => d[1]));
  const bars = h("div", { class: "bars", role: "img", "aria-label": "Recorded per UTC day: " + days.map((d) => `${d[0]} ${bytes(d[1])}`).join(", ") },
    days.map((d, i) => h("div", { class: `bar${i === days.length - 1 ? " today" : ""}`, title: `${d[0]}: ${bytes(d[1])}` },
      h("i", { style: { height: `${Math.max(1, (d[1] / top) * 100)}%` } }))));
  const labels = h("div", { class: "bar-labels", "aria-hidden": "true" },
    days.map((d, i) => h("span", {}, i === days.length - 1 ? bytes(d[1]) : new Date(d[0] + "T12:00:00Z").toLocaleDateString("en-GB", { weekday: "narrow" }))));
  const table = h("table", {},
    h("thead", {}, h("tr", {}, h("th", {}, ""), h("th", {}, "Stream"), h("th", { class: "n" }, "Today"), h("th", { class: "n" }, "Messages"), h("th", { class: "n" }, "Gaps"))),
    h("tbody", {}, r.streams.map((st) => {
      const g = today[`${st.venue}:${st.instrument} ${st.stream}`];
      const level = st.idle === null ? "bad" : st.idle > 300 ? "bad" : st.idle > 60 ? "warn" : "ok";
      return h("tr", {},
        h("td", {}, icon(level, st.idle === null ? "No files" : `Last write ${span(st.idle)} ago`)),
        h("td", {}, st.stream, h("div", { class: "venue" }, `${st.venue.replace("-mainnet", "")} ${st.instrument}`)),
        h("td", { class: "n" }, bytes(st.today)),
        h("td", { class: "n" }, g ? count(g.messages) : "–"),
        h("td", { class: "n", title: g ? Object.entries(g.reasons).map(([k, v]) => `${v}× ${k}`).join("\n") : "" },
          g ? (g.gaps ? `${g.gaps} · ${span(g.missing)}` : "0") : "–"));
    })));
  const note = gaps.updated ? `Messages and gaps as of ${clock(gaps.updated)} (counted every 30 min).` : gaps.error ? `Gap count failed: ${gaps.error}` : "Counting messages and gaps…";
  card("recorder", "Market-data recorder",
       r.active === "active" ? `jrxd up ${span(Date.now() / 1000 - r.since)}` : `jrxd ${r.active}`,
       h("h3", {}, "Recorded per UTC day"), bars, labels, h("h3", {}, "Streams today"), table,
       h("p", { class: "small muted", style: { margin: "8px 0 0" } }, note));
}

function comingCard() {
  const sec = section("media");
  const m = sec.data;
  if (!m) {
    card("coming", "Coming up", freshness(sec), empty("Waiting…"));
    return;
  }
  const body = [];
  const eps = ((m.sonarr && m.sonarr.upcoming) || []).filter((e) => when(e.airs) > Date.now() - 86400e3);
  const byDay = new Map();
  for (const e of eps) {
    const k = dayLabel(e.airs);
    if (!byDay.has(k)) byDay.set(k, []);
    byDay.get(k).push(e);
  }
  if (!eps.length) body.push(h("h3", {}, "Episodes"), empty("Nothing airing this week."));
  for (const [label, list] of byDay) {
    body.push(h("h3", {}, label), rows(list.map((e) =>
      row(e.downloaded ? "ok" : "idle", [e.show, h("span", { class: "muted small" }, ` ${e.code}`)], clock(e.airs), e.downloaded ? "Downloaded" : "Not downloaded yet"))));
  }
  const movies = (m.radarr && m.radarr.upcoming) || [];
  if (movies.length) {
    body.push(h("h3", {}, "Movies"), rows(movies.slice(0, 6).map((mv) =>
      row(null, `${mv.title}${mv.year ? ` (${mv.year})` : ""}`, dayLabel(mv.release)))));
  }
  card("coming", "Coming up", freshness(sec), body);
}

function arrivedCard() {
  const sec = section("media");
  const m = sec.data;
  if (!m) {
    card("arrived", "New in the library", null, empty("Waiting…"));
    return;
  }
  const items = [
    ...((m.sonarr && m.sonarr.imported) || []).map((e) => ({ at: e.at, main: [e.show, h("span", { class: "muted small" }, ` ${e.code}`)], q: e.quality })),
    ...((m.radarr && m.radarr.imported) || []).map((mv) => ({ at: mv.at, main: `${mv.title}${mv.year ? ` (${mv.year})` : ""}`, q: mv.quality })),
  ].sort((a, b) => b.at.localeCompare(a.at));
  const body = [items.length ? rows(items.slice(0, 10).map((i) => row(null, [i.main, h("span", { class: "muted small" }, ` · ${i.q}`)], ago(i.at))))
                             : empty("Nothing new in the last two days.")];
  const jf = m.jellyfin;
  if (jf && jf.playing) {
    body.push(h("h3", {}, "Playing now"), jf.playing.length ? rows(jf.playing.map((p) =>
      h("div", { class: "row" }, icon(p.paused ? "idle" : "ok", p.paused ? "Paused" : "Playing"),
        h("div", { class: "grow" }, p.title, h("div", { class: "small muted" }, `${p.user} · ${p.device}${p.method ? " · " + p.method : ""}`),
          p.progress !== null ? h("div", { class: "progress" }, h("i", { style: { width: `${p.progress * 100}%` } })) : null)))) : empty("Nobody's watching."));
  }
  card("arrived", "New in the library", jf ? link(`Jellyfin ${jf.version}`, jf.url) : null, body);
}

function downloadsCard() {
  const sec = section("media");
  const m = sec.data;
  if (!m) {
    card("downloads", "Downloads & requests", null, empty("Waiting…"));
    return;
  }
  const queue = [...((m.sonarr && m.sonarr.queue) || []), ...((m.radarr && m.radarr.queue) || [])];
  const body = [h("h3", {}, "Downloading"), queue.length ? rows(queue.map((q) =>
    h("div", { class: "row" }, icon(q.health === "ok" ? "info" : q.health === "warning" ? "warn" : q.health === "error" ? "bad" : "info", q.status),
      h("div", { class: "grow" }, q.title, q.problem ? h("div", { class: "small muted" }, q.problem) : null,
        q.progress !== null ? h("div", { class: "progress" }, h("i", { style: { width: `${q.progress * 100}%` } })) : null),
      h("div", { class: "right" }, q.progress !== null ? pct(q.progress) : q.status, q.eta ? h("div", {}, q.eta) : null))))
    : empty("Nothing downloading.")];
  const seerr = m.seerr;
  if (seerr) {
    body.push(h("h3", {}, link("Requests", seerr.url)));
    if (seerr.pending.length) {
      body.push(rows(seerr.pending.map((p) => row("warn", [p.title, h("span", { class: "muted small" }, ` ${p.kind === "tv" ? "show" : "movie"}${p.seasons.length ? " · seasons " + p.seasons.join(", ") : ""}`)],
        `${p.by || "?"} · ${ago(p.at)}`, "Waiting for approval"))));
    } else {
      body.push(empty("None waiting for approval."));
    }
    const c = seerr.counts || {};
    body.push(h("p", { class: "small muted", style: { margin: "6px 0 0" } },
      `${c.total || 0} in all · ${c.processing || 0} processing · ${c.completed || 0} done`));
  }
  if (m.sonarr && m.sonarr.missing) body.push(h("p", { class: "small muted", style: { margin: "4px 0 0" } }, `${plural(m.sonarr.missing, "monitored episode")} missing in Sonarr`));
  card("downloads", "Downloads & requests", null, body);
}

function laptopCard() {
  const sec = section("laptop");
  const lap = sec.data;
  const repos = section("repos").data || [];
  if (!lap) {
    card("laptop", "Laptop", null, empty("The laptop's agent hasn't reported yet (pulse-agent-install.sh on the laptop sets it up)."));
    return;
  }
  const facts = [
    lap.battery ? `battery ${lap.battery.percent}% (${(lap.battery.status || "").toLowerCase()})` : null,
    ...lap.disks.map((d) => `${d.path} ${bytes(d.free)} free`),
    `up ${span(lap.uptime)}`,
    lap.backup ? `aurea backup pulled ${ago(lap.backup.finished)}` : null,
    lap.failed.length ? `${plural(lap.failed.length, "failed unit")}: ${lap.failed.join(", ")}` : null,
  ].filter(Boolean);
  const table = h("table", {},
    h("thead", {}, h("tr", {}, h("th", {}, "Repo"), h("th", {}, "Branch"), h("th", { class: "n" }, "Changed"), h("th", { class: "n" }, "Unpushed"), h("th", { class: "n" }, "Behind"), h("th", {}, "Last commit"))),
    h("tbody", {}, repos.map((r) => r.error
      ? h("tr", {}, h("td", {}, r.name), h("td", { colspan: 5, class: "muted" }, "couldn't read it"))
      : h("tr", {},
          h("td", { title: r.path }, link(r.name, r.remote ? `https://github.com/${r.remote}` : null)),
          h("td", {}, r.branch || "detached", r.upstream ? null : h("span", { class: "muted small" }, r.remote ? " · not pushed" : " · no remote")),
          h("td", { class: "n" }, r.changed || "–"),
          h("td", { class: "n" }, r.ahead || "–"),
          h("td", { class: "n" }, r.behind || "–"),
          h("td", { class: "small" }, r.last_commit ? [h("span", { class: "muted" }, ago(r.last_commit.at) + " · "), r.last_commit.subject] : "–")))));
  card("laptop", "Laptop", [`${lap.host} · ${lap.os} · `, freshness(sec, "reported ")],
       h("p", { class: "small ink-2", style: { margin: "0 0 10px" } }, facts.join(" · ")),
       h("div", { style: { overflowX: "auto" } }, table));
}

function render() {
  sparks = [];
  header();
  brief();
  attentionCard();
  todayCard();
  projectsCard();
  tilesCard();
  servicesCard();
  jobsCard();
  recorderCard();
  comingCard();
  arrivedCard();
  downloadsCard();
  laptopCard();
  requestAnimationFrame(() => sparks.forEach((f) => f()));
}

async function load() {
  try {
    const reply = await fetch("/api/snapshot", { cache: "no-store" });
    if (!reply.ok) throw new Error(reply.statusText);
    snap = await reply.json();
    $("main").classList.remove("stale");
    $("updated").textContent = `Updated ${clock(snap.generated)}`;
    render();
  } catch (err) {
    $("updated").textContent = snap ? `Can't reach aurea · showing ${clock(snap.generated)}` : "Can't reach aurea";
    $("main").classList.add("stale");
  }
}

// Asking: the conversation lives in the page; the server keeps the snapshot
// it started with, so follow-ups are about the same data

let conversation = null;
const history = [];

async function ask(e) {
  e.preventDefault();
  const input = $("question");
  const question = input.value.trim();
  if (!question) return;
  input.value = "";
  conversation = conversation || (crypto.randomUUID ? crypto.randomUUID() : String(Math.random()));
  history.push({ role: "user", content: question });
  const answer = h("div", { class: "a markdown typing" });
  $("thread").append(h("div", { class: "q" }, question), answer);
  $("ask-send").disabled = true;
  let text = "";
  try {
    const reply = await fetch("/api/ask", { method: "POST", headers: { "Content-Type": "application/json" },
                                           body: JSON.stringify({ conversation, messages: history }) });
    if (!reply.ok) throw new Error((await reply.json()).error || reply.statusText);
    const reader = reply.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let cut;
      while ((cut = buffer.indexOf("\n\n")) >= 0) {
        const line = buffer.slice(0, cut);
        buffer = buffer.slice(cut + 2);
        if (!line.startsWith("data: ")) continue;
        const event = JSON.parse(line.slice(6));
        if (event.error) throw new Error(event.error);
        if (event.text) {
          text += event.text;
          answer.replaceChildren(...markdown(text));
        }
      }
    }
    history.push({ role: "assistant", content: text });
  } catch (err) {
    history.pop();
    answer.classList.add("error");
    answer.replaceChildren(h("p", {}, `Couldn't answer: ${err.message || err}`));
  } finally {
    answer.classList.remove("typing");
    $("ask-send").disabled = false;
    input.focus();
  }
}

function theme() {
  const root = document.documentElement;
  const system = matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  const next = (root.dataset.theme || system) === "dark" ? "light" : "dark";
  // Back to following the OS when the choice matches it
  try {
    if (next === system) localStorage.removeItem("pulse-theme");
    else localStorage.setItem("pulse-theme", next);
  } catch (err) { /* private window: just this page view */ }
  if (next === system) delete root.dataset.theme;
  else root.dataset.theme = next;
  if (snap) render();
}

$("ask-form").addEventListener("submit", ask);
$("theme").addEventListener("click", theme);
$("refresh").addEventListener("click", async () => {
  $("refresh").disabled = true;
  try {
    await fetch("/api/refresh", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
    await new Promise((r) => setTimeout(r, 5000));
    await load();
  } finally {
    $("refresh").disabled = false;
  }
});
let resizing;
addEventListener("resize", () => {
  clearTimeout(resizing);
  resizing = setTimeout(() => snap && sparks.forEach((f) => f()), 150);
});
document.addEventListener("visibilitychange", () => { if (!document.hidden) load(); });
load();
setInterval(load, 60000);
