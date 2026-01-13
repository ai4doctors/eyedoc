
let uploadedFile = null
let jobId = ""
let latestAnalysis = null
let latestLetterHtml = ""
let theme = "dark"

function el(id){ return document.getElementById(id) }

function toast(msg){
  const t = el("toast")
  t.textContent = msg
  t.style.display = "block"
  setTimeout(() => { t.style.display = "none" }, 2400)
}

function setAnalyzeStatus(state){
  el("analyzeStatus").textContent = `Wait for analysis to complete (${state})`
}

function openPicker(){
  el("file").click()
}

function clearAll(){
  uploadedFile = null
  jobId = ""
  latestAnalysis = null
  latestLetterHtml = ""
  el("file").value = ""
  el("summaryBox").innerHTML = "Upload exam notes to begin."
  el("dxBox").textContent = "No data yet."
  el("planBox").textContent = "No data yet."
  el("refBox").textContent = "No data yet."
  el("letter").value = ""
  el("fromDoctor").value = ""
  el("toWhom").value = ""
  el("specialRequests").value = ""
  el("reasonDx").innerHTML = '<option value="">REASON:</option>'
  el("reasonOther").classList.add("hidden")
  el("reasonOther").value = ""
  setAnalyzeStatus("waiting")
  toast("Reset complete")
}

function applyTheme(){
  if(theme === "light"){
    document.body.classList.add("light")
  }else{
    document.body.classList.remove("light")
  }
}

function toggleTheme(){
  theme = (theme === "dark") ? "light" : "dark"
  applyTheme()
  toast(`Theme ${theme}`)
}

function buildReasonOptions(){
  const sel = el("reasonDx")
  sel.innerHTML = '<option value="">REASON:</option>'
  const dx = (latestAnalysis && latestAnalysis.diagnoses) ? latestAnalysis.diagnoses : []
  dx.forEach(item => {
    const o = document.createElement("option")
    const code = item.code ? `${item.code} ` : ""
    const val = `${code}${item.label || ""}`.trim()
    o.value = val
    o.textContent = val ? `${item.number}. ${val}` : `${item.number}.`
    sel.appendChild(o)
  })
  const other = document.createElement("option")
  other.value = "Other"
  other.textContent = "Other"
  sel.appendChild(other)
}

function setToPrefix(){
  const t = el("recipientType").value
  const v = el("toWhom").value.trim()

  const shouldPrefix = (t === "Specialist" || t === "Family physician")
  if(shouldPrefix){
    if(!v){
      el("toWhom").value = "Dr. "
    }else if(!v.toLowerCase().startsWith("dr.")){
      el("toWhom").value = "Dr. " + v
    }
    el("toWhom").placeholder = "TO:"
  }else{
    if(v.toLowerCase().startsWith("dr.")){
      el("toWhom").value = v.replace(/^dr\.\s*/i, "")
    }
    el("toWhom").placeholder = "TO:"
  }
}

function renderSummary(){
  const box = el("summaryBox")
  if(!latestAnalysis){
    box.innerHTML = "No data yet."
    return
  }
  const header = (latestAnalysis.patient_block || "").trim()
  const summary = (latestAnalysis.summary_html || "").trim()
  let html = ""
  if(header){
    html += `<div class="patientBlock">${header}</div>`
  }
  if(summary){
    html += summary
  }else{
    html += "<p>No summary extracted.</p>"
  }
  box.innerHTML = html
}

function renderDx(){
  const box = el("dxBox")
  const dx = (latestAnalysis && latestAnalysis.diagnoses) ? latestAnalysis.diagnoses : []
  if(!dx.length){
    box.textContent = "No diagnoses extracted."
    return
  }
  const frag = document.createDocumentFragment()
  dx.forEach(item => {
    const wrap = document.createElement("div")

    const title = document.createElement("div")
    title.className = "itemTitle"
    const code = item.code ? `${item.code} ` : ""
    title.textContent = `${item.number}. ${code}${item.label || ""}`.trim()
    wrap.appendChild(title)

    const meta = document.createElement("div")
    meta.className = "itemMeta"
    const refs = (item.refs || []).map(n => `[${n}]`).join(" ")
    meta.textContent = refs ? `Evidence ${refs}` : ""
    wrap.appendChild(meta)

    const ul = document.createElement("ul")
    ul.className = "bullets"
    ;(item.bullets || []).forEach(b => {
      const li = document.createElement("li")
      li.textContent = b
      ul.appendChild(li)
    })
    wrap.appendChild(ul)

    frag.appendChild(wrap)
  })
  box.innerHTML = ""
  box.appendChild(frag)
}

function renderPlan(){
  const box = el("planBox")
  const plan = (latestAnalysis && latestAnalysis.plan) ? latestAnalysis.plan : []
  if(!plan.length){
    box.textContent = "No plan extracted."
    return
  }
  const frag = document.createDocumentFragment()
  plan.forEach(item => {
    const wrap = document.createElement("div")

    const title = document.createElement("div")
    title.className = "itemTitle"
    title.textContent = `${item.number}. ${item.title || ""}`.trim()
    wrap.appendChild(title)

    const meta = document.createElement("div")
    meta.className = "itemMeta"
    const refs = (item.refs || []).map(n => `[${n}]`).join(" ")
    const aligned = (item.aligned_dx_numbers || []).length ? `Dx ${item.aligned_dx_numbers.join(", ")}` : ""
    const metaText = [aligned, refs ? `Evidence ${refs}` : ""].filter(Boolean).join("   ")
    meta.textContent = metaText
    wrap.appendChild(meta)

    const ul = document.createElement("ul")
    ul.className = "bullets"
    ;(item.bullets || []).forEach(b => {
      const li = document.createElement("li")
      li.textContent = b
      ul.appendChild(li)
    })
    wrap.appendChild(ul)

    frag.appendChild(wrap)
  })
  box.innerHTML = ""
  box.appendChild(frag)
}

function renderRefs(){
  const box = el("refBox")
  const refs = (latestAnalysis && latestAnalysis.references) ? latestAnalysis.references : []
  if(!refs.length){
    box.textContent = "No references found."
    return
  }
  const frag = document.createDocumentFragment()
  refs.forEach(r => {
    const wrap = document.createElement("div")

    const title = document.createElement("div")
    title.className = "itemTitle"
    title.textContent = `[${r.number}]`
    wrap.appendChild(title)

    const p = document.createElement("div")
    p.style.marginBottom = "10px"
    const cite = r.citation || ""
    const pmid = r.pmid ? ` PMID ${r.pmid}` : ""
    p.textContent = `${cite}${pmid}`.trim()
    wrap.appendChild(p)

    frag.appendChild(wrap)
  })
  box.innerHTML = ""
  box.appendChild(frag)
}

async function startAnalyze(){
  if(!uploadedFile){
    toast("Select a PDF first")
    return
  }
  setAnalyzeStatus("processing")
  const fd = new FormData()
  fd.append("pdf", uploadedFile)

  const res = await fetch("/analyze_start", { method:"POST", body: fd })
  const json = await res.json()
  if(!json.ok){
    setAnalyzeStatus("waiting")
    toast(json.error || "Analyze failed")
    return
  }
  jobId = json.job_id
  pollAnalyze()
}

async function pollAnalyze(){
  if(!jobId){
    return
  }
  try{
    const res = await fetch(`/analyze_status?job_id=${encodeURIComponent(jobId)}`)
    const json = await res.json()
    if(!json.ok){
      setAnalyzeStatus("waiting")
      toast(json.error || "Analyze status error")
      return
    }
    const status = json.status || "waiting"
    if(status === "waiting"){
      setAnalyzeStatus("waiting")
      setTimeout(pollAnalyze, 600)
      return
    }
    if(status === "processing"){
      setAnalyzeStatus("processing")
      setTimeout(pollAnalyze, 1200)
      return
    }
    if(status === "error"){
      setAnalyzeStatus("waiting")
      toast(json.error || "Analyze failed")
      return
    }
    if(status === "complete"){
      setAnalyzeStatus("analysis complete")
      latestAnalysis = json.data || {}
      el("fromDoctor").value = (latestAnalysis.provider_name || "").trim()
      buildReasonOptions()
      renderSummary()
      renderDx()
      renderPlan()
      renderRefs()
      toast("Analysis complete")
      return
    }
  }catch(e){
    setAnalyzeStatus("processing")
    setTimeout(pollAnalyze, 1500)
  }
}

function buildForm(){
  const recipientType = el("recipientType").value
  const toWhom = el("toWhom").value.trim()
  const fromDoctor = el("fromDoctor").value.trim()
  const reasonDx = el("reasonDx").value
  const reasonOther = el("reasonOther").value.trim()
  const reason = (reasonDx === "Other") ? reasonOther : reasonDx
  const special = el("specialRequests").value.trim()

  const mappedRecipient = (recipientType === "Family physician" || recipientType === "Specialist") ? "Physician" : recipientType

  return {
    recipient_type: mappedRecipient,
    to_whom: toWhom,
    from_doctor: fromDoctor,
    reason_for_referral: reason,
    special_requests: special,
    letter_type: "Report"
  }
}

async function generateReport(){
  if(!latestAnalysis){
    toast("Analyze first")
    return
  }
  const payload = { form: buildForm(), analysis: latestAnalysis }
  const res = await fetch("/generate_report", {
    method:"POST",
    headers: {"Content-Type":"application/json"},
    body: JSON.stringify(payload)
  })
  const json = await res.json()
  if(!json.ok){
    toast(json.error || "Generation failed")
    return
  }
  el("letter").value = json.letter_plain || ""
  latestLetterHtml = json.letter_html || ""
  toast("Report ready")
}

async function copyPlain(){
  const text = el("letter").value || ""
  if(!text.trim()){
    toast("Nothing to copy")
    return
  }
  try{
    await navigator.clipboard.writeText(text)
    toast("Copied")
  }catch(e){
    toast("Copy failed")
  }
}

async function copyRich(){
  const plain = el("letter").value || ""
  const html = latestLetterHtml || ""
  if(!plain.trim()){
    toast("Nothing to copy")
    return
  }
  try{
    if(html.trim() && window.ClipboardItem){
      const item = new ClipboardItem({
        "text/plain": new Blob([plain], {type:"text/plain"}),
        "text/html": new Blob([html], {type:"text/html"})
      })
      await navigator.clipboard.write([item])
    }else{
      await navigator.clipboard.writeText(plain)
    }
    toast("Copied")
  }catch(e){
    toast("Copy failed")
  }
}

async function exportPdf(){
  const text = el("letter").value || ""
  if(!text.trim()){
    toast("Nothing to export")
    return
  }
  const res = await fetch("/export_pdf", {
    method:"POST",
    headers: {"Content-Type":"application/json"},
    body: JSON.stringify({ text })
  })
  if(!res.ok){
    toast("PDF export failed")
    return
  }
  const blob = await res.blob()
  const url = URL.createObjectURL(blob)
  const a = document.createElement("a")
  a.href = url
  a.download = "ai4health_output.pdf"
  document.body.appendChild(a)
  a.click()
  a.remove()
  URL.revokeObjectURL(url)
  toast("Downloaded")
}

el("uploadBtn").addEventListener("click", openPicker)
el("file").addEventListener("change", async (e) => {
  const f = e.target.files && e.target.files[0]
  if(!f){
    return
  }
  uploadedFile = f
  setAnalyzeStatus("waiting")
  toast("Upload successful")
  await startAnalyze()
})

el("recipientType").addEventListener("change", setToPrefix)
el("toWhom").addEventListener("focus", setToPrefix)

el("reasonDx").addEventListener("change", () => {
  const v = el("reasonDx").value
  if(v === "Other"){
    el("reasonOther").classList.remove("hidden")
  }else{
    el("reasonOther").classList.add("hidden")
    el("reasonOther").value = ""
  }
})

el("generateBtn").addEventListener("click", generateReport)
el("copyPlain").addEventListener("click", copyPlain)
el("copyRich").addEventListener("click", copyRich)
el("exportPdf").addEventListener("click", exportPdf)

el("resetBtn").addEventListener("click", clearAll)
el("themeBtn").addEventListener("click", toggleTheme)

applyTheme()
setAnalyzeStatus("waiting")
