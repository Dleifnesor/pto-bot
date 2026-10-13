const $ = (s, el = document) => el.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const api = async (path, opts = {}) => {
  const r = await fetch(path, opts);
  if (!r.ok) throw new Error(await r.text());
  const ct = r.headers.get("content-type") || "";
  return ct.includes("json") ? r.json() : r.text();
};
const fmt = (iso) => (iso ? new Date(iso).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }) : "—");
let state = { tab: "overview", status: null };

function pill(label, cls) { return `<span class="pill ${cls}">${esc(label)}</span>`; }

function extPill(b) {
  if (!b || b.mode !== "extension") return pill("Bot browser profile", "");
  const seen = b.extension_last_seen ? (Date.now() - new Date(b.extension_last_seen)) / 60000 : null;
  const live = seen !== null && seen < 3;
  const jobs = b.jobs_in_flight ? ` · ${b.jobs_in_flight} upload${b.jobs_in_flight > 1 ? "s" : ""} in flight` : "";
  return pill((live ? "Chrome extension connected" : `Chrome extension ${seen === null ? "never seen" : `last seen ${Math.round(seen)} min ago`}`) + jobs, live ? "ok" : "warn");
}

async function refreshStatus() {
  const s = await api("/api/status");
  state.status = s;
  $("#pills").innerHTML = [
    pill(s.paused ? "Paused" : "Running", s.paused ? "warn" : "ok"),
    pill(s.generating ? "Generating…" : "Idle", s.generating ? "warn" : ""),
    pill("ComfyUI " + (s.comfyui ? "up" : "off"), s.comfyui ? "ok" : ""),
    pill("Ollama " + (s.ollama ? "up" : "down"), s.ollama ? "ok" : "bad"),
    pill(`${s.disk_free_gb} GB free`, s.disk_free_gb < 10 ? "bad" : ""),
    extPill(s.browser),
  ].join("");
  $("#btn-pause").textContent = s.paused ? "Resume" : "Pause";
  const c = s.counts;
  const n = (k) => c[k] || 0;
  $("#stats").innerHTML = [
    ["Ready stickers", n("sticker:ready")], ["Ready posters", n("poster:ready")], ["Planned", n("sticker:planned") + n("poster:planned")],
    ["Failed QA", n("sticker:failed") + n("poster:failed")], ["Trends fetched", fmt(s.trends_last_fetch)], ["Last night run", fmt(s.last_night_run)],
  ].map(([k, v]) => `<div class="stat"><span class="muted">${k}</span><b>${esc(v)}</b></div>`).join("");
  $("#platforms").innerHTML = Object.entries(s.platforms).map(([name, p]) => `
    <div class="card"><h3>${name}</h3>
      <div>${p.enabled ? (p.live ? '<span class="badge success">live</span>' : '<span class="badge dry_run">dry run</span>') : '<span class="badge">disabled</span>'}</div>
      <div class="bar"><i style="width:${Math.min(100, (100 * p.today) / Math.max(1, p.limit))}%"></i></div>
      <div class="muted">${p.today} / ${p.limit} today · next ${p.next ? fmt(p.next) : "asap"}</div>
    </div>`).join("");
  $("#jobs").innerHTML = `<table><tr><th>Job</th><th>Next run</th></tr>${s.jobs.map((j) => `<tr><td>${esc(j.name)}</td><td>${fmt(j.next_run)}</td></tr>`).join("")}
    <tr><td>GPU window</td><td>${s.generation_window.start}–${s.generation_window.end}</td></tr>
    <tr><td>Upload window</td><td>${s.upload_window.start}–${s.upload_window.end}</td></tr></table>`;
}

function tile(d) {
  const thumb = d.files && d.files.thumb ? `<img loading="lazy" src="/files/${esc(d.files.thumb)}">` : '<span class="muted">no image yet</span>';
  const posted = (d.posted || "").split(",").filter(Boolean).map((p) => { const [pl, st] = p.split(":"); return `<span class="badge ${st}">${pl}</span>`; }).join("");
  return `<div class="tile" data-id="${d.id}"><div class="img checker">${thumb}</div><div class="meta">
    <div class="t" title="${esc(d.title)}">${esc(d.title || "(untitled)")}</div>
    <div><span class="badge ${d.status}">${d.status}</span><span class="badge">${esc(d.style)}</span>${posted}</div></div></div>`;
}

async function loadDesigns() {
  const q = new URLSearchParams({ limit: 120 });
  if ($("#f-status").value) q.set("status", $("#f-status").value);
  if ($("#f-kind").value) q.set("kind", $("#f-kind").value);
  const rows = await api("/api/designs?" + q);
  $("#designs").innerHTML = rows.map(tile).join("") || '<p class="muted">No designs yet.</p>';
}

async function loadLatest() {
  const rows = await api("/api/designs?status=ready&limit=12");
  $("#latest").innerHTML = rows.map(tile).join("") || '<p class="muted">Nothing ready yet. Click "Generate now" or wait for the night run.</p>';
}

async function showDesign(id) {
  const d = await api(`/api/designs/${id}`);
  const v = (d.qa && d.qa.vision) || {};
  const ch = (d.qa && d.qa.checks) || {};
  const attempts = (d.qa && d.qa.attempts) || [];
  const big = d.files && d.files.final ? `/files/${d.files.final}` : null;
  const r = (d.trend && d.trend.research) || {};
  const researched = r.page ? `<a href="${esc(r.url)}" target="_blank">${esc(r.page)}</a>${(r.characters || []).length
    ? " · " + esc(r.characters.map((c) => c.name).join(", ")) : ""}` : "—";
  $("#detail-body").innerHTML = `<div class="detail">
    <div><div class="big checker">${big ? `<img src="${esc(big)}">` : '<span class="muted">not rendered</span>'}</div>
      ${d.files && d.files.raw ? `<p class="muted">Raw render: <a href="/files/${esc(d.files.raw)}" target="_blank">open</a>${big ? ` · Final file: <a href="${esc(big)}" target="_blank">open</a>` : ""}</p>` : ""}
    </div>
    <div>
      <h2 style="margin-top:0">${esc(d.title)}</h2>
      <div class="kv">
        <div>Status</div><div><span class="badge ${d.status}">${d.status}</span> ${d.error ? `<span class="muted">${esc(d.error)}</span>` : ""}</div>
        <div>Trend</div><div>${esc(d.trend ? `${d.trend.title} (${d.trend.source})` : "—")}</div>
        <div>Theme</div><div>${esc(d.theme || "—")}</div>
        <div>Research</div><div class="muted">${researched}</div>
        <div>Licensed fan art</div><div>${esc(d.license || "—")}</div>
        <div>${d.license ? "Kept out" : "Pivoted from"}</div><div>${esc((d.protected_terms || []).join(", ") || "—")}</div>
        <div>Style</div><div>${esc(d.style)} · ${esc(d.kind)}</div>
        <div>Slogan</div><div>${esc(d.slogan || "—")}</div>
        <div>Tags</div><div>${(d.tags || []).map((t) => `<span class="badge">${esc(t)}</span>`).join("")}</div>
        <div>Description</div><div>${esc(d.description)}</div>
        <div>Prompt</div><div class="muted">${esc(d.prompt || d.subject)}</div>
        <div>Vision QA</div><div>${v.score != null ? `score ${v.score}/10 ${(v.issues || []).length ? "· " + esc(v.issues.join("; ")) : ""}` : "—"}</div>
        <div>Checks</div><div class="muted">${ch.sharpness != null ? `sharpness ${ch.sharpness} · coverage ${(+ch.coverage || 0).toFixed(2)} · pieces ${ch.fragments ?? "—"}` : "—"}</div>
        <div>Attempts</div><div class="muted">${attempts.map((a) => `#${a.attempt}: ${esc((a.reasons || [a.error || "ok"]).join("; ") || "passed")}`).join("<br>") || "—"}</div>
      </div>
      <h2>Uploads</h2>
      ${(d.uploads || []).map((u) => `<div><span class="badge ${u.status}">${u.platform}: ${u.status}</span> ${fmt(u.created_at)}
        ${u.url ? u.url.split(" ").map((x) => `<a href="${esc(x)}" target="_blank">link</a>`).join(" ") : ""} ${u.error ? `<span class="muted">${esc(u.error)}</span>` : ""}</div>`).join("") || '<p class="muted">Not posted yet.</p>'}
      <div class="row" style="margin-top:14px">
        <button data-act="retry" data-id="${d.id}">Re-render</button>
        <button data-act="retire" data-id="${d.id}">Retire (never post)</button>
      </div>
    </div></div>`;
  $("#detail").showModal();
}

async function loadTrends() {
  const rows = await api("/api/trends");
  $("#trends").innerHTML = `<table><tr><th>Fetched</th><th>Source</th><th>Trend</th><th>Score</th><th>Status</th><th>Reason</th></tr>
    ${rows.map((t) => `<tr><td>${fmt(t.fetched_at)}</td><td>${esc(t.source)} ${esc(t.region || "")}</td><td class="wrap">${esc(t.title)}</td>
      <td>${t.score ?? "—"}</td><td><span class="badge">${esc(t.status)}</span></td><td class="wrap muted">${esc(t.score_reason || "")}</td></tr>`).join("")}</table>`;
}

async function loadUploads() {
  const rows = await api("/api/uploads");
  const shot = (p) => { if (!p) return ""; const i = p.replace(/\\/g, "/").indexOf("/screenshots/"); return i >= 0 ? `<a href="/shots/${esc(p.replace(/\\/g, "/").slice(i + 13))}" target="_blank">screenshot</a>` : ""; };
  $("#uploads").innerHTML = `<table><tr><th>When</th><th>Platform</th><th>Design</th><th>Status</th><th>Details</th></tr>
    ${rows.map((u) => `<tr><td>${fmt(u.created_at)}</td><td>${esc(u.platform)}</td><td class="wrap"><a href="#" data-open="${u.design_id}">${esc(u.title)}</a></td>
      <td><span class="badge ${u.status}">${esc(u.status)}</span></td>
      <td class="wrap">${u.url ? u.url.split(" ").map((x) => `<a href="${esc(x)}" target="_blank">listing</a>`).join(" ") : ""} ${shot(u.screenshot)} <span class="muted">${esc(u.error || "")}</span></td></tr>`).join("")}</table>`;
}

async function loadLogs() { $("#logs").textContent = await api("/api/logs?n=500"); $("#logs").scrollTop = 1e9; }
async function loadConfig() { $("#config").value = await api("/api/config"); }

const loaders = { overview: loadLatest, designs: loadDesigns, trends: loadTrends, uploads: loadUploads, logs: loadLogs, settings: loadConfig };

function switchTab(name) {
  state.tab = name;
  document.querySelectorAll("#tabs button").forEach((b) => b.classList.toggle("active", b.dataset.tab === name));
  document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("active", t.id === "tab-" + name));
  loaders[name]().catch(console.error);
}

document.addEventListener("click", async (e) => {
  const t = e.target.closest("button, .tile, a[data-open]");
  if (!t) return;
  if (t.dataset.tab) return switchTab(t.dataset.tab);
  if (t.dataset.run) { await api(`/api/run/${t.dataset.run}`, { method: "POST" }); t.textContent += " ✓"; setTimeout(refreshStatus, 1500); return; }
  if (t.dataset.login) { await api(`/api/login/${t.dataset.login}`, { method: "POST" }); return; }
  if (t.dataset.inspect) {
    try { const r = await api(`/api/inspect/${t.dataset.inspect}`, { method: "POST" }); t.textContent = `Queued (job ${r.queued})`; }
    catch (err) { t.textContent = "Needs extension mode"; }
    return;
  }
  if (t.dataset.act) { await api(`/api/designs/${t.dataset.id}/${t.dataset.act}`, { method: "POST" }); $("#detail").close(); loaders[state.tab](); return; }
  if (t.dataset.open) { e.preventDefault(); return showDesign(t.dataset.open); }
  if (t.classList.contains("tile")) return showDesign(t.dataset.id);
  if (t.id === "btn-pause") { await api("/api/pause", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ paused: !state.status.paused }) }); return refreshStatus(); }
  if (t.id === "btn-stop") { await api("/api/stop", { method: "POST" }); return; }
  if (t.id === "btn-save") {
    try { const r = await api("/api/config", { method: "PUT", headers: { "Content-Type": "text/plain" }, body: $("#config").value }); $("#save-msg").textContent = `Saved (backup ${r.backup}). ${r.note}`; }
    catch (err) { $("#save-msg").textContent = "Not saved: " + err.message; }
  }
});
$("#f-status").addEventListener("change", loadDesigns);
$("#f-kind").addEventListener("change", loadDesigns);

refreshStatus().then(() => switchTab("overview"));
setInterval(() => { refreshStatus().catch(() => {}); if (state.tab === "logs") loadLogs(); }, 10000);
