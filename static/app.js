
let uploadedFile = null
let latestAnalysis = null
let latestLetterHtml = ""

function el(id){ return document.getElementById(id) }

function toast(msg){
  const t = el("toast")
  t.textContent = msg
  t.style.display = "block"
  setTimeout(() => { t.style.display = "none" }, 2400)
}

function setStatus(msg){
  el("status").textContent = msg
}

function openPicker(){
  el("file").click()
}

function buildForm(){
  const recipientType = el("recipientType").value
  const toWhom = el("toWhom").value.trim()
  const reasonDx = el("reasonDx").value
  const reasonOther = el("reasonOther").value.trim()
  const reason = (reasonDx === "Other") ? reasonOther : reasonDx

  return {
    letter_type: el("letterType").value,
    recipient_type: recipientType,
    to_whom: toWhom,
    from_doctor: el("fromDoctor").value.trim(),
    reason_for_referral: reason,
    special_requests: el("specialRequests").value.trim()
  }
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

function populateReason(){
  const sel = el("reasonDx")
  const dx = (latestAnalysis && latestAnalysis.diagnoses) ? latestAnalysis.diagnoses : []
  sel.innerHTML = ""
  const opt0 = document.createElement("option")
  opt0.value = ""
  opt0.textContent = "Reason for referral"
  sel.appendChild(opt0)

  dx.forEach(item => {
    const o = document.createElement("option")
    const code = item.code ? `${item.code} ` : ""
    const val = `${code}${item.label || ""}`.trim()
    o.value = val
    o.textContent = `${item.number}. ${val}`.trim()
    sel.appendChild(o)
  })

  const other = document.createElement("option")
  other.value = "Other"
  other.textContent = "Other"
  sel.appendChild(other)
}

function applyRecipientBehavior(){
  const t = el("recipientType").value
  if(t === "Physician"){
    el("toWhom").placeholder = "Physician name"
  }else if(t === "Patient"){
    el("toWhom").placeholder = "Patient name"
  }else if(t === "Insurance"){
    el("toWhom").placeholder = "Insurance contact"
  }else{
    el("toWhom").placeholder = "Recipient name"
  }
}

async function analyze(){
  if(!uploadedFile){
    toast("Select a PDF first")
    return
  }
  setStatus("Upload successful. Analyzing.")
  const fd = new FormData()
  fd.append("pdf", uploadedFile)

  const res = await fetch("/analyze", { method:"POST", body: fd })
  const json = await res.json()
  if(!json.ok){
    setStatus("Analyze failed")
    toast(json.error || "Analyze failed")
    return
  }
  latestAnalysis = json.data || {}

  const provider = (latestAnalysis.provider_name || "").trim()
  if(provider){
    el("fromDoctor").value = provider
  }else{
    el("fromDoctor").value = ""
  }

  populateReason()
  renderDx()
  renderPlan()

  setStatus("Analysis complete. Ready to generate output.")
  toast("Please choose your preferred output.")
}

async function generate(){
  if(!uploadedFile){
    toast("Upload exam notes first")
    return
  }
  if(!latestAnalysis){
    toast("Please wait for analysis to complete")
    return
  }
  setStatus("Generating output.")
  const fd = new FormData()
  fd.append("pdf", uploadedFile)
  fd.append("payload", JSON.stringify({ form: buildForm(), analysis: latestAnalysis }))

  const res = await fetch("/generate", { method:"POST", body: fd })
  const json = await res.json()
  if(!json.ok){
    setStatus("Generation failed")
    toast(json.error || "Generation failed")
    return
  }
  el("letter").value = json.letter_plain || ""
  latestLetterHtml = json.letter_html || ""
  setStatus("Output ready.")
  toast("Output ready")
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
  setStatus("Preparing PDF.")
  const res = await fetch("/export_pdf", {
    method:"POST",
    headers: {"Content-Type":"application/json"},
    body: JSON.stringify({ text })
  })
  if(!res.ok){
    setStatus("PDF export failed")
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
  setStatus("Output ready.")
  toast("Downloaded")
}

el("uploadBtn").addEventListener("click", openPicker)
el("file").addEventListener("change", async (e) => {
  const f = e.target.files && e.target.files[0]
  if(!f){
    return
  }
  uploadedFile = f
  toast("Upload successful")
  await analyze()
})

el("recipientType").addEventListener("change", applyRecipientBehavior)

el("reasonDx").addEventListener("change", () => {
  const v = el("reasonDx").value
  if(v === "Other"){
    el("reasonOther").classList.remove("hidden")
  }else{
    el("reasonOther").classList.add("hidden")
    el("reasonOther").value = ""
  }
})

el("generateBtn").addEventListener("click", generate)
el("copyPlain").addEventListener("click", copyPlain)
el("copyRich").addEventListener("click", copyRich)
el("exportPdf").addEventListener("click", exportPdf)

applyRecipientBehavior()
