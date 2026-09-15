/* Popup: injects the fill engine into the current tab, runs it, and reports what it did. */

async function loadProfile() {
  const res = await fetch(chrome.runtime.getURL("profile.json"));
  return res.json();
}

function render(result) {
  const out = document.getElementById("out");
  if (!result) { out.innerHTML = '<div class="warn">Could not run on this page. Open the actual ' +
                                 'application form, then try again.</div>'; return; }
  const { filled, skipped, details } = result;
  const answered = details.filter((d) => d.a);
  const missed = details.filter((d) => !d.a && d.q);
  let html = `<div class="ok">✅ Filled ${filled} field${filled === 1 ? "" : "s"}</div>`;
  for (const d of answered) {
    html += `<div class="row"><div class="q">${escapeHtml(d.q).slice(0, 90)}</div>` +
            `<div class="a">${escapeHtml(String(d.a)).slice(0, 140)}</div></div>`;
  }
  if (missed.length) {
    html += `<div class="row miss"><b>${missed.length} left for you</b> (open questions or fields ` +
            `I had no answer for):</div>`;
    for (const d of missed.slice(0, 12)) {
      html += `<div class="row"><div class="q miss">• ${escapeHtml(d.q).slice(0, 100)}</div></div>`;
    }
  }
  out.innerHTML = html;
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

document.getElementById("fill").addEventListener("click", async () => {
  const out = document.getElementById("out");
  out.textContent = "Filling…";
  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    const profile = await loadProfile();
    // inject the engine, then run it with the profile
    await chrome.scripting.executeScript({ target: { tabId: tab.id }, files: ["fill.js"] });
    const [res] = await chrome.scripting.executeScript({
      target: { tabId: tab.id },
      func: (p) => window.__jobAutofillRun(p),
      args: [profile],
    });
    render(res && res.result);
  } catch (e) {
    out.innerHTML = `<div class="warn">${escapeHtml(e.message || String(e))}</div>`;
  }
});

document.getElementById("copy").addEventListener("click", async () => {
  const p = await loadProfile();
  const a = p.answers || {};
  const lines = [
    `Name: ${a.name}`, `Email: ${a.email}`, `Phone: ${a.phone}`,
    `Location: ${a.city_location || a.location}`, `Address: ${a.addr_full || ""}`,
    `Right to work: ${a.rtw_uk}`, `Nationality: ${a.nationality}`,
    `Notice: ${a.notice}`, `Salary: ${a.salary}`, `Current company: ${a.current_company}`,
    `Years: ${a.years}`, `LinkedIn: ${a.linkedin}`, `GitHub: ${a.github}`,
    `Gender: ${a.gender} · Ethnicity: ${a.ethnicity} · DOB: ${a.dob}`,
    "",
    "— Common answers —",
    ...(p.question_bank || []).map((q) => `Q: ${q.q}\nA: ${q.a}\n`),
  ];
  await navigator.clipboard.writeText(lines.join("\n"));
  document.getElementById("out").innerHTML =
    '<div class="ok">📋 Answer sheet copied — paste anywhere.</div>';
});
