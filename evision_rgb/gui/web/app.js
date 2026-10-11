"use strict";
/* EVision RGB — UI logic. Talks to the local API served by the same process. */

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const S = {
  layout: null, modes: [], settings: null, info: null, status: null,
  custom: {}, sel: new Set(), brush: "ff0000", group: 0, dirty: false,
  tab: "light", keyEls: {}, frameBusy: false, lastFrameAt: 0, hwBrushColor: "ff0000",
  cHist: null, layers: [], lySel: null, lyHist: null, liveView: "cards", profileName: null,
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
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
// profile names: same rules as the server applies to imported ones
const cleanName = s => String(s || "").replace(/[<>\u0000-\u001f\u007f]/g, "").replace(/\s+/g, " ").trim().slice(0, 60);

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
  // selection: custom tab = keys to paint (click paints), layer editor = where the selected layer shows
  let start = null, moved = false, additive = false, base = null;
  board.addEventListener("mousedown", e => {
    const mode = boardMode();
    if (!mode) return;
    const r = board.getBoundingClientRect();
    start = { x: e.clientX - r.left, y: e.clientY - r.top, mode }; moved = false; additive = e.shiftKey || e.ctrlKey;
    base = mode === "custom" ? new Set(S.sel) : maskSet(S.layers[S.lySel]);
  });
  window.addEventListener("mousemove", e => {
    if (!start) return;
    const r = board.getBoundingClientRect(), x = e.clientX - r.left, y = e.clientY - r.top;
    if (!moved && Math.hypot(x - start.x, y - start.y) < 5) return;
    moved = true;
    const sb = $("#selbox"), L = Math.min(x, start.x), T = Math.min(y, start.y);
    Object.assign(sb.style, { display: "block", left: L + "px", top: T + "px", width: Math.abs(x - start.x) + "px", height: Math.abs(y - start.y) + "px" });
    const box = sb.getBoundingClientRect(), boxed = new Set();
    for (const [n, el] of Object.entries(S.keyEls)) {
      const kr = el.getBoundingClientRect();
      if (kr.right > box.left && kr.left < box.right && kr.bottom > box.top && kr.top < box.bottom) boxed.add(n);
    }
    const next = additive ? new Set([...base, ...boxed]) : boxed;
    if (start.mode === "custom") S.sel = next; else setMask(S.layers[S.lySel], next);
    paintSelection();
  });
  window.addEventListener("mouseup", e => {
    if (!start) return;
    $("#selbox").style.display = "none";
    const mode = start.mode, key = e.target instanceof Element ? e.target.closest(".key") : null;
    if (!moved && key) {
      const n = key.dataset.name;
      if (mode === "mask") {
        const m = maskSet(S.layers[S.lySel]);
        m.has(n) ? m.delete(n) : m.add(n);
        setMask(S.layers[S.lySel], m);
      } else if (additive) { S.sel.has(n) ? S.sel.delete(n) : S.sel.add(n); }
      else { S.sel = new Set([n]); setCustom({ [n]: S.brush }); }
      paintSelection();
    }
    if (mode === "mask" && (moved || key)) lyCommit();
    start = null;
  });
}
function boardMode() {
  if (S.tab === "custom") return "custom";
  if (S.tab === "live" && S.liveView === "layers" && S.layers[S.lySel]) return "mask";
  return null;
}
function colorKeys(colors) {
  for (const [n, el] of Object.entries(S.keyEls)) {
    const c = hex(colors[n] || "000000");
    el.classList.toggle("off", c === "000000");
    el.style.setProperty("--c", "#" + c);
  }
}
function paintSelection() {
  const mode = boardMode(), mask = mode === "mask" ? maskSet(S.layers[S.lySel]) : null;
  for (const [n, el] of Object.entries(S.keyEls)) {
    el.classList.toggle("sel", mode === "custom" && S.sel.has(n));
    el.classList.toggle("msk", !!mask && mask.has(n));
  }
}

// preview: a 60 fps Server-Sent-Events stream mirrors the keyboard; polling is the fallback
function showFrame(f) {
  if (S.tab === "custom" && (S.dirty || Object.keys(S.custom).length)) return;  // editor shows local colours
  colorKeys(f.colors);
  S.animated = !!f.animated;
  const now = Date.now();
  if (now - (S.captionAt || 0) > 250) { setCaption(f); S.captionAt = now; }
}
function startStream() {
  if (!window.EventSource) return previewLoop();
  const es = new EventSource("/stream");
  es.onmessage = e => { try { showFrame(JSON.parse(e.data)); } catch (err) { } };
  es.onerror = () => { es.close(); setTimeout(startStream, 1500); };  // reconnect (app restarting, …)
  const local = () => { if (S.tab === "custom" && (S.dirty || Object.keys(S.custom).length)) colorKeys(S.custom); setTimeout(local, 200); };
  local();
}
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
    markLayersRunning();
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
  $("#hwVariantRow").style.display = i.mode_id === 17 ? "" : "none";
  $$("#hwVariant button").forEach(b => b.classList.toggle("on", +b.dataset.v === i.variant));
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
  if (body.mode === 17) body.variant = +(($("#hwVariant button.on") || {}).dataset?.v ?? 3);
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
  $$("#hwVariant button").forEach(b => b.onclick = () => { $$("#hwVariant button").forEach(x => x.classList.remove("on")); b.classList.add("on"); pushEffect(); });
  sel.addEventListener("change", () => ($("#hwVariantRow").style.display = +sel.value === 17 ? "" : "none"));
  buildPalette($("#hwPalette"), c => {
    S.hwBrushColor = c; $("#hwChip").style.background = "#" + c; $("#hwHex").textContent = "#" + c;
    $("#hwRandom").checked = false;  // choosing a colour means "use this colour" (random overrides it)
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
  if (S.cHist) S.cHist.push(S.custom);
  markUndo();
  $("#caption").innerHTML = "<b>Preview</b> of unsaved custom colours — Save to keep them";
}
function customStep(redo) {
  const s = S.cHist && (redo ? S.cHist.redo() : S.cHist.undo());
  if (!s) return;
  S.custom = s; setDirty(true); colorKeys(S.custom); pushPreview(); markUndo();
  $("#caption").innerHTML = "<b>Preview</b> of unsaved custom colours — Save to keep them";
}
function markUndo() {
  const can = (h, m) => !!(h && h[m]());
  $("#cUndo").disabled = !can(S.cHist, "canUndo"); $("#cRedo").disabled = !can(S.cHist, "canRedo");
  $("#lyUndo").disabled = !can(S.lyHist, "canUndo"); $("#lyRedo").disabled = !can(S.lyHist, "canRedo");
}
async function loadCustom(fromKeyboard = false) {
  const saved = (S.settings.custom_groups || [])[S.group];
  const active = (S.settings.custom_active || 0) === S.group;
  // the keyboard only displays memory area 0; groups are layouts saved in this app
  S.custom = (!fromKeyboard && saved) ? { ...saved } : await api(`/custom?colorset=${active || fromKeyboard ? 0 : S.group}`);
  S.cHist = EVHistory.create(S.custom); markUndo();
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
  $("#cUndo").onclick = () => customStep(false);
  $("#cRedo").onclick = () => customStep(true);
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
// one parameter control; `val` = current value (undefined: the default)
function paramHtml([k, label, type, a, b, step, def, inv], val) {
  if (type === "range") {
    const v = val ?? def;
    return `<div class="p"><span>${label}</span><input type="range" data-p="${k}" min="${a}" max="${b}" step="${step}" value="${inv ? +(1.5 - v).toFixed(2) : v}" ${inv ? 'data-inv="1"' : ""}><output>${v}</output></div>`;
  }
  if (type === "color") return `<div class="p"><span>${label}</span><input type="color" data-p="${k}" value="#${hex(val ?? a)}"><output></output></div>`;
  if (type === "colorOrRainbow") {
    const rb = val == null;
    return `<div class="p"><span>${label}</span><input type="color" data-p="${k}" value="#${hex(val || "ffffff")}" ${rb ? "disabled" : ""}><label class="row" style="gap:4px"><input type="checkbox" data-rainbow ${rb ? "checked" : ""}>🌈</label></div>`;
  }
  if (type === "check") return `<div class="p"><span>${label}</span><label class="toggle"><input type="checkbox" data-p="${k}" ${(val ?? a) ? "checked" : ""}><span></span></label><output></output></div>`;
  if (type === "select") return `<div class="p"><span>${label}</span><select data-p="${k}">${a.map(o => `<option ${o === (val ?? b) ? "selected" : ""}>${o}</option>`).join("")}</select><output></output></div>`;
  return "";
}
function readParams(el, effect) {
  const out = {};
  for (const inp of $$("[data-p]", el)) {
    const k = inp.dataset.p;
    if (inp.type === "checkbox") out[k] = inp.checked;
    else if (inp.type === "range") out[k] = inp.dataset.inv ? +(1.5 - +inp.value).toFixed(2) : +inp.value;
    else if (inp.type === "color") { if (!inp.disabled) out[k] = hex(inp.value); }
    else out[k] = inp.value;
  }
  if (out.color === undefined && effect === "reactive") out.color = null;
  return out;
}
function wireParams(el, onInput, onChange) {
  for (const inp of $$("input,select", el)) {
    const rainbow = inp.matches("[data-rainbow]");  // must update the colour input before onChange runs
    inp.addEventListener(rainbow ? "change" : "input", () => {
      if (rainbow) $("[data-p=color]", el).disabled = inp.checked;
      const o = inp.parentElement.querySelector("output");
      if (inp.type === "range" && o) o.textContent = inp.dataset.inv ? (1.5 - +inp.value).toFixed(2) : inp.value;
      onInput();
    });
    if (onChange) inp.addEventListener("change", onChange);
  }
}
function cardParams(card) { return readParams(card, card.dataset.effect); }
function initLive() {
  const wrap = $("#cards");
  wrap.innerHTML = EFFECTS.map(e => `<div class="card" data-effect="${e.id}">
    <h3>${e.name}<button class="btn primary" data-start>Start</button></h3><div class="desc">${e.desc}</div>
    ${e.p.map(p => paramHtml(p)).join("")}</div>`).join("");
  for (const card of $$(".card", wrap)) {
    const eff = card.dataset.effect;
    const start = () => safe(() => api("/run", { effect: eff, params: cardParams(card) }));
    $("[data-start]", card).onclick = () => start().then(() => toast(`${eff} running`));
    wireParams(card, debounce(() => { if (S.status && S.status.effect === eff) start(); }, 250));
  }
  $("#liveStop").onclick = () => safe(() => api("/stop", {}), "Back to the keyboard's own effect").then(() => (S.lastFrameAt = 0));
}

// ------------------------------------------------------------------ layer editor (Live tab)
const KEYBOARD_LAYER = { id: "keyboard", name: "Keyboard's own effect",
  desc: "The built-in effect stored on the keyboard (choose it in Light Settings). Put live layers on top of it.", p: [] };
const BLENDS = [["normal", "Normal"], ["screen", "Screen (lighten)"], ["add", "Add"], ["max", "Brightest wins"], ["multiply", "Multiply (tint)"]];
const AUTO_BLEND = { reactive: "screen", music: "max", sysmon: "max" };  // sensible default when added on top
const MAX_LAYERS = 16;
const layerDef = id => id === "keyboard" ? KEYBOARD_LAYER : EFFECTS.find(e => e.id === id) || { id, name: id, desc: "", p: [] };
const allKeys = () => S.layout.keys.map(k => k.name);
function maskSet(L) { return new Set(!L ? [] : L.keys == null ? allKeys() : L.keys); }
function setMask(L, set) {
  const all = allKeys();
  if (all.every(n => set.has(n))) delete L.keys;
  else L.keys = all.filter(n => set.has(n));
}
function maskLabel(L) {
  if (L.keys == null) return "every key";
  if (!L.keys.length) return "no keys";
  return L.keys.length <= 6 ? L.keys.join(" ") : `${L.keys.length} keys`;
}
function keyRows() {  // key names per physical row, top to bottom
  const rows = [];
  for (const k of [...S.layout.keys].sort((a, b) => a.y - b.y)) {
    const r = rows[rows.length - 1];
    if (r && k.y - r.y < 14) r.keys.push(k.name); else rows.push({ y: k.y, keys: [k.name] });
  }
  return rows.map(r => r.keys);
}
function defaultsFor(id) {
  const out = {};
  for (const [k, , type, a, b, , def] of layerDef(id).p) {
    if (type === "range") out[k] = def;
    else if (type === "color" || type === "check") out[k] = a;
    else if (type === "select") out[k] = b;  // colorOrRainbow: left out = rainbow
  }
  return out;
}
const LAYER_TEMPLATES = [
  ["Keyboard effect + red WASD", () => [{ effect: "keyboard" },
    { effect: "solid", params: { color: "ff0000" }, keys: ["W", "A", "S", "D", "Up", "Down", "Left", "Right"] }]],
  ["Rainbow + typing ripples", () => [{ effect: "rainbow", params: { speed: 0.25, scale: 1, angle: 0, brightness: 0.5 } },
    { effect: "reactive", params: { base: "000000", color: null, fade: 0.6, ripple_speed: 1.2, ripple: true }, blend: "screen" }]],
  ["Keyboard effect + music meter on the number row", () => [{ effect: "keyboard" },
    { effect: "music", params: { style: "vu", fall: 1.2, scale: 0.8, hue_speed: 0.03 }, keys: keyRows()[1], blend: "max" }]],
  ["Rainbow + CPU / RAM bars", () => [{ effect: "rainbow", params: { speed: 0.12, scale: 1, angle: 0, brightness: 0.35 } },
    { effect: "sysmon", params: { base: "000000" }, keys: [...keyRows()[0], ...keyRows()[1]], blend: "max" }]],
];
const saveLayerDraft = debounce(() => api("/settings", { layers_draft: S.layers }).catch(() => { }), 700);
const layersRunning = () => !!(S.status && S.status.effect === "layers" && !S.status.apps);
const lyLive = debounce(() => { if (layersRunning()) runLayers(false).catch(() => { }); }, 250);
async function runLayers(announce = true) {
  if (!S.layers.length) return toast("Add a layer first", true);
  await safe(() => api("/run", { effect: "layers", params: { layers: S.layers } }));
  S.status = { ...(S.status || {}), live: true, effect: "layers", apps: null };
  markLayersRunning();
  if (announce) toast("Layers running — changes apply live");
}
function markLayersRunning() { $("#lyStart").textContent = layersRunning() ? "● Layers running" : "Start layers"; }
// after every edit: remember it for undo, redraw, save the draft, update the keyboard if the stack runs
function lyCommit({ structural = true, record = true } = {}) {
  if (record && S.lyHist) S.lyHist.push(S.layers);
  if (structural) renderLayers();
  paintSelection(); markUndo(); saveLayerDraft(); lyLive();
}
function lyStep(redo) {
  const s = S.lyHist && (redo ? S.lyHist.redo() : S.lyHist.undo());
  if (!s) return;
  S.layers = s; renderLayers(); paintSelection(); markUndo(); saveLayerDraft(); lyLive();
}
function renderLayers() {
  if (S.lySel != null && !S.layers[S.lySel]) S.lySel = S.layers.length ? S.layers.length - 1 : null;
  const list = $("#lyList");
  list.innerHTML = S.layers.map((L, i) => [L, i]).reverse().map(([L, i]) => {
    const d = layerDef(L.effect), on = L.enabled !== false, op = L.opacity ?? 1;
    return `<div class="layer${i === S.lySel ? " sel" : ""}${on ? "" : " off"}" data-i="${i}">
      <div class="lhead" title="Click to edit this layer">
        <label class="toggle" title="Show this layer"><input type="checkbox" data-l="enabled" ${on ? "checked" : ""}><span></span></label>
        <div class="lname"><b>${esc(L.name || d.name)}</b><span class="hint">${esc(maskLabel(L))}</span></div>
        <select data-l="blend" title="How this layer mixes with the layers below">${BLENDS.map(([v, t]) =>
          `<option value="${v}" ${(L.blend || "normal") === v ? "selected" : ""}>${t}</option>`).join("")}</select>
        <input type="range" data-l="opacity" min="0" max="1" step="0.05" value="${op}" title="Opacity"><output>${Math.round(op * 100)}%</output>
        <button class="x" data-act="up" title="Move up" ${i === S.layers.length - 1 ? "disabled" : ""}>▲</button>
        <button class="x" data-act="down" title="Move down" ${i === 0 ? "disabled" : ""}>▼</button>
        <button class="x" data-act="del" title="Remove layer">✕</button>
      </div>
      ${i === S.lySel ? `<div class="lbody">
        <div class="hint">${esc(d.desc)}</div>
        <div class="lparams">${d.p.map(p => paramHtml(p, (L.params || {})[p[0]])).join("")}</div>
        <div class="row"><span class="hint">Shows on <b>${esc(maskLabel(L))}</b> · click / drag keys above</span>
          <button class="btn" data-mask="all">Every key</button><button class="btn" data-mask="none">No keys</button>
          <button class="btn" data-mask="invert">Invert</button></div>
      </div>` : ""}
    </div>`;
  }).join("") || `<div class="hint">No layers yet — add one above or start from a template.</div>`;
  for (const row of $$(".layer", list)) {
    const i = +row.dataset.i, L = S.layers[i];
    $(".lhead", row).addEventListener("click", e => {
      if (e.target.closest("input,select,button,label")) return;
      S.lySel = S.lySel === i ? null : i; renderLayers(); paintSelection();
    });
    $("[data-l=enabled]", row).onchange = e => { if (e.target.checked) delete L.enabled; else L.enabled = false; lyCommit(); };
    $("[data-l=blend]", row).onchange = e => { if (e.target.value === "normal") delete L.blend; else L.blend = e.target.value; lyCommit(); };
    const op = $("[data-l=opacity]", row);
    op.oninput = () => {
      const v = +op.value;
      if (v >= 1) delete L.opacity; else L.opacity = v;
      op.nextElementSibling.textContent = Math.round(v * 100) + "%";
      lyCommit({ structural: false, record: false });
    };
    op.onchange = () => lyCommit({ structural: false });
    for (const b of $$("[data-act]", row)) b.onclick = () => {
      if (b.dataset.act === "del") {
        S.layers.splice(i, 1);
        S.lySel = S.lySel === i ? null : (S.lySel > i ? S.lySel - 1 : S.lySel);
      } else {
        const j = b.dataset.act === "up" ? i + 1 : i - 1;
        if (j < 0 || j >= S.layers.length) return;
        [S.layers[i], S.layers[j]] = [S.layers[j], S.layers[i]];
        S.lySel = S.lySel === i ? j : (S.lySel === j ? i : S.lySel);
      }
      lyCommit();
    };
    const body = $(".lbody", row);
    if (!body) continue;
    wireParams(body, () => { L.params = readParams($(".lparams", body), L.effect); lyCommit({ structural: false, record: false }); },
      () => lyCommit({ structural: false }));
    for (const b of $$("[data-mask]", body)) b.onclick = () => {
      const m = b.dataset.mask, cur = maskSet(L);
      if (m === "all") delete L.keys;
      else if (m === "none") L.keys = [];
      else setMask(L, new Set(allKeys().filter(n => !cur.has(n))));
      lyCommit();
    };
  }
}
function setLiveView(v) {
  S.liveView = v === "layers" ? "layers" : "cards";
  $$("#liveView button").forEach(b => b.classList.toggle("on", b.dataset.v === S.liveView));
  $("#cards").style.display = S.liveView === "cards" ? "" : "none";
  $("#layerEditor").style.display = S.liveView === "layers" ? "" : "none";
  try { localStorage.setItem("evrgb.liveView", S.liveView); } catch (e) { }
  paintSelection();
}
function setLayers(layers, select = null) {
  S.layers = JSON.parse(JSON.stringify(layers));
  S.lySel = select;
  lyCommit();
}
function initLayers() {
  const s = S.settings, last = s.last_live;
  const fromLast = last && last.effect === "layers" && last.params && Array.isArray(last.params.layers) ? last.params.layers : null;
  S.layers = JSON.parse(JSON.stringify(Array.isArray(s.layers_draft) && s.layers_draft.length ? s.layers_draft
    : fromLast || LAYER_TEMPLATES[0][1]()));
  S.lyHist = EVHistory.create(S.layers);
  $("#lyAdd").innerHTML = `<option value="">+ Add layer…</option>` +
    [KEYBOARD_LAYER, ...EFFECTS].map(e => `<option value="${e.id}">${esc(e.name)}</option>`).join("");
  $("#lyAdd").onchange = () => {
    const id = $("#lyAdd").value; $("#lyAdd").value = "";
    if (!id) return;
    if (S.layers.length >= MAX_LAYERS) return toast(`At most ${MAX_LAYERS} layers`, true);
    const L = { effect: id }, p = defaultsFor(id);
    if (S.layers.length && AUTO_BLEND[id]) { L.blend = AUTO_BLEND[id]; if ("base" in p) p.base = "000000"; }
    if (Object.keys(p).length) L.params = p;
    S.layers.push(L); S.lySel = S.layers.length - 1; lyCommit();
  };
  $("#lyTemplate").innerHTML = `<option value="">Templates…</option>` +
    LAYER_TEMPLATES.map(([n], i) => `<option value="${i}">${esc(n)}</option>`).join("");
  $("#lyTemplate").onchange = () => {
    const i = $("#lyTemplate").value; $("#lyTemplate").value = "";
    if (i !== "") setLayers(LAYER_TEMPLATES[+i][1](), 1);
  };
  $("#lyUndo").onclick = () => lyStep(false);
  $("#lyRedo").onclick = () => lyStep(true);
  $("#lyStart").onclick = () => runLayers().catch(() => { });
  $("#lySave").onclick = () => saveProfileFlow({ effect: "layers", params: { layers: S.layers } });
  $$("#liveView button").forEach(b => b.onclick = () => setLiveView(b.dataset.v));
  let v = "cards";
  try { v = localStorage.getItem("evrgb.liveView") || "cards"; } catch (e) { }
  setLiveView(v);
  renderLayers(); markUndo();
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
  const hm = h => {
    $("#hmOn").checked = h.enabled;
    $("#hmStats").textContent = h.total ? `${h.total.toLocaleString()} presses since ${h.since} · most used: ${h.top.slice(0, 5).map(t => t[0]).join(", ")}`
      : (h.enabled ? "Counting… type something." : "");
  };
  hm(await api("/heatmap"));
  $("#hmOn").onchange = () => safe(async () => hm(await api("/heatmap", { enabled: $("#hmOn").checked })));
  $("#hmShow").onclick = () => safe(() => api("/run", { effect: "heatmap" }), "Heatmap on the keyboard (Stop live to return)");
  $("#hmClear").onclick = async () => {
    if (await modal("Clear the heatmap?", "All key counts are deleted.", [["Cancel", false], ["Clear", true, "primary"]]))
      hm(await api("/heatmap", { clear: true }));
  };
  setInterval(async () => { if (S.tab === "auto") hm(await api("/heatmap").catch(() => ({ enabled: false, total: 0 }))); }, 5000);
}
function renderTimer() {
  const t = S.status && S.status.timer;
  $("#tStatus").textContent = t ? `${t.label ? t.label + ": " : ""}${Math.floor(t.remaining_s / 60)}:${String(Math.floor(t.remaining_s % 60)).padStart(2, "0")} left`
    : "The number row fills up as time passes; the board flashes when it's done.";
}

// ------------------------------------------------------------------ app profiles tab
function effectOptions(sel) {
  return [`<option value="${NO_EFFECT}" ${!sel ? "selected" : ""}>Keyboard's own effect</option>`]
    .concat(EFFECTS.map(e => `<option value="${e.id}" ${sel === e.id ? "selected" : ""}>${e.name}</option>`))
    .concat([`<option value="layers" ${sel === "layers" ? "selected" : ""}>Layer stack</option>`]).join("");
}
function appRow(r = { match: "", effect: "reactive", params: {} }) {
  const tr = document.createElement("tr");
  tr.innerHTML = `<td><input type="text" data-k="match" value="${esc(r.match || "")}" placeholder="e.g. spotify, code.exe, YouTube"></td>
    <td><select data-k="effect">${effectOptions(r.effect)}</select></td>
    <td><input type="text" data-k="params" value="${esc(JSON.stringify(r.params || {}))}" title="effect options as JSON"></td>
    <td><button class="x" title="remove">✕</button></td>`;
  $(".x", tr).onclick = () => tr.remove();
  $("[data-k=effect]", tr).onchange = e => {  // a layer stack rule starts as a copy of the layer editor's stack
    if (e.target.value === "layers") $("[data-k=params]", tr).value = JSON.stringify({ layers: S.layers });
  };
  $("#appsRows").appendChild(tr);
}
function appsConfig() {
  const rules = [];
  for (const tr of $$("#appsRows tr")) {
    const m = $("[data-k=match]", tr).value.trim(); if (!m) continue;
    let params = {}; try { params = JSON.parse($("[data-k=params]", tr).value || "{}"); } catch (e) { throw new Error(`Options for "${m}" are not valid JSON`); }
    rules.push({ match: m, effect: $("[data-k=effect]", tr).value || null, params });
  }
  const d = $("#appsDefault").value, old = (S.settings.apps || {}).default;
  const dParams = d === "layers" ? (old && old.effect === "layers" ? old.params : { layers: S.layers }) : {};
  return { enabled: $("#appsOn").checked, rules, default: d ? { effect: d, params: dParams } : null };
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
  S.profileName = selName && ps.some(p => p.name === selName) ? selName : null;
  $("#profileSel").innerHTML = `<option value="">${ps.length ? "Profiles…" : "No profiles"}</option>` +
    ps.map((p, i) => `<option value="${i}" ${p.name === S.profileName ? "selected" : ""}>${esc(p.name)}</option>`).join("");
  $("#profileName").textContent = S.profileName || "no profile";
}
async function captureProfile(name, live) {
  const info = await api("/info"), st = await api("/status");
  const hw = { mode: info.mode_id, brightness: info.brightness, speed: info.speed, color: info.color,
    direction: info.direction, random: !!info.random };
  if (info.mode_id === 20) hw.colorset = 0;
  else hw.variant = Math.min(3, info.variant || 0);  // byte 18 = effect style outside custom mode (Surmount presets)
  const p = { name, hardware: hw };
  if (info.mode_id === 20) p.custom = await api(`/custom?colorset=0`);
  if (live) p.live = live;
  else if (st.live && st.effect && !st.apps && st.effect !== "emulate" && !(st.effect === "solid" && S.dirty))
    p.live = { effect: st.effect, params: st.params || {} };
  return p;
}
async function applyProfile(p) {
  const r = await api("/profiles/apply", { name: p.name });  // one step on the server: no flash of the old lighting
  S.status = { ...(S.status || {}), ...r.live, apps: null };
  if (p.live && p.live.effect === "layers" && Array.isArray((p.live.params || {}).layers)) setLayers(p.live.params.layers);
  markLayersRunning();
  await loadInfo(); S.lastFrameAt = 0; setDirty(false);
  if (S.tab === "custom") await loadCustom();
}
async function saveProfileFlow(live) {
  const name = await modal("Save current lighting as a profile", '<input class="inp" placeholder="Profile name" value="profile ' + ((S.settings.profiles || []).length + 1) + '">',
    [["Cancel", null], ["Save", "__input", "primary"]]);
  if (!cleanName(name)) return;
  const p = await safe(() => captureProfile(cleanName(name), live));
  const ps = (S.settings.profiles || []).filter(x => x.name !== p.name).concat([p]);
  S.settings = await safe(() => api("/settings", { profiles: ps }), `Saved profile "${p.name}"`);
  renderProfiles(p.name);
}
const fileName = name => name.replace(/[\\/:*?"<>|]+/g, "_").trim() + ".evrgb.json";
async function exportProfile(p) {
  const doc = await api(`/profiles/export?name=${encodeURIComponent(p.name)}`);
  const text = JSON.stringify(doc, null, 2), file = fileName(p.name);
  const native = window.pywebview && window.pywebview.api && window.pywebview.api.save_text;
  if (native) {  // desktop app: native Save dialog
    const path = await native(file, text);
    if (path) toast(`Saved ${path}`);
    return;
  }
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([text], { type: "application/json" })); a.download = file; a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}
async function shareProfile(p) {
  const { code } = await api(`/profiles/export?name=${encodeURIComponent(p.name)}&as=code`);
  let copied = false;
  try { await navigator.clipboard.writeText(code); copied = true; } catch (e) { }
  await modal(`Share "${p.name}"`, `<p class="hint" style="margin-top:0">${copied ? "Copied to the clipboard. " : ""}Anyone with
    EVision RGB can import this code with the ⇩ button next to the profile list.</p>
    <textarea class="inp code" rows="5" readonly>${esc(code)}</textarea>`, [["Close", null, "primary"]]);
}
async function importFlow() {
  let fileText = null;
  const pending = modal("Import a profile", `<p class="hint" style="margin-top:0">Paste a share code (<code>evrgb1:…</code>) or choose a
    profile file. Nothing changes on the keyboard until you pick the profile in the list.</p>
    <textarea class="inp code" id="impCode" rows="4" placeholder="evrgb1:…"></textarea>
    <div class="row" style="margin-top:8px"><button class="btn" id="impPick">Choose file…</button><span class="hint" id="impFile"></span></div>`,
    [["Cancel", null], ["Import", "go", "primary"]]);
  const fi = $("#fileInput");
  fi.value = "";
  fi.onchange = async () => {
    const f = fi.files[0];
    if (!f) return;
    if (f.size > 512 * 1024) return toast("That file is too large to be a profile", true);
    fileText = await f.text(); $("#impFile").textContent = f.name;
  };
  $("#impPick").onclick = () => fi.click();
  $("#impCode").focus();
  if ((await pending) !== "go") return;
  const text = (fileText ?? $("#impCode").value).trim();
  if (!text) return toast("Paste a share code or choose a file first", true);
  let body;
  if (text.startsWith("{")) {
    try { body = JSON.parse(text); } catch (e) { return toast("That file is not a valid profile (JSON error)", true); }
  } else body = { code: text };
  const r = await safe(() => api("/profiles/import", body));
  S.settings = await api("/settings");
  renderProfiles(S.profileName);
  toast(`Imported "${r.name}". Choose it in the profile list to apply it.`);
}
function initProfiles() {
  renderProfiles();
  $("#profileSel").onchange = () => {
    const p = S.settings.profiles[+$("#profileSel").value]; if (!p) return;
    safe(() => applyProfile(p), `Profile "${p.name}" applied`).then(() => renderProfiles(p.name), () => renderProfiles(S.profileName));
  };
  $("#profileAdd").onclick = () => saveProfileFlow();
  $("#profileImport").onclick = () => importFlow().catch(() => { });
  $("#profileMenu").onclick = async () => {
    const i = $("#profileSel").value; if (i === "") return toast("Choose a profile first");
    const p = S.settings.profiles[+i];
    const act = await modal(`Profile "${p.name}"`, '<input class="inp" value="' + esc(p.name) + '">',
      [["Delete", "delete"], ["Export file", "export"], ["Share code", "share"], ["Update with current lighting", "update"],
       ["Rename", "rename", "primary"]]);
    if (act === "export") return safe(() => exportProfile(p)).catch(() => { });
    if (act === "share") return safe(() => shareProfile(p)).catch(() => { });
    let ps = [...S.settings.profiles];
    if (act === "delete") ps.splice(+i, 1);
    else if (act === "update") ps[+i] = await captureProfile(p.name);
    else if (act === "rename") ps[+i] = { ...p, name: cleanName($("#mBody input").value) || p.name };
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
// Ctrl+Z / Ctrl+Y (Ctrl+Shift+Z) undo and redo in the custom-key painter and the layer editor
document.addEventListener("keydown", e => {
  if (!(e.ctrlKey || e.metaKey) || e.altKey || $("#modal").classList.contains("show")) return;
  const t = e.target;
  if (t.tagName === "TEXTAREA" || (t.tagName === "INPUT" && !["range", "checkbox", "color", "button"].includes(t.type))) return;
  const k = e.key.toLowerCase(), redo = k === "y" || (k === "z" && e.shiftKey);
  if (k !== "z" && k !== "y") return;
  if (S.tab === "custom") customStep(redo);
  else if (S.tab === "live" && S.liveView === "layers") lyStep(redo);
  else return;
  e.preventDefault();
});
async function boot() {
  [S.layout, S.modes, S.settings] = await Promise.all([fetch("ui/layout.json").then(r => r.json()), api("/modes"), api("/settings")]);
  buildBoard(); initLight(); initCustom(); initLive(); initLayers(); initApps(); initProfiles(); await initAutomation();
  await initSettings();
  $$("#nav button").forEach(b => b.onclick = () => showTab(b.dataset.tab));
  $("#conflictPill").onclick = () => showTab("settings");
  await loadInfo().catch(e => toast(e.message, true));
  statusLoop(); conflictLoop(); startStream();
  if (location.hash.length > 1) {  // deep links: #custom, #live:layers, ...
    const [tab, view] = location.hash.slice(1).split(":");
    showTab(tab);
    if (tab === "live" && view) setLiveView(view);
  }
}
boot().catch(e => toast("Start-up failed: " + e.message, true));
