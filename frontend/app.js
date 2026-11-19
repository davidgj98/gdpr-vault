"use strict";

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => [...document.querySelectorAll(sel)];

const CATEGORY = {
  PERSON: "person", DNI: "id", NIE: "id", NSS: "id",
  IBAN: "finance", CREDIT_CARD: "finance",
  PHONE: "contact", EMAIL: "contact",
  ADDRESS: "place", POSTAL_CODE: "place",
  ORGANIZATION: "org",
  DATE_OF_BIRTH: "sensitive", HEALTH: "sensitive",
};
const TYPE_NAME = {
  PERSON: "Person", DNI: "DNI", NIE: "NIE", NSS: "Social Security", IBAN: "IBAN", CREDIT_CARD: "Card",
  PHONE: "Phone", EMAIL: "Email", ADDRESS: "Address", POSTAL_CODE: "Postal code",
  ORGANIZATION: "Organization", DATE_OF_BIRTH: "Date of birth", HEALTH: "Health (Art. 9)",
};
const STEPS = ["extracting", "detecting", "anonymizing", "done"];

const state = { input: "pdf", file: null, job: null, mapping: null, ttlTimer: null, polling: false };

// ── Utilities ────────────────────────────────────────────────────────────────
const esc = (s) => s.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const fmtSize = (b) => (b < 1024 * 1024 ? `${(b / 1024).toFixed(1)} KB` : `${(b / 1024 / 1024).toFixed(1)} MB`);

let toastTimer;
function toast(msg, isError = false) {
  const t = $("#toast");
  t.textContent = msg;
  t.className = "toast" + (isError ? " error" : "");
  t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (t.hidden = true), 3500);
}

async function api(path, opts = {}) {
  const res = await fetch(path, opts);
  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`;
    try { detail = (await res.json()).detail || detail; } catch { /* not JSON */ }
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return res.status === 204 ? null : res.json();
}

// ── LLM status ───────────────────────────────────────────────────────────────
async function refreshHealth() {
  const pill = $("#llmStatus");
  try {
    const { llm } = await api("/api/health");
    if (llm.available) {
      pill.className = "pill ok";
      pill.lastElementChild.textContent = `LLM · ${llm.model}`;
      pill.title = "Local LLM ready (Ollama)";
    } else {
      pill.className = "pill warn";
      pill.lastElementChild.textContent = llm.reachable ? `LLM · pulling ${llm.model}?` : "LLM offline · regex only";
      pill.title = llm.reachable
        ? `Ollama is up but ${llm.model} is not pulled yet`
        : "Ollama is not reachable: only structured identifiers will be detected";
    }
  } catch {
    pill.className = "pill warn";
    pill.lastElementChild.textContent = "API offline";
  }
}

// ── Input ────────────────────────────────────────────────────────────────────
function setInput(kind) {
  state.input = kind;
  $$(".seg-btn").forEach((b) => {
    const on = b.dataset.input === kind;
    b.classList.toggle("active", on);
    b.setAttribute("aria-selected", on);
  });
  $("#input-pdf").hidden = kind !== "pdf";
  $("#input-text").hidden = kind !== "text";
  updateRunButton();
}

function setFile(file) {
  if (!file) return;
  if (!/\.pdf$/i.test(file.name) && file.type !== "application/pdf") {
    toast("Only PDF files are supported.", true);
    return;
  }
  state.file = file;
  $("#fileName").textContent = file.name;
  $("#fileSize").textContent = fmtSize(file.size);
  $("#fileChip").hidden = false;
  $("#drop").hidden = true;
  updateRunButton();
}

function clearFile() {
  state.file = null;
  $("#fileInput").value = "";
  $("#fileChip").hidden = true;
  $("#drop").hidden = false;
  updateRunButton();
}

function updateRunButton() {
  const hasInput = state.input === "pdf" ? !!state.file : $("#textInput").value.trim().length > 0;
  const hasLayer = $("#useRegex").checked || $("#useLlm").checked;
  $("#run").disabled = !hasInput || !hasLayer || state.polling;
}

// ── Processing ───────────────────────────────────────────────────────────────
function setProgress(status, text, isError = false) {
  $("#progress").hidden = false;
  const idx = STEPS.indexOf(status);
  $$("#steps li").forEach((li, i) => {
    li.classList.toggle("done", idx > i || status === "done");
    li.classList.toggle("current", idx === i && status !== "done");
  });
  $("#steps").classList.toggle("error", isError);
  const p = $("#progressText");
  p.textContent = text;
  p.classList.toggle("error", isError);
}

async function run() {
  const form = new FormData();
  if (state.input === "pdf") form.append("file", state.file);
  else form.append("text", $("#textInput").value);
  form.append("use_regex", $("#useRegex").checked);
  form.append("use_llm", $("#useLlm").checked);

  if (state.job) deleteJob(true);
  state.polling = true;
  updateRunButton();
  setProgress("extracting", "Uploading…");

  try {
    const { job_id } = await api("/api/jobs", { method: "POST", body: form });
    let job;
    for (;;) {
      job = await api(`/api/jobs/${job_id}`);
      if (job.status === "done" || job.status === "error") break;
      setProgress(job.status === "queued" ? "extracting" : job.status, job.progress + "…");
      await sleep(400);
    }
    if (job.status === "error") throw new Error(job.error);
    state.job = job;
    state.mapping = job.mapping;
    const a = job.audit;
    setProgress("done", `${a.occurrences_masked} occurrences of ${a.entities_found} entities masked in ${(a.duration_ms / 1000).toFixed(1)} s`);
    render(job);
  } catch (e) {
    setProgress($$("#steps li.current")[0]?.dataset.step || "extracting", e.message, true);
  } finally {
    state.polling = false;
    updateRunButton();
  }
}

async function deleteJob(silent = false) {
  if (!state.job) return;
  const id = state.job.job_id;
  try { await api(`/api/jobs/${id}`, { method: "DELETE" }); } catch { /* already expired */ }
  state.job = null;
  clearInterval(state.ttlTimer);
  $("#retention").hidden = true;
  if (!silent) {
    resetResults();
    $("#progress").hidden = true;
    toast("Job erased from server memory.");
  }
}

// ── Rendering ────────────────────────────────────────────────────────────────
function highlight(text, items, startKey, endKey, labelOf) {
  let html = "";
  let cursor = 0;
  for (const r of items) {
    html += esc(text.slice(cursor, r[startKey]));
    const cat = CATEGORY[r.type] || "id";
    html += `<mark class="t-${cat}" data-label="${esc(r.label)}" title="${esc(TYPE_NAME[r.type] || r.type)} · ${esc(r.label)}">${esc(labelOf(r))}</mark>`;
    cursor = r[endKey];
  }
  return html + esc(text.slice(cursor));
}

function render(job) {
  const reps = job.replacements;
  const a = job.audit;

  // Compare
  $("#emptyState").hidden = true;
  $("#compare").hidden = false;
  $("#origText").innerHTML = highlight(job.original_text, reps, "start", "end", (r) => job.original_text.slice(r.start, r.end));
  $("#anonText").innerHTML = highlight(job.anonymized_text, reps, "out_start", "out_end", (r) => r.label);
  $("#origMeta").textContent = [a.pages && `${a.pages} page${a.pages > 1 ? "s" : ""}`, `${a.characters.toLocaleString()} chars`].filter(Boolean).join(" · ");

  const present = [...new Set(reps.map((r) => r.type))];
  $("#legend").innerHTML = present
    .map((t) => `<span class="t-${CATEGORY[t]}"><i></i>${esc(TYPE_NAME[t] || t)}</span>`)
    .join("");

  // Warnings
  const w = $("#warnings");
  w.hidden = !a.warnings.length;
  w.textContent = a.warnings.join(" ");

  // PDF
  const hasPdf = job.has_redacted_pdf;
  $("#pdfFrame").hidden = !hasPdf;
  $("#pdfEmpty").hidden = hasPdf;
  if (hasPdf) $("#pdfFrame").src = `/api/jobs/${job.job_id}/files/redacted.pdf?inline=true`;

  renderAudit(job);
  startTtl(job.expires_in);
  $("#restoreIn").value ||= job.anonymized_text.slice(0, 600);
  $("#mappingInfo").textContent = `Using mapping from this job (${Object.keys(job.mapping).length} labels)`;
}

function renderAudit(job) {
  const a = job.audit;
  const llm = a.layers.llm;
  const byMethod = (m) => job.replacements.filter((r) => r.method === m).length;
  $("#auditEmpty").hidden = true;
  $("#audit").hidden = false;
  $("#auditCount").textContent = a.entities_found;

  const llmStat = llm.status === "ok"
    ? `<div class="stat"><b>${byMethod("llm") + byMethod("rule")}</b><span>via LLM · ${esc(llm.model)}</span></div>`
    : `<div class="stat llm-off"><b>LLM ${esc(llm.status)}</b><span>semantic layer</span></div>`;
  $("#stats").innerHTML = `
    <div class="stat"><b>${a.entities_found}</b><span>unique entities</span></div>
    <div class="stat"><b>${a.occurrences_masked}</b><span>occurrences masked</span></div>
    <div class="stat"><b>${byMethod("regex")}</b><span>via regex + checksum</span></div>
    ${llmStat}`;

  const max = Math.max(1, ...Object.values(a.by_type));
  $("#bars").innerHTML = Object.entries(a.by_type)
    .map(([t, n]) => `<div class="bar t-${CATEGORY[t]}"><span>${esc(TYPE_NAME[t] || t)}</span>
      <div class="track"><div class="fill" data-w="${(n / max) * 100}"></div></div><span class="n">${n}</span></div>`)
    .join("") || `<p class="empty small">No personal data found.</p>`;
  // Set via CSSOM: the CSP forbids inline style attributes.
  $$("#bars .fill").forEach((f) => (f.style.width = `${f.dataset.w}%`));

  const base = `/api/jobs/${job.job_id}/files`;
  const files = [
    ["anonymized.txt", "safe to share"],
    ...(job.has_redacted_pdf ? [["redacted.pdf", "text removed, metadata scrubbed"]] : []),
    ["audit.json", "no raw personal data"],
    ["mapping.json", "re-identification key · keep private", "sensitive"],
  ];
  $("#downloads").innerHTML = files
    .map(([f, note, cls = ""]) => `<a class="dl ${cls}" href="${base}/${f}" download><code>${f}</code><small>${note}</small></a>`)
    .join("");

  $("#entityRows").innerHTML = a.entities
    .map((e) => `<tr>
      <td><mark class="t-${CATEGORY[e.type]}">${esc(e.label)}</mark></td>
      <td>${esc(TYPE_NAME[e.type] || e.type)}</td>
      <td>${esc(e.preview)}</td>
      <td>${e.methods.map((m) => `<span class="method ${m}">${m}</span>`).join("")}</td>
      <td class="num">${e.occurrences}</td></tr>`)
    .join("");
}

function startTtl(seconds) {
  clearInterval(state.ttlTimer);
  const end = Date.now() + seconds * 1000;
  $("#retention").hidden = false;
  const tick = () => {
    const left = Math.max(0, Math.round((end - Date.now()) / 1000));
    $("#ttl").textContent = `${String(Math.floor(left / 60)).padStart(2, "0")}:${String(left % 60).padStart(2, "0")}`;
    if (!left) {
      clearInterval(state.ttlTimer);
      $("#retention").hidden = true;
      state.job = null;
      $("#pdfFrame").removeAttribute("src");
      toast("Job expired and was erased from the server. Downloads are no longer available.");
    }
  };
  tick();
  state.ttlTimer = setInterval(tick, 1000);
}

function resetResults() {
  $("#emptyState").hidden = false;
  $("#compare").hidden = true;
  $("#warnings").hidden = true;
  $("#pdfFrame").hidden = true;
  $("#pdfFrame").removeAttribute("src");
  $("#pdfEmpty").hidden = false;
  $("#audit").hidden = true;
  $("#auditEmpty").hidden = false;
  $("#auditCount").textContent = "";
}

// Hovering a highlight lights up every occurrence of the same label on both sides.
function linkHover(e) {
  const mark = e.target.closest("mark[data-label]");
  $$("mark.hot").forEach((m) => m.classList.remove("hot"));
  if (mark) $$(`mark[data-label="${CSS.escape(mark.dataset.label)}"]`).forEach((m) => m.classList.add("hot"));
}

// ── Restore ──────────────────────────────────────────────────────────────────
async function restore() {
  if (!state.mapping) return toast("Anonymize a document or load a mapping.json first.", true);
  try {
    const { text, restored } = await api("/api/deanonymize", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text: $("#restoreIn").value, mapping: state.mapping }),
    });
    $("#restoreOut").textContent = text;
    toast(`${restored} label${restored === 1 ? "" : "s"} restored.`);
  } catch (e) {
    toast(e.message, true);
  }
}

async function loadMapping(file) {
  try {
    const data = JSON.parse(await file.text());
    const mapping = data.mapping || data;
    if (typeof mapping !== "object" || !Object.keys(mapping).every((k) => /^\[[A-Z_]+_\d+\]$/.test(k))) throw new Error();
    state.mapping = mapping;
    $("#mappingInfo").textContent = `Using ${file.name} (${Object.keys(mapping).length} labels)`;
  } catch {
    toast("That file is not a GDPR-Vault mapping.json.", true);
  }
}

// ── Wiring ───────────────────────────────────────────────────────────────────
function init() {
  $$(".seg-btn").forEach((b) => b.addEventListener("click", () => setInput(b.dataset.input)));

  const drop = $("#drop");
  $("#fileInput").addEventListener("change", (e) => setFile(e.target.files[0]));
  ["dragenter", "dragover"].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.add("over"); }));
  ["dragleave", "drop"].forEach((ev) => drop.addEventListener(ev, () => drop.classList.remove("over")));
  drop.addEventListener("drop", (e) => { e.preventDefault(); setFile(e.dataTransfer.files[0]); });
  $("#fileClear").addEventListener("click", clearFile);

  $("#loadSample").addEventListener("click", async () => {
    const blob = await (await fetch("samples/contrato_ficticio.pdf")).blob();
    setFile(new File([blob], "contrato_ficticio.pdf", { type: "application/pdf" }));
  });

  $("#textInput").addEventListener("input", updateRunButton);
  $("#useRegex").addEventListener("change", updateRunButton);
  $("#useLlm").addEventListener("change", updateRunButton);
  $("#run").addEventListener("click", run);
  $("#deleteJob").addEventListener("click", () => deleteJob());

  $$(".tab").forEach((tab) => tab.addEventListener("click", () => {
    $$(".tab").forEach((t) => t.classList.toggle("active", t === tab));
    $$(".pane").forEach((p) => p.classList.toggle("active", p.id === `pane-${tab.dataset.tab}`));
  }));

  ["#origText", "#anonText"].forEach((s) => {
    $(s).addEventListener("mouseover", linkHover);
    $(s).addEventListener("mouseleave", linkHover);
  });

  $("#copyAnon").addEventListener("click", async () => {
    if (!state.job) return;
    await navigator.clipboard.writeText(state.job.anonymized_text);
    toast("Pseudonymized text copied.");
  });

  $("#restoreBtn").addEventListener("click", restore);
  $("#mappingFile").addEventListener("change", (e) => e.target.files[0] && loadMapping(e.target.files[0]));

  refreshHealth();
  setInterval(refreshHealth, 15000);
  updateRunButton();
}

init();
