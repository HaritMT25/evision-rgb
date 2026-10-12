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
function editingCustom() { return S.tab === "custom" && (S.dirty || Object.keys(S.custom).length); }
function showFrame(f) {
  S.frame = f;  // a still picture is sent only once: keep it to redraw after the editor and for the caption
  if (editingCustom()) return;  // editor shows local colours
  colorKeys(f.colors);
  S.animated = !!f.animated;
  const now = Date.now();
  if (now - (S.captionAt || 0) > 250) { setCaption(f); S.captionAt = now; }
}
function startStream() {
  if (!window.EventSource) return previewLoop();
  S.es = null;
  const open = () => {
    if (S.es || document.hidden) return;  // window hidden in the tray: no 60 fps preview for nobody
    const es = S.es = new EventSource("/stream");
    es.onmessage = e => { try { showFrame(JSON.parse(e.data)); } catch (err) { } };
    es.onerror = () => { es.close(); if (S.es === es) S.es = null; setTimeout(open, 1500); };  // reconnect (app restarting, …)
  };
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) { if (S.es) S.es.close(); S.es = null; } else open();
  });
  open();
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
// a live effect's name as the app shows it ("nowplaying" -> "Album colours")
function effectLabel(id) {
  const names = { layers: "Layer stack", heatmap: "Typing heatmap", emulate: "Keyboard effect copy", keyboard: "Keyboard's own effect",
    cs2: "CS2 game lighting", dota2: "Dota 2 game lighting", openrgb: "OpenRGB program" };
  const e = EFFECTS.find(x => x.id === id);
  return e ? e.name : names[id] || modeLabel(id);
}
function setCaption(f) {
  const st = S.status || {};
  let t;
  if (S.tab === "custom" && S.dirty) t = "<b>Preview</b> of unsaved custom colours";
  else if (f.source === "live") t = `<b>Live:</b> ${esc(effectLabel(f.effect))} · ` + (st.idle ? "still (drawn less often to save CPU)" : `${st.fps || 0} fps`)
    + (st.apps ? ` · app profile: ${st.apps.foreground || "?"} → ${st.apps.rule}` : "");
  else if (f.source === "disconnected") t = "<b>Keyboard disconnected</b> · plug it in and it connects by itself";
  else if (f.source === "custom") t = `<b>Keyboard effect:</b> Custom (group ${(S.settings.custom_active || 0) + 1})`;
  else t = `<b>Keyboard effect:</b> ${modeLabel(f.mode)}` + (f.animated ? ' <span class="hint">— preview is an approximation of the keyboard\'s own animation</span>' : "");
  $("#caption").innerHTML = t;
}

// ------------------------------------------------------------------ status
async function statusLoop() {
  try {
    S.status = await api("/status");
    const dot = $("#statusDot");
    dot.className = "dot " + (S.status.keyboard === "disconnected" ? "bad" : S.status.live ? "live" : "ok");
    const bat = S.status.battery;
    $("#statusText").textContent = S.status.live ? `live: ${effectLabel(S.status.effect)}`
      : bat && bat.paused ? `battery saver: ${effectLabel(bat.paused)} paused` : "keyboard connected";
    $$(".card").forEach(c => c.classList.toggle("on", S.status.effect === c.dataset.effect && !S.status.apps));
    markLayersRunning();
    const err = S.status.stream_error || S.status.audio_error;
    if (S.status.keyboard === "disconnected") $("#statusText").textContent = "keyboard disconnected · reconnecting…";
    else if (err) $("#statusText").textContent += " · " + err.slice(0, 60);
    renderTimer();
    renderBattery();
    if (S.frame && Date.now() - (S.captionAt || 0) > 900) { setCaption(S.frame); S.captionAt = Date.now(); }
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
  { id: "nowplaying", name: "Album colours", desc: "Keys take the colours of the cover of whatever is playing (Spotify, YouTube…).", p: [
    ["style", "Style", "select", ["flow", "cover", "solid"], "flow"], ["speed", "Drift", "range", 0, 0.3, 0.01, 0.05],
    ["brightness", "Brightness", "range", 0.1, 1, 0.05, 1], ["paused", "When paused", "range", 0, 1, 0.05, 0.35]] },
  { id: "ambient", name: "Screen ambient", desc: "Keys take the colours of your screen — like a mini Ambilight.", p: [
    ["mode", "Mode", "select", ["map", "average"], "map"], ["saturation", "Vividness", "range", 1, 2.5, 0.1, 1.4],
    ["smooth", "Smoothing (s)", "range", 0, 1, 0.05, 0.25], ["brightness", "Brightness", "range", 0.1, 1, 0.05, 1]] },
  { id: "fire", name: "Fire", desc: "A flame in every column, rising from the bottom row.", p: [
    ["cooling", "Cooling", "range", 0, 1, 0.05, 0.55], ["sparking", "Sparks", "range", 0, 1, 0.05, 0.5],
    ["brightness", "Brightness", "range", 0.1, 1, 0.05, 1]] },
  { id: "ocean", name: "Ocean", desc: "Blue-green waves rolling across the keys, whitecaps where they meet.", p: [
    ["speed", "Speed", "range", 0.1, 3, 0.1, 1], ["brightness", "Brightness", "range", 0.1, 1, 0.05, 1]] },
  { id: "twinkle", name: "Twinkle", desc: "Keys light up and fade one by one, like stars.", p: [
    ["density", "How many", "range", 0.05, 1, 0.05, 0.4], ["speed", "Speed", "range", 0.1, 3, 0.1, 1],
    ["background", "Night sky", "color", "000000"], ["brightness", "Brightness", "range", 0.1, 1, 0.05, 1]] },
  { id: "matrix", name: "Digital rain", desc: "Green trails falling down the keys.", p: [
    ["density", "Rain", "range", 0.02, 1, 0.02, 0.15], ["speed", "Speed", "range", 0.2, 3, 0.1, 1],
    ["brightness", "Brightness", "range", 0.1, 1, 0.05, 1]] },
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
    ${e.id === "nowplaying" ? '<div class="np" id="npInfo"></div>' : ""}${e.p.map(p => paramHtml(p)).join("")}</div>`).join("");
  for (const card of $$(".card", wrap)) {
    const eff = card.dataset.effect;
    const start = () => safe(() => api("/run", { effect: eff, params: cardParams(card) }));
    $("[data-start]", card).onclick = () => start().then(() => toast(`${effectLabel(eff)} running`));
    wireParams(card, debounce(() => { if (S.status && S.status.effect === eff) start(); }, 250));
  }
  $("#liveStop").onclick = () => safe(() => api("/stop", {}), "Back to the keyboard's own effect").then(() => (S.lastFrameAt = 0));
  npLoop();
}
// what is playing, shown in the Album colours card while the Live tab is open
async function npLoop() {
  const el = $("#npInfo");
  if (el && S.tab === "live" && !document.hidden) {
    try {
      const n = await api("/nowplaying");
      if (!n.available) el.innerHTML = `<span class="hint">${esc(n.reason || "This PC cannot say what is playing.")}</span>`;
      else if (!n.title) el.innerHTML = '<span class="hint">Nothing is playing.</span>';
      else {
        if (S.npId !== n.id) {
          el.innerHTML = `<img alt="" src="/nowplaying/cover.png?id=${n.id}" onerror="this.style.display='none'"><div><b>${n.playing ? "▶" : "⏸"} ${esc(n.title)}</b>`
            + `<div class="hint">${esc(n.artist || "")}${n.app ? " · " + esc(n.app.replace(/\.exe$/i, "")) : ""}</div>`
            + `<div>${(n.palette || []).map(c => `<i class="sw" style="background:#${esc(c)}"></i>`).join("")}</div></div>`;
        }
      }
      S.npId = n.title ? n.id : null;
    } catch (e) { }
  }
  setTimeout(npLoop, 2000);
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
  $$("#tab-auto select[data-a]:not(.opts)").forEach(s => s.innerHTML = keyOpts);
  const fill = c => {
    for (const el of $$("#tab-auto [data-a]")) {
      const v = getPath(c, el.dataset.a);
      if (el.type === "checkbox") el.checked = !!v;
      else if (el.type === "color") el.value = "#" + v;
      else el.value = v == null ? "" : v;
      const o = $(`#tab-auto [data-o="${el.dataset.a}"]`); if (o) o.textContent = Math.round(v * 100) + "%";
    }
  };
  fill(cfg);
  const save = debounce(async () => {
    const patch = {};
    for (const el of $$("#tab-auto [data-a]")) {
      const [sec, key] = el.dataset.a.split(".");
      let v = el.type === "checkbox" ? el.checked : el.type === "color" ? hex(el.value)
        : (el.type === "range" || el.type === "number") ? +el.value
        : "num" in el.dataset ? (el.value === "" ? null : +el.value) : el.value;
      (patch[sec] = patch[sec] || {})[key] = v;
    }
    try {
      const c = await api("/automation", patch);
      fill(c);
      if (S.status && S.status.battery) { Object.assign(S.status.battery, { saver: c.battery.enabled, brightness: c.battery.brightness }); renderBattery(); }
    } catch (e) { toast(e.message, true); }
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
function renderBattery() {
  const b = S.status && S.status.battery, el = $("#batStatus");
  if (!b || !el) return;
  el.textContent = b.on_battery == null ? "This PC has no battery, so nothing is paused."
    : !b.on_battery ? "Plugged in. On battery the keyboard shows its own effect, which costs the PC nothing."
    : b.paused ? `On battery: ${effectLabel(b.paused)} is paused and comes back when you plug in.`
    : b.saver ? "On battery. Anything you start now keeps running." : "On battery (battery saver is off).";
  if (b.dimmed) el.textContent += b.on_battery ? ` The keyboard is at brightness ${b.dimmed.to} of 4 until you plug in (then back to ${b.dimmed.from}).`
    : ` The keyboard's brightness goes back to ${b.dimmed.from} of 4 as soon as it can.`;
  else if (b.brightness != null && b.saver && b.on_battery === false) el.textContent += ` On battery it dims to ${b.brightness} of 4 (its lights run on the laptop's USB power).`;
}
function renderTimer() {
  const t = S.status && S.status.timer;
  $("#tStatus").textContent = t ? `${t.label ? t.label + ": " : ""}${Math.floor(t.remaining_s / 60)}:${String(Math.floor(t.remaining_s % 60)).padStart(2, "0")} left`
    : "The number row fills up as time passes; the board flashes when it's done.";
}

// ------------------------------------------------------------------ game lighting tab
const GAME_LEGEND = {
  cs2: "Number row = health · F-row = ammo, or the bomb timer once it is planted · WASD = your team. Flashbangs, hits, kills and round results flash the board.",
  dota2: "Number row = health · F-row = mana · Q W E D F R = abilities, Z X C V B N = items, T = TP scroll (bright = ready, dim red = cooldown). The number row counts down to respawn.",
};
async function gamesLoad() {
  const st = await api("/games");
  $("#gamesAuto").checked = !!st.auto;
  $("#gamesNow").textContent = st.on ? `now: ${st.games[st.on].name}` : st.steam ? "" : "Steam was not found on this PC.";
  $("#gameCards").innerHTML = Object.entries(st.games).map(([id, g]) => {
    const state = g.receiving ? "● Receiving the game's state."
      : g.configured ? "Set up. Start the game to see it." + (g.launch_option ? ` In Steam, add ${g.launch_option} to its launch options.` : "")
      : g.installed ? "Installed, not set up yet." : "Not found in a Steam library on this PC.";
    const btn = !g.installed ? "" : g.configured ? '<button class="btn" data-remove>Remove set-up</button>' : '<button class="btn primary" data-setup>Set up</button>';
    return `<div class="card" data-game="${esc(id)}"><h3>${esc(g.name)}<button class="btn" data-demo>Try the demo</button></h3>
      <div class="desc">${GAME_LEGEND[id] || ""}</div><div class="hint gstate">${esc(state)}</div><div class="row">${btn}</div></div>`;
  }).join("");
  for (const card of $$("#gameCards .card")) {
    const id = card.dataset.game;
    $("[data-demo]", card).onclick = () => safe(() => api("/run", { effect: id, params: { demo: true } }), "Demo round on the keyboard (Stop live to end it)");
    const s = $("[data-setup]", card), r = $("[data-remove]", card);
    if (s) s.onclick = () => safe(async () => { await api("/games/setup", { game: id }); await gamesLoad(); }, "Set up. Start the game to see it.");
    if (r) r.onclick = () => safe(async () => { await api("/games/remove", { game: id }); await gamesLoad(); }, "Set-up removed");
  }
}
function initGames() {
  $("#gamesAuto").onchange = () => safe(() => api("/games", { auto: $("#gamesAuto").checked }));
  gamesLoad().catch(() => { });
  setInterval(() => { if (S.tab === "games" && !document.hidden) gamesLoad().catch(() => { }); }, 3000);
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
// OpenRGB SDK server (Other Settings)
async function orgbLoad() {
  const s = await api("/openrgb");
  $("#orgbOn").checked = !!s.enabled;
  if (document.activeElement !== $("#orgbPort")) $("#orgbPort").value = s.port;
  $("#orgbState").textContent = s.error ? s.error : !s.enabled ? "Off."
    : `Listening on 127.0.0.1:${s.port} · ` + (s.clients.length ? `connected: ${s.clients.join(", ")}` : "no program connected yet");
}
// recent events (Other Settings): newest first
async function eventsLoad() {
  const r = await api("/events");
  $("#events").innerHTML = r.events.length ? r.events.slice(-30).reverse()
    .map(e => `<div><time>${esc(e.time.slice(5, 16))}</time>${esc(e.text)}</div>`).join("") : '<span class="hint">Nothing yet.</span>';
}
function initEvents() {
  eventsLoad().catch(() => { });
  setInterval(() => { if (S.tab === "settings" && !document.hidden) eventsLoad().catch(() => { }); }, 3000);
}
function initOpenRGB() {
  const save = () => safe(async () => {
    await api("/openrgb", { enabled: $("#orgbOn").checked, port: Math.max(1, Math.min(65535, +$("#orgbPort").value || 6742)) });
    await orgbLoad();
  });
  $("#orgbOn").onchange = save;
  $("#orgbPort").onchange = save;
  orgbLoad().catch(() => { });
  setInterval(() => { if (S.tab === "settings" && !document.hidden) orgbLoad().catch(() => { }); }, 3000);
}
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
  $("#sDiag").onclick = () => safe(copyDiagnostics);
}
async function copyDiagnostics() {
  const text = JSON.stringify(await api("/diagnostics"), null, 1);
  let copied = false;
  try { await navigator.clipboard.writeText(text); copied = true; } catch (e) { }
  await modal("Diagnostics", `<p class="hint" style="margin-top:0">${copied ? "Copied to the clipboard. " : ""}Paste it into a bug
    report. Tokens are hidden and your home folder shows as ~.</p>
    <textarea class="inp code" rows="10" readonly>${esc(text)}</textarea>`, [["Close", null, "primary"]]);
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

// ------------------------------------------------------------------ macro pad tab
const PAD_ACTIONS = [["none", "Nothing"], ["effect", "Live effect"], ["layers", "Layer stack"], ["profile", "Lighting profile"],
  ["profile_next", "Next profile"], ["profile_prev", "Previous profile"], ["stop", "Keyboard's own effect"],
  ["hardware", "Built-in effect"], ["brightness", "Keyboard brightness"], ["notify", "Flash the keyboard"],
  ["timer", "Timer (start / cancel)"], ["media", "Media key"], ["hotkey", "Hotkey"], ["launch", "Open program / website"]];
const PAD_MEDIA = [["play_pause", "Play / pause"], ["next", "Next track"], ["previous", "Previous track"],
  ["volume_up", "Volume up"], ["volume_down", "Volume down"], ["mute", "Mute"]];
const PAD_ICONS = ["🌈", "⌨️", "🎵", "🧅", "🖥️", "💡", "🔔", "⏱️", "☀️", "🔅", "🎮", "🎧", "🔇", "⏯️", "⏭️", "⏮️", "🎙️", "📷", "🚀", "⭐"];
const PAD_MOVES = [["left", "Turn left"], ["right", "Turn right"], ["press", "Press"]];
S.pad = { st: null, family: null, sel: { kind: "key", i: 0 }, v: 0 };

function padModel() { return S.pad.st.connected && S.pad.st.model.family === S.pad.family ? S.pad.st.model : S.pad.st.families[S.pad.family]; }
function padCfg() { return S.pad.st.configs[S.pad.family]; }
function actionLabel(a) {
  a = a || { type: "none" };
  const name = (PAD_ACTIONS.find(x => x[0] === (a.type || "none")) || [, a.type])[1];
  if (a.type === "effect") return `${name}: ${modeLabel(a.effect)}`;
  if (a.type === "profile") return `Profile: ${a.name}`;
  if (a.type === "hardware") return `Built-in: ${modeLabel(String(a.mode))}`;
  if (a.type === "brightness") return `Brightness ${a.step > 0 ? "+" : "−"}`;
  if (a.type === "media") return (PAD_MEDIA.find(x => x[0] === a.key) || [, a.key])[1];
  if (a.type === "hotkey") return `Hotkey ${a.keys}`;
  if (a.type === "launch") return `Open ${a.target}`;
  if (a.type === "timer") return `Timer ${a.minutes || 25} min`;
  return name;
}
async function padLoad() {
  S.pad.st = await api("/pad");
  if (!S.pad.family) S.pad.family = S.pad.st.connected ? S.pad.st.model.family : "akp03";
  renderPad();
}
function renderPadState() {
  const st = S.pad.st, el = $("#padState");
  el.className = "pill2 " + (st.connected ? "ok" : "");
  el.textContent = st.connected ? `${st.model.name} connected` : "No pad connected";
  $("#padHint").textContent = st.connected ? "" : (st.error && !/disconnected/.test(st.error) ? st.error :
    "Plug in a supported pad and it starts working; you can set it up now.");
}
function renderPad() {
  renderPadState();
  $$("#padFamily button").forEach(b => b.classList.toggle("on", b.dataset.v === S.pad.family));
  const m = padModel(), cfg = padCfg(), view = $("#padView");
  const cols = Math.max(...m.keys.map(k => k.col), ...m.knobs.map(k => k.col)) + 1;
  const rows = Math.max(...m.keys.map(k => k.row), ...m.knobs.map(k => k.row)) + 1;
  const U = 74;  // px per grid cell
  view.style.width = (cols * U + 12) + "px"; view.style.height = (rows * U + 12) + "px";
  view.innerHTML = m.keys.map(k => {
    const c = cfg.keys[k.index], sel = S.pad.sel.kind === "key" && S.pad.sel.i === k.index;
    const pos = `left:${6 + k.col * U}px;top:${6 + k.row * U}px`;
    return k.kind === "lcd"
      ? `<button class="pkey lcd${sel ? " sel" : ""}" data-key="${k.index}" style="${pos}" title="${esc(k.name + " · " + actionLabel(c.action))}">
           <img alt="" src="/pad/key.png?model=${S.pad.st.connected && S.pad.st.model.family === S.pad.family ? S.pad.st.model.id : S.pad.family}&i=${k.index}&v=${S.pad.v}"></button>`
      : `<button class="pkey btn2${sel ? " sel" : ""}" data-key="${k.index}" style="${pos}" title="${esc(k.name + " · " + actionLabel(c.action))}">
           <span>${esc(c.label || k.name)}</span></button>`;
  }).join("") + m.knobs.map(k => {
    const sel = S.pad.sel.kind === "knob" && S.pad.sel.i === k.index;
    return `<button class="pknob${sel ? " sel" : ""}" data-knob="${k.index}" style="left:${6 + k.col * U}px;top:${6 + k.row * U}px" title="${esc(k.name)}"></button>`;
  }).join("");
  $$("[data-key]", view).forEach(b => b.onclick = () => { S.pad.sel = { kind: "key", i: +b.dataset.key }; renderPad(); });
  $$("[data-knob]", view).forEach(b => b.onclick = () => { S.pad.sel = { kind: "knob", i: +b.dataset.knob }; renderPad(); });
  $("#padBright").value = cfg.brightness; $("#padBrightV").textContent = cfg.brightness + "%";
  renderPadEditor();
}
function actionEditorHtml(a, id) {
  a = a || { type: "none" };
  const t = a.type || "none";
  const opts = (list, cur) => list.map(([v, n]) => `<option value="${esc(v)}" ${String(v) === String(cur) ? "selected" : ""}>${esc(n)}</option>`).join("");
  let extra = "";
  if (t === "effect") extra = `<select class="inp" data-f="effect">${opts(EFFECTS.map(e => [e.id, e.name]), a.effect)}</select>`;
  if (t === "profile") {
    const ps = (S.settings.profiles || []).map(p => [p.name, p.name]);
    extra = ps.length ? `<select class="inp" data-f="name">${opts(ps, a.name)}</select>` : `<span class="hint">Save a profile first (sidebar +)</span>`;
  }
  if (t === "hardware") extra = `<select class="inp" data-f="mode">${opts(S.modes.map(m => [m.name, modeLabel(m.name)]), a.mode)}</select>`;
  if (t === "brightness") extra = `<select class="inp" data-f="step">${opts([[1, "One step brighter"], [-1, "One step dimmer"]], a.step ?? 1)}</select>`;
  if (t === "notify") extra = `<input type="color" data-f="color" value="#${hex(a.color || "ff3000")}">`;
  if (t === "timer") extra = `<input class="inp" type="number" min="1" max="600" data-f="minutes" value="${a.minutes || 25}" style="width:90px"> min`;
  if (t === "media") extra = `<select class="inp" data-f="key">${opts(PAD_MEDIA, a.key || "play_pause")}</select>`;
  if (t === "hotkey") extra = `<input class="inp" type="text" data-f="keys" placeholder="ctrl+shift+m" value="${esc(a.keys || "")}">`;
  if (t === "launch") extra = `<input class="inp" type="text" data-f="target" placeholder="C:\\Program Files\\…\\app.exe or https://…" value="${esc(a.target || "")}">`;
  return `<div class="pact" data-act="${id}"><select class="inp" data-f="type">${opts(PAD_ACTIONS, t)}</select>${extra}</div>`;
}
const PAD_FIELDS = { effect: ["effect"], profile: ["name"], hardware: ["mode"], brightness: ["step"], notify: ["color"],
  timer: ["minutes"], media: ["key"], hotkey: ["keys"], launch: ["target"] };
function readAction(el) {
  const a = { type: $("[data-f=type]", el).value };
  for (const f of $$("[data-f]", el)) {  // only the fields of this type (the editor may still show the old type's)
    const k = f.dataset.f;
    if (!(PAD_FIELDS[a.type] || []).includes(k)) continue;
    if (k === "step" || k === "minutes") a[k] = +f.value;
    else if (f.type === "color") a[k] = hex(f.value);
    else a[k] = f.value.trim();
  }
  if (a.type === "media" && !a.key) a.key = "play_pause";
  if (a.type === "brightness" && !a.step) a.step = 1;
  if (a.type === "effect" && !a.effect) a.effect = EFFECTS[0].id;
  if (a.type === "hardware" && !a.mode) a.mode = S.modes[0].name;
  if (a.type === "profile" && !a.name && (S.settings.profiles || []).length) a.name = S.settings.profiles[0].name;
  return a;
}
function renderPadEditor() {
  const m = padModel(), cfg = padCfg(), ed = $("#padEdit"), sel = S.pad.sel;
  if (sel.kind === "knob" && !m.knobs[sel.i]) S.pad.sel = { kind: "key", i: 0 };
  if (S.pad.sel.kind === "key") {
    const k = m.keys[S.pad.sel.i], c = cfg.keys[k.index];
    ed.innerHTML = `<h2>${esc(k.name)} <span class="hint">${k.kind === "lcd" ? "screen key" : "button (no screen)"}</span></h2>
      <div class="row" style="margin-bottom:10px"><input class="inp" style="width:170px" data-k="label" maxlength="24" placeholder="Label" value="${esc(c.label)}">
        <input class="inp" style="width:70px" data-k="icon" maxlength="8" placeholder="Icon" value="${esc(c.icon)}"
        ${k.kind === "lcd" ? "" : "disabled"}><input type="color" data-k="color" value="#${hex(c.color)}" ${k.kind === "lcd" ? "" : "disabled"} title="Background"></div>
      ${k.kind === "lcd" ? `<div class="icons">${PAD_ICONS.map(i => `<button class="ico" data-ico="${i}">${i}</button>`).join("")}</div>` : ""}
      <div class="field"><label>When pressed</label>${actionEditorHtml(c.action, "key")}</div>
      <div class="row"><button class="btn" id="padTest">Run it now</button><span class="hint" id="padErr">Saved automatically.</span></div>`;
  } else {
    const k = m.knobs[S.pad.sel.i], c = cfg.knobs[k.index] || {};
    ed.innerHTML = `<h2>${esc(k.name)}</h2>` + PAD_MOVES.map(([mv, n]) =>
      `<div class="field"><label>${n}</label>${actionEditorHtml(c[mv], mv)}</div>`).join("") +
      `<div class="row"><button class="btn" id="padTest">Turn right now</button><span class="hint" id="padErr">Saved automatically.</span></div>`;
  }
  const save = debounce(padSave, 350);
  $$("input,select", ed).forEach(el => el.addEventListener(el.tagName === "SELECT" || el.type === "color" ? "change" : "input", () => {
    padCollect();
    if (el.dataset.f === "type") renderPadEditor();  // show the fields of the new action type
    save();
  }));
  $$("[data-ico]", ed).forEach(b => b.onclick = () => { $("[data-k=icon]", ed).value = b.dataset.ico; padCollect(); save(); });
  $("#padTest").onclick = () => safe(async () => {
    await padSaveNow();
    const body = S.pad.sel.kind === "key" ? { model: S.pad.family, key: S.pad.sel.i } : { model: S.pad.family, knob: S.pad.sel.i, move: "right" };
    await api("/pad/press", body);
  }, "Done");
}
function padCollect() {  // editor -> S.pad.st.configs
  const cfg = padCfg(), ed = $("#padEdit");
  if (S.pad.sel.kind === "key") {
    const c = cfg.keys[S.pad.sel.i];
    c.label = $("[data-k=label]", ed).value; c.icon = $("[data-k=icon]", ed).value; c.color = hex($("[data-k=color]", ed).value);
    c.action = readAction($("[data-act=key]", ed));
  } else {
    const k = {};
    for (const [mv] of PAD_MOVES) { const a = readAction($(`[data-act=${mv}]`, ed)); if (a.type !== "none") k[mv] = a; }
    cfg.knobs[S.pad.sel.i] = k;
  }
}
async function padSaveNow() {
  const cfg = padCfg();
  const res = await api("/pad", { model: S.pad.family, brightness: cfg.brightness, keys: cfg.keys, knobs: cfg.knobs });
  S.pad.st.configs[S.pad.family] = res;
  S.pad.v++;
  for (const b of $$("#padView .pkey.lcd img")) b.src = b.src.replace(/v=\d+/, "v=" + S.pad.v);
  return res;
}
function padErr(msg) {  // shown next to the editor (typing a hotkey is invalid until it is finished)
  const el = $("#padErr");
  if (!el) return;
  el.textContent = msg || "Saved automatically.";
  el.classList.toggle("err", !!msg);
}
function padIncomplete() {  // still being typed: don't send it yet
  const cfg = padCfg(), acts = S.pad.sel.kind === "key" ? [cfg.keys[S.pad.sel.i].action] : Object.values(cfg.knobs[S.pad.sel.i] || {});
  for (const a of acts) {
    if (a.type === "hotkey" && (!a.keys || /\+\s*$/.test(a.keys))) return "Finish the hotkey, e.g. ctrl+shift+m";
    if (a.type === "launch" && !a.target) return "Enter a program, file or web address";
  }
  return "";
}
const padSave = () => {
  const wait = padIncomplete();
  if (wait) return padErr(wait);
  return padSaveNow().then(() => padErr(""), e => padErr(e.message));
};
function initPad() {
  $$("#padFamily button").forEach(b => b.onclick = () => { S.pad.family = b.dataset.v; S.pad.sel = { kind: "key", i: 0 }; renderPad(); });
  const saveB = debounce(padSave, 300);
  $("#padBright").oninput = () => { padCfg().brightness = +$("#padBright").value; $("#padBrightV").textContent = $("#padBright").value + "%"; saveB(); };
  padLoad().catch(e => toast("Macro pad: " + e.message, true));
  setInterval(async () => {  // follow plug / unplug and what is lit on the pad
    if (S.tab !== "pad") return;
    try {
      const st = await api("/pad"), was = S.pad.st;
      const changed = st.connected !== was.connected || st.active !== was.active || (st.model || {}).id !== (was.model || {}).id;
      S.pad.st = { ...st, configs: was.configs };
      if (changed) { if (st.connected && !was.connected) S.pad.family = st.model.family; S.pad.v++; renderPad(); } else renderPadState();
    } catch (e) { }
  }, 1500);
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
  if (S.frame && !editingCustom()) { colorKeys(S.frame.colors); setCaption(S.frame); }  // no editor colours left behind
  if (t === "light") loadInfo().catch(() => { });
  if (t === "pad" && S.pad.st) padLoad().catch(() => { });
  if (t === "games") gamesLoad().catch(() => { });
  if (t === "apps") api("/settings").then(s => {  // lighting chosen elsewhere (tray, pad, API) may have ended them
    S.settings.apps = { ...(S.settings.apps || {}), enabled: !!(s.apps || {}).enabled };
    $("#appsOn").checked = S.settings.apps.enabled;
  }).catch(() => { });
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
  buildBoard(); initLight(); initCustom(); initLive(); initLayers(); initApps(); initProfiles(); initPad(); initGames(); initOpenRGB(); initEvents(); await initAutomation();
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
