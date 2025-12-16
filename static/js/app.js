let token = null;
let lastPlain = "";

const $ = (id) => document.getElementById(id);

function setStatus(id, msg){
  $(id).textContent = msg || "";
}

function setTheme(theme){
  document.body.classList.remove("theme-dark","theme-light");
  document.body.classList.add(theme);
  localStorage.setItem("ai4_theme", theme);
}

function renderSummary(encounter){
  const d = encounter.demographics || {};
  const meta = encounter.extraction_meta || {};
  const rows = [
    ["Filename", encounter.source_filename || ""],
    ["Pages", String(meta.pages ?? "")],
    ["Extracted chars", String(meta.chars ?? "")],
    ["Scanned suspected", meta.scanned_suspected ? "Yes" : "No"],
    ["Patient name", d.patient_name || ""],
    ["DOB", d.dob || ""],
    ["PHN", d.phn || ""],
    ["Phone", d.phone || ""],
    ["Address", d.address || ""],
    ["Appointment date", d.appointment_date || ""],
    ["Clinical focus", encounter.clinical_focus || ""],
    ["Differential hint", encounter.differential_hint || ""],
  ];
  $("summaryKV").innerHTML = rows.map(([k,v]) => `
    <div class="kv-row">
      <div class="kv-key">${escapeHtml(k)}</div>
      <div class="kv-val">${escapeHtml(v || "—")}</div>
    </div>
  `).join("");
}

function renderAnalysis(analysis){
  const ddx = analysis.differential || [];
  const nextSteps = analysis.next_steps || [];
  const plan = analysis.plan || [];
  const summary = analysis.summary || "";

  const ddxHtml = ddx.length ? `
    <ol>${ddx.map(d => `
      <li>
        <strong>${escapeHtml(d.diagnosis || "")}</strong>
        <span class="muted"> (${escapeHtml(d.probability || "")})</span><br>
        <span class="small">${escapeHtml(d.rationale || "")}</span>
      </li>
    `).join("")}</ol>
  ` : `<div class="muted">No differential produced.</div>`;

  const nsHtml = nextSteps.length ? `<ul>${nextSteps.map(x => `<li>${escapeHtml(x)}</li>`).join("")}</ul>` : `<div class="muted">No next steps.</div>`;
  const planHtml = plan.length ? `<ul>${plan.map(x => `<li>${escapeHtml(x)}</li>`).join("")}</ul>` : `<div class="muted">No plan.</div>`;

  $("analysisBlock").innerHTML = `
    <h3>Clinical summary</h3>
    <p>${escapeHtml(summary)}</p>
    <h3>Ranked differential</h3>
    ${ddxHtml}
    <h3>Next steps</h3>
    ${nsHtml}
    <h3>Plan</h3>
    ${planHtml}
    <div class="tiny muted" style="margin-top:10px">
      This tool curates PubMed context and drafts structured content. It does not replace clinical judgment.
    </div>
  `;
}

function renderRefs(refs){
  if(!refs || !refs.length){
    $("refsBlock").innerHTML = `<div class="muted">No references found. If this is unexpected, try a clearer clinical focus or enable LLM + NCBI API keys.</div>`;
    return;
  }
  $("refsBlock").innerHTML = refs.map(r => `
    <div class="ref">
      <div class="ref-title"><a href="${r.url}" target="_blank" rel="noopener">${escapeHtml(r.title || "Untitled")}</a></div>
      <div class="ref-meta">${escapeHtml([r.authors, r.source, r.pubdate, "PMID " + (r.pmid || "")].filter(Boolean).join(" • "))}</div>
    </div>
  `).join("");
}

function fillDemographics(d){
  $("demoName").value = d.patient_name || "";
  $("demoDob").value = d.dob || "";
  $("demoPhn").value = d.phn || "";
  $("demoPhone").value = d.phone || "";
  $("demoAddress").value = d.address || "";
  $("demoAppt").value = d.appointment_date || "";
}

function getDemoPayload(){
  return {
    patient_name: $("demoName").value.trim(),
    dob: $("demoDob").value.trim(),
    phn: $("demoPhn").value.trim(),
    phone: $("demoPhone").value.trim(),
    address: $("demoAddress").value.trim(),
    appointment_date: $("demoAppt").value.trim(),
  };
}

function escapeHtml(s){
  return (s || "").replaceAll("&","&amp;").replaceAll("<","&lt;").replaceAll(">","&gt;");
}

async function copyRich(html){
  if(!navigator.clipboard || !window.ClipboardItem){
    await navigator.clipboard.writeText(stripTags(html));
    return;
  }
  const blob = new Blob([html], {type:"text/html"});
  const item = new ClipboardItem({"text/html": blob, "text/plain": new Blob([stripTags(html)], {type:"text/plain"})});
  await navigator.clipboard.write([item]);
}

function stripTags(html){
  const div = document.createElement("div");
  div.innerHTML = html;
  return (div.textContent || div.innerText || "").trim();
}

$("themeToggle").addEventListener("click", () => {
  const current = document.body.classList.contains("theme-light") ? "theme-light" : "theme-dark";
  setTheme(current === "theme-dark" ? "theme-light" : "theme-dark");
});

$("resetBtn").addEventListener("click", () => {
  token = null;
  lastPlain = "";
  $("analysisBlock").innerHTML = `<div class="muted">No analysis yet.</div>`;
  $("refsBlock").innerHTML = `<div class="muted">No references yet.</div>`;
  $("summaryKV").innerHTML = `<div class="kv-row"><div class="kv-key">Status</div><div class="kv-val muted">Awaiting upload</div></div>`;
  $("letterEditor").innerHTML = "";
  $("letterWarnings").textContent = "";
  $("letterBtn").disabled = true;
  $("copyRichBtn").disabled = true;
  $("copyPlainBtn").disabled = true;
  $("scanNote").hidden = true;
  setStatus("analyzeStatus","");
  setStatus("letterStatus","");
});

$("analyzeForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  setStatus("analyzeStatus","Analyzing...");
  $("analyzeBtn").disabled = true;
  $("letterBtn").disabled = true;
  $("copyRichBtn").disabled = true;
  $("copyPlainBtn").disabled = true;
  $("scanNote").hidden = true;

  const fd = new FormData(e.target);
  try{
    const res = await fetch("/api/analyze", {method:"POST", body: fd});
    const j = await res.json();
    if(!res.ok){
      throw new Error(j.error || "Analyze failed");
    }
    token = j.token;
    renderSummary(j.encounter);
    renderAnalysis(j.analysis);
    renderRefs(j.analysis.references || []);
    fillDemographics((j.encounter && j.encounter.demographics) || {});
    if(j.encounter && j.encounter.extraction_meta && j.encounter.extraction_meta.scanned_suspected){
      $("scanNote").hidden = false;
    }
    $("letterBtn").disabled = false;
    setStatus("analyzeStatus","Done.");
  }catch(err){
    setStatus("analyzeStatus", err.message || String(err));
  }finally{
    $("analyzeBtn").disabled = false;
  }
});

$("letterBtn").addEventListener("click", async () => {
  if(!token){
    setStatus("letterStatus","Analyze a PDF first.");
    return;
  }
  const referralReason = $("referralReason").value.trim();
  if(!referralReason){
    setStatus("letterStatus","Reason for referral is required.");
    return;
  }

  setStatus("letterStatus","Generating letter...");
  $("letterBtn").disabled = true;
  $("copyRichBtn").disabled = true;
  $("copyPlainBtn").disabled = true;

  const payload = {
    token,
    letter_type: $("letterType").value,
    referral_reason: referralReason,
    referring_doctor: $("referringDoctor").value.trim(),
    refer_to: $("referTo").value.trim(),
    special_requests: $("specialRequests").value.trim(),
    additional_context: $("additionalContext").value.trim(),
    demographics: getDemoPayload()
  };

  try{
    const res = await fetch("/api/letter", {method:"POST", headers: {"Content-Type":"application/json"}, body: JSON.stringify(payload)});
    const j = await res.json();
    if(!res.ok){
      if(j.missing_fields && j.missing_fields.length){
        throw new Error("Missing required demographics: " + j.missing_fields.join(", "));
      }
      throw new Error(j.error || "Letter failed");
    }
    $("letterEditor").innerHTML = j.html || "";
    lastPlain = j.plain || stripTags(j.html || "");
    $("letterWarnings").textContent = (j.warnings || []).join(" ");
    $("copyRichBtn").disabled = false;
    $("copyPlainBtn").disabled = false;
    setStatus("letterStatus","Done.");
  }catch(err){
    setStatus("letterStatus", err.message || String(err));
  }finally{
    $("letterBtn").disabled = false;
  }
});

$("copyRichBtn").addEventListener("click", async () => {
  const html = $("letterEditor").innerHTML || "";
  try{
    await copyRich(html);
    setStatus("letterStatus","Copied rich text.");
  }catch(err){
    setStatus("letterStatus","Copy failed.");
  }
});

$("copyPlainBtn").addEventListener("click", async () => {
  try{
    await navigator.clipboard.writeText(lastPlain || stripTags($("letterEditor").innerHTML || ""));
    setStatus("letterStatus","Copied plain text.");
  }catch(err){
    setStatus("letterStatus","Copy failed.");
  }
});

(function init(){
  const saved = localStorage.getItem("ai4_theme");
  setTheme(saved === "theme-light" ? "theme-light" : "theme-dark");
})();
