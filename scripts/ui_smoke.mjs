// UI smoke test: drives the real app UI in headless Edge/Chrome over the DevTools protocol and fails on
// any JavaScript error or broken interaction. Serve the UI against the simulated keyboard first:
//   python scripts/ui_preview_server.py 8899        (another terminal)
//   node scripts/ui_smoke.mjs [port] [screenshot-dir]
import { spawn } from "node:child_process";
import { existsSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const PORT = +(process.argv[2] || 8899), OUT = process.argv[3] || null, DEVTOOLS = 9300 + Math.floor(Math.random() * 600);
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
  await js(`$("#cDiscard").click(); await T.wait(500);`);

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
