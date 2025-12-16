let encounterToken = null;

function qs(sel){ return document.querySelector(sel); }
function setStatus(msg){ qs("#status").textContent = msg || ""; }

function setTheme(theme){
  document.documentElement.setAttribute("data-theme", theme);
  localStorage.setItem("ai4d_theme", theme);
}
function toggleTheme(){
  const cur = document.documentElement.getAttribute("data-theme") || "dark";
  setTheme(cur === "dark" ? "light" : "dark");
}

function escapeHtml(s){
  return (s || "").replaceAll("&","&amp;").replaceAll("<","&lt;").replaceAll(">","&gt;");
}

function renderDx(ddx, pubmed, query){
  const box = qs("#dxBox");
  const items = (ddx || []).map(d => `<li>${escapeHtml(d)}</li>`).join("");
  const refs = (pubmed || []).slice(0,8).map(p => {
    return `<li><a href="${p.link}" target="_blank" rel="noopener">${escapeHtml(p.title)}</a>
      <div class="refmeta">${escapeHtml([p.first_author,p.year,p.journal].filter(Boolean).join(" "))}</div></li>`;
  }).join("");
  box.innerHTML = `
    <div class="badge">PubMed query: ${escapeHtml(query || "")}</div>
    <div class="kv">
      <h3>Ranked differential</h3>
      <ul>${items}</ul>
      <h3>References</h3>
      <ol class="refs">${refs || "<div class='muted'>No references returned. Try a more detailed note.</div>"}</ol>
    </div>`;
}

function renderTx(plan){
  const box = qs("#txBox");
  const items = (plan || []).map(p => `<li>${escapeHtml(p)}</li>`).join("");
  box.innerHTML = `
    <div class="kv">
      <h3>Plan</h3>
      <ul>${items}</ul>
      <div class="muted mt10">Outputs are a structured synthesis of the uploaded note plus evidence links. Clinician judgment remains essential.</div>
    </div>`;
}

function renderLetter(html){
  const box = qs("#letterBox");
  box.innerHTML = `<div id="letterContent" contenteditable="true" class="letter-edit">${html}</div>`;
}

async function copyRich(){
  const el = qs("#letterContent");
  if(!el) return;
  const htmlData = el.innerHTML;
  const plain = el.innerText;
  try{
    const item = new ClipboardItem({
      "text/html": new Blob([htmlData], {type:"text/html"}),
      "text/plain": new Blob([plain], {type:"text/plain"}),
    });
    await navigator.clipboard.write([item]);
    setStatus("Copied rich text");
  }catch(e){
    await navigator.clipboard.writeText(plain);
    setStatus("Copied plain text (browser limited)");
  }
}

async function copyPlain(){
  const el = qs("#letterContent");
  if(!el) return;
  await navigator.clipboard.writeText(el.innerText);
  setStatus("Copied plain text");
}

function setDemoFields(d){
  qs("#d_name").value = d.name || "";
  qs("#d_dob").value = d.dob || "";
  qs("#d_phn").value = d.phn || "";
  qs("#d_phone").value = d.phone || "";
  qs("#d_address").value = d.address || "";
  qs("#d_appt").value = d.appt_date || "";
}

function getDemoOverrides(){
  return {
    name: qs("#d_name").value,
    dob: qs("#d_dob").value,
    phn: qs("#d_phn").value,
    phone: qs("#d_phone").value,
    address: qs("#d_address").value,
    appt_date: qs("#d_appt").value,
  };
}

function enableLetterButtons(enabled){
  qs("#generateLetter").disabled = !enabled;
  qs("#copyLetterRich").disabled = !enabled;
  qs("#copyLetterPlain").disabled = !enabled;
}

qs("#themeToggle").addEventListener("click", toggleTheme);

const savedTheme = localStorage.getItem("ai4d_theme");
if(savedTheme){ setTheme(savedTheme); }

qs("#uploadForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  setStatus("Analyzing...");
  enableLetterButtons(false);

  const pdfInput = qs("#pdfInput");
  if(!pdfInput.files || !pdfInput.files[0]){
    setStatus("Select a PDF first");
    return;
  }
  const fd = new FormData();
  fd.append("pdf", pdfInput.files[0]);

  try{
    const res = await fetch("/api/analyze", {method:"POST", body: fd});
    const data = await res.json();
    if(!res.ok){
      setStatus(data.error || "Analyze failed");
      return;
    }
    encounterToken = data.token;
    setDemoFields(data.demographics || {});
    renderDx(data.ddx, data.pubmed, data.pubmed_query);
    renderTx(data.plan);
    setStatus("Ready");
    enableLetterButtons(true);
  }catch(err){
    setStatus("Analyze failed");
  }
});

qs("#generateLetter").addEventListener("click", async () => {
  if(!encounterToken){
    setStatus("Upload a PDF first");
    return;
  }
  const letterType = qs("#letterType").value;
  const referring = qs("#referringDoctor").value;
  const referTo = qs("#referTo").value;
  const reason = qs("#reason").value;
  const special = qs("#special").value;
  const context = qs("#context").value;

  setStatus("Generating letter...");
  try{
    const payload = {
      token: encounterToken,
      letter_type: letterType,
      referring_doctor: referring,
      refer_to: referTo,
      reason_for_referral: reason,
      special_requests: special,
      additional_context: context,
      demographics_overrides: getDemoOverrides()
    };
    const res = await fetch("/api/letter", {method:"POST", headers: {"Content-Type":"application/json"}, body: JSON.stringify(payload)});
    const data = await res.json();
    if(!res.ok){
      if(data.missing){
        setStatus("Missing demographics: " + data.missing.join(", "));
      }else{
        setStatus(data.error || "Letter failed");
      }
      return;
    }
    renderLetter(data.html);
    setStatus("Letter ready");
    qs("#copyLetterRich").disabled = false;
    qs("#copyLetterPlain").disabled = false;
  }catch(e){
    setStatus("Letter failed");
  }
});

qs("#copyLetterRich").addEventListener("click", copyRich);
qs("#copyLetterPlain").addEventListener("click", copyPlain);
