const out = document.getElementById("s");

async function show(msg) {
  out.textContent = msg === "poll" ? "Checking…" : out.textContent;
  const s = await chrome.runtime.sendMessage(msg);
  const when = (iso) => (iso ? new Date(iso).toLocaleTimeString() : "never");
  out.textContent = [
    `Bot: ${s.server}`,
    `Last check: ${when(s.lastPoll)}${s.busy ? " (working on a job)" : ""}`,
    `Last job: ${s.lastJob || "none yet"}`,
    s.lastError ? `Problem: ${s.lastError}` : "Status: connected",
  ].join("\n");
}

document.getElementById("poll").addEventListener("click", () => show("poll"));
show("status");
