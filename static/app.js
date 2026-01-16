
let uploadedFile = null
let jobId = ""
let latestAnalysis = null
let latestLetterHtml = ""
// Single theme for consistency
let theme = "light"

function cleanNameToken(s){
  return (s || "")
    .toString()
    .trim()
    .replace(/\s+/g, " ")
    .replace(/[^A-Za-z0-9 ]+/g, "")
}

function patientTokenFromBlock(block){
  const raw = (block || "").toString()
  const first = raw.split(/<br\s*\/?\s*>|\n/gi)[0] || ""
  const cleaned = cleanNameToken(first)
  if(!cleaned){
    return "PxUnknown_Unknown"
  }
  const parts = cleaned.split(" ").filter(Boolean)
  if(parts.length === 1){
    return `Px${parts[0]}_Unknown`
  }
  const firstName = parts[0]
  const lastName = parts[parts.length - 1]
  return `Px${lastName}_${firstName}`
}

function doctorToken(providerName){
  const low = (providerName || "").toLowerCase()
  if(low.includes("henry") && low.includes("reis")){
    return "DrReis"
  }
  const cleaned = cleanNameToken(providerName)
  const parts = cleaned.split(" ").filter(Boolean)
  const last = parts.length ? parts[parts.length - 1] : "Doctor"
  return `Dr${last}`
}

function focusOptionsForDiagnosis(dxLabel){
  const d = (dxLabel || "").toLowerCase()
  if(d.includes("glaucoma")){
    return [
      "Consult and management recommendations",
      "Laser evaluation",
      "LPI evaluation",
      "Gonioscopy and angle assessment",
      "Surgical opinion",
      "Other"
    ]
  }
  if(d.includes("dry eye") || d.includes("mgd") || d.includes("blephar") || d.includes("meibomian")){
    return [
      "Confirm diagnosis and stage severity",
      "Advanced therapy recommendations",
      "Procedure consideration",
      "Medication options",
      "Other"
    ]
  }
  if(d.includes("cataract")){
    return [
      "Surgical consultation",
      "Pre operative evaluation",
      "Co management plan",
      "Other"
    ]
  }
  if(d.includes("retina") || d.includes("macula") || d.includes("amd") || d.includes("diabetic")){
    return [
      "Urgent assessment",
      "Treatment options and follow up plan",
      "Imaging review",
      "Other"
    ]
  }
  return [
    "Consultation",
    "Management recommendations",
    "Second opinion",
    "Co management",
    "Other"
  ]
}

function setFocusOptions(){
  const dx = el("reasonDx").value
  const focus = el("reasonFocus")
  const focusOther = el("reasonFocusOther")

  focus.innerHTML = ""
  const placeholder = document.createElement("option")
  placeholder.value = ""
  placeholder.textContent = "REFERRAL FOCUS:"
  focus.appendChild(placeholder)

  if(!dx || dx === "Other"){
    focus.disabled = true
    focusOther.classList.add("hidden")
    focusOther.value = ""
    return
  }

  const opts = focusOptionsForDiagnosis(dx)
  for(const o of opts){
    const opt = document.createElement("option")
    opt.value = o
    opt.textContent = o
    focus.appendChild(opt)
  }
  focus.disabled = false
  focus.value = ""
  focusOther.classList.add("hidden")
  focusOther.value = ""
}

const CASES_KEY = "ai4health_cases_v1"

function loadCases(){
  try{
    const raw = localStorage.getItem(CASES_KEY)
    return raw ? JSON.parse(raw) : []
  }catch(e){
    return []
  }
}

function saveCases(cases){
  try{
    localStorage.setItem(CASES_KEY, JSON.stringify(cases))
  }catch(e){
    return
  }
}

function upsertCase(analysis){
  if(!analysis){
    return
  }
  const cases = loadCases()
  const patient = (analysis.patient_name || "").trim() || "Untitled case"
  const provider = (analysis.provider_name || "").trim()
  const ts = Date.now()
  const id = `${ts}_${Math.random().toString(16).slice(2)}`
  const entry = {
    id,
    ts,
    patient,
    provider,
    dx_count: (analysis.diagnoses || []).length,
    analysis
  }
  cases.unshift(entry)
  saveCases(cases.slice(0, 30))
  renderCaseList()
}

function renderCaseList(){
  const list = el("caseList")
  if(!list){
    return
  }
  const cases = loadCases()
  if(!cases.length){
    list.innerHTML = "<div class=\"caseMeta\">No saved cases yet.</div>"
    return
  }
  const frag = document.createDocumentFragment()
  cases.forEach(c => {
    const card = document.createElement("div")
    card.className = "caseCard"
    card.dataset.caseId = c.id

    const t = document.createElement("div")
    t.className = "caseTitle"
    t.textContent = c.patient
    card.appendChild(t)

    const m = document.createElement("div")
    m.className = "caseMeta"
    const d = new Date(c.ts)
    const when = d.toLocaleString()
    const dx = c.dx_count ? `${c.dx_count} dx` : ""
    const prov = c.provider ? c.provider : ""
    m.textContent = [when, prov, dx].filter(Boolean).join("  ")
    card.appendChild(m)

    frag.appendChild(card)
  })
  list.innerHTML = ""
  list.appendChild(frag)
}

function loadCaseById(id){
  const cases = loadCases()
  const c = cases.find(x => x.id === id)
  if(!c){
    toast("Case not found")
    return
  }
  latestAnalysis = c.analysis
  latestLetterHtml = ""
  el("letter").value = ""
  updatePreview()
  el("fromDoctor").value = (latestAnalysis.provider_name || "").trim()
  buildReasonOptions()
  renderSummary()
  renderDx()
  renderPlan()
  renderRefs()
  setAnalyzeStatus("analysis complete")
  toast("Case loaded")
}

function clearCases(){
  saveCases([])
  renderCaseList()
}

function el(id){ return document.getElementById(id) }

function updatePreview(){
  const box = el("letterPreview")
  if(!box){
    return
  }
  const text = (el("letter").value || "").trim()
  box.innerHTML = ""
  if(!text){
    const empty = document.createElement("div")
    empty.className = "previewEmpty"
    empty.textContent = "Preview will appear here."
    box.appendChild(empty)
    return
  }
  const paras = text.split(/\n\s*\n/g)
  paras.forEach(p => {
    const para = document.createElement("p")
    para.className = "previewP"
    const lines = p.split(/\n/g)
    lines.forEach((line, idx) => {
      if(idx > 0){
        para.appendChild(document.createElement("br"))
      }
      para.appendChild(document.createTextNode(line))
    })
    box.appendChild(para)
  })
}

function openEmailDraft(){
  const text = (el("letter").value || "").trim()
  if(!text){
    toast("Nothing to email")
    return
  }

  const recipientType = (el("recipientType").value || "").trim()
  const providerName = (el("fromDoctor").value || "").trim()
  const patientBlock = latestAnalysis ? (latestAnalysis.patient_block || "") : ""
  const patientToken = patientTokenFromBlock(patientBlock)
  const subjectParts = ["AI4Health", recipientType, patientToken].filter(Boolean)
  const subject = subjectParts.join(" ")

  const body = text
  const href = "mailto:?subject=" + encodeURIComponent(subject) + "&body=" + encodeURIComponent(body)
  window.location.href = href
}

function toast(msg){
  const t = el("toast")
  t.textContent = msg
  t.style.display = "block"
  setTimeout(() => { t.style.display = "none" }, 2400)
}

function setAnalyzeStatus(state){
  const box = el("analyzeStatus")
  if(state === "processing"){
    box.innerHTML = 'Wait for analysis to complete <span class="muted">(processing)</span> <span class="spinner" aria-hidden="true"></span>'
    return
  }
  if(state === "analysis complete"){
    box.innerHTML = 'Analysis complete <span class="muted">(ready)</span>'
    return
  }
  box.innerHTML = 'Wait for analysis to complete <span class="muted">(waiting)</span>'
}

function setGenerateStatus(state){
  const box = el("generateStatus")
  if(!box){
    return
  }
  if(state === "processing"){
    box.classList.remove("hidden")
    box.innerHTML = 'Generating report <span class="muted">(processing)</span> <span class="spinner" aria-hidden="true"></span>'
    return
  }
  if(state === "ready"){
    box.classList.add("hidden")
    box.textContent = "Ready"
    return
  }
  box.classList.add("hidden")
  box.textContent = ""
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
  updatePreview()
  el("fromDoctor").value = ""
  el("toWhom").value = ""
  el("specialRequests").value = ""
  el("reasonDx").innerHTML = '<option value="">REASON:</option>'
  el("reasonOther").classList.add("hidden")
  el("reasonOther").value = ""
  setAnalyzeStatus("waiting")
  toast("Reset complete")
}

function newCase(){
  clearAll()
}

function applyTheme(){
  document.body.classList.remove("dark")
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
    wrap.dataset.refs = (item.refs || []).join(",")
    wrap.id = `dx_${item.number}`

    const title = document.createElement("div")
    title.className = "itemTitle"
    const code = item.code ? `${item.code} ` : ""
    title.textContent = `${item.number}. ${code}${item.label || ""}`.trim()
    wrap.appendChild(title)

    const meta = document.createElement("div")
    meta.className = "itemMeta"
    const refs = (item.refs || []).map(n => String(n)).filter(Boolean)
    if(refs.length){
      const label = document.createElement("span")
      label.textContent = "Evidence "
      meta.appendChild(label)
      refs.forEach((n, idx) => {
        const s = document.createElement("span")
        s.className = "citeRef"
        s.dataset.ref = n
        s.textContent = `[${n}]`
        meta.appendChild(s)
        if(idx < refs.length - 1){
          meta.appendChild(document.createTextNode(" "))
        }
      })
    }
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
    wrap.dataset.refs = (item.refs || []).join(",")
    wrap.id = `plan_${item.number}`

    const title = document.createElement("div")
    title.className = "itemTitle"
    title.textContent = `${item.number}. ${item.title || ""}`.trim()
    wrap.appendChild(title)

    const meta = document.createElement("div")
    meta.className = "itemMeta"
    const aligned = (item.aligned_dx_numbers || []).length ? `Dx ${item.aligned_dx_numbers.join(", ")}` : ""
    if(aligned){
      const a = document.createElement("span")
      a.textContent = aligned
      meta.appendChild(a)
    }
    const refs = (item.refs || []).map(n => String(n)).filter(Boolean)
    if(refs.length){
      if(aligned){
        meta.appendChild(document.createTextNode("   "))
      }
      const label = document.createElement("span")
      label.textContent = "Evidence "
      meta.appendChild(label)
      refs.forEach((n, idx) => {
        const s = document.createElement("span")
        s.className = "citeRef"
        s.dataset.ref = n
        s.textContent = `[${n}]`
        meta.appendChild(s)
        if(idx < refs.length - 1){
          meta.appendChild(document.createTextNode(" "))
        }
      })
    }
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
    wrap.className = "clickableRef"
    wrap.dataset.ref = String(r.number)

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

function clearHighlights(){
  document.querySelectorAll(".highlight").forEach(n => n.classList.remove("highlight"))
}

function highlightRef(refNum){
  const n = String(refNum)
  clearHighlights()
  const candidates = []
  ;[el("dxBox"), el("planBox")].forEach(container => {
    if(!container){
      return
    }
    Array.from(container.children).forEach(child => {
      const refs = (child.dataset && child.dataset.refs) ? child.dataset.refs.split(",").filter(Boolean) : []
      if(refs.includes(n)){
        child.classList.add("highlight")
        candidates.push(child)
      }
    })
  })
  if(candidates.length){
    candidates[0].scrollIntoView({behavior:"smooth", block:"center"})
  }
}

async function startAnalyze(){
  if(!uploadedFile){
    toast("Select a PDF first")
    return
  }
  setAnalyzeStatus("processing")
  el("ocrPrompt").classList.add("hidden")
  const fd = new FormData()
  fd.append("pdf", uploadedFile)

  const handwritten = el("handwrittenCheck") && el("handwrittenCheck").checked
  if(handwritten){
    fd.append("handwritten", "1")
  }

  const res = await fetch("/analyze_start", { method:"POST", body: fd })
  const json = await res.json()
  if(!json.ok){
    setAnalyzeStatus("waiting")
    if(json.needs_ocr){
      el("ocrPrompt").classList.remove("hidden")
      toast(json.error || "Text extraction failed")
      return
    }
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
      upsertCase(latestAnalysis)
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
  const focusSel = el("reasonFocus").value
  const focusOther = el("reasonFocusOther").value.trim()
  const reasonDetail = (focusSel === "Other") ? focusOther : focusSel
  const special = el("specialRequests").value.trim()

  const mappedRecipient = (recipientType === "Family physician" || recipientType === "Specialist") ? "Physician" : recipientType

  return {
    recipient_type: mappedRecipient,
    to_whom: toWhom,
    from_doctor: fromDoctor,
    reason_for_referral: reason,
    reason_detail: reasonDetail,
    special_requests: special,
    letter_type: "Report"
  }
}

async function generateReport(){
  if(!latestAnalysis){
    toast("Analyze first")
    return
  }
  const btn = el("generateBtn")
  btn.disabled = true
  setGenerateStatus("processing")
  const originalLabel = btn.textContent
  btn.textContent = "Generating report"
  const payload = { form: buildForm(), analysis: latestAnalysis }
  try{
    const res = await fetch("/generate_report", {
      method:"POST",
      headers: {"Content-Type":"application/json"},
      body: JSON.stringify(payload)
    })
    const json = await res.json()
    if(!json.ok){
      toast(json.error || "Generation failed")
      setGenerateStatus("idle")
      return
    }
    el("letter").value = json.letter_plain || ""
    latestLetterHtml = json.letter_html || ""
    updatePreview()
    toast("Report ready")
    setGenerateStatus("idle")
  }catch(e){
    toast("Generation failed")
    setGenerateStatus("idle")
  }finally{
    btn.disabled = false
    btn.textContent = originalLabel
  }
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
  const providerName = (el("fromDoctor").value || "").trim()
  const patientBlock = latestAnalysis ? (latestAnalysis.patient_block || "") : ""
  const patientToken = patientTokenFromBlock(patientBlock)
  const recipientType = (el("recipientType").value || "").trim()
  const res = await fetch("/export_pdf", {
    method:"POST",
    headers: {"Content-Type":"application/json"},
    body: JSON.stringify({
      text,
      provider_name: providerName,
      patient_token: patientToken,
      recipient_type: recipientType
    })
  })
  if(!res.ok){
    let msg = "PDF export failed"
    try{
      const j = await res.json()
      if(j && j.error){
        msg = j.error
      }
    }catch(e){}
    toast(msg)
    return
  }
  const blob = await res.blob()
  const url = URL.createObjectURL(blob)
  const a = document.createElement("a")
  a.href = url
  let filename = "ai4health_output.pdf"
  const cd = res.headers.get("Content-Disposition") || ""
  const m = cd.match(/filename\s*=\s*"?([^";]+)"?/i)
  if(m && m[1]){
    filename = m[1]
  }
  a.download = filename
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
  setFocusOptions()
})

el("reasonFocus").addEventListener("change", () => {
  const v = el("reasonFocus").value
  if(v === "Other"){
    el("reasonFocusOther").classList.remove("hidden")
  }else{
    el("reasonFocusOther").classList.add("hidden")
    el("reasonFocusOther").value = ""
  }
})

el("generateBtn").addEventListener("click", generateReport)
el("copyPlain").addEventListener("click", copyPlain)
el("copyRich").addEventListener("click", copyRich)
if(el("emailDraft")){
  el("emailDraft").addEventListener("click", openEmailDraft)
}
el("exportPdf").addEventListener("click", exportPdf)

if(el("letter")){
  el("letter").addEventListener("input", updatePreview)
}

if(el("runOcrBtn")){
  el("runOcrBtn").addEventListener("click", async () => {
    if(el("handwrittenCheck")){
      el("handwrittenCheck").checked = true
    }
    await startAnalyze()
  })
}

el("resetBtn").addEventListener("click", clearAll)
el("newCaseBtn").addEventListener("click", clearAll)

el("caseList").addEventListener("click", (e) => {
  const card = e.target.closest(".caseCard")
  if(!card){
    return
  }
  const id = card.dataset.caseId
  if(id){
    loadCaseById(id)
  }
})

el("refBox").addEventListener("click", (e) => {
  const node = e.target.closest(".clickableRef")
  if(!node){
    return
  }
  const ref = node.dataset.ref
  if(ref){
    highlightRef(ref)
  }
})

document.addEventListener("click", (e) => {
  const n = e.target.closest(".citeRef")
  if(!n){
    return
  }
  const ref = n.dataset.ref
  if(ref){
    highlightRef(ref)
  }
})


applyTheme()
setAnalyzeStatus("waiting")
renderCaseList()
updatePreview()
