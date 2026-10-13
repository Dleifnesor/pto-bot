// PTO Uploader: polls the local bot for upload jobs and runs them in a background tab of this Chrome profile.
// It never bypasses CAPTCHAs: it brings the tab to the front and waits for you to solve them.
import { CONFIG } from "./config.js";

const ALARM = "pto-poll";
const state = { lastPoll: null, lastJob: null, lastError: null };
let busy = false;

chrome.runtime.onInstalled.addListener(setup);
chrome.runtime.onStartup.addListener(setup);
chrome.alarms.onAlarm.addListener((a) => a.name === ALARM && poll());
chrome.runtime.onMessage.addListener((msg, _sender, reply) => {
  if (msg === "status" || msg === "poll") {
    (msg === "poll" ? poll() : Promise.resolve())
      .then(() => chrome.storage.session.get("pto"))
      .then((r) => reply({ ...(r.pto || {}), ...state, busy, server: CONFIG.server }));
    return true;
  }
});

function setup() {
  chrome.alarms.create(ALARM, { periodInMinutes: 1 });
  poll();
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function api(path, opts = {}) {
  return fetch(CONFIG.server + path, {
    ...opts,
    headers: { "X-PTO-Token": CONFIG.token, "Content-Type": "application/json", ...(opts.headers || {}) },
  });
}

async function save() {
  await chrome.storage.session.set({ pto: { ...state } });
}

async function poll() {
  if (busy) return;
  busy = true;
  try {
    const r = await api("/api/ext/next", { method: "POST", body: "{}" });
    state.lastPoll = new Date().toISOString();
    state.lastError = r.status === 200 || r.status === 204 ? null : `bot replied HTTP ${r.status}`;
    if (r.status === 200) {
      const job = await r.json();
      state.lastJob = `${job.platform} ${job.kind} #${job.id} at ${new Date().toLocaleTimeString()}`;
      await save();
      await runJob(job);
    }
  } catch (e) {
    state.lastError = "bot not reachable (is `pto serve` running?): " + e.message;
  } finally {
    busy = false;
    await save();
  }
}

function notify(title, message) {
  chrome.notifications.create({ type: "basic", iconUrl: "icon.png", title, message, priority: 2 });
}

function bufToB64(buf) {
  const bytes = new Uint8Array(buf);
  let bin = "";
  for (let i = 0; i < bytes.length; i += 0x8000) bin += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
  return btoa(bin);
}

async function inject(tabId) {
  await chrome.scripting.executeScript({ target: { tabId }, files: ["page.js"] });
}

async function call(tabId, fn, ...args) {
  const [r] = await chrome.scripting.executeScript({
    target: { tabId },
    func: (name, a) => window.__pto[name](...a),
    args: [fn, args],
  });
  return r ? r.result : undefined;
}

async function waitLoaded(tabId, ms) {
  const end = Date.now() + ms;
  while (Date.now() < end) {
    const t = await chrome.tabs.get(tabId);
    if (t.status === "complete") return t;
    await sleep(500);
  }
  throw new Error("page load timed out");
}

async function onLoginPage(tabId, job) {
  const t = await chrome.tabs.get(tabId);
  return job.login_markers.some((m) => t.url.includes(m));
}

async function waitForHuman(tabId, job, isClear) {
  const t = await chrome.tabs.update(tabId, { active: true });
  await chrome.windows.update(t.windowId, { focused: true });
  notify(`${job.platform} needs you`, "A CAPTCHA appeared. Solve it in the open tab; the upload continues by itself.");
  const end = Date.now() + job.captcha_wait_minutes * 60000;
  while (Date.now() < end) {
    await sleep(4000);
    try {
      await inject(tabId);
      if (await isClear()) return true;
    } catch (_) { /* page navigating */ }
  }
  return false;
}

async function waitAfterSubmit(tabId, job) {
  const end = Date.now() + job.submit_timeout_s * 1000;
  while (Date.now() < end) {
    await sleep(2000);
    const t = await chrome.tabs.get(tabId).catch(() => null);
    if (!t) return { ok: false, error: "the upload tab was closed" };
    if (t.status === "complete" && !job.still_on.some((s) => t.url.includes(s))) return { ok: true, url: t.url };
    try {
      await inject(tabId);
      if (await call(tabId, "captchaPresent")) {
        const cleared = await waitForHuman(tabId, job, async () => !(await call(tabId, "captchaPresent")));
        if (!cleared) return { ok: false, status: "captcha_timeout", error: "CAPTCHA was not solved in time" };
      }
    } catch (_) { /* navigating */ }
  }
  let errs = "";
  try { await inject(tabId); errs = await call(tabId, "pageErrors"); } catch (_) {}
  return { ok: false, error: "no confirmation after submit" + (errs ? `: ${errs}` : "") };
}

async function runJob(job) {
  const res = { job_id: job.id, status: "failed", url: null, error: null, data: null };
  let tabId = null;
  let keepTab = false;
  try {
    let fileB64 = null;
    if (job.kind === "upload") {
      const fr = await api(`/api/ext/file/${job.id}`, { method: "GET" });
      if (!fr.ok) throw new Error(`could not fetch the design file (HTTP ${fr.status})`);
      fileB64 = bufToB64(await fr.arrayBuffer());
    }
    tabId = (await chrome.tabs.create({ url: job.url, active: false })).id;
    await waitLoaded(tabId, 90000);
    if (await onLoginPage(tabId, job)) {
      keepTab = true;
      res.status = "needs_login";
      res.error = `not logged in to ${job.platform} in this Chrome profile`;
      notify(`Log in to ${job.platform}`, "The upload tab is open on the login page. Log in; the bot retries later.");
      return;
    }
    await inject(tabId);
    const readySel = job.selectors[job.ready_key] || [];
    let ready = await call(tabId, "waitFor", readySel, 20000, false);
    if (!ready && (await call(tabId, "captchaPresent"))) {
      ready = await waitForHuman(tabId, job, () => call(tabId, "waitFor", readySel, 3000, false));
      if (!ready) { keepTab = true; res.status = "captcha_timeout"; res.error = "CAPTCHA was not solved in time"; return; }
    }
    if (!ready) {
      keepTab = true;
      if (await onLoginPage(tabId, job)) { res.status = "needs_login"; res.error = "redirected to login"; return; }
      res.error = `upload form not found at ${(await chrome.tabs.get(tabId)).url}`;
      return;
    }
    if (job.kind === "inspect") {
      res.status = "inspect";
      res.data = await call(tabId, "dumpFields");
      res.url = (await chrome.tabs.get(tabId)).url;
      return;
    }
    const filled = await call(tabId, "runSteps", job.steps, job.selectors, fileB64, job.file_name);
    fileB64 = null;
    if (!filled || !filled.ok) { keepTab = true; res.error = (filled && filled.error) || "form fill failed"; return; }
    if (job.dry_run) {
      keepTab = true; // leave the filled-in form open so you can look at it
      res.status = "dry_run";
      res.url = (await chrome.tabs.get(tabId)).url;
      return;
    }
    if (!(await call(tabId, "clickSubmit", job.selectors[job.submit_key] || []))) {
      keepTab = true; res.error = "submit button not found"; return;
    }
    const done = await waitAfterSubmit(tabId, job);
    if (done.ok) { res.status = "success"; res.url = done.url; }
    else { keepTab = true; res.status = done.status || "failed"; res.error = done.error; }
  } catch (e) {
    keepTab = true;
    res.error = String((e && e.message) || e);
  } finally {
    if (tabId !== null && !keepTab) {
      await sleep(2500);
      chrome.tabs.remove(tabId).catch(() => {});
    }
    await api("/api/ext/result", { method: "POST", body: JSON.stringify(res) }).catch(() => {});
  }
}
