// UI smoke test: drives the real app UI in headless Edge/Chrome over the DevTools protocol and fails on
// any JavaScript error or broken interaction. Serve the UI against the simulated keyboard first:
//   python scripts/ui_preview_server.py 8899 [--pad akp03e-rev2]       (another terminal)
//   node scripts/ui_smoke.mjs [port] [screenshot-dir] [--pad]          (--pad: expect the simulated pad)
import { spawn } from "node:child_process";
import { existsSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const args = process.argv.slice(2).filter(a => a !== "--pad"), EXPECT_PAD = process.argv.includes("--pad");
const PORT = +(args[0] || 8899), OUT = args[1] || null, DEVTOOLS = 9300 + Math.floor(Math.random() * 600);
const BROWSERS = ["C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe", "C:/Program Files/Microsoft/Edge/Application/msedge.exe",
  "C:/Program Files/Google/Chrome/Application/chrome.exe", "/usr/bin/google-chrome", "/usr/bin/chromium", "/usr/bin/microsoft-edge"];
const exe = BROWSERS.find(p => existsSync(p));
if (!exe) { console.log("no Chromium-based browser found; skipping"); process.exit(0); }
const profile = mkdtempSync(join(tmpdir(), "evrgb-smoke-"));
const browser = spawn(exe, ["--headless=new", `--remote-debugging-port=${DEVTOOLS}`, `--user-data-dir=${profile}`, "--no-first-run",
  "--disable-gpu", "--window-size=1280,860", "about:blank"], { stdio: "ignore" });
const sleep = ms => new Promise(r => setTimeout(r, ms));
let failures = 0;
const check = (name, ok, detail = "") => { if (!ok) failures++; console.log(`${ok ? "ok  " : "FAIL"} ${name}${detail ? "  — " + detail : ""}`); };

async function getJson(url) {
  for (let i = 0; i < 60; i++) { try { return await (await fetch(url)).json(); } catch (e) { await sleep(250); } }
  throw new Error("browser DevTools not reachable");
}
try {
  const page = (await getJson(`http://127.0.0.1:${DEVTOOLS}/json/list`)).find(t => t.type === "page");
  const ws = new WebSocket(page.webSocketDebuggerUrl);
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
  let seq = 0;
  const pending = new Map(), errors = [];
  ws.onmessage = ev => {
    const m = JSON.parse(ev.data);
    if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id); return; }
    if (m.method === "Runtime.exceptionThrown") { const d = m.params.exceptionDetails; errors.push((d.exception && d.exception.description) || d.text); }
    if (m.method === "Runtime.consoleAPICalled" && m.params.type === "error") errors.push("console.error " + m.params.args.map(a => a.value ?? a.description).join(" "));
    if (m.method === "Log.entryAdded" && m.params.entry.level === "error" && !/favicon/.test(m.params.entry.url || "")) errors.push(`${m.params.entry.text} ${m.params.entry.url || ""}`);
  };
  const cmd = (method, params = {}) => new Promise(res => { const id = ++seq; pending.set(id, res); ws.send(JSON.stringify({ id, method, params })); });
  const js = async body => {
    const r = await cmd("Runtime.evaluate", { expression: `(async () => { ${body} })()`, awaitPromise: true, returnByValue: true });
    if (r.result.exceptionDetails) throw new Error((r.result.exceptionDetails.exception || {}).description || r.result.exceptionDetails.text);
    return r.result.result.value;
  };
  const shot = async name => {
    if (!OUT) return;
    const r = await cmd("Page.captureScreenshot", { format: "png" });
    writeFileSync(join(OUT, `${name}.png`), Buffer.from(r.result.data, "base64"));
  };
  // helpers injected into the page: click a key on the board, drag a box, press Ctrl+<key>
  const HELPERS = `
    window.T = {
      at(el) { const r = el.getBoundingClientRect(); return { clientX: r.left + r.width / 2, clientY: r.top + r.height / 2, bubbles: true }; },
      clickKey(name) { const el = S.keyEls[name], p = T.at(el);
        $("#board").dispatchEvent(new MouseEvent("mousedown", p)); el.querySelector(".cap").dispatchEvent(new MouseEvent("mouseup", p)); },
      drag(a, b) { const A = T.at(S.keyEls[a]), B = T.at(S.keyEls[b]);
        $("#board").dispatchEvent(new MouseEvent("mousedown", A));
        window.dispatchEvent(new MouseEvent("mousemove", { ...B, clientX: B.clientX + 1 }));
        window.dispatchEvent(new MouseEvent("mouseup", B)); },
      ctrl(key, shift = false) { document.dispatchEvent(new KeyboardEvent("keydown", { key, ctrlKey: true, shiftKey: shift, bubbles: true })); },
      set(sel, value, ev = "change") { const el = $(sel); el.value = value; el.dispatchEvent(new Event(ev, { bubbles: true })); },
      modalButton(label) { [...document.querySelectorAll("#mBtns button")].find(b => b.textContent === label).click(); },
      wait: ms => new Promise(r => setTimeout(r, ms)),
    };`;
  await cmd("Runtime.enable"); await cmd("Page.enable"); await cmd("Log.enable");
  await cmd("Emulation.setDeviceMetricsOverride", { width: 1280, height: 860, deviceScaleFactor: 1, mobile: false });
  await cmd("Page.navigate", { url: `http://127.0.0.1:${PORT}/#live:layers` });
  await sleep(3000);
  await js(HELPERS);

  // ---- layer editor
  check("layer editor opens from #live:layers", await js(`return $("#layerEditor").style.display !== "none" && S.liveView === "layers"`));
  check("starts with the default stack (keyboard + red WASD)", await js(`return S.layers.length === 2 && S.layers[0].effect === "keyboard" && $$(".layer").length === 2`));
  await js(`$("#lyStart").click(); await T.wait(900);`);
  const st = await js(`return await api("/status")`);
  check("Start layers runs the stack", st.effect === "layers", JSON.stringify(st.params).slice(0, 80));
  let f = await js(`await T.wait(300); return (await api("/frame")).colors`);
  check("WASD red on top of the keyboard's own green", f.W === "ff0000" && f.Q === "00ff00", `W=${f.W} Q=${f.Q}`);
  await js(`$('.layer[data-i="1"] .lname').click(); await T.wait(100);`);
  check("selecting a layer shows its keys on the board", await js(`return S.lySel === 1 && $$(".key.msk").length === 8`));
  await shot("layers-selected");
  await js(`T.clickKey("Q"); await T.wait(700);`);
  check("clicking a key adds it to the layer", await js(`return S.layers[1].keys.includes("Q") && $$(".key.msk").length === 9`));
  f = await js(`return (await api("/frame")).colors`);
  check("edits apply live while the layers run", f.Q === "ff0000", `Q=${f.Q}`);
  await js(`T.ctrl("z"); await T.wait(100);`);
  check("Ctrl+Z undoes the mask edit", await js(`return !S.layers[1].keys.includes("Q") && $$(".key.msk").length === 8`));
  await js(`T.ctrl("y"); await T.wait(100);`);
  check("Ctrl+Y redoes it", await js(`return S.layers[1].keys.includes("Q")`));
  await js(`T.drag("Q", "E"); await T.wait(100);`);
  check("dragging a box replaces the mask", await js(`return JSON.stringify(S.layers[1].keys) === JSON.stringify(["Q","W","E"].filter(k => S.layers[1].keys.includes(k))) && S.layers[1].keys.length === 3`),
    JSON.stringify(await js(`return S.layers[1].keys`)));
  await js(`const o = $('.layer[data-i="1"] [data-l=opacity]'); o.value = 0.5; o.dispatchEvent(new Event("input", {bubbles: true})); o.dispatchEvent(new Event("change", {bubbles: true}));`);
  check("opacity slider", await js(`return S.layers[1].opacity === 0.5`));
  await js(`T.set('.layer[data-i="1"] [data-l=blend]', "screen"); await T.wait(50);`);
  check("blend mode select", await js(`return S.layers[1].blend === "screen"`));
  await js(`T.set("#lyAdd", "reactive"); await T.wait(50);`);
  check("adding a reactive layer puts it on top with Screen blend and a black background",
    await js(`const L = S.layers[2]; return S.layers.length === 3 && L.effect === "reactive" && L.blend === "screen" && L.params.base === "000000" && S.lySel === 2`));
  await js(`const c = $('.layer[data-i="2"] [data-p=fade]'); c.value = 1.1; c.dispatchEvent(new Event("input", {bubbles: true})); c.dispatchEvent(new Event("change", {bubbles: true}));`);
  check("layer parameters are edited in place", await js(`return S.layers[2].params.fade === 1.1`));
  await js(`$('.layer[data-i="2"] [data-act=down]').click(); await T.wait(50);`);
  check("moving a layer down", await js(`return S.layers[1].effect === "reactive" && S.lySel === 1`));
  await js(`$('.layer[data-i="1"] [data-l=enabled]').click(); await T.wait(50);`);
  check("switching a layer off", await js(`return S.layers[1].enabled === false && $(".layer.off") !== null`));
  await js(`$('.layer[data-i="1"] [data-act=del]').click(); await T.wait(50);`);
  check("removing a layer", await js(`return S.layers.length === 2`));
  await js(`for (let i = 0; i < 4; i++) T.ctrl("z"); await T.wait(50);`);
  check("several undos walk back through the edits", await js(`return S.layers.length === 3`));
  await js(`T.set("#lyTemplate", "1"); await T.wait(700);`);
  check("templates replace the stack", await js(`return S.layers.length === 2 && S.layers[0].effect === "rainbow"`));
  check("the running stack follows the template", (await js(`return await api("/status")`)).params.layers[0].effect === "rainbow");
  await shot("layers-template");
  const draft = await js(`await T.wait(900); return (await api("/settings")).layers_draft`);
  check("the editor's stack is kept in the settings", Array.isArray(draft) && draft[0].effect === "rainbow");

  // ---- profiles: save, share code, import, apply
  await js(`$("#lySave").click(); await T.wait(100); $("#mBody input").value = "Layered <b>"; T.modalButton("Save"); await T.wait(800);`);
  const profs = await js(`return S.settings.profiles`);
  check("Save as profile stores the layer stack", profs.length === 1 && profs[0].live.effect === "layers" && profs[0].hardware.mode === 6,
    JSON.stringify(profs[0] || {}).slice(0, 120));
  check("profile names lose markup like imported ones do", profs[0].name === "Layered b", profs[0].name);
  await js(`shareProfile(S.settings.profiles[0]); await T.wait(600);`);
  const code = await js(`return $("#mBody textarea").value`);
  check("Share code modal shows an evrgb1: code", /^evrgb1:/.test(code), code.slice(0, 30));
  await shot("share-code");
  await js(`T.modalButton("Close"); await T.wait(100); importFlow(); await T.wait(200);`);
  await shot("import");
  await js(`$("#impCode").value = ${JSON.stringify("  " + code + "\n")}; T.modalButton("Import"); await T.wait(900);`);
  const names = await js(`return S.settings.profiles.map(p => p.name)`);
  check("importing the code adds a second copy under a new name", names.length === 2 && names[1] === names[0] + " (2)", JSON.stringify(names));
  check("profile names are escaped in the list", await js(`return [...$("#profileSel").options].every(o => !o.querySelector("b"))`));
  await js(`await api("/stop", {}); T.set("#profileSel", "1"); await T.wait(1500);`);
  const applied = await js(`return await api("/status")`);
  check("choosing a profile applies its layers", applied.effect === "layers", applied.effect);
  check("the applied profile is shown as current", await js(`return $("#profileName").textContent === S.settings.profiles[1].name`));

  // ---- custom keys undo / redo
  await js(`showTab("custom"); await T.wait(700); S.brush = "0000ff"; T.clickKey("Esc"); await T.wait(200);`);
  check("painting a key in Custom Keys", await js(`return S.custom.Esc === "0000ff" && !$("#cUndo").disabled`));
  await js(`T.ctrl("z"); await T.wait(100);`);
  check("Ctrl+Z restores the previous colour", await js(`return S.custom.Esc !== "0000ff" && !$("#cRedo").disabled`));
  await js(`$("#cRedo").click(); await T.wait(100);`);
  check("Redo button paints it again", await js(`return S.custom.Esc === "0000ff"`));
  await shot("custom");
  // editor colours the keyboard is not showing (no new frame comes for a still picture): leaving must redraw it
  await js(`await api("/stop", {}); await T.wait(400); S.custom.Esc = "123456"; S.dirty = true; colorKeys(S.custom);
            showTab("light"); await T.wait(400);`);
  check("leaving the editor shows the keyboard's real colours again",
    await js(`return S.keyEls.Esc.style.getPropertyValue("--c") === "#" + S.frame.colors.Esc`),
    await js(`return S.keyEls.Esc.style.getPropertyValue("--c") + " vs " + S.frame.colors.Esc`));
  await js(`showTab("custom"); await T.wait(500); $("#cDiscard").click(); await T.wait(500);`);

  // ---- macro pad tab (start the preview server with --pad akp03e-rev2 and pass --pad here to expect one)
  await js(`showTab("pad"); await T.wait(900);`);
  if (EXPECT_PAD) check("the simulated pad shows as connected", await js(`return S.pad.st.connected && /connected/.test($("#padState").textContent)`),
    await js(`return $("#padState").textContent`));
  check("pad layout: 6 screen keys, 3 buttons, 3 knobs", await js(`return $$("#padView .pkey.lcd").length === 6 && $$("#padView .pkey.btn2").length === 3 && $$("#padView .pknob").length === 3`));
  check("key images load", await js(`await T.wait(400); return $$("#padView .pkey.lcd img").every(i => i.complete && i.naturalWidth > 0)`));
  await shot("pad");
  await js(`$('#padView [data-key="1"]').click(); await T.wait(100);`);
  check("selecting a key opens its editor", await js(`return /Key 2/.test($("#padEdit h2").textContent)`));
  await js(`const l = $("#padEdit [data-k=label]"); l.value = "Mic"; l.dispatchEvent(new Event("input", {bubbles: true})); await T.wait(700);`);
  check("label edits are saved", (await js(`return (await api("/pad")).configs.akp03.keys[1].label`)) === "Mic");
  await js(`T.set("#padEdit [data-f=type]", "hotkey"); await T.wait(150);`);
  check("choosing Hotkey shows its field", await js(`return !!$("#padEdit [data-f=keys]")`));
  await js(`const k = $("#padEdit [data-f=keys]"); k.value = "ctrl+"; k.dispatchEvent(new Event("input", {bubbles: true})); await T.wait(700);`);
  check("an unfinished hotkey is reported next to the editor, not saved", await js(`return $("#padErr").classList.contains("err")`) &&
    (await js(`return (await api("/pad")).configs.akp03.keys[1].action.type`)) !== "hotkey");
  await js(`const k = $("#padEdit [data-f=keys]"); k.value = "ctrl+shift+m"; k.dispatchEvent(new Event("input", {bubbles: true})); await T.wait(700);`);
  const act = await js(`return (await api("/pad")).configs.akp03.keys[1].action`);
  check("a finished hotkey is saved", act.type === "hotkey" && act.keys === "ctrl+shift+m", JSON.stringify(act));
  await js(`$('#padView [data-key="0"]').click(); await T.wait(100); $("#padTest").click(); await T.wait(900);`);
  check("Run it now starts the key's effect", (await js(`return (await api("/status")).effect`)) === "rainbow");
  await js(`$('#padView [data-knob="0"]').click(); await T.wait(100);`);
  check("a knob has turn-left / turn-right / press actions", await js(`return $$("#padEdit .pact").length === 3`));
  await js(`T.set("#padEdit [data-act=press] [data-f=type]", "notify"); await T.wait(700);`);
  check("knob actions are saved", (await js(`return (await api("/pad")).configs.akp03.knobs[0].press.type`)) === "notify");
  await js(`const b = $("#padBright"); b.value = 55; b.dispatchEvent(new Event("input", {bubbles: true})); await T.wait(700);`);
  check("pad brightness is saved", (await js(`return (await api("/pad")).configs.akp03.brightness`)) === 55);
  await js(`$('#padFamily [data-v="akp153"]').click(); await T.wait(600);`);
  check("the AKP153 layout has 18 screen keys", await js(`return $$("#padView .pkey.lcd").length === 18 && $$("#padView .pknob").length === 0`));
  await shot("pad-akp153");

  // ---- hidden window (tray): the 60 fps preview stream must pause, and resume when shown again
  await js(`Object.defineProperty(document, "hidden", {value: true, configurable: true});
            document.dispatchEvent(new Event("visibilitychange")); await T.wait(200);`);
  check("the preview stream pauses while the window is hidden", await js(`return S.es === null`));
  await js(`Object.defineProperty(document, "hidden", {value: false, configurable: true});
            document.dispatchEvent(new Event("visibilitychange")); await T.wait(600);`);
  check("and resumes when it is shown", await js(`return !!S.es && S.es.readyState !== 2`));

  // ---- album colours (the preview server pretends "Demo Song" with a pink/blue cover is playing)
  await js(`showTab("live"); setLiveView("cards"); await T.wait(2600);`);
  check("the Album colours card shows what is playing", /Demo Song/.test(await js(`return $("#npInfo").textContent`)),
    await js(`return $("#npInfo").textContent`));
  check("with its cover", await js(`const i = $("#npInfo img"); return !!i && i.complete && i.naturalWidth > 0`));
  check("and the cover's colours", await js(`return $$("#npInfo .sw").length >= 2`));
  await js(`$('.card[data-effect="nowplaying"] [data-start]').click(); await T.wait(1200);`);
  check("Album colours runs", (await js(`return (await api("/status")).effect`)) === "nowplaying");
  const npc = Object.values(await js(`return (await api("/frame")).colors`));
  const rgbOf = h => [0, 2, 4].map(i => parseInt(h.slice(i, i + 2), 16));
  check("the keys take the cover's pink and blue", npc.some(h => { const [r, g, b] = rgbOf(h); return r > 150 && b < 150; })
    && npc.some(h => { const [r, g, b] = rgbOf(h); return b > 150 && r < 100; }), npc.slice(0, 6).join(" "));
  await js(`window.scrollTo(0, 0); $('.card[data-effect="nowplaying"]').scrollIntoView({block: "center"}); await T.wait(300);`);
  await shot("album-colours");

  // ---- game lighting (the preview server pretends CS2 is installed in a temporary Steam folder)
  await js(`showTab("games"); await T.wait(900);`);
  check("the Games tab lists CS2 and Dota 2", await js(`return $$("#gameCards .card").length === 2`));
  check("CS2 shows as installed with a Set up button", await js(`return !!$('.card[data-game="cs2"] [data-setup]')`));
  check("Dota 2 shows as not found", /Not found/.test(await js(`return $('.card[data-game="dota2"] .gstate').textContent`)));
  await js(`$('.card[data-game="cs2"] [data-setup]').click(); await T.wait(900);`);
  check("Set up writes the config and offers to remove it", await js(`return !!$('.card[data-game="cs2"] [data-remove]')`));
  await js(`$('.card[data-game="cs2"] [data-demo]').click(); await T.wait(1200);`);
  check("Try the demo plays CS2 lighting", (await js(`return (await api("/status")).effect`)) === "cs2");
  await shot("games");
  await js(`$('.card[data-game="cs2"] [data-remove]').click(); await T.wait(700);`);
  check("Remove set-up deletes it again", await js(`return !!$('.card[data-game="cs2"] [data-setup]')`));

  // ---- OpenRGB programs (Other Settings), on a high port so nothing real is disturbed
  await js(`showTab("settings"); await T.wait(500); $("#orgbPort").value = 47421; $("#orgbOn").checked = true;
            $("#orgbOn").dispatchEvent(new Event("change")); await T.wait(900);`);
  check("the OpenRGB server can be switched on", /Listening on 127\.0\.0\.1:47421/.test(await js(`return $("#orgbState").textContent`)),
    await js(`return $("#orgbState").textContent`));
  await js(`$("#orgbOn").checked = false; $("#orgbOn").dispatchEvent(new Event("change")); await T.wait(900);`);
  check("and off again", (await js(`return $("#orgbState").textContent`)) === "Off.");
  check("recent events are listed, newest first", /app started/.test(await js(`await eventsLoad(); return $("#events").textContent`)),
    await js(`return $("#events").textContent.slice(0, 200)`));
  await js(`$("#sDiag").click(); await T.wait(800);`);
  check("Copy diagnostics shows the report", /"version"/.test(await js(`return ($("#mBody textarea") || {}).value || ""`)));
  await js(`T.modalButton("Close"); await T.wait(300); $("#events").scrollIntoView();`);
  await shot("settings-events");

  // ---- battery saver & still pictures
  await js(`showTab("auto"); await T.wait(1300);`);
  check("the battery saver switch is on by default", await js(`return $('[data-a="battery.enabled"]').checked === true`));
  check("its state is shown (the simulated PC is plugged in)", (await js(`return $("#batStatus").textContent`)).startsWith("Plugged in."),
    await js(`return $("#batStatus").textContent`));
  await js(`const t = $('[data-a="battery.enabled"]'); t.checked = false; t.dispatchEvent(new Event("input")); await T.wait(700);`);
  check("switching it off is saved", (await js(`return (await api("/automation")).battery.enabled`)) === false);
  await js(`const t = $('[data-a="battery.enabled"]'); t.checked = true; t.dispatchEvent(new Event("input")); await T.wait(700);`);
  check("dimming on battery is off by default (leave it as it is)", (await js(`return $('[data-a="battery.brightness"]').value`)) === "");
  check("its list holds brightness levels, not keys", (await js(`return $$('[data-a="battery.brightness"] option').length`)) === 5);
  await js(`const s = $('[data-a="battery.brightness"]'); s.value = "1"; s.dispatchEvent(new Event("input")); await T.wait(700);`);
  check("picking a level is saved as a number", (await js(`return (await api("/automation")).battery.brightness`)) === 1);
  await js(`const s = $('[data-a="battery.brightness"]'); s.value = ""; s.dispatchEvent(new Event("input")); await T.wait(700);`);
  check("leave it as it is is saved as null", (await js(`return (await api("/automation")).battery.brightness`)) === null);
  check("the key pickers still list keys", (await js(`return $$('[data-a="caps_lock.key"] option').length`)) > 80);
  await shot("automation-battery");
  await js(`await api("/run", {effect: "solid", params: {color: "203040"}}); await T.wait(2300);`);
  check("a still live picture is captioned as drawn less often", /still/.test(await js(`return $("#caption").textContent`)),
    await js(`return $("#caption").textContent`));
  check("and the board shows it", await js(`return S.frame && S.frame.colors.Q === "203040"`));

  // ---- other tabs still render
  for (const t of ["light", "live", "auto", "apps", "settings"]) await js(`showTab("${t}"); await T.wait(250);`);
  await js(`showTab("apps"); await T.wait(100); $("#appsAdd").click(); const tr = $("#appsRows tr:last-child"); const s = tr.querySelector("[data-k=effect]"); s.value = "layers"; s.dispatchEvent(new Event("change"));`);
  check("an app rule can use the layer stack", (await js(`return $("#appsRows tr:last-child [data-k=params]").value`)).includes('"layers"'));
  check("no JavaScript errors", errors.length === 0, errors.slice(0, 5).join(" | "));
  ws.close();
} catch (e) {
  failures++;
  console.log("FAIL smoke test crashed: " + (e.stack || e));
} finally {
  browser.kill();
  await sleep(500);
  try { rmSync(profile, { recursive: true, force: true }); } catch (e) { }
}
console.log(failures ? `${failures} UI check(s) failed` : "all UI checks passed");
process.exit(failures ? 1 : 0);
