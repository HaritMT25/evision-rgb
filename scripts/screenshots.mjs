// README screenshots of the app, taken against the simulators (never your keyboard, pad or music).
//   python scripts/ui_preview_server.py 8899 --pad akp03e-rev2        (another terminal)
//   node scripts/screenshots.mjs [port] [out-dir]                       (default 8899, docs/screenshots)
import { spawn } from "node:child_process";
import { existsSync, mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const PORT = +(process.argv[2] || 8899), OUT = process.argv[3] || join("docs", "screenshots"), DEVTOOLS = 9300 + Math.floor(Math.random() * 600);
const sleep = ms => new Promise(r => setTimeout(r, ms));
const BROWSERS = ["C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe", "C:/Program Files/Microsoft/Edge/Application/msedge.exe",
  "C:/Program Files/Google/Chrome/Application/chrome.exe", "/usr/bin/google-chrome", "/usr/bin/chromium"];
const exe = BROWSERS.find(p => existsSync(p));
if (!exe) { console.log("no Edge/Chrome found"); process.exit(1); }
mkdirSync(OUT, { recursive: true });
const profile = mkdtempSync(join(tmpdir(), "evrgb-shots-"));
const browser = spawn(exe, ["--headless=new", `--remote-debugging-port=${DEVTOOLS}`, `--user-data-dir=${profile}`,
  "--no-first-run", "--disable-gpu",
  "--hide-scrollbars", "--force-device-scale-factor=1", "--window-size=1280,820", "about:blank"], { stdio: "ignore" });

async function getJson(url) {
  for (let i = 0; i < 60; i++) { try { return await (await fetch(url)).json(); } catch (e) { await sleep(250); } }
  throw new Error("browser DevTools not reachable");
}
let failed = false;
try {
  const page = (await getJson(`http://127.0.0.1:${DEVTOOLS}/json/list`)).find(t => t.type === "page");
  const ws = new WebSocket(page.webSocketDebuggerUrl);
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
  let seq = 0;
  const pending = new Map();
  ws.onmessage = ev => { const m = JSON.parse(ev.data); if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id); } };
  const cmd = (method, params = {}) => new Promise(res => { const id = ++seq; pending.set(id, res); ws.send(JSON.stringify({ id, method, params })); });
  const js = async body => {
    const r = await cmd("Runtime.evaluate", { expression: `(async () => { ${body} })()`, awaitPromise: true, returnByValue: true });
    if (r.result.exceptionDetails) throw new Error((r.result.exceptionDetails.exception || {}).description || r.result.exceptionDetails.text);
    return r.result.result.value;
  };
  const shot = async name => {
    const r = await cmd("Page.captureScreenshot", { format: "webp", quality: 88 });
    writeFileSync(join(OUT, `${name}.webp`), Buffer.from(r.result.data, "base64"));
    console.log("saved", join(OUT, `${name}.webp`));
  };
  await cmd("Runtime.enable"); await cmd("Page.enable");
  await cmd("Emulation.setDeviceMetricsOverride", { width: 1280, height: 820, deviceScaleFactor: 1, mobile: false });
  await cmd("Page.navigate", { url: `http://127.0.0.1:${PORT}/` });
  await sleep(3500);
  const wait = ms => js(`await new Promise(r => setTimeout(r, ${ms}));`);

  // 1. the layer editor running "keyboard effect + red WASD + typing ripples"
  await js(`showTab("live"); setLiveView("layers");`); await wait(800);
  await js(`$("#lyStart").click();`); await wait(1500);
  await shot("layers");
  // 2. album colours (a pretend song with a pink and blue cover)
  await js(`setLiveView("cards");`); await wait(400);
  await js(`$('.card[data-effect="nowplaying"] [data-start]').click();`); await wait(2800);
  await js(`$('.card[data-effect="nowplaying"]').scrollIntoView({block: "center"});`); await wait(400);
  await shot("album-colours");
  // 3. per-key colours
  await js(`await api("/stop", {}); showTab("custom");`); await wait(900);
  await js(`for (const k of ["W", "A", "S", "D"]) S.custom[k] = "ff2020"; for (const k of ["Up", "Down", "Left", "Right"]) S.custom[k] = "20a0ff";
            S.custom.Esc = "ffffff"; S.dirty = true; colorKeys(S.custom);`); await wait(500);
  await shot("custom-keys");
  // 4. the macro pad tab (simulated AKP03)
  await js(`S.dirty = false; S.custom = {}; showTab("pad");`); await wait(1500);
  await shot("macro-pad");
  // 5. automation: indicators, battery saver, timer
  await js(`showTab("auto");`); await wait(1500);
  await shot("automation");
  // 6. game lighting: the CS2 demo round (the preview server pretends CS2 is installed)
  await js(`showTab("games");`); await wait(900);
  await js(`$('.card[data-game="cs2"] [data-demo]').click();`); await wait(4600);  // a few seconds into the round: shooting
  await shot("games");
  await js(`await api("/stop", {});`); await wait(500);
  // 7. built-in effects with the firmware-exact preview
  await js(`showTab("light");`); await wait(1500);
  await shot("built-in-effects");
  ws.close();
} catch (e) {
  failed = true;
  console.log("FAILED: " + (e.stack || e));
} finally {
  browser.kill();
  await sleep(500);
  try { rmSync(profile, { recursive: true, force: true }); } catch (e) { }
}
process.exit(failed ? 1 : 0);
