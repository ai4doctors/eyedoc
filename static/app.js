
let uploadedFile = null
let jobId = ""
let latestAnalysis = null
let latestLetterHtml = ""
// Single theme for consistency
let theme = "light"

const SETTINGS_KEY = "maneiro_settings_v1"
const DEFAULT_SETTINGS = {
  input_language: "auto",
  output_language: "auto",
  letterhead_data_url: "",
  signature_data_url: ""
}

function loadSettings(){
  try{
    const raw = localStorage.getItem(SETTINGS_KEY)
    if(!raw){ return { ...DEFAULT_SETTINGS } }
    const parsed = JSON.parse(raw)
    return { ...DEFAULT_SETTINGS, ...(parsed || {}) }
  }catch(e){
    return { ...DEFAULT_SETTINGS }
  }
}

function saveSettings(next){
  const merged = { ...DEFAULT_SETTINGS, ...(next || {}) }
  try{ localStorage.setItem(SETTINGS_KEY, JSON.stringify(merged)) }catch(e){}
  return merged
}

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

const CASES_KEY = "maneiro_cases_v1"
const CASE_COUNTER_KEY = "maneiro_case_counter_v1"

function patientInitials(name){
  const cleaned = cleanNameToken(name)
  const parts = cleaned.split(" ").filter(Boolean)
  if(!parts.length){
    return "PX"
  }
  const a = parts[0][0] || "P"
  const b = parts.length > 1 ? (parts[parts.length-1][0] || "X") : (parts[0][1] || "X")
  return (a + b).toUpperCase()
}

function nextCaseNumber(){
  try{
    const raw = localStorage.getItem(CASE_COUNTER_KEY)
    const n = raw ? parseInt(raw, 10) : 0
    const next = (isNaN(n) ? 0 : n) + 1
    localStorage.setItem(CASE_COUNTER_KEY, String(next))
    return next
  }catch(e){
    return Math.floor(Date.now()/1000)
  }
}


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

function clearCaseCache(){
  const ok = window.confirm("Clear all saved cases from this browser")
  if(!ok){
    return
  }
  try{
    localStorage.removeItem(CASES_KEY)
    localStorage.removeItem(CASE_COUNTER_KEY)
  }catch(e){
  }
  renderCaseList()
  toast("Cases cleared")
}

function upsertCase(analysis){
  if(!analysis){
    return
  }
  const cases = loadCases()
  const patient = (analysis.patient_name || "").trim() || "Untitled case"
  const caseNumber = nextCaseNumber()
  const initials = patientInitials(patient)
  const provider = (analysis.provider_name || "").trim()
  const ts = Date.now()
  const id = `${ts}_${Math.random().toString(16).slice(2)}`
  const entry = {
    id,
    ts,
    patient,
    case_number: caseNumber,
    initials,
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
    t.textContent = `${(c.initials || patientInitials(c.patient || ""))} ${(c.case_number || "").toString()}`.trim()
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
  if(el("letterRich")){ el("letterRich").innerHTML = "" }
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
  if(el("letterRich")){ el("letterRich").innerHTML = "" }
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

function openFaq(){
  const m = el("faqModal")
  if(m){ m.classList.remove("hidden") }
}
function closeFaq(){
  const m = el("faqModal")
  if(m){ m.classList.add("hidden") }
}
if(el("faqBtn")){
  el("faqBtn").addEventListener("click", openFaq)
}
if(el("faqClose")){
  el("faqClose").addEventListener("click", closeFaq)
}
if(el("faqModal")){
  el("faqModal").addEventListener("click", (e) => {
    if(e.target && (e.target.dataset && e.target.dataset.close)){
      closeFaq()
    }
  })
}
document.addEventListener("keydown", (e) => {
  if(e.key === "Escape"){
    closeFaq()
    closeRecord()
  }
})

function openFaq(){
  const m = el("faqModal")
  if(m){ m.classList.remove("hidden") }
}
function closeFaq(){
  const m = el("faqModal")
  if(m){ m.classList.add("hidden") }
}
if(el("faqBtn")){
  el("faqBtn").addEventListener("click", openFaq)
}
if(el("faqClose")){
  el("faqClose").addEventListener("click", closeFaq)
}
if(el("faqModal")){
  el("faqModal").addEventListener("click", (e) => {
    if(e.target && e.target.dataset && e.target.dataset.close){
      closeFaq()
    }
  })
}
if(el("recordModal")){
  el("recordModal").addEventListener("click", (e) => {
    if(e.target && e.target.dataset && e.target.dataset.close){
      closeRecord()
    }
  })
}
document.addEventListener("keydown", (e) => {
  if(e.key === "Escape"){
    closeFaq()
    closeRecord()
  }
})

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
    const source = r.source ? `  ${String(r.source)}` : ""
    p.textContent = `${cite}${pmid}${source}`.trim()
    wrap.appendChild(p)

    const url = r.url || ""
    if(url){
      const a = document.createElement("a")
      a.href = url
      a.target = "_blank"
      a.rel = "noopener"
      a.className = "refLink"
      a.textContent = "Open source"
      a.addEventListener("click", (ev) => ev.stopPropagation())
      wrap.appendChild(a)
    }

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
    toast("Select a file first")
    return
  }
  setAnalyzeStatus("processing")
  el("ocrPrompt").classList.add("hidden")
  const fd = new FormData()
  fd.append("file", uploadedFile)

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

  const settings = loadSettings()

  return {
    document_type: recipientType,
    recipient_type: mappedRecipient,
    to_whom: toWhom,
    from_doctor: fromDoctor,
    reason_for_referral: reason,
    reason_detail: reasonDetail,
    special_requests: special,
    output_language: settings.output_language || "auto",
    signature_present: !!settings.signature_data_url,
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
    const rich = el("letterRich")
    if(rich){
      const html = (latestLetterHtml || "").trim()
      rich.innerHTML = html ? html : ((el("letter").value || "").split("\n").join("<br>"))
    }
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
  const settings = loadSettings()
  const res = await fetch("/export_pdf", {
    method:"POST",
    headers: {"Content-Type":"application/json"},
    body: JSON.stringify({
      text,
      provider_name: providerName,
      patient_token: patientToken,
      recipient_type: recipientType,
      letterhead_data_url: settings.letterhead_data_url || "",
      signature_data_url: settings.signature_data_url || ""
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
  let filename = "maneiro_output.pdf"
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


let mediaRecorder = null
let recChunks = []
let recTimerInt = null
let recStartMs = 0
let transcribeJobId = ""

function openRecord(){
  const m = el("recordModal")
  if(m){ m.classList.remove("hidden") }
  const settings = loadSettings()
  const sel = el("recordLang")
  if(sel){
    const v = (settings.input_language || "auto").trim()
    if([...sel.options].some(o => o.value === v)){
      sel.value = v
    }else{
      sel.value = "auto"
    }
  }
  el("recordState").textContent = "Ready"
  el("recordTimer").textContent = "00:00"
  el("recordTranscript").value = ""
  el("recStart").disabled = false
  el("recPause").disabled = true
  el("recStop").disabled = true
  el("recSave").disabled = true
  transcribeJobId = ""
}

function closeRecord(){
  const m = el("recordModal")
  if(m){ m.classList.add("hidden") }
  if(recTimerInt){ clearInterval(recTimerInt); recTimerInt = null }
  try{ if(mediaRecorder && mediaRecorder.state !== "inactive"){ mediaRecorder.stop() } }catch(e){}
  mediaRecorder = null
  recChunks = []
}

function fmtTime(ms){
  const s = Math.max(0, Math.floor(ms / 1000))
  const m = Math.floor(s / 60)
  const r = s % 60
  const mm = String(m).padStart(2,"0")
  const rr = String(r).padStart(2,"0")
  return mm + ":" + rr
}

async function startRec(){
  recChunks = []
  try{
    const stream = await navigator.mediaDevices.getUserMedia({ audio:true })
    mediaRecorder = new MediaRecorder(stream)
    mediaRecorder.ondataavailable = (e) => {
      if(e.data && e.data.size > 0){ recChunks.push(e.data) }
    }
    mediaRecorder.onstop = () => {
      try{ stream.getTracks().forEach(t => t.stop()) }catch(e){}
    }
    mediaRecorder.start(1000)
    recStartMs = Date.now()
    el("recordState").textContent = "Recording"
    el("recStart").disabled = true
    el("recPause").disabled = false
    el("recStop").disabled = false
    el("recSave").disabled = true
    if(recTimerInt){ clearInterval(recTimerInt) }
    recTimerInt = setInterval(() => {
      el("recordTimer").textContent = fmtTime(Date.now() - recStartMs)
    }, 500)
  }catch(e){
    toast("Microphone permission denied")
  }
}

function pauseRec(){
  if(!mediaRecorder){ return }
  if(mediaRecorder.state === "recording"){
    mediaRecorder.pause()
    el("recordState").textContent = "Paused"
    el("recPause").textContent = "Resume"
  }else if(mediaRecorder.state === "paused"){
    mediaRecorder.resume()
    el("recordState").textContent = "Recording"
    el("recPause").textContent = "Pause"
  }
}

async function stopRec(){
  if(!mediaRecorder){ return }
  el("recordState").textContent = "Preparing audio"
  el("recPause").disabled = true
  el("recStop").disabled = true
  try{ mediaRecorder.stop() }catch(e){}
  if(recTimerInt){ clearInterval(recTimerInt); recTimerInt = null }
  setTimeout(async () => {
    const blob = new Blob(recChunks, { type: "audio/webm" })
    await startTranscribeBlob(blob)
  }, 250)
}

async function startTranscribeBlob(blob){
  el("recordState").textContent = "Transcribing"
  const fd = new FormData()
  const lang = el("recordLang") ? el("recordLang").value : "auto"
  fd.append("language", lang)
  fd.append("audio", blob, "recording.webm")
  const res = await fetch("/transcribe_start", { method:"POST", body: fd })
  const json = await res.json()
  if(!json.ok){
    el("recordState").textContent = "Ready"
    toast(json.error || "Transcription failed")
    el("recStart").disabled = false
    return
  }
  transcribeJobId = json.job_id
  pollTranscribe()
}

async function pollTranscribe(){
  if(!transcribeJobId){ return }
  try{
    const res = await fetch(`/transcribe_status?job_id=${encodeURIComponent(transcribeJobId)}`)
    const json = await res.json()
    if(!json.ok){
      toast(json.error || "Transcription status error")
      el("recordState").textContent = "Ready"
      el("recStart").disabled = false
      return
    }
    const st = json.status || "transcribing"
    if(st === "transcribing"){
      el("recordState").textContent = "Transcribing"
      setTimeout(pollTranscribe, 1500)
      return
    }
    if(st === "error"){
      el("recordState").textContent = "Ready"
      toast(json.error || "Transcription failed")
      el("recStart").disabled = false
      return
    }
    if(st === "complete"){
      const txt = (json.transcript || "").trim()
      el("recordTranscript").value = txt
      el("recordState").textContent = "Transcript ready"
      el("recSave").disabled = !txt
      return
    }
  }catch(e){
    setTimeout(pollTranscribe, 2000)
  }
}

async function saveTranscriptToAnalyze(){
  const txt = (el("recordTranscript").value || "").trim()
  if(!txt){ toast("Transcript is empty"); return }
  el("recordState").textContent = "Analyzing"
  const res = await fetch("/analyze_text_start", {
    method:"POST",
    headers:{"Content-Type":"application/json"},
    body: JSON.stringify({ text: txt })
  })
  const json = await res.json()
  if(!json.ok){
    el("recordState").textContent = "Transcript ready"
    toast(json.error || "Analyze failed")
    return
  }
  jobId = json.job_id
  closeRecord()
  setAnalyzeStatus("processing")
  pollAnalyze()
}

el("uploadBtn").addEventListener("click", openPicker)
if(el("recordBtn")){
  el("recordBtn").addEventListener("click", openRecord)
}
if(el("recordClose")){
  el("recordClose").addEventListener("click", closeRecord)
}
if(el("recStart")){
  el("recStart").addEventListener("click", startRec)
}
if(el("recPause")){
  el("recPause").addEventListener("click", pauseRec)
}
if(el("recStop")){
  el("recStop").addEventListener("click", stopRec)
}
if(el("recSave")){
  el("recSave").addEventListener("click", saveTranscriptToAnalyze)
}
el("file").addEventListener("change", async (e) => {
  const f = e.target.files && e.target.files[0]
  if(!f){
    return
  }
  uploadedFile = f
  const type = (f.type || "").toLowerCase()
  if(type.startsWith("audio/")){
    openRecord()
    el("recStart").disabled = true
    el("recPause").disabled = true
    el("recStop").disabled = true
    el("recSave").disabled = true
    el("recordTranscript").value = ""
    el("recordState").textContent = "Transcribing"
    await startTranscribeBlob(f)
    return
  }
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
el("exportPdf").addEventListener("click", exportPdf)

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

if(el("clearCasesBtn")){
  el("clearCasesBtn").addEventListener("click", clearCaseCache)
}

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


function openFaq(){
  const m = el("faqModal")
  if(m){ m.classList.remove("hidden") }
}
function closeFaq(){
  const m = el("faqModal")
  if(m){ m.classList.add("hidden") }
}
if(el("faqBtn")){
  el("faqBtn").addEventListener("click", openFaq)
}
if(el("faqClose")){
  el("faqClose").addEventListener("click", closeFaq)
}
if(el("faqModal")){
  el("faqModal").addEventListener("click", (e) => {
    if(e.target && e.target.dataset && e.target.dataset.close){
      closeFaq()
    }
  })
}
document.addEventListener("keydown", (e) => {
  if(e.key === "Escape"){
    closeFaq()
  }
})

function openSettings(){
  const m = el("settingsModal")
  if(!m){ return }
  const s = loadSettings()
  if(el("settingsInputLang")){ el("settingsInputLang").value = s.input_language || "auto" }
  if(el("settingsOutputLang")){ el("settingsOutputLang").value = s.output_language || "auto" }
  if(el("settingsLetterheadHint")){ el("settingsLetterheadHint").textContent = s.letterhead_data_url ? "Custom letterhead loaded" : "Using default letterhead" }
  if(el("settingsSignatureHint")){ el("settingsSignatureHint").textContent = s.signature_data_url ? "Custom signature loaded" : "Using server signature" }
  m.classList.remove("hidden")
}

function closeSettings(){
  const m = el("settingsModal")
  if(m){ m.classList.add("hidden") }
}

async function fileToDataUrl(file){
  return new Promise((resolve, reject) => {
    const r = new FileReader()
    r.onload = () => resolve(String(r.result || ""))
    r.onerror = () => reject(new Error("read"))
    r.readAsDataURL(file)
  })
}

async function saveSettingsFromModal(){
  const s = loadSettings()
  const next = { ...s }
  if(el("settingsInputLang")){ next.input_language = el("settingsInputLang").value || "auto" }
  if(el("settingsOutputLang")){ next.output_language = el("settingsOutputLang").value || "auto" }

  try{
    const lh = el("settingsLetterhead")
    if(lh && lh.files && lh.files[0]){
      next.letterhead_data_url = await fileToDataUrl(lh.files[0])
    }
  }catch(e){}
  try{
    const sig = el("settingsSignature")
    if(sig && sig.files && sig.files[0]){
      next.signature_data_url = await fileToDataUrl(sig.files[0])
    }
  }catch(e){}

  saveSettings(next)
  toast("Settings saved")
  closeSettings()
}

function clearUploads(){
  const s = loadSettings()
  saveSettings({ ...s, letterhead_data_url: "", signature_data_url: "" })
  toast("Uploads cleared")
  closeSettings()
}

if(el("settingsBtn")){
  el("settingsBtn").addEventListener("click", openSettings)
}
if(el("settingsClose")){
  el("settingsClose").addEventListener("click", closeSettings)
}
if(el("settingsSave")){
  el("settingsSave").addEventListener("click", saveSettingsFromModal)
}
if(el("settingsClearUploads")){
  el("settingsClearUploads").addEventListener("click", clearUploads)
}
if(el("settingsModal")){
  el("settingsModal").addEventListener("click", (e) => {
    if(e.target && e.target.dataset && e.target.dataset.close){
      closeSettings()
    }
  })
}


function emailDraft(){
  const providerName = (el("fromDoctor").value || "").trim()
  const patientName = (latestAnalysis && latestAnalysis.patient_name) ? String(latestAnalysis.patient_name).trim() : ""
  const examDate = (latestAnalysis && (latestAnalysis.exam_date || latestAnalysis.date)) ? String(latestAnalysis.exam_date || latestAnalysis.date).trim() : ""
  const subj = patientName ? ("Clinical documents for " + patientName) : "Clinical documents"
  const who = patientName ? (patientName + "'s") : "the patient's"
  const when = examDate ? (" on " + examDate) : ""
  const body = ("Hi,\n\nPlease find attached the exam notes, referral letter, report etc related to " + who + " exam" + when + ".\n\nFeel free to reach out should you have any questions or concerns.\n\nKind regards,\n" + (providerName || "")).trim()
  window.location.href = "mailto:?subject=" + encodeURIComponent(subj) + "&body=" + encodeURIComponent(body)
}
if(el("emailBtn")){ el("emailBtn").addEventListener("click", emailDraft) }

function syncLetterRich(){
  const rich = el("letterRich")
  const plain = el("letter")
  if(!rich || !plain){ return }
  const text = (rich.innerText || "").split("\u00A0").join(" ").replace(/\n{3,}/g, "\n\n").trimEnd()
  plain.value = text
  latestLetterHtml = rich.innerHTML || ""
}
if(el("letterRich")){ el("letterRich").addEventListener("input", syncLetterRich) }

document.querySelectorAll(".toolBtn").forEach((btn) => {
  btn.addEventListener("click", () => {
    const rich = el("letterRich")
    if(!rich){ return }
    rich.focus()
    document.execCommand(btn.dataset.cmd, false, null)
    syncLetterRich()
  })
})

applyTheme()
setAnalyzeStatus("waiting")
renderCaseList()
