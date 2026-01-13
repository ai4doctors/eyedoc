let latestAnalysis = null
function el(id){ return document.getElementById(id) }

function toast(msg){
  const t = el("toast")
  t.textContent = msg
  t.style.display = "block"
  setTimeout(() => { t.style.display = "none" }, 2600)
}

function setBusy(which, busy){
  if(which === "analyze"){
    el("analyzeSpin").style.display = busy ? "inline-block" : "none"
    el("analyzeBtn").disabled = busy
    el("statusLeft").textContent = busy ? "Analyzing" : "Ready"
  }
  if(which === "generate"){
    el("genSpin").style.display = busy ? "inline-block" : "none"
    el("generateBtn").disabled = busy
    el("statusLeft").textContent = busy ? "Generating" : "Ready"
  }
}

function getForm(){
  return {
    letter_type: el("letter_type").value,
    referring_doctor: el("referring_doctor").value,
    recipient_name: el("recipient_name").value,
    reason_for_referral: el("reason_for_referral").value,
    special_requests: el("special_requests").value,
    additional_context: el("additional_context").value
  }
}

el("demoToggle").addEventListener("click", () => {
  const p = el("demoPanel")
  p.style.display = (p.style.display === "block") ? "none" : "block"
})

el("themeBtn").addEventListener("click", () => toast("Theme toggle is a stub in this build"))

async function analyze(){
  const file = el("pdf").files && el("pdf").files[0]
  if(!file){ toast("Choose a PDF first"); return }

  const fd = new FormData()
  fd.append("pdf", file)

  setBusy("analyze", true)
  try{
    const res = await fetch("/analyze", { method:"POST", body: fd })
    const json = await res.json()
    if(!json.ok){ toast(json.error || "Analyze failed"); return }

    latestAnalysis = json.data || {}
    const patient = latestAnalysis.patient || {}
    el("patient_name").value = patient.name || ""
    el("patient_dob").value = patient.dob || ""
    el("patient_phn").value = patient.phn || ""

    el("diagnosisBox").textContent = latestAnalysis.diagnosis || ""
    el("treatmentBox").textContent = latestAnalysis.treatment || ""

    const pm = latestAnalysis.pubmed || []
    const lines = pm.map(x => [x.title, x.journal, x.year, x.pmid].filter(Boolean).join(" | "))
    el("pubmedBox").textContent = lines.length ? lines.join("\n") : "No citations returned"

    toast("Analysis complete")
  }catch(e){
    toast("Analyze failed")
  }finally{
    setBusy("analyze", false)
  }
}

async function generateLetter(){
  const reason = (el("reason_for_referral").value || "").trim()
  if(!reason){ toast("Reason for referral is required"); return }

  const file = el("pdf").files && el("pdf").files[0]
  if(!file){ toast("Choose a PDF first"); return }

  const payload = { form: getForm(), analysis: latestAnalysis || {} }
  const fd = new FormData()
  fd.append("pdf", file)
  fd.append("payload", JSON.stringify(payload))

  setBusy("generate", true)
  try{
    const res = await fetch("/generate_letter", { method:"POST", body: fd })
    const json = await res.json()
    if(!json.ok){ toast(json.error || "Letter generation failed"); return }
    el("letterBox").value = json.letter_plain || ""
    toast("Letter ready")
  }catch(e){
    toast("Letter generation failed")
  }finally{
    setBusy("generate", false)
  }
}

async function copyText(text){
  try{ await navigator.clipboard.writeText(text); toast("Copied") }
  catch(e){ toast("Copy failed") }
}

function downloadText(filename, text){
  const blob = new Blob([text], {type:"text/plain"})
  const url = URL.createObjectURL(blob)
  const a = document.createElement("a")
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  a.remove()
  URL.revokeObjectURL(url)
}

el("analyzeBtn").addEventListener("click", analyze)
el("generateBtn").addEventListener("click", generateLetter)
el("copyPlainBtn").addEventListener("click", () => copyText(el("letterBox").value || ""))
el("downloadBtn").addEventListener("click", () => {
  const text = el("letterBox").value || ""
  if(!text.trim()){ toast("Nothing to download"); return }
  downloadText("letter.txt", text)
})
