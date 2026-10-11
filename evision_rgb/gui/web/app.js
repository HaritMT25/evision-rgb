"use strict";
/* EVision RGB — UI logic. Talks to the local API served by the same process. */

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const S = {
  layout: null, modes: [], settings: null, info: null, status: null,
  custom: {}, sel: new Set(), brush: "ff0000", group: 0, dirty: false,
  tab: "light", keyEls: {}, frameBusy: false, lastFrameAt: 0, hwBrushColor: "ff0000",
};
const NO_EFFECT = "";  // app rules: keep the keyboard's own effect

// ------------------------------------------------------------------ api
async function api(path, body) {
  const opt = body === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
  const r = await fetch(path, opt);
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.error || r.statusText);
  return j;
}
function toast(msg, err = false) {
  const t = $("#toast");
  t.textContent = msg; t.className = "show" + (err ? " err" : "");
  clearTimeout(toast.t); toast.t = setTimeout(() => (t.className = ""), err ? 5000 : 2200);
}
async function safe(fn, okMsg) {
  try { const r = await fn(); if (okMsg) toast(okMsg); return r; }
  catch (e) { toast(e.message, true); throw e; }
}
function debounce(fn, ms) { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; }
const hex = c => (c || "000000").replace("#", "").toLowerCase();

function modal(title, bodyHtml, buttons) {
  return new Promise(res => {
    $("#mTitle").textContent = title; $("#mBody").innerHTML = bodyHtml; const b = $("#mBtns"); b.innerHTML = "";
    for (const [label, val, cls] of buttons) {
      const x = document.createElement("button"); x.className = "btn " + (cls || ""); x.textContent = label;
      x.onclick = () => { $("#modal").classList.remove("show"); res(val === "__input" ? ($("#mBody input") || {}).value : val); };
      b.appendChild(x);
    }
    $("#modal").classList.add("show");
    const inp = $("#mBody input"); if (inp) { inp.focus(); inp.select(); }
  });
}

// ------------------------------------------------------------------ colour helpers
function hsv(h, s = 1, v = 1) {
  const f = n => { const k = (n + h * 6) % 6; return v - v * s * Math.max(0, Math.min(k, 4 - k, 1)); };
  return [f(5), f(3), f(1)].map(x => Math.round(x * 255).toString(16).padStart(2, "0")).join("");
}
function mix(a, b, t) {
  const p = x => [0, 2, 4].map(i => parseInt(x.substr(i, 2), 16));
  const A = p(a), B = p(b);
  return A.map((v, i) => Math.round(v + (B[i] - v) * t).toString(16).padStart(2, "0")).join("");
}

// ------------------------------------------------------------------ keyboard board
function buildBoard() {
  const board = $("#board"), keys = S.layout.keys, PAD = 14;
  const x0 = Math.min(...keys.map(k => k.x)) - PAD, y0 = Math.min(...keys.map(k => k.y)) - PAD;
  const W = Math.max(...keys.map(k => k.x + k.w), ...(S.layout.decor || []).map(d => d.x + d.w)) + PAD - x0;
  const H = Math.max(...keys.map(k => k.y + k.h)) + PAD - y0;
  board.style.aspectRatio = `${W} / ${H}`;
  // fit inside the stage by width AND height (leave room for the caption)
  const fit = () => {
    const st = $("#stage"), cs = getComputedStyle(st);
    const availW = st.clientWidth - parseFloat(cs.paddingLeft) - parseFloat(cs.paddingRight);
    const availH = st.clientHeight - parseFloat(cs.paddingTop) - parseFloat(cs.paddingBottom) - 34;
    board.style.width = Math.max(200, Math.min(availW, availH * W / H, 980)) + "px";
  };
  new ResizeObserver(fit).observe($("#stage"));
  fit();
  const place = (el, r) => Object.assign(el.style, { left: ((r.x - x0) / W * 100) + "%", top: ((r.y - y0) / H * 100) + "%",
    width: (r.w / W * 100) + "%", height: (r.h / H * 100) + "%" });
  for (const d of S.layout.decor || []) { const el = document.createElement("div"); el.className = d.type; place(el, d); board.appendChild(el); }
  for (const k of keys) {
    const d = document.createElement("div");
    d.className = "key off"; d.dataset.name = k.name; d.title = k.label;
    place(d, k);
    const SHORT = { PageDown: "PgDn", PageUp: "PgUp", BackSpace: "Bksp", CapsLock: "Caps", "Right Alt": "Alt",
      "Right Ctrl": "Ctrl", Rightshift: "Shift", Delete: "Del", Left: "←", Right: "→", Up: "↑", Down: "↓" };
    d.innerHTML = `<div class="under"></div><div class="cap">${SHORT[k.label] || k.label}</div>`;
    board.appendChild(d);
    S.keyEls[k.name] = d;
  }
  // selection: click paints (custom tab), drag draws a box
  let start = null, moved = false, additive = false;
  board.addEventListener("mousedown", e => {
    if (S.tab !== "custom") return;
    const r = board.getBoundingClientRect();
    start = { x: e.clientX - r.left, y: e.clientY - r.top }; moved = false; additive = e.shiftKey || e.ctrlKey;
  });
  window.addEventListener("mousemove", e => {
    if (!start) return;
    const r = board.getBoundingClientRect(), x = e.clientX - r.left, y = e.clientY - r.top;
    if (!moved && Math.hypot(x - start.x, y - start.y) < 5) return;
    moved = true;
    const sb = $("#selbox"), L = Math.min(x, start.x), T = Math.min(y, start.y);
    Object.assign(sb.style, { display: "block", left: L + "px", top: T + "px", width: Math.abs(x - start.x) + "px", height: Math.abs(y - start.y) + "px" });
    const box = sb.getBoundingClientRect();
    if (!additive) S.sel.clear();
    for (const [n, el] of Object.entries(S.keyEls)) {
      const kr = el.getBoundingClientRect();
      if (kr.right > box.left && kr.left < box.right && kr.bottom > box.top && kr.top < box.bottom) S.sel.add(n);
    }
    paintSelection();
  });
  window.addEventListener("mouseup", e => {
    if (!start) return;
    $("#selbox").style.display = "none";
    if (!moved) {
      const key = e.target.closest(".key");
      if (key) {
        const n = key.dataset.name;
        if (additive) { S.sel.has(n) ? S.sel.delete(n) : S.sel.add(n); }
        else { S.sel = new Set([n]); setCustom({ [n]: S.brush }); }
        paintSelection();
      }
    }
    start = null;
  });
}
function colorKeys(colors) {
  for (const [n, el] of Object.entries(S.keyEls)) {
    const c = hex(colors[n] || "000000");
    el.classList.toggle("off", c === "000000");
    el.style.setProperty("--c", "#" + c);
  }
}
function paintSelection() { for (const [n, el] of Object.entries(S.keyEls)) el.classList.toggle("sel", S.tab === "custom" && S.sel.has(n)); }

// preview loop: custom-tab edits show local state; otherwise mirror the keyboard
async function previewLoop() {
  const live = (S.status && S.status.live) || S.animated;
  const wait = live ? 90 : 1500;
  if (S.tab === "custom" && (S.dirty || Object.keys(S.custom).length)) {
    colorKeys(S.custom);
  } else if (!S.frameBusy && Date.now() - S.lastFrameAt > wait) {
    S.frameBusy = true;
    try {
      const f = await api("/frame");
      colorKeys(f.colors);
      S.animated = !!f.animated;
      S.lastFrameAt = Date.now();
      setCaption(f);
    } catch (e) { /* status loop reports disconnects */ }
    S.frameBusy = false;
  }
  setTimeout(previewLoop, live ? 60 : 250);
}
function modeLabel(name) { return (name || "").replace(/_/g, " ").replace(/\b\w/g, c => c.toUpperCase()); }
function setCaption(f) {
  const st = S.status || {};
  let t;
  if (S.tab === "custom" && S.dirty) t = "<b>Preview</b> of unsaved custom colours";
  else if (f.source === "live") t = `<b>Live:</b> ${modeLabel(f.effect)} · ${st.fps || 0} fps` + (st.apps ? ` · app profile: ${st.apps.foreground || "?"} → ${st.apps.rule}` : "");
  else if (f.source === "custom") t = `<b>Keyboard effect:</b> Custom (group ${(S.settings.custom_active || 0) + 1})`;
  else t = `<b>Keyboard effect:</b> ${modeLabel(f.mode)}` + (f.animated ? ' <span class="hint">— preview is an approximation of the keyboard\'s own animation</span>' : "");
  $("#caption").innerHTML = t;
}

// ------------------------------------------------------------------ status
async function statusLoop() {
  try {
    S.status = await api("/status");
    const dot = $("#statusDot");
    dot.className = "dot " + (S.status.live ? "live" : "ok");
    $("#statusText").textContent = S.status.live ? `live: ${modeLabel(S.status.effect)}` : "keyboard connected";
    $$(".card").forEach(c => c.classList.toggle("on", S.status.effect === c.dataset.effect && !S.status.apps));
    const err = S.status.stream_error || S.status.audio_error;
    if (err) $("#statusText").textContent += " · " + err.slice(0, 60);
    renderTimer();
    if (S.status.apps) $("#appsNow").textContent = `now: ${S.status.apps.foreground} → ${S.status.apps.rule}`;
    else $("#appsNow").textContent = "";
  } catch (e) {
    $("#statusDot").className = "dot bad";
    $("#statusText").textContent = "keyboard / service not reachable";
  }
  setTimeout(statusLoop, 1000);
}
async function conflictLoop() {
  try {
    const c = await api("/conflicts");
    const pill = $("#conflictPill");
    pill.style.display = c.length ? "" : "none";
    pill.textContent = `⚠ ${c.length} conflicting program${c.length > 1 ? "s" : ""}`;
    renderConflicts(c);
  } catch (e) { }
  setTimeout(conflictLoop, 5000);
}
function renderConflicts(list) {
  const box = $("#conflictBox");
  if (!list.length) { box.innerHTML = ""; return; }
  box.innerHTML = `<div class="warnbox"><b>Other RGB software is running.</b> It talks to the same keyboard channel and
    overrides or breaks this app (this is why the AJAZZ app's Custom/Music never worked).<table>${list.map(p => `
    <tr><td>${p.name}</td><td class="hint">${p.why}</td><td style="width:90px">${p.closable ?
      `<button class="btn" data-pid="${p.pid}">Close</button>` : `<span class="hint">needs admin</span>`}</td></tr>`).join("")}
    </table>${list.some(p => !p.closable) ? '<div class="hint">Nexus: run <code>Stop-Service NexusService</code> in an admin PowerShell, or uninstall Nexus.</div>' : ""}</div>`;
  $$("button[data-pid]", box).forEach(b => b.onclick = () => safe(() => api("/close_conflict", { pid: +b.dataset.pid }), "Closed").then(conflictLoop));
}

// ------------------------------------------------------------------ palettes
function buildPalette(el, onPick, getCur) {
  el.innerHTML = "";
  for (const c of S.settings.palette) {
    const d = document.createElement("div"); d.className = "sw"; d.style.background = "#" + c; d.dataset.c = c;
    d.onclick = () => onPick(c); el.appendChild(d);
  }
  const p = document.createElement("label"); p.className = "sw picker"; p.title = "Any colour";
  p.innerHTML = '<input type="color">';
  const inp = p.firstChild;
  inp.oninput = debounce(() => onPick(hex(inp.value)), 120);
  el.appendChild(p);
  el._mark = () => { const cur = getCur(); $$(".sw", el).forEach(s => s.classList.toggle("on", s.dataset.c === cur)); };
  el._mark();
}

// ------------------------------------------------------------------ light (hardware) tab
async function loadInfo() {
  S.info = await api("/info");
  const i = S.info;
  $("#hwMode").value = String(i.mode_id);
  $("#hwBright").value = i.brightness; $("#hwBrightV").textContent = `${i.brightness}/4`;
  $("#hwSpeed").value = 4 - i.speed; $("#hwSpeedV").textContent = `${4 - i.speed}/4`;
  $$("#hwDir button").forEach(b => b.classList.toggle("on", +b.dataset.v === i.direction));
  $("#hwRandom").checked = !!i.random;
  S.hwBrushColor = i.color; $("#hwChip").style.background = "#" + i.color; $("#hwHex").textContent = "#" + i.color;
  $("#hwPalette")._mark && $("#hwPalette")._mark();
  if (!S.dirty) { S.group = S.settings.custom_active || 0; markGroup(); }
  renderDevInfo();
}
const pushEffect = debounce(async () => {
  const body = {
    mode: +$("#hwMode").value, brightness: +$("#hwBright").value, speed: 4 - +$("#hwSpeed").value,
    color: S.hwBrushColor, random: $("#hwRandom").checked,
    direction: +(($("#hwDir button.on") || {}).dataset?.v || 0),
  };
  if (body.mode === 20) body.colorset = 0;
  await safe(() => api("/effect", body));
  await loadInfo(); S.lastFrameAt = 0;
}, 220);
function initLight() {
  const sel = $("#hwMode");
  sel.innerHTML = S.modes.map(m => `<option value="${m.id}">${modeLabel(m.name)}${m.id === 20 ? " (per-key)" : ""}</option>`).join("");
  sel.onchange = pushEffect;
  $("#hwBright").oninput = () => { $("#hwBrightV").textContent = `${$("#hwBright").value}/4`; pushEffect(); };
  $("#hwSpeed").oninput = () => { $("#hwSpeedV").textContent = `${$("#hwSpeed").value}/4`; pushEffect(); };
  $$("#hwDir button").forEach(b => b.onclick = () => { $$("#hwDir button").forEach(x => x.classList.remove("on")); b.classList.add("on"); pushEffect(); });
  $("#hwRandom").onchange = pushEffect;
  buildPalette($("#hwPalette"), c => {
    S.hwBrushColor = c; $("#hwChip").style.background = "#" + c; $("#hwHex").textContent = "#" + c;
    $("#hwPalette")._mark(); pushEffect();
  }, () => S.hwBrushColor);
}

// ------------------------------------------------------------------ custom tab
function markGroup() { $$("#cGroup button").forEach(b => b.classList.toggle("on", +b.dataset.v === S.group)); }
function setDirty(d) { S.dirty = d; $("#cDirty").textContent = d ? "unsaved changes (previewing live)" : ""; }
const pushPreview = debounce(() => {
  api("/run", { effect: "solid", params: { color: "000000", keys: S.custom }, remember: false }).catch(e => toast(e.message, true));
}, 60);
function setCustom(patch) {
  Object.assign(S.custom, patch); setDirty(true); colorKeys(S.custom); pushPreview();
  $("#caption").innerHTML = "<b>Preview</b> of unsaved custom colours — Save to keep them";
}
async function loadCustom(fromKeyboard = false) {
  const saved = (S.settings.custom_groups || [])[S.group];
  const active = (S.settings.custom_active || 0) === S.group;
  // the keyboard only displays memory area 0; groups are layouts saved in this app
  S.custom = (!fromKeyboard && saved) ? { ...saved } : await api(`/custom?colorset=${active || fromKeyboard ? 0 : S.group}`);
  setDirty(false); colorKeys(S.custom);
}
function presets(name) {
  const keys = S.layout.keys, out = {};
  const minX = Math.min(...keys.map(k => k.x)), maxX = Math.max(...keys.map(k => k.x + k.w));
  const minY = Math.min(...keys.map(k => k.y)), maxY = Math.max(...keys.map(k => k.y + k.h));
  const second = S.settings.palette.find(c => c !== S.brush) || "0099ff";
  for (const k of keys) {
    const fx = (k.x + k.w / 2 - minX) / (maxX - minX), fy = (k.y + k.h / 2 - minY) / (maxY - minY);
    if (name === "wasd") out[k.name] = ["W", "A", "S", "D", "Up", "Down", "Left", "Right"].includes(k.name) ? S.brush : "000000";
    if (name === "gradient") out[k.name] = mix(S.brush, second, fx);
    if (name === "rainbow") out[k.name] = hsv(fx * 0.85);
    if (name === "rows") out[k.name] = hsv(fy * 0.8);
  }
  return out;
}
function initCustom() {
  buildPalette($("#cPalette"), c => {
    S.brush = c; $("#cChip").style.background = "#" + c; $("#cHex").textContent = "#" + c; $("#cPalette")._mark();
    if (S.sel.size) setCustom(Object.fromEntries([...S.sel].map(n => [n, c])));
  }, () => S.brush);
  $("#cChip").style.background = "#" + S.brush; $("#cHex").textContent = "#" + S.brush;
  $("#cSelAll").onclick = () => { S.sel = new Set(Object.keys(S.keyEls)); paintSelection(); };
  $("#cSelNone").onclick = () => { S.sel.clear(); paintSelection(); };
  $("#cSelInv").onclick = () => { S.sel = new Set(Object.keys(S.keyEls).filter(n => !S.sel.has(n))); paintSelection(); };
  $("#cPaint").onclick = () => S.sel.size ? setCustom(Object.fromEntries([...S.sel].map(n => [n, S.brush]))) : toast("Select keys first");
  $("#cFill").onclick = () => setCustom(Object.fromEntries(Object.keys(S.keyEls).map(n => [n, S.brush])));
  $("#cOff").onclick = () => setCustom(Object.fromEntries(Object.keys(S.keyEls).map(n => [n, "000000"])));
  $$("[data-preset]").forEach(b => b.onclick = () => setCustom(presets(b.dataset.preset)));
  $$("#cGroup button").forEach(b => b.onclick = async () => {
    if (S.dirty && !(await modal("Discard changes?", "Switching group discards the unsaved preview.", [["Cancel", false], ["Discard", true, "primary"]]))) return;
    S.group = +b.dataset.v; markGroup(); await loadCustom(); await api("/stop", {}).catch(() => { });
  });
  $("#cLoad").onclick = () => safe(() => loadCustom(true), "Loaded what the keyboard currently shows");
  $("#cDiscard").onclick = async () => { await api("/stop", {}).catch(() => { }); await loadCustom(); toast("Preview discarded"); };
  $("#cSave").onclick = () => safe(async () => {
    await api("/custom", { colors: S.custom, colorset: 0, default: "000000" });
    const groups = [...(S.settings.custom_groups || [null, null, null])];
    groups[S.group] = { ...S.custom };
    S.settings = await api("/settings", { custom_groups: groups, custom_active: S.group });
    setDirty(false); await loadInfo();
    toast(`Group ${S.group + 1} saved and shown on the keyboard`);
  });
}

// ------------------------------------------------------------------ live tab
const EFFECTS = [
  { id: "rainbow", name: "Rainbow wave", desc: "Flowing rainbow across the keys.", p: [
    ["speed", "Speed", "range", 0.02, 1, 0.01, 0.25], ["scale", "Width", "range", 0.3, 3, 0.1, 1],
    ["angle", "Angle", "range", 0, 360, 5, 0], ["brightness", "Brightness", "range", 0.1, 1, 0.05, 1]] },
  { id: "reactive", name: "Reactive typing", desc: "Pressed keys flash and send a ripple outwards.", p: [
    ["base", "Background", "color", "000008"], ["color", "Ripple colour", "colorOrRainbow", null],
    ["fade", "Fade (s)", "range", 0.2, 2, 0.05, 0.6], ["ripple_speed", "Ripple speed", "range", 0.3, 3, 0.1, 1.2],
    ["ripple", "Ripples", "check", true]] },
  { id: "music", name: "Music visualiser", desc: "Spectrum of whatever your PC is playing (bass on the left).", p: [
    ["style", "Style", "select", ["bars", "vu", "beat", "ripple", "pulse"], "bars"], ["fall", "Fall speed", "range", 0.5, 4, 0.1, 1.2],
    ["scale", "Sensitivity", "range", 0.5, 1, 0.05, 0.8, true], ["hue_speed", "Colour drift", "range", 0, 0.3, 0.01, 0.03]] },
  { id: "ambient", name: "Screen ambient", desc: "Keys take the colours of your screen — like a mini Ambilight.", p: [
    ["mode", "Mode", "select", ["map", "average"], "map"], ["saturation", "Vividness", "range", 1, 2.5, 0.1, 1.4],
    ["smooth", "Smoothing (s)", "range", 0, 1, 0.05, 0.25], ["brightness", "Brightness", "range", 0.1, 1, 0.05, 1]] },
  { id: "sysmon", name: "System monitor", desc: "F-row = CPU load, number row = RAM use; Esc pulses above 90 % CPU.", p: [
    ["base", "Background", "color", "000010"]] },
  { id: "breathing", name: "Breathing", desc: "Whole board fades in and out.", p: [
    ["color", "Colour", "color", "00a0ff"], ["period", "Period (s)", "range", 1, 10, 0.5, 4]] },
  { id: "gradient", name: "Gradient", desc: "Static two-colour gradient.", p: [
    ["left", "From", "color", "ff0080"], ["right", "To", "color", "00c0ff"], ["vertical", "Top → bottom", "check", false]] },
  { id: "solid", name: "Solid colour", desc: "One colour on every key.", p: [["color", "Colour", "color", "ffffff"]] },
];
function cardParams(card) {
  const out = {};
  for (const el of $$("[data-p]", card)) {
    const k = el.dataset.p;
    if (el.type === "checkbox") out[k] = el.checked;
    else if (el.type === "range") out[k] = el.dataset.inv ? +(1.5 - +el.value).toFixed(2) : +el.value;
    else if (el.type === "color") { if (!el.disabled) out[k] = hex(el.value); }
    else out[k] = el.value;
  }
  if (out.color === undefined && card.dataset.effect === "reactive") out.color = null;
  return out;
}
function initLive() {
  const wrap = $("#cards");
  wrap.innerHTML = EFFECTS.map(e => `<div class="card" data-effect="${e.id}">
    <h3>${e.name}<button class="btn primary" data-start>Start</button></h3><div class="desc">${e.desc}</div>
    ${e.p.map(([k, label, type, a, b, step, def, inv]) => {
      if (type === "range") return `<div class="p"><span>${label}</span><input type="range" data-p="${k}" min="${a}" max="${b}" step="${step}" value="${inv ? (1.5 - def) : def}" ${inv ? 'data-inv="1"' : ""}><output>${def}</output></div>`;
      if (type === "color") return `<div class="p"><span>${label}</span><input type="color" data-p="${k}" value="#${a}"><output></output></div>`;
      if (type === "colorOrRainbow") return `<div class="p"><span>${label}</span><input type="color" data-p="${k}" value="#ffffff" disabled><label class="row" style="gap:4px"><input type="checkbox" data-rainbow checked>🌈</label></div>`;
      if (type === "check") return `<div class="p"><span>${label}</span><label class="toggle"><input type="checkbox" data-p="${k}" ${a ? "checked" : ""}><span></span></label><output></output></div>`;
      if (type === "select") return `<div class="p"><span>${label}</span><select data-p="${k}">${a.map(o => `<option ${o === b ? "selected" : ""}>${o}</option>`).join("")}</select><output></output></div>`;
    }).join("")}</div>`).join("");
  for (const card of $$(".card", wrap)) {
    const eff = card.dataset.effect;
    const start = () => safe(() => api("/run", { effect: eff, params: cardParams(card) }));
    $("[data-start]", card).onclick = () => start().then(() => toast(`${eff} running`));
    const restart = debounce(() => { if (S.status && S.status.effect === eff) start(); }, 250);
    for (const el of $$("input,select", card)) {
      el.addEventListener("input", () => {
        const o = el.parentElement.querySelector("output");
        if (el.type === "range" && o) o.textContent = el.dataset.inv ? (1.5 - +el.value).toFixed(2) : el.value;
        restart();
      });
    }
    const rb = $("[data-rainbow]", card);
    if (rb) rb.onchange = () => { $("[data-p=color]", card).disabled = rb.checked; restart(); };
  }
  $("#liveStop").onclick = () => safe(() => api("/stop", {}), "Back to the keyboard's own effect").then(() => (S.lastFrameAt = 0));
}

// ------------------------------------------------------------------ automation tab
function getPath(o, path) { return path.split(".").reduce((a, k) => a && a[k], o); }
async function initAutomation() {
  const cfg = await api("/automation");
  const keyOpts = S.layout.keys.map(k => `<option value="${k.name}">${k.label}</option>`).join("");
  $$("#tab-auto select[data-a]").forEach(s => s.innerHTML = keyOpts);
  const fill = c => {
    for (const el of $$("#tab-auto [data-a]")) {
      const v = getPath(c, el.dataset.a);
      if (el.type === "checkbox") el.checked = !!v;
      else if (el.type === "color") el.value = "#" + v;
      else el.value = v;
      const o = $(`#tab-auto [data-o="${el.dataset.a}"]`); if (o) o.textContent = Math.round(v * 100) + "%";
    }
  };
  fill(cfg);
  const save = debounce(async () => {
    const patch = {};
    for (const el of $$("#tab-auto [data-a]")) {
      const [sec, key] = el.dataset.a.split(".");
      let v = el.type === "checkbox" ? el.checked : el.type === "color" ? hex(el.value)
        : (el.type === "range" || el.type === "number") ? +el.value : el.value;
      (patch[sec] = patch[sec] || {})[key] = v;
    }
    try { fill(await api("/automation", patch)); } catch (e) { toast(e.message, true); }
  }, 300);
  $$("#tab-auto [data-a]").forEach(el => el.addEventListener("input", () => {
    const o = $(`#tab-auto [data-o="${el.dataset.a}"]`); if (o) o.textContent = Math.round(el.value * 100) + "%";
    save();
  }));
  $("#tStart").onclick = () => safe(() => api("/timer", { minutes: +$("#tMin").value, label: $("#tLabel").value }), "Timer started");
  $("#tCancel").onclick = () => safe(() => api("/timer", { cancel: true }), "Timer cancelled");
  $("#nTest").onclick = () => safe(() => api("/notify", { color: hex($("#nColor").value), flashes: 3, period: 0.35 }));
}
function renderTimer() {
  const t = S.status && S.status.timer;
  $("#tStatus").textContent = t ? `${t.label ? t.label + ": " : ""}${Math.floor(t.remaining_s / 60)}:${String(Math.floor(t.remaining_s % 60)).padStart(2, "0")} left`
    : "The number row fills up as time passes; the board flashes when it's done.";
}

// ------------------------------------------------------------------ app profiles tab
function effectOptions(sel) {
  return [`<option value="${NO_EFFECT}" ${!sel ? "selected" : ""}>Keyboard's own effect</option>`]
    .concat(EFFECTS.map(e => `<option value="${e.id}" ${sel === e.id ? "selected" : ""}>${e.name}</option>`)).join("");
}
function appRow(r = { match: "", effect: "reactive", params: {} }) {
  const tr = document.createElement("tr");
  tr.innerHTML = `<td><input type="text" data-k="match" value="${(r.match || "").replace(/"/g, "&quot;")}" placeholder="e.g. spotify, code.exe, YouTube"></td>
    <td><select data-k="effect">${effectOptions(r.effect)}</select></td>
    <td><input type="text" data-k="params" value='${JSON.stringify(r.params || {}).replace(/'/g, "&#39;")}' title="effect options as JSON"></td>
    <td><button class="x" title="remove">✕</button></td>`;
  $(".x", tr).onclick = () => tr.remove();
  $("#appsRows").appendChild(tr);
}
function appsConfig() {
  const rules = [];
  for (const tr of $$("#appsRows tr")) {
    const m = $("[data-k=match]", tr).value.trim(); if (!m) continue;
    let params = {}; try { params = JSON.parse($("[data-k=params]", tr).value || "{}"); } catch (e) { throw new Error(`Options for "${m}" are not valid JSON`); }
    rules.push({ match: m, effect: $("[data-k=effect]", tr).value || null, params });
  }
  const d = $("#appsDefault").value;
  return { enabled: $("#appsOn").checked, rules, default: d ? { effect: d, params: {} } : null };
}
function initApps() {
  const a = S.settings.apps || { rules: [] };
  $("#appsRows").innerHTML = ""; (a.rules || []).forEach(appRow);
  $("#appsDefault").innerHTML = effectOptions(a.default ? a.default.effect : null);
  $("#appsOn").checked = !!a.enabled;
  $("#appsAdd").onclick = () => appRow();
  $("#appsPick").onclick = () => {
    toast("Click the app you want within 3 seconds…");
    setTimeout(async () => {
      const f = await api("/foreground");
      appRow({ match: f.name.toLowerCase(), effect: "reactive", params: {} });
      toast(`Added ${f.name}`); window.focus();
    }, 3000);
  };
  const save = () => safe(async () => {
    const cfg = appsConfig(); const r = await api("/apps", cfg); S.settings.apps = cfg; return r;
  }, "App profiles saved");
  $("#appsSave").onclick = save;
  $("#appsOn").onchange = save;
}

// ------------------------------------------------------------------ settings tab
async function initSettings() {
  const a = await api("/autostart");
  $("#sAuto").checked = a.enabled;
  $("#sAuto").onchange = () => safe(() => api("/autostart", { enabled: $("#sAuto").checked }),
    $("#sAuto").checked ? "Will start with Windows" : "Autostart removed");
  $("#sTray").checked = S.settings.close_to_tray !== false;
  $("#sTray").onchange = () => safe(() => api("/settings", { close_to_tray: $("#sTray").checked }));
  const mark = () => $$("#sStartup button").forEach(b => b.classList.toggle("on", b.dataset.v === S.settings.startup));
  $$("#sStartup button").forEach(b => b.onclick = () => safe(async () => {
    S.settings = await api("/settings", { startup: b.dataset.v }); mark();
  }));
  mark();
  $("#sRestore").onclick = async () => {
    if (!(await modal("Restore original lighting?", "Puts back the custom colours and effect that were on the keyboard before this app first changed them (WASD + arrows preset).",
      [["Cancel", false], ["Restore", true, "primary"]]))) return;
    await safe(() => api("/restore", {}), "Restored"); await loadInfo(); S.lastFrameAt = 0;
  };
}
function renderDevInfo() {
  const i = S.info || {};
  $("#devInfo").innerHTML = [["Model", "AJAZZ AK820 (wired RGB)"], ["USB id", "320F:505B · firmware V0103"],
    ["Protocol", "EVision V2 · 128 LED slots"], ["Stored effect", modeLabel(i.mode)], ["Brightness", `${i.brightness}/4`],
    ["Speed", `${4 - (i.speed ?? 0)}/4`], ["Colour", "#" + (i.color || "")], ["Custom layout", `group ${(S.settings.custom_active || 0) + 1}`]]
    .map(([k, v]) => `<div>${k}</div><div>${v}</div>`).join("");
}

// ------------------------------------------------------------------ profiles (sidebar)
function renderProfiles(selName) {
  const ps = S.settings.profiles || [];
  $("#profileSel").innerHTML = `<option value="">${ps.length ? "Choose a profile…" : "No profiles yet"}</option>` +
    ps.map((p, i) => `<option value="${i}" ${p.name === selName ? "selected" : ""}>${p.name}</option>`).join("");
  $("#profileName").textContent = selName || "no profile";
}
async function captureProfile(name) {
  const info = await api("/info"), st = await api("/status");
  const p = { name, hardware: { mode: info.mode_id, brightness: info.brightness, speed: info.speed, color: info.color,
    direction: info.direction, random: !!info.random, colorset: info.colorset } };
  if (info.mode_id === 20) p.custom = await api(`/custom?colorset=0`);
  if (st.live && st.effect && !st.apps && !(st.effect === "solid" && S.dirty)) p.live = { effect: st.effect, params: st.params || {} };
  return p;
}
async function applyProfile(p) {
  await api("/stop", {});
  if (p.custom) await api("/custom", { colors: p.custom, colorset: 0, activate: false });
  await api("/effect", { ...p.hardware, colorset: p.hardware.mode === 20 ? 0 : p.hardware.colorset });
  if (p.live) await api("/run", p.live);
  await loadInfo(); S.lastFrameAt = 0; setDirty(false);
  if (S.tab === "custom") await loadCustom();
}
function initProfiles() {
  renderProfiles();
  $("#profileSel").onchange = () => {
    const p = S.settings.profiles[+$("#profileSel").value]; if (!p) return;
    safe(() => applyProfile(p), `Profile "${p.name}" applied`).then(() => ($("#profileName").textContent = p.name));
  };
  $("#profileAdd").onclick = async () => {
    const name = await modal("Save current lighting as a profile", '<input class="inp" placeholder="Profile name" value="profile ' + ((S.settings.profiles || []).length + 1) + '">',
      [["Cancel", null], ["Save", "__input", "primary"]]);
    if (!name) return;
    const p = await safe(() => captureProfile(name.trim()));
    const ps = (S.settings.profiles || []).filter(x => x.name !== p.name).concat([p]);
    S.settings = await safe(() => api("/settings", { profiles: ps }), `Saved profile "${p.name}"`);
    renderProfiles(p.name);
  };
  $("#profileMenu").onclick = async () => {
    const i = $("#profileSel").value; if (i === "") return toast("Choose a profile first");
    const p = S.settings.profiles[+i];
    const act = await modal(`Profile "${p.name}"`, '<input class="inp" value="' + p.name.replace(/"/g, "&quot;") + '">',
      [["Delete", "delete"], ["Update with current lighting", "update"], ["Rename", "rename", "primary"]]);
    let ps = [...S.settings.profiles];
    if (act === "delete") ps.splice(+i, 1);
    else if (act === "update") ps[+i] = await captureProfile(p.name);
    else if (act === "rename") ps[+i] = { ...p, name: ($("#mBody input").value || p.name).trim() };
    else return;
    S.settings = await safe(() => api("/settings", { profiles: ps }), "Profiles updated");
    renderProfiles(act === "delete" ? null : ps[+i].name);
  };
}

// ------------------------------------------------------------------ tabs + boot
function showTab(t) {
  S.tab = t;
  $$("#nav button").forEach(b => b.classList.toggle("active", b.dataset.tab === t));
  $$(".tab").forEach(x => x.classList.toggle("active", x.id === "tab-" + t));
  paintSelection();
  S.lastFrameAt = 0;
  if (t === "custom" && !S.dirty) loadCustom().catch(() => { });
  if (t !== "custom") S.custom = S.dirty ? S.custom : {};
  if (t === "light") loadInfo().catch(() => { });
}
async function boot() {
  [S.layout, S.modes, S.settings] = await Promise.all([fetch("ui/layout.json").then(r => r.json()), api("/modes"), api("/settings")]);
  buildBoard(); initLight(); initCustom(); initLive(); initApps(); initProfiles(); await initAutomation();
  await initSettings();
  $$("#nav button").forEach(b => b.onclick = () => showTab(b.dataset.tab));
  $("#conflictPill").onclick = () => showTab("settings");
  await loadInfo().catch(e => toast(e.message, true));
  statusLoop(); conflictLoop(); previewLoop();
  if (location.hash.length > 1) showTab(location.hash.slice(1));
}
boot().catch(e => toast("Start-up failed: " + e.message, true));
