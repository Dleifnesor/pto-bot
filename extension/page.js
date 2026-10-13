// Injected into the upload page (extension's isolated world). Mirrors BrowserUploader.run_steps in Python.
(() => {
  if (window.__pto) return;
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const jitter = (a, b) => sleep(a + Math.random() * (b - a));
  const visible = (el) => !!(el && (el.offsetWidth || el.offsetHeight || el.getClientRects().length));

  // Selector syntax shared with the Python side: CSS, "label:Text", "text:Text", "css:has-text('Text')".
  function one(sel) {
    try {
      if (sel.startsWith("label:")) {
        const want = sel.slice(6).trim().toLowerCase();
        for (const l of document.querySelectorAll("label")) {
          if (!l.textContent.trim().toLowerCase().includes(want)) continue;
          if (l.htmlFor && document.getElementById(l.htmlFor)) return document.getElementById(l.htmlFor);
          const inner = l.querySelector("input,textarea,select");
          if (inner) return inner;
        }
        return null;
      }
      if (sel.startsWith("text:")) {
        const want = sel.slice(5).trim().toLowerCase();
        return [...document.querySelectorAll("button,a,label,span")].find((e) => e.textContent.trim().toLowerCase().includes(want)) || null;
      }
      const m = sel.match(/^(.*):has-text\((['"])(.*)\2\)$/);
      if (m) return [...document.querySelectorAll(m[1] || "*")].find((e) => e.textContent.includes(m[3])) || null;
      if (sel.startsWith("text=") || sel.startsWith("role:")) return null; // Playwright-only syntax
      return document.querySelector(sel);
    } catch (_) {
      return null;
    }
  }

  function find(cands, needVisible) {
    for (const s of cands || []) {
      const el = one(s);
      if (el && (!needVisible || visible(el))) return el;
    }
    return null;
  }

  async function waitFor(cands, timeoutMs, needVisible) {
    const end = Date.now() + timeoutMs;
    do {
      if (find(cands, needVisible)) return true;
      await sleep(500);
    } while (Date.now() < end);
    return false;
  }

  function setValue(el, v) {
    const proto = el.tagName === "TEXTAREA" ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    el.focus();
    Object.getOwnPropertyDescriptor(proto, "value").set.call(el, ""); // works with React-controlled inputs too
    el.dispatchEvent(new Event("input", { bubbles: true }));
    Object.getOwnPropertyDescriptor(proto, "value").set.call(el, v);
    el.dispatchEvent(new Event("input", { bubbles: true }));
    el.dispatchEvent(new Event("change", { bubbles: true }));
    el.blur();
  }

  function setFile(el, b64, name) {
    const bin = atob(b64);
    const bytes = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
    const type = name.toLowerCase().endsWith(".jpg") ? "image/jpeg" : "image/png";
    const dt = new DataTransfer();
    dt.items.add(new File([bytes], name, { type }));
    el.files = dt.files;
    el.dispatchEvent(new Event("input", { bubbles: true }));
    el.dispatchEvent(new Event("change", { bubbles: true }));
  }

  function check(el) {
    if (el.type === "checkbox" || el.type === "radio") {
      if (!el.checked) el.click();
      if (!el.checked) {
        el.checked = true;
        el.dispatchEvent(new Event("change", { bubbles: true }));
      }
    } else {
      el.click();
    }
  }

  async function runSteps(steps, selectors, b64, fileName) {
    for (const st of steps) {
      const cands = selectors[st.key] || [];
      if (st.op === "pause") { await sleep(st.seconds * 1000); continue; }
      if (st.op === "wait") {
        const ok = await waitFor(cands, (st.timeout_s || 60) * 1000, true);
        if (!ok && st.fallback_s) await sleep(st.fallback_s * 1000);
        continue;
      }
      const el = find(cands, !["file", "check"].includes(st.op));
      if (!el) {
        if (st.optional) continue;
        return { ok: false, error: `field '${st.key}' not found (selectors: ${cands.join(" | ")})` };
      }
      if (el.scrollIntoView) el.scrollIntoView({ block: "center" });
      if (st.op === "file") setFile(el, b64, fileName);
      else if (st.op === "fill") setValue(el, st.value);
      else if (st.op === "check") check(el);
      else if (st.op === "tags_enter") {
        for (const v of st.values) {
          setValue(el, v);
          el.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", code: "Enter", keyCode: 13, bubbles: true }));
          await jitter(300, 700);
        }
      }
      await jitter(500, 1500);
    }
    return { ok: true };
  }

  function clickSubmit(cands) {
    const el = find(cands, true);
    if (!el) return false;
    setTimeout(() => el.click(), 200); // return before the page navigates away
    return true;
  }

  function captchaPresent() {
    const frames = ["iframe[title*='challenge' i]", "iframe[src*='hcaptcha.com']", "iframe[src*='challenges.cloudflare.com']",
      "#challenge-form", "#px-captcha"];
    if (frames.some((s) => visible(document.querySelector(s)))) return true;
    const t = (document.body && document.body.innerText) || "";
    return /verify (that )?you are (a )?human|press (&|and) hold/i.test(t);
  }

  function pageErrors() {
    return [...document.querySelectorAll(".error,.errors,.alert,.flash,[role=alert],[class*='error' i]")]
      .filter(visible).map((e) => e.textContent.trim()).filter(Boolean).slice(0, 5).join(" | ").slice(0, 500);
  }

  function dumpFields() {
    return [...document.querySelectorAll("input,textarea,select,button,label,[contenteditable=true]")].map((e) => ({
      tag: e.tagName.toLowerCase(), type: e.type || null, id: e.id || null, name: e.name || null,
      text: (e.innerText || e.value || e.placeholder || "").slice(0, 80), for: e.htmlFor || null,
      aria: e.getAttribute("aria-label"), visible: visible(e),
    }));
  }

  window.__pto = { waitFor, runSteps, clickSubmit, captchaPresent, pageErrors, dumpFields };
})();
