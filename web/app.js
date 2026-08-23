"use strict";

// ---------- element refs ----------
const $ = (id) => document.getElementById(id);
const bodyEl = $("body"), reader = $("reader");
const audio = new Audio();

let SEGS = [];           // [{i,speaker,kind,text}]
let COLORS = {};         // speaker -> css color
let cache = {};          // segment index -> object URL (synthesized wav)
let idx = 0;             // current segment
let playing = false;     // actively advancing
let started = false;     // has playback begun this session
let pendingResolve = null;
let playGen = 0;         // bumped whenever a new play loop starts; kills stale loops
const PREFETCH = 4;      // how many upcoming segments to synthesize ahead of the playhead

// Only allow http(s) URLs from scraped content (block javascript:, data:, etc).
function safeUrl(u){
  try { const p = new URL(u, location.href); return /^https?:$/.test(p.protocol) ? p.href : "#"; }
  catch { return "#"; }
}

// Canonical-ish colors for the main cast; others get palette colors in order.
const CANON = {
  Narrator:"#7f8598", Subaru:"#e0655f", Emilia:"#b7a6ea", Rem:"#7fb2f0",
  Ram:"#e98aa8", Beatrice:"#f2c1d0", Roswaal:"#d5c86b", Puck:"#6fd0e8",
  Otto:"#c8b06b", Garfiel:"#f2b872", Echidna:"#e6e6ea", Felix:"#8fd694",
  Priscilla:"#f0a24f", Elsa:"#c98af0", Satella:"#9b6ce0",
};
const PALETTE = ["#54d1c4","#f2b872","#8fd694","#d59bf0","#f08a8a","#6fd0e8",
                 "#c8b06b","#7fb2f0","#e98aa8","#b7a6ea","#a0e0b0","#f0c07a"];

function colorFor(name, order){
  if (COLORS[name]) return COLORS[name];
  const c = CANON[name] || PALETTE[order % PALETTE.length];
  COLORS[name] = c; return c;
}

// ---------- tabs ----------
$("tab-url").onclick = () => switchTab("url");
$("tab-text").onclick = () => switchTab("text");
function switchTab(which){
  const url = which === "url";
  $("tab-url").classList.toggle("active", url);
  $("tab-text").classList.toggle("active", !url);
  $("pane-url").classList.toggle("hidden", !url);
  $("pane-text").classList.toggle("hidden", url);
}

// ---------- toast ----------
let toastTimer;
function toast(msg, isErr=false, sticky=false){
  const t = $("toast");
  t.innerHTML = msg; t.classList.remove("hidden");
  t.classList.toggle("err", isErr);
  clearTimeout(toastTimer);
  if (!sticky) toastTimer = setTimeout(() => t.classList.add("hidden"), 3200);
}
function hideToast(){ $("toast").classList.add("hidden"); }

// ---------- load chapter ----------
$("load").onclick = loadChapter;
$("url").addEventListener("keydown", e => { if (e.key === "Enter") loadChapter(); });

async function loadChapter(){
  const url = $("url").value.trim();
  const text = $("text").value.trim();
  if (!url && !text){ toast("Enter a chapter URL or paste some text.", true); return; }

  const btn = $("load");
  btn.disabled = true; btn.innerHTML = '<span class="spin"></span> Loading';
  stopPlayback();
  try{
    const payload = url ? {url, engine: ENGINE} : {text, engine: ENGINE};
    const res = await fetch("/api/load", {
      method:"POST", headers:{"Content-Type":"application/json"},
      body: JSON.stringify(payload),
    });
    const data = await res.json();
    if (!res.ok){ throw new Error(data.error || "Load failed"); }
    render(data);
    if (data.note) toast(data.note, true, false);
    startStatus();
  }catch(err){
    toast("Could not load chapter: " + err.message, true);
  }finally{
    btn.disabled = false; btn.textContent = "Load";
  }
}

let statusTimer = null;
function startStatus(){
  clearInterval(statusTimer);
  fetch("/api/prewarm", {method:"POST"}).catch(()=>{});   // nudge the model to load
  const buf = $("buffer");
  const poll = async () => {
    try{
      const s = await (await fetch("/api/status")).json();
      if (!s.model_loaded){ buf.textContent = "loading voices…"; buf.classList.add("warn"); return; }
      buf.classList.remove("warn");
      if (s.total && s.ready < s.total){
        buf.textContent = `voicing ${s.ready}/${s.total}`;
      } else {
        buf.textContent = "voiced ✓";
        clearInterval(statusTimer);
        setTimeout(() => { if (buf.textContent === "voiced ✓") buf.textContent = ""; }, 4000);
      }
    }catch(e){}
  };
  poll();
  statusTimer = setInterval(poll, 1200);
}

// ---------- engine toggle ----------
let ENGINE = "rules";
let GROQ_KEY = false;
fetch("/api/config").then(r => r.json()).then(c => {
  GROQ_KEY = !!c.groq_key_present;
  setEngine(c.engine || "rules");
}).catch(()=>{});
$("eng-rules").onclick = () => setEngine("rules");
$("eng-groq").onclick = () => {
  if (!GROQ_KEY){ $("key-modal").classList.remove("hidden"); $("key-input").focus(); return; }
  setEngine("groq");
  toast("Free Groq voices on — reload a chapter to give every character their own voice.");
};
$("eng-local").onclick = async () => {
  // free local LLM — only switch if a chat model is actually loaded
  try{
    const s = await (await fetch("/api/local_status")).json();
    if (s.available){ setEngine("local"); toast("Free local voices on — reload a chapter to hear the cast."); }
    else { openLocalModal(); }
  }catch(e){ openLocalModal(); }
};
function setEngine(e){
  ENGINE = e;
  $("eng-rules").classList.toggle("active", e === "rules");
  $("eng-groq").classList.toggle("active", e === "groq");
  $("eng-local").classList.toggle("active", e === "local");
}

// ---------- local (free) setup modal ----------
function openLocalModal(){ $("local-modal").classList.remove("hidden"); recheckLocal(); }
$("close-local").onclick = () => $("local-modal").classList.add("hidden");
$("local-modal").onclick = (e) => { if (e.target.id === "local-modal") $("local-modal").classList.add("hidden"); };
$("recheck-local").onclick = recheckLocal;
async function recheckLocal(){
  const st = $("local-state");
  st.textContent = "Checking…"; st.classList.remove("ok");
  try{
    const s = await (await fetch("/api/local_status")).json();
    if (s.available){
      st.textContent = "Model ready: " + s.models[0]; st.classList.add("ok");
      setTimeout(() => { $("local-modal").classList.add("hidden"); setEngine("local");
        toast("Free local voices on — reload a chapter to hear the cast."); }, 900);
    } else {
      st.textContent = "No chat model detected on " + s.base_url;
    }
  }catch(e){ st.textContent = "Local server not reachable yet."; }
}

// ---------- AI key modal ----------
$("close-key").onclick = () => $("key-modal").classList.add("hidden");
$("key-modal").onclick = (e) => { if (e.target.id === "key-modal") $("key-modal").classList.add("hidden"); };
$("key-input").addEventListener("keydown", e => { if (e.key === "Enter") $("save-key").click(); });
$("save-key").onclick = async () => {
  const key = $("key-input").value.trim();
  if (!key){ toast("Paste your free Groq key first.", true); return; }
  try{
    const r = await fetch("/api/set_key", {method:"POST", headers:{"Content-Type":"application/json"}, body: JSON.stringify({key})});
    const d = await r.json();
    if (!r.ok) throw new Error(d.error || "could not save key");
    const eng = d.provider === "anthropic" ? "ai" : "groq";
    if (eng === "groq") GROQ_KEY = true;
    $("key-input").value = "";
    $("key-modal").classList.add("hidden");
    setEngine(eng);
    toast("Free AI voices on ✓ — reload a chapter to hear distinct characters.");
  }catch(err){ toast(err.message, true); }
};

// ---------- voices modal ----------
$("open-voices").onclick = openVoices;
$("close-voices").onclick = () => $("voices-modal").classList.add("hidden");
$("voices-modal").onclick = (e) => { if (e.target.id === "voices-modal") $("voices-modal").classList.add("hidden"); };

async function openVoices(){
  $("voices-modal").classList.remove("hidden");
  const grid = $("voices-grid");
  grid.innerHTML = '<div style="grid-column:1/-1;color:var(--faint)">Loading…</div>';
  try{
    const { voices } = await (await fetch("/api/voices")).json();
    grid.innerHTML = "";
    voices.forEach(v => grid.appendChild(voiceCard(v)));
  }catch(e){ grid.innerHTML = '<div style="color:var(--rose)">Could not load voices.</div>'; }
}

function voiceCard(v){
  const c = colorFor(v.name);
  const card = document.createElement("div"); card.className = "voice-card";
  card.innerHTML = `
    <span class="dot" style="background:${c}"></span>
    <div>
      <div class="vname">${escapeHtml(v.name)}</div>
      <div class="vstatus ${v.cloned ? "on" : ""}">${v.cloned ? "Cloned ✓" : "Built-in voice"}</div>
    </div>
    <div class="voice-actions">
      <button class="vbtn preview" title="Preview">▶</button>
      <button class="vbtn upload" title="Upload a clip">⬆</button>
      ${v.cloned ? '<button class="vbtn del" title="Remove clone">🗑</button>' : ""}
    </div>`;
  const file = document.createElement("input");
  file.type = "file"; file.accept = "audio/*,video/*"; file.style.display = "none";
  card.appendChild(file);

  card.querySelector(".preview").onclick = () => previewVoice(v.name, card.querySelector(".preview"));
  card.querySelector(".upload").onclick = () => file.click();
  file.onchange = () => uploadVoice(v.name, file.files[0]);
  const del = card.querySelector(".del");
  if (del) del.onclick = () => deleteVoice(v.name);
  return card;
}

async function previewVoice(name, btn){
  const old = btn.textContent; btn.textContent = "…";
  try{
    const r = await fetch(`/api/voices/${encodeURIComponent(name)}/sample`);
    if (!r.ok) throw new Error();
    const a = new Audio(URL.createObjectURL(await r.blob()));
    a.onended = () => { btn.textContent = old; };
    a.play();
  }catch(e){ toast("Preview failed (model may still be warming up).", true); btn.textContent = old; }
}

async function uploadVoice(name, fileObj){
  if (!fileObj) return;
  toast(`<span class="spin"></span> Cloning ${escapeHtml(name)}'s voice…`, false, true);
  const fd = new FormData(); fd.append("clip", fileObj);
  try{
    const r = await fetch(`/api/voices/${encodeURIComponent(name)}`, { method: "POST", body: fd });
    const d = await r.json();
    if (!r.ok) throw new Error(d.error || "upload failed");
    toast(`${name} is now cloned ✓`);
    openVoices();
  }catch(e){ toast("Upload failed: " + e.message, true); }
}

async function deleteVoice(name){
  try{
    await fetch(`/api/voices/${encodeURIComponent(name)}`, { method: "DELETE" });
    toast(`${name} reverted to built-in voice`);
    openVoices();
  }catch(e){ toast("Could not remove clip.", true); }
}

// ---------- render ----------
function render(data){
  Object.values(cache).forEach(URL.revokeObjectURL);   // free previous chapter's blobs
  SEGS = data.segments; cache = {}; COLORS = {}; idx = 0; started = false;
  // assign colors in appearance order
  let order = 0;
  for (const s of SEGS){ if (!COLORS[s.speaker]) colorFor(s.speaker, order++); }

  $("empty").classList.add("hidden");
  $("chapter").classList.remove("hidden");
  $("sidebar").classList.remove("hidden");
  $("playbar").classList.remove("hidden");
  $("chapter-title").textContent = data.title || "Untitled Chapter";

  // category chips
  const chips = $("chips"); chips.innerHTML = "";
  (data.categories || []).forEach(c => {
    const el = document.createElement("span"); el.className = "chip"; el.textContent = c;
    chips.appendChild(el);
  });

  // body
  bodyEl.innerHTML = "";
  SEGS.forEach(s => bodyEl.appendChild(segEl(s)));

  renderAnalysis(data.analysis);
  renderCast(data.analysis);
  renderImages(data.images);
  renderNav(data.nav);
  renderLinks(data.links);
  renderComments(data.comments);

  setNow(-1, "Ready", "Press play to start reading");
  setProgress();
  window.scrollTo({top:0, behavior:"smooth"});
}

function segEl(s){
  const div = document.createElement("div");
  div.className = "seg-line " + (s.kind === "dialogue" ? "dialogue" : "narration");
  div.id = "seg-" + s.i;
  if (s.kind === "dialogue"){
    div.style.setProperty("--sp", colorFor(s.speaker));
    const who = document.createElement("span"); who.className = "who";
    who.textContent = s.speaker; div.appendChild(who);
    const say = document.createElement("span"); say.className = "say";
    say.textContent = " " + s.text; div.appendChild(say);
  } else {
    div.textContent = s.text;
  }
  div.onclick = () => jumpTo(s.i);
  return div;
}

function renderAnalysis(a){
  const g = $("stat-grid");
  const mins = a.est_minutes;
  const dur = mins >= 60 ? `${Math.floor(mins/60)}h ${Math.round(mins%60)}` : mins;
  g.innerHTML = `
    <div class="stat"><div class="n">${a.total_words.toLocaleString()}</div><div class="k">Words</div></div>
    <div class="stat"><div class="n">${a.total_segments}</div><div class="k">Segments</div></div>
    <div class="stat"><div class="n">${dur}<small>${mins>=60?'':' min'}</small></div><div class="k">Est. audio</div></div>
    <div class="stat"><div class="n">${a.cast_size}</div><div class="k">Speakers</div></div>`;
  $("dialogue-bar").querySelector(".bar-fill").style.width = a.dialogue_pct + "%";
}

function renderCast(a){
  $("cast-count").textContent = "(" + a.cast_size + ")";
  const list = $("cast-list"); list.innerHTML = "";
  const max = Math.max(...a.speakers.map(s => s.words), 1);
  a.speakers.forEach(s => {
    const c = colorFor(s.speaker);
    const row = document.createElement("div"); row.className = "cast-row";
    row.style.setProperty("--sp", c);
    row.innerHTML = `
      <span class="dot" style="background:${c}"></span>
      <span class="cast-name">${escapeHtml(s.speaker)}</span>
      <span class="cast-meta">${s.segments} lines · ${s.words}w</span>`;
    const bar = document.createElement("div");
    bar.className = "cast-bar";
    bar.style.cssText = `width:${Math.round(60*s.words/max)+40}%;--sp:${c}`;
    list.appendChild(row);
  });
}

function renderImages(images){
  const card = $("card-images"), g = $("gallery");
  g.innerHTML = "";
  const imgs = (images || []).filter(Boolean);
  if (!imgs.length){ card.classList.add("hidden"); return; }
  card.classList.remove("hidden");
  imgs.forEach(src => {
    const safe = safeUrl(src);
    if (safe === "#") return;
    const im = document.createElement("img");
    im.src = safe; im.loading = "lazy";
    im.onerror = () => im.remove();
    im.onclick = () => window.open(safe, "_blank", "noopener");
    g.appendChild(im);
  });
}

function renderNav(nav){
  const card = $("card-nav"), box = $("nav-links");
  box.innerHTML = "";
  const items = [];
  if (nav && nav.prev) items.push(["Previous", nav.prev]);
  if (nav && nav.next) items.push(["Next", nav.next]);
  if (!items.length){ card.classList.add("hidden"); return; }
  card.classList.remove("hidden");
  items.forEach(([dir, n]) => {
    const href = safeUrl(n.href || "#");
    const a = document.createElement("a");
    a.className = "nav-item"; a.href = href; a.target = "_blank"; a.rel = "noopener";
    a.innerHTML = `<div class="dir">${dir}</div><div class="t">${escapeHtml(n.title || n.href || "")}</div>`;
    if (href !== "#") a.onclick = (e) => { e.preventDefault(); $("url").value = href; switchTab("url"); loadChapter(); };
    box.appendChild(a);
  });
}

function renderLinks(links){
  const card = $("card-links"), box = $("links-list");
  box.innerHTML = "";
  const ls = links || [];
  if (!ls.length){ card.classList.add("hidden"); return; }
  card.classList.remove("hidden");
  $("links-count").textContent = "(" + ls.length + ")";
  ls.forEach(l => {
    const href = safeUrl(l.href);
    if (href === "#") return;
    const a = document.createElement("a");
    a.href = href; a.target = "_blank"; a.rel = "noopener";
    a.textContent = l.text || l.href;
    box.appendChild(a);
  });
}

function renderComments(comments){
  const card = $("card-comments"), box = $("comments-list");
  box.innerHTML = "";
  const cs = comments || [];
  if (!cs.length){ card.classList.add("hidden"); return; }
  card.classList.remove("hidden");
  $("comments-count").textContent = "(" + cs.length + ")";
  cs.forEach(c => {
    const el = document.createElement("div");
    el.className = "comment" + (c.is_reply ? " reply" : "");
    const head = document.createElement("div"); head.className = "comment-head";
    const au = document.createElement("span"); au.className = "comment-author";
    au.textContent = c.author || "Reader";           // textContent = XSS-safe
    head.appendChild(au);
    if (c.is_reply){ const t=document.createElement("span"); t.className="reply-tag"; t.textContent="reply"; head.appendChild(t); }
    if (c.date){ const d=document.createElement("span"); d.className="comment-date"; d.textContent=c.date; head.appendChild(d); }
    const body = document.createElement("div"); body.className = "comment-body";
    body.textContent = c.content || "";
    el.appendChild(head); el.appendChild(body);
    box.appendChild(el);
  });
}

// ---------- playback ----------
$("play").onclick = togglePlay;
$("stop").onclick = stopPlayback;
$("speed").onchange = () => { audio.playbackRate = parseFloat($("speed").value); };

function startLoop(from){ playGen++; playing = true; started = true; setPlayIcon(true); runFrom(from, playGen); }

function togglePlay(){
  if (!SEGS.length) return;
  if (!started){ startLoop(idx); return; }
  if (playing){ playing = false; audio.pause(); setPlayIcon(false); }        // pause
  else if (audio.paused && audio.src){ playing = true; setPlayIcon(true); audio.play(); }  // resume same segment
  else { startLoop(idx); }                                                    // resume from a clean state
}

function jumpTo(i){
  cancelWait();
  audio.pause();
  idx = i;
  startLoop(i);
}

async function runFrom(start, gen){
  idx = start;
  while (playing && gen === playGen && idx < SEGS.length){
    highlight(idx);
    setNow(idx);
    let url;
    try{ url = await getSeg(idx); }
    catch(e){ if (gen===playGen){ toast("Voice synthesis failed at line " + (idx+1), true); playing=false; setPlayIcon(false); } return; }
    if (!playing || gen !== playGen) return;    // paused/stopped/superseded while synthesizing
    // Buffer several lines ahead so synthesis overlaps playback and never stalls.
    for (let k = 1; k <= PREFETCH; k++){
      if (idx + k < SEGS.length) getSeg(idx + k).catch(()=>{});
    }
    await playUrl(url);
    if (!playing || gen !== playGen) return;    // stopped/superseded during playback
    markDone(idx);
    idx++; setProgress();
  }
  if (gen === playGen && idx >= SEGS.length){
    playing = false; started = false; setPlayIcon(false);
    setNow(-1, "Finished", "Reached the end of the chapter");
  }
}

async function getSeg(i){
  if (cache[i]) return cache[i];
  const r = await fetch("/api/audio/" + i);
  if (!r.ok) throw new Error("audio " + i);
  const url = URL.createObjectURL(await r.blob());
  cache[i] = url; return url;
}

function playUrl(url){
  return new Promise((resolve) => {
    pendingResolve = resolve;
    const done = () => { if (pendingResolve){ pendingResolve = null; resolve(); } };
    audio.src = url;
    audio.playbackRate = parseFloat($("speed").value);
    audio.onended = done;
    audio.onerror = done;                       // don't freeze on a bad segment
    audio.play().catch(() => { setPlayIcon(false); }); // autoplay blocked: wait for user
  });
}
function cancelWait(){ if (pendingResolve){ const r = pendingResolve; pendingResolve = null; r(); } }

function stopPlayback(){
  playGen++;                                     // invalidate any running loop
  playing = false; started = false;
  audio.pause(); audio.removeAttribute("src");
  cancelWait();
  setPlayIcon(false);
  document.querySelectorAll(".seg-line").forEach(e => e.classList.remove("active","done"));
  idx = 0; setProgress();
  setNow(-1, "Ready", "Press play to start reading");
}

// ---------- ui helpers ----------
function highlight(i){
  document.querySelectorAll(".seg-line.active").forEach(e => e.classList.remove("active"));
  const el = $("seg-" + i);
  if (el){ el.classList.add("active"); el.scrollIntoView({behavior:"smooth", block:"center"}); }
}
function markDone(i){ const el = $("seg-"+i); if (el){ el.classList.remove("active"); el.classList.add("done"); } }
function setPlayIcon(isPlaying){
  $("play").querySelector(".ic-play").classList.toggle("hidden", isPlaying);
  $("play").querySelector(".ic-pause").classList.toggle("hidden", !isPlaying);
}
function setNow(i, speakerOverride, lineOverride){
  const sp = $("now-speaker"), ln = $("now-line");
  if (i < 0){ sp.textContent = speakerOverride || "Ready"; sp.style.color = "var(--accent)";
              ln.textContent = lineOverride || ""; return; }
  const s = SEGS[i];
  sp.textContent = s.kind === "dialogue" ? s.speaker : "Narration";
  sp.style.color = colorFor(s.speaker);
  ln.textContent = s.text;
}
function setProgress(){
  const done = idx;
  const pct = SEGS.length ? Math.round(100*done/SEGS.length) : 0;
  $("progress-fill").style.width = pct + "%";
  $("prog-count").textContent = `${Math.min(done+ (playing?1:0), SEGS.length)} / ${SEGS.length}`;
}
function escapeHtml(s){ return (s||"").replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c])); }

// keyboard: space = play/pause
document.addEventListener("keydown", e => {
  if (e.code === "Space" && e.target.tagName !== "INPUT" && e.target.tagName !== "TEXTAREA"){
    e.preventDefault(); togglePlay();
  }
});
