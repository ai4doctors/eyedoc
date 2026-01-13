
let latestAnalysis = null

function toast(msg){
  const el = document.getElementById("toast")
  el.textContent = msg
  el.style.display = "block"
  setTimeout(() => { el.style.display = "none" }, 2400)
}

function getForm(){
  return {
    letter_type: document.getElementById("letter_type").value,
    referring_doctor: document.getElementById("referring_doctor").value,
    recipient_name: document.getElementById("recipient_name").value,
    reason_for_referral: document.getElementById("reason_for_referral").value,
    special_requests: document.getElementById("special_requests").value,
    additional_context: document.getElementById("additional_context").value
  }
}

document.getElementById("demoToggle").addEventListener("click", () => {
  const p = document.getElementById("demoPanel")
  const open = p.style.display === "block"
  p.style.display = open ? "none" : "block"
})

document.getElementById("themeBtn").addEventListener("click", () => {
  toast("Theme toggle is a stub in this build")
})

document.getElementById("pubmedBtn").addEventListener("click", () => {
  document.getElementById("pubmedBox").scrollIntoView({behavior:"smooth"})
})

document.getElementById("analyzeBtn").addEventListener("click", async () => {
  const fileInput = document.getElementById("pdf")
  const file = fileInput.files && fileInput.files[0]
  if(!file){
    toast("Choose a PDF first")
    return
  }

  const fd = new FormData()
  fd.append("pdf", file)

  toast("Analyzing")
  const res = await fetch("/analyze", { method:"POST", body: fd })
  const json = await res.json()

  if(!json.ok){
    toast(json.error || "Analyze failed")
    return
  }

  latestAnalysis = json.data || {}

  const patient = latestAnalysis.patient || {}
  document.getElementById("patient_name").value = patient.name || ""
  document.getElementById("patient_dob").value = patient.dob || ""
  document.getElementById("patient_phn").value = patient.phn || ""

  document.getElementById("diagnosisBox").textContent = latestAnalysis.diagnosis || ""
  document.getElementById("treatmentBox").textContent = latestAnalysis.treatment || ""

  const pm = latestAnalysis.pubmed || []
  const lines = pm.map(x => {
    const parts = [x.title, x.journal, x.year, x.pmid].filter(Boolean)
    return parts.join(" | ")
  })
  document.getElementById("pubmedBox").textContent = lines.length ? lines.join("\n") : "No citations yet"

  toast("Analysis complete")
})

document.getElementById("generateBtn").addEventListener("click", async () => {
  const reason = document.getElementById("reason_for_referral").value.trim()
  if(!reason){
    toast("Reason for referral is required")
    return
  }

  const fileInput = document.getElementById("pdf")
  const file = fileInput.files && fileInput.files[0]
  if(!file){
    toast("Choose a PDF first")
    return
  }

  const payload = { form: getForm(), analysis: latestAnalysis || {} }

  const fd = new FormData()
  fd.append("pdf", file)
  fd.append("payload", JSON.stringify(payload))

  toast("Generating letter")
  const res = await fetch("/generate_letter", { method:"POST", body: fd })
  const json = await res.json()

  if(!json.ok){
    toast(json.error || "Letter generation failed")
    return
  }

  document.getElementById("letterBox").value = json.letter_plain || ""
  toast("Letter ready")
})

async function copyText(text){
  try{
    await navigator.clipboard.writeText(text)
    toast("Copied")
  }catch(e){
    toast("Copy failed")
  }
}

document.getElementById("copyPlainBtn").addEventListener("click", () => {
  copyText(document.getElementById("letterBox").value || "")
})

document.getElementById("copyRichBtn").addEventListener("click", () => {
  copyText(document.getElementById("letterBox").value || "")
})
