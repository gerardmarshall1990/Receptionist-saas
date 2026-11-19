// Lead Explorer front end. No framework, no build step.
// Rule: data is only ever inserted with textContent (via el()), never as HTML.

const $ = (sel) => document.querySelector(sel);

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") node.className = v;
    else if (k === "dataset") Object.assign(node.dataset, v);
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else node.setAttribute(k, v === true ? "" : String(v));
  }
  for (const c of children.flat()) {
    if (c === null || c === undefined || c === false) continue;
    node.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return node;
}

function safeUrl(u) {
  try {
    const url = new URL(u);
    return url.protocol === "http:" || url.protocol === "https:" ? url.href : null;
  } catch { return null; }
}

const fmt = new Intl.NumberFormat();
const ARRAY_KEYS = ["category", "subcategory", "state", "tier", "flag", "not_flag", "signal"];
const TRI_KEYS = [["has_phone", "Phone"], ["has_website", "Website"], ["has_email", "Email"]];

function blankState() {
  return {
    category: [], subcategory: [], state: [], tier: [], flag: [], not_flag: [], signal: [],
    q: "", has_phone: "", has_website: "", has_email: "",
    callable_now: false, include_suppressed: false, sort: "score", lead: "",
  };
}

let META = null;
let state = blankState();
let cursor = null;
let selectedId = null;
let inflight = null;
let facets = {};

// ------------------------------------------------------------ URL state

function readUrl() {
  const p = new URLSearchParams(location.search);
  const s = blankState();
  for (const k of ARRAY_KEYS) s[k] = p.getAll(k);
  for (const [k] of TRI_KEYS) s[k] = p.get(k) || "";
  s.q = p.get("q") || "";
  s.callable_now = p.get("callable_now") === "true";
  s.include_suppressed = p.get("include_suppressed") === "true";
  s.sort = p.get("sort") || "score";
  s.lead = p.get("lead") || "";
  return s;
}

function filterParams() {
  const p = new URLSearchParams();
  for (const k of ARRAY_KEYS) for (const v of state[k]) p.append(k, v);
  for (const [k] of TRI_KEYS) if (state[k]) p.set(k, state[k]);
  if (state.q.trim()) p.set("q", state.q.trim());
  if (state.callable_now) p.set("callable_now", "true");
  if (state.include_suppressed) p.set("include_suppressed", "true");
  return p;
}

function writeUrl() {
  const p = filterParams();
  if (state.sort !== "score") p.set("sort", state.sort);
  if (state.lead) p.set("lead", state.lead);
  const qs = p.toString();
  history.replaceState(null, "", qs ? `?${qs}` : location.pathname);
}

// ------------------------------------------------------------------- api

async function api(path, opts = {}) {
  const res = await fetch(path, { credentials: "same-origin", ...opts });
  if (!res.ok) {
    let msg = `${res.status}`;
    try { const j = await res.json(); msg = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail); } catch {}
    throw new Error(msg);
  }
  return res.json();
}

function showError(err) {
  const box = $("#error");
  if (!err) { box.hidden = true; return; }
  box.hidden = false;
  box.textContent = `Something went wrong: ${err.message || err}`;
}

// --------------------------------------------------------------- filters

function labelFor(kind, value) {
  if (kind === "category") return META.categories.find((c) => c.slug === value)?.label ?? value;
  if (kind === "subcategory") {
    for (const c of META.categories) {
      const s = c.subcategories.find((x) => x.slug === value);
      if (s) return s.label;
    }
  }
  if (kind === "flag" || kind === "not_flag") return META.flags.find((f) => f.key === value)?.label ?? value;
  if (kind === "signal") return META.signals.find((f) => f.key === value)?.label ?? value;
  return value;
}

function toggleIn(list, value) {
  const i = list.indexOf(value);
  if (i >= 0) list.splice(i, 1); else list.push(value);
}

function countMap(name) {
  return new Map((facets[name] || []).map((f) => [f.value, f.count]));
}

function renderChecklist(container, kind, options) {
  const counts = countMap(kind);
  container.replaceChildren(...options.map(({ value, label }) => {
    const n = counts.get(value);
    return el("label", { class: "check" },
      el("input", {
        type: "checkbox", checked: state[kind].includes(value),
        onchange: () => { toggleIn(state[kind], value); if (kind === "category") pruneSubcategories(); refresh(); },
      }),
      el("span", { class: "check-row" }, el("span", {}, label),
        el("span", { class: "n" }, fmt.format(n ?? 0))),
    );
  }));
}

function pruneSubcategories() {
  const allowed = new Set(subcategoryOptions().map((o) => o.value));
  state.subcategory = state.subcategory.filter((s) => allowed.has(s));
}

function subcategoryOptions() {
  const cats = META.categories.filter((c) => state.category.includes(c.slug));
  return cats.flatMap((c) => c.subcategories.map((s) => ({ value: s.slug, label: cats.length > 1 ? `${c.label}: ${s.label}` : s.label })));
}

function renderFilters() {
  renderChecklist($("#f-category"), "category", META.categories.map((c) => ({ value: c.slug, label: c.label })));

  const subs = subcategoryOptions();
  $("#g-subcategory").hidden = subs.length === 0;
  renderChecklist($("#f-subcategory"), "subcategory", subs);

  $("#f-contact").replaceChildren(...TRI_KEYS.map(([key, label]) =>
    el("div", { class: "tri" }, el("span", {}, `Has ${label.toLowerCase()}`),
      el("span", { class: "seg", role: "group", "aria-label": `Has ${label}` },
        ...[["", "Any"], ["yes", "Yes"], ["no", "No"]].map(([v, t]) =>
          el("button", { type: "button", "aria-pressed": String(state[key] === v),
            onclick: () => { state[key] = v; refresh(); } }, t))))));

  $("#f-flags").replaceChildren(...META.flags.map((f) => {
    const mode = state.flag.includes(f.key) ? "on" : state.not_flag.includes(f.key) ? "off" : "";
    return el("button", {
      type: "button", class: "chip", dataset: { mode },
      title: mode === "on" ? "Required (click to exclude)" : mode === "off" ? "Excluded (click to clear)" : "Click to require",
      onclick: () => {
        state.flag = state.flag.filter((x) => x !== f.key);
        state.not_flag = state.not_flag.filter((x) => x !== f.key);
        if (mode === "") state.flag.push(f.key);
        else if (mode === "on") state.not_flag.push(f.key);
        refresh();
      },
    }, f.label);
  }));

  $("#f-signals").replaceChildren(...META.signals.map((sg) => el("button", {
    type: "button", class: "chip", dataset: { mode: state.signal.includes(sg.key) ? "on" : "" },
    onclick: () => { toggleIn(state.signal, sg.key); refresh(); },
  }, sg.label)));

  renderChecklist($("#f-tier"), "tier", ["A", "B", "C", "D"].map((t) => ({ value: t, label: t })));
  const scored = (facets.tier || []).some((f) => f.value);
  $("#tier-hint").hidden = scored;

  const needle = $("#state-search").value.trim().toUpperCase();
  const stateCounts = countMap("state");
  const states = META.states
    .filter((s) => !needle || s.includes(needle))
    .sort((a, b) => (stateCounts.get(b) || 0) - (stateCounts.get(a) || 0) || a.localeCompare(b));
  renderChecklist($("#f-state"), "state", states.map((s) => ({ value: s, label: s })));

  $("#f-callable").checked = state.callable_now;
  $("#f-suppressed").checked = state.include_suppressed;
  $("#q").value = state.q;
  $("#sort").value = state.sort;
  renderActive();
}

function renderActive() {
  const chips = [];
  for (const k of ARRAY_KEYS) {
    for (const v of state[k]) {
      const prefix = k === "not_flag" ? "Not: " : k === "tier" ? "Tier " : k === "signal" ? "Evidence: " : "";
      chips.push(el("button", { type: "button", class: "chip",
        onclick: () => { state[k] = state[k].filter((x) => x !== v); if (k === "category") pruneSubcategories(); refresh(); } },
        prefix + labelFor(k, v)));
    }
  }
  for (const [k, label] of TRI_KEYS) {
    if (state[k]) chips.push(el("button", { type: "button", class: "chip", onclick: () => { state[k] = ""; refresh(); } },
      `${state[k] === "yes" ? "Has" : "No"} ${label.toLowerCase()}`));
  }
  if (state.callable_now) chips.push(el("button", { type: "button", class: "chip", onclick: () => { state.callable_now = false; refresh(); } }, "Callable now"));
  if (state.q.trim()) chips.push(el("button", { type: "button", class: "chip", onclick: () => { state.q = ""; refresh(); } }, `“${state.q.trim()}”`));
  $("#active").replaceChildren(...chips);
}

// ------------------------------------------------------------------ list

function catLine(category, subcategory) {
  const c = labelFor("category", category);
  const s = labelFor("subcategory", subcategory);
  return c.toLowerCase().startsWith(s.toLowerCase()) ? c : `${c} · ${s}`;
}

function phonePill(status) {
  const cls = { verified: "pill-good", likely: "pill-accent", unverified: "pill-muted", invalid: "pill-bad", none: "pill-bad" }[status] || "pill-muted";
  return el("span", { class: `pill ${cls}` }, status === "none" ? "no phone" : status);
}

function emailPill(status) {
  const map = {
    none: ["pill-muted", "none"], source_listed: ["pill-warn", "listed, unverified"],
    site_found: ["pill-accent", "on own site"], valid: ["pill-good", "valid"],
    risky: ["pill-warn", "risky"], invalid: ["pill-bad", "invalid"],
  };
  const [cls, text] = map[status] || ["pill-muted", status];
  return el("span", { class: `pill ${cls}` }, text);
}

const FLAG_SHORT = {
  uses_answering_service: ["pill-bad", "has answering svc"],
  franchise_disclaimer: ["pill-bad", "franchise"],
  website_mismatch: ["pill-warn", "site mismatch"],
  crawl_blocked: ["pill-muted", "site blocked us"],
  website_parked: ["pill-bad", "site parked"],
  thin_homepage: ["pill-muted", "JS-only site?"],
  multiple_locations: ["pill-accent", "multi-location"],
  shared_phone: ["pill-warn", "shared phone"],
  name_repeated_review: ["pill-warn", "check chain"],
  generic_name: ["pill-warn", "generic name"],
  no_website: ["pill-muted", "no site"],
  no_usable_phone: ["pill-bad", "phone unusable"],
  subdomain_platform: ["pill-muted", "site builder"],
};

function row(item) {
  const flags = (item.flags || []).filter((f) => FLAG_SHORT[f]).map((f) => el("span", { class: `pill ${FLAG_SHORT[f][0]}` }, FLAG_SHORT[f][1]));
  if (item.suppressed) flags.unshift(el("span", { class: "pill pill-bad" }, "suppressed"));
  const site = item.website && safeUrl(item.website);
  const tr = el("tr", {
    tabindex: "0", dataset: { id: item.id }, class: item.id === selectedId ? "selected" : null,
    onclick: () => openLead(item.id),
    onkeydown: (e) => { if (e.key === "Enter") openLead(item.id); },
  },
    el("td", { "data-label": null }, el("div", { class: "name" }, item.name),
      el("div", { class: "sub" }, catLine(item.category, item.subcategory))),
    el("td", { "data-label": "Where" }, el("div", {}, [item.city, item.state].filter(Boolean).join(", ") || "—"),
      el("div", { class: "sub" }, item.local_time || "", " ", item.callable_now ? el("span", { class: "pill pill-good" }, "callable now") : null)),
    el("td", { "data-label": "Phone" }, item.phone ? el("div", { class: "mono" }, item.phone) : null, phonePill(item.phone_status)),
    el("td", { "data-label": "Website" }, site
      ? el("a", { href: site, target: "_blank", rel: "noopener noreferrer nofollow", onclick: (e) => e.stopPropagation() }, item.domain)
      : el("span", { class: "muted" }, "—")),
    el("td", { "data-label": "Email" }, emailPill(item.email_status)),
    el("td", { class: "num", "data-label": "Locations" }, item.location_count > 1 ? String(item.location_count) : "1"),
    el("td", { "data-label": "Flags" }, el("div", { class: "pills" }, flags)),
    el("td", { class: "num", "data-label": "Score" }, item.tier
      ? el("span", {}, el("span", { class: `tier tier-${item.tier}` }, item.tier), " ", String(Math.round(item.score)))
      : el("span", { class: "muted", title: "Scoring arrives in Stage 4" }, "—")),
  );
  return tr;
}

let appending = false;
async function loadList({ append = false } = {}) {
  if (append && (appending || !cursor)) return; // scroll + button must not fetch the same page twice
  appending = append;
  try { await fetchList(append); } finally { if (append) appending = false; }
}

async function fetchList(append) {
  const p = filterParams();
  p.set("sort", state.sort);
  p.set("limit", "50");
  if (append && cursor) p.set("cursor", cursor);
  const t0 = performance.now();
  const data = await api(`/api/leads?${p}`, { signal: inflight?.signal });
  const rows = data.items.map(row);
  if (append) $("#rows").append(...rows); else $("#rows").replaceChildren(...rows);
  cursor = data.next_cursor;
  $("#more").hidden = !cursor;
  $("#empty").hidden = $("#rows").children.length > 0;
  $("#timing").textContent = `${$("#rows").children.length} shown · ${Math.round(performance.now() - t0)} ms`;
}

async function loadCount() {
  const data = await api(`/api/count?${filterParams()}`, { signal: inflight?.signal });
  $("#count").textContent = `${data.exact ? "" : "≈ "}${fmt.format(data.value)} leads`;
  $("#count").title = data.exact ? "Exact count" : "Planner estimate (exact counting stops at 50,000)";
}

async function loadFacets() {
  const p = filterParams();
  for (const f of ["category", "state", "tier"]) p.append("facet", f);
  if (state.category.length) p.append("facet", "subcategory");
  facets = await api(`/api/facets?${p}`, { signal: inflight?.signal });
}

let refreshTimer = null;
function refresh({ debounce = 0 } = {}) {
  clearTimeout(refreshTimer);
  writeUrl();
  renderActive();
  refreshTimer = setTimeout(async () => {
    inflight?.abort();
    inflight = new AbortController();
    cursor = null;
    showError(null);
    try {
      await Promise.all([loadList(), loadCount(), loadFacets().then(renderFilters)]);
    } catch (err) {
      if (err.name !== "AbortError") showError(err);
    }
  }, debounce);
}

// ---------------------------------------------------------------- detail

function section(title, ...children) {
  return el("section", { class: "dsec" }, el("h3", {}, title), ...children);
}

function kv(pairs) {
  return el("dl", { class: "kv" }, pairs.filter(([, v]) => v !== null && v !== undefined && v !== "")
    .flatMap(([k, v]) => [el("dt", {}, k), el("dd", {}, v)]));
}

const FACT_PILL = { value: "pill-accent", reachability: "pill-good", caution: "pill-warn", info: "pill-muted" };

function renderDetail(d) {
  const b = d.business;
  $("#d-name").textContent = b.name;
  $("#d-sub").textContent = catLine(b.category, b.subcategory);
  const body = [];

  if (d.suppression.length) {
    body.push(el("div", { class: "banner banner-bad" }, `Suppressed (${d.suppression.length} rule${d.suppression.length > 1 ? "s" : ""}): ${d.suppression[0].reason}. Never contact.`));
  }

  // Why this lead: evidence-backed reasons only (Stage 2+). Never invented.
  const why = el("div", { class: "why" }, el("h3", {}, "Why this lead"));
  if (d.reasons.length) {
    for (const r of d.reasons) {
      const src = r.source_url && safeUrl(r.source_url);
      why.append(el("div", { class: "reason" },
        el("div", {}, el("span", { class: "pill pill-accent" }, r.dimension), " ", el("strong", {}, r.signal.replaceAll("_", " "))),
        el("q", {}, r.evidence),
        src ? el("a", { href: src, target: "_blank", rel: "noopener noreferrer nofollow", class: "small" }, "source") : null));
    }
  } else {
    why.append(el("p", { class: "muted small" },
      "No evidence collected yet. Pain signals (e.g. “leave a message”, phone-only booking, 24/7 claims, review complaints) are added by the website crawl in Stage 2, each with the quoted text and the page it came from."));
  }
  body.push(why);

  if (d.signals && d.signals.length) {
    body.push(section("Signals found on their website",
      el("table", { class: "subtable" }, el("tbody", {}, d.signals.map((sg) => {
        const src = sg.source_url && safeUrl(sg.source_url);
        return el("tr", {},
          el("td", {}, el("span", { class: `pill ${sg.value ? "pill-accent" : "pill-muted"}` }, sg.value ? "yes" : "no")),
          el("td", {}, el("div", {}, sg.signal.replaceAll("_", " ")),
            el("div", { class: "muted small" }, sg.evidence || ""),
            src ? el("a", { href: src, target: "_blank", rel: "noopener noreferrer nofollow", class: "small" }, "page") : null));
      })))));
  }

  body.push(section("What we know so far",
    el("ul", { class: "facts" }, d.facts.map((f) => el("li", {}, el("span", { class: `pill ${FACT_PILL[f.kind]}` }, f.kind), el("span", {}, f.text))))));

  const site = b.website && safeUrl(b.website);
  body.push(section("Contact",
    kv([
      ["Website", site ? el("a", { href: site, target: "_blank", rel: "noopener noreferrer nofollow" }, b.domain) : "—"],
      ["Local time", `${d.local_time || "unknown"} (${b.tz || "no time zone"})`],
      ["Call now?", d.callable_now ? el("span", { class: "pill pill-good" }, "inside call window") : el("span", { class: "pill pill-muted" }, "outside call window")],
    ]),
    d.phones.length ? el("table", { class: "subtable" }, el("tbody", {}, d.phones.map((p) =>
      el("tr", {}, el("td", { class: "mono" }, p.e164), el("td", {}, phonePill(p.status)),
        el("td", { class: "muted small" }, [p.number_type_guess, ...(p.flags || [])].filter(Boolean).join(", ")))))) : el("p", { class: "muted" }, "No phone numbers."),
    d.emails.length ? el("table", { class: "subtable" }, el("tbody", {}, d.emails.map((e) =>
      el("tr", {}, el("td", { class: "mono" }, e.email),
        el("td", {}, e.origin === "business_site" ? el("span", { class: "pill pill-accent" }, "on own site") : el("span", { class: "pill pill-warn" }, "listed, not exportable")),
        el("td", { class: "muted small" }, [e.is_role_based ? "role" : "named", e.is_freemail ? "free-mail" : null, e.domain_matches_site ? "own domain" : null].filter(Boolean).join(", ")))))) : null,
  ));

  const gbox = el("div", { id: "google-box" },
    el("p", { class: "muted small" }, "Google rating and up to 5 reviews, fetched live from Google Maps when you ask (not stored). One lookup per click."),
    el("button", { type: "button", class: "btn", onclick: () => loadGoogle(b.id, gbox) }, "Load Google reviews"));
  body.push(section("Google reviews (live)", gbox));

  const cat = META.categories.find((c) => c.slug === b.category);
  if (cat) {
    body.push(section("Best call windows (business local time)",
      el("ul", { class: "facts" }, cat.call_windows.map((w) =>
        el("li", {}, el("span", { class: "mono" }, `${w.days.join(" ")} ${w.start}–${w.end}`), el("span", { class: "muted small" }, w.why))))));
  }

  const osm = b.lat !== null && b.lng !== null
    ? el("a", { href: `https://www.openstreetmap.org/?mlat=${encodeURIComponent(b.lat)}&mlon=${encodeURIComponent(b.lng)}#map=17/${encodeURIComponent(b.lat)}/${encodeURIComponent(b.lng)}`, target: "_blank", rel: "noopener noreferrer" }, "OpenStreetMap")
    : null;
  body.push(section("Location", kv([
    ["Address", [b.address, b.city, b.state, b.postcode].filter(Boolean).join(", ")],
    ["Map", osm],
    ["Locations", b.location_count > 1 ? `${b.location_count} (same website)` : "1"],
  ])));

  body.push(section("Source", kv([
    ["Source", `${b.source} · ${b.source_release}`],
    ["Datasets", (b.source_datasets || []).join(", ")],
    ["License", (b.licenses || []).join(", ")],
    ["Attribution", b.attribution],
    ["Confidence", b.source_confidence !== null ? b.source_confidence.toFixed(2) : null],
    ["Merged IDs", (b.merged_source_ids || []).length ? `${b.merged_source_ids.length} duplicate listing(s)` : null],
    ["Record ID", el("span", { class: "mono" }, b.source_id)],
  ])));

  body.push(section("Actions", el("div", { class: "actions" },
    d.suppression.some((s) => s.match_type === "business_id")
      ? el("span", { class: "muted" }, "Already suppressed.")
      : el("button", { type: "button", class: "btn btn-danger", onclick: () => askSuppress(b) }, "Suppress (opt-out)…"),
    el("span", { class: "muted small", title: META.export.why }, "Export opens after the compliance gate."))));

  $("#d-body").replaceChildren(...body);
}

async function openLead(id) {
  selectedId = id;
  state.lead = id;
  writeUrl();
  for (const tr of $("#rows").children) tr.classList.toggle("selected", tr.dataset.id === id);
  $("#detail").hidden = false;
  $("#d-name").textContent = "Loading…";
  $("#d-sub").textContent = "";
  $("#d-body").replaceChildren();
  try {
    renderDetail(await api(`/api/leads/${encodeURIComponent(id)}`));
  } catch (err) {
    $("#d-body").replaceChildren(el("p", { class: "error" }, `Couldn't load this lead: ${err.message}`));
  }
}

function closeLead() {
  $("#detail").hidden = true;
  selectedId = null;
  state.lead = "";
  writeUrl();
  for (const tr of $("#rows").children) tr.classList.remove("selected");
}

// ----------------------------------------------------------- Google live

function highlighted(text, terms) {
  // Split on the pain phrases and wrap matches in <mark>, using text nodes only.
  const esc = terms.filter(Boolean).map((t) => t.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
  if (!esc.length) return [text];
  const rx = new RegExp(`(${esc.join("|")})`, "gi");
  return text.split(rx).map((part, i) => (i % 2 ? el("mark", {}, part) : part));
}

async function loadGoogle(id, box) {
  box.replaceChildren(el("p", { class: "muted" }, "Asking Google…"));
  let d;
  try {
    d = await api(`/api/leads/${encodeURIComponent(id)}/google`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: "{}",
    });
  } catch (err) {
    box.replaceChildren(el("p", { class: "error" }, `Google reviews unavailable: ${err.message}`));
    return;
  }
  if (d.status !== "matched") {
    box.replaceChildren(el("p", { class: "muted" }, d.detail));
    return;
  }
  const p = d.place;
  const maps = p.maps_url && safeUrl(p.maps_url);
  const out = [
    el("div", {}, el("strong", {}, p.rating != null ? `★ ${p.rating.toFixed(1)}` : "No rating"),
      " ", el("span", { class: "muted" }, `${p.rating_count ?? 0} reviews on `),
      maps ? el("a", { href: maps, target: "_blank", rel: "noopener noreferrer" }, "Google Maps") : "Google Maps"),
    el("p", { class: "muted small" }, `Matched: ${d.detail}. ${p.name || ""}, ${p.address || ""}`),
  ];
  for (const r of p.reviews) {
    const url = r.url && safeUrl(r.url);
    const authorUrl = r.author_url && safeUrl(r.author_url);
    const photo = r.author_photo && safeUrl(r.author_photo);
    out.push(el("div", { class: "reason" },
      el("div", {},
        photo && photo.startsWith("https://lh3.googleusercontent.com/") ? el("img", { src: photo, alt: "", class: "avatar", referrerpolicy: "no-referrer" }) : null,
        authorUrl ? el("a", { href: authorUrl, target: "_blank", rel: "noopener noreferrer" }, r.author || "Google user") : (r.author || "Google user"),
        " ", el("span", { class: "muted small" }, `${r.rating ? "★".repeat(r.rating) : ""} ${r.when || ""}`)),
      el("q", {}, ...highlighted(r.text || "", d.highlight)),
      url ? el("a", { href: url, target: "_blank", rel: "noopener noreferrer", class: "small" }, "View on Google Maps") : null));
  }
  if (!p.reviews.length) out.push(el("p", { class: "muted" }, "Google returned no review text."));
  out.push(el("p", { class: "muted small" }, d.notice));
  box.replaceChildren(...out);
}

// -------------------------------------------------------------- suppress

let suppressTarget = null;
function askSuppress(b) {
  suppressTarget = b;
  $("#suppress-reason").value = "";
  $("#suppress-domain").checked = false;
  $("#suppress-domain-wrap").hidden = !b.domain;
  $("#suppress-domain-name").textContent = b.domain || "";
  $("#suppress-dialog").showModal();
}

async function doSuppress(e) {
  e.preventDefault();
  const reason = $("#suppress-reason").value.trim();
  if (reason.length < 3) { $("#suppress-reason").reportValidity(); return; }
  try {
    await api(`/api/leads/${encodeURIComponent(suppressTarget.id)}/suppress`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ reason, include_domain: $("#suppress-domain").checked }),
    });
    $("#suppress-dialog").close();
    await openLead(suppressTarget.id);
    refresh();
  } catch (err) {
    showError(err);
    $("#suppress-dialog").close();
  }
}

// ------------------------------------------------------------------ init

async function init() {
  try {
    META = await api("/api/meta");
  } catch (err) {
    $("#meta-line").textContent = "Unavailable";
    showError(err);
    return;
  }
  $("#meta-line").textContent = `${fmt.format(META.total)} businesses · Overture ${META.release ?? "—"} · config ${META.config_hash}`;
  $("#export-pill").title = META.export.why;
  $("#sort").replaceChildren(...META.sorts.map((s) => el("option", { value: s.key }, s.label)));

  state = readUrl();
  state.category = state.category.filter((c) => META.categories.some((x) => x.slug === c));
  renderFilters();

  $("#q").addEventListener("input", (e) => { state.q = e.target.value; refresh({ debounce: 250 }); });
  $("#sort").addEventListener("change", (e) => { state.sort = e.target.value; refresh(); });
  $("#f-callable").addEventListener("change", (e) => { state.callable_now = e.target.checked; refresh(); });
  $("#f-suppressed").addEventListener("change", (e) => { state.include_suppressed = e.target.checked; refresh(); });
  $("#state-search").addEventListener("input", renderFilters);
  $("#reset").addEventListener("click", () => { const lead = state.lead; state = blankState(); state.lead = lead; $("#state-search").value = ""; refresh(); });
  $("#more").addEventListener("click", async () => {
    try { await loadList({ append: true }); } catch (err) { if (err.name !== "AbortError") showError(err); }
  });
  $("#d-close").addEventListener("click", closeLead);
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !$("#suppress-dialog").open) closeLead(); });
  $("#suppress-form").addEventListener("submit", doSuppress);
  $("#suppress-cancel").addEventListener("click", () => $("#suppress-dialog").close());
  $("#toggle-filters").addEventListener("click", () => {
    const open = $("#filters").classList.toggle("open");
    $("#toggle-filters").setAttribute("aria-expanded", String(open));
  });

  new IntersectionObserver((entries) => {
    if (entries.some((x) => x.isIntersecting) && cursor && !$("#more").hidden) $("#more").click();
  }, { rootMargin: "400px" }).observe($("#more"));

  refresh();
  if (state.lead) openLead(state.lead);
}

init();
