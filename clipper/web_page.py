"""The two pages of the clipper website, kept here so the package needs no data files."""

_HEAD = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="theme-color" content="#111214"><title>Clipper</title>
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E%3Crect width='32' height='32' rx='8' fill='%23ff5a36'/%3E%3Cpath d='M11 9v14l11-7z' fill='white'/%3E%3C/svg%3E">
<style>
:root{--bg:#111214;--card:#1b1d21;--line:#2c2f35;--text:#f1f1f3;--dim:#9a9ca3;--accent:#ff5a36;--ok:#39c47a;--bad:#ff6b6b}
*{box-sizing:border-box}[hidden]{display:none!important}
body{margin:0;background:var(--bg);color:var(--text);font:16px/1.45 system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
main{max-width:720px;margin:0 auto;padding:16px 16px 64px}
h1{font-size:22px;margin:8px 0 2px}h2{font-size:17px;margin:28px 0 10px}
.sub{color:var(--dim);font-size:14px;margin:0 0 16px}
.card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:14px;margin-bottom:12px}
label{display:block;font-size:14px;color:var(--dim);margin:12px 0 6px}
input,textarea,select{width:100%;background:#0d0e10;color:var(--text);border:1px solid var(--line);border-radius:10px;padding:11px 12px;font:inherit}
textarea{min-height:96px;resize:vertical;font-family:ui-monospace,Menlo,Consolas,monospace;font-size:15px}
input:focus,textarea:focus,select:focus{outline:2px solid var(--accent);border-color:transparent}
.row{display:flex;gap:10px;flex-wrap:wrap}.row>*{flex:1 1 140px}
button,.btn{display:inline-flex;align-items:center;justify-content:center;gap:6px;border:0;border-radius:10px;padding:11px 16px;font-family:inherit;font-weight:600;font-size:15px;line-height:1.2;cursor:pointer;text-decoration:none;color:var(--text);background:#2a2d33;min-height:44px}
button.main{background:var(--accent);color:#fff;width:100%;margin-top:14px;font-size:16px}
button:disabled{opacity:.5}
.btn.dl{background:var(--accent);color:#fff}
.small{font-size:13px;color:var(--dim)}
.title{font-weight:650;word-break:break-word}
.st{font-size:14px;color:var(--dim);margin-top:4px;word-break:break-word}
.st.bad{color:var(--bad)}.st.ok{color:var(--ok)}
.acts{display:flex;gap:8px;flex-wrap:wrap;margin-top:10px}
.clip{display:flex;align-items:center;gap:10px;justify-content:space-between;border-top:1px solid var(--line);padding:10px 0}
.clip:first-of-type{border-top:0}
.seg{display:flex;background:#0d0e10;border:1px solid var(--line);border-radius:10px;padding:3px}
.seg label{flex:1;margin:0;text-align:center;padding:9px 6px;border-radius:8px;color:var(--dim);cursor:pointer;font-size:14px}
.seg input{display:none}.seg input:checked+span{color:#fff}
.seg label:has(input:checked){background:#2a2d33}
video{width:100%;border-radius:10px;background:#000;margin-top:10px;max-height:60vh}
.panel{border-top:1px solid var(--line);margin-top:12px;padding-top:4px}
.err{color:var(--bad);font-size:14px;margin-top:10px;min-height:1em}
.bar{height:4px;background:#2a2d33;border-radius:4px;margin-top:8px;overflow:hidden}.bar i{display:block;height:100%;background:var(--accent);width:0}
.empty{color:var(--dim);font-size:14px}
</style></head><body><main>"""

LOGIN_PAGE = _HEAD + """
<h1>Clipper</h1><p class="sub">Sign in to make clips.</p>
<form class="card" method="post" action="/login">
<label for="p">Password</label><input id="p" name="password" type="password" autocomplete="current-password" autofocus>
<button class="main">Sign in</button><div class="err"><!--msg--></div></form></main></body></html>"""

PAGE = _HEAD + """
<h1>Clipper</h1>
<p class="sub">The server downloads and keeps the video, so your data only goes on the clips you download.</p>

<form class="card" id="add">
  <label for="url">Video link</label>
  <input id="url" name="url" inputmode="url" placeholder="https://..." autocomplete="off">
  <label for="times">End time of each clip <span class="small">(leave empty to just save the video for later)</span></label>
  <textarea id="times" name="times" placeholder="4:12&#10;9:30&#10;end"></textarea>
  <div class="row">
    <div><label for="start">Start from <span class="small">(optional)</span></label><input id="start" name="start" placeholder="0:00"></div>
    <div><label for="quality">Download quality</label><select id="quality" name="quality">
      <option value="1080">Up to 1080p</option><option value="best">Best available (4K, slower)</option></select></div>
  </div>
  <label>Clip shape</label>
  <div class="seg"><label><input type="radio" name="shape" value="vertical" checked><span>Vertical 9:16, follows faces</span></label>
  <label><input type="radio" name="shape" value="original"><span>Original shape</span></label></div>
  <button class="main" id="go">Save video</button>
  <div class="err" id="adderr"></div>
</form>

<h2>Clips</h2><div id="jobs"><p class="empty">No clips yet.</p></div>
<h2>Saved videos <span class="small" id="free"></span></h2><div id="videos"><p class="empty">Nothing saved yet.</p></div>
</main>
<script>
const $ = (s, el = document) => el.querySelector(s);
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const mb = n => n >= 1e9 ? (n / 1e9).toFixed(2) + " GB" : Math.max(0.1, n / 1e6).toFixed(1) + " MB";
const clock = s => { s = Math.round(s); const h = Math.floor(s / 3600), m = Math.floor(s % 3600 / 60), x = String(s % 60).padStart(2, "0");
  return h ? `${h}:${String(m).padStart(2, "0")}:${x}` : `${m}:${x}`; };
// "[2/3] name_part02.mp4  0:20.000 -> 4:10.000  finding faces 36%" -> "Clip 2 of 3 (0:20 to 4:10): finding faces 36%"
const nice = t => { const m = String(t || "").match(/^\[(\d+)\/(\d+)\]\s+\S+\s+([\d:.]+)\s+->\s+([\d:.]+)\s*(.*)$/);
  if (!m) return String(t || "").trim();
  const short = x => x.replace(/\.000$/, "");
  return `Clip ${m[1]} of ${m[2]} (${short(m[3])} to ${short(m[4])})${m[5] ? ": " + m[5].replace(/^done in/, "done in") : ""}`; };
const pct = t => { const m = String(t || "").match(/(\\d+)%/); return m ? +m[1] : null; };
let state = {videos: {}, jobs: {}};

async function api(path, body) {
  const r = await fetch(path, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body || {})});
  if (r.status === 401) location.reload();
  const data = await r.json().catch(() => ({error: "The server didn't answer properly."}));
  if (!r.ok) throw new Error(data.error || "Something went wrong.");
  return data;
}

const form = $("#add");
form.times.addEventListener("input", () => $("#go").textContent = form.times.value.trim() ? "Save video and make clips" : "Save video");
form.addEventListener("submit", async e => {
  e.preventDefault();
  $("#adderr").textContent = "";
  $("#go").disabled = true;
  try {
    await api("/api/add", {url: form.url.value, times: form.times.value, start: form.start.value,
      quality: form.quality.value, shape: form.shape.value});
    form.url.value = form.times.value = form.start.value = "";
    $("#go").textContent = "Save video";
    refresh();
  } catch (err) { $("#adderr").textContent = err.message; }
  $("#go").disabled = false;
});

function videoStatus(v) {
  if (v.status === "ready") return ["ok", `${clock(v.duration)} · ${v.height}p · ${mb(v.size)} saved on the server`];
  if (v.status === "failed") return ["bad", v.message];
  if (v.status === "waiting") return ["", "Waiting its turn…"];
  return ["", v.progress || (v.status === "preview" ? "Making a small preview…" : "Downloading on the server…")];
}

function renderVideos() {
  const box = $("#videos");
  const ids = Object.keys(state.videos).sort((a, b) => state.videos[b].added - state.videos[a].added);
  if (!ids.length) { box.innerHTML = '<p class="empty">Nothing saved yet.</p>'; return; }
  $(".empty", box)?.remove();
  for (const el of [...box.children]) if (!state.videos[el.dataset.id]) el.remove();
  ids.forEach((id, i) => {
    const v = state.videos[id];
    let card = box.querySelector(`[data-id="${id}"]`);
    if (!card) {
      card = document.createElement("div");
      card.className = "card"; card.dataset.id = id;
      card.innerHTML = `<div class="title"></div><div class="st"></div><div class="bar" hidden><i></i></div>
        <div class="acts"><button data-a="clips">Make clips</button><a class="btn" data-a="dl">Download video</a>
        <button data-a="retry">Try again</button><button data-a="del">Delete</button></div>
        <div class="panel" hidden>
          <button data-a="watch" style="margin-top:10px">Watch small preview to find times</button>
          <video controls playsinline preload="none" hidden></video>
          <button data-a="mark" hidden style="margin-top:8px;width:100%">Clip ends here</button>
          <label>End time of each clip</label><textarea placeholder="4:12&#10;9:30&#10;end"></textarea>
          <div class="row"><div><label>Start from (optional)</label><input class="start" placeholder="0:00"></div></div>
          <label>Clip shape</label>
          <div class="seg"><label><input type="radio" name="s${id}" value="vertical" checked><span>Vertical 9:16</span></label>
          <label><input type="radio" name="s${id}" value="original"><span>Original shape</span></label></div>
          <button class="main" data-a="make">Make clips</button><div class="err"></div></div>`;
      card.addEventListener("click", e => videoAction(e, id, card));
    }
    if (box.children[i] !== card) box.insertBefore(card, box.children[i] || null);
    $(".title", card).textContent = v.title;
    const [cls, text] = videoStatus(v);
    const st = $(".st", card); st.className = "st " + cls; st.textContent = text;
    const p = pct(v.progress); $(".bar", card).hidden = p === null || v.status !== "downloading";
    if (p !== null) $(".bar i", card).style.width = p + "%";
    const ready = v.status === "ready";
    $('[data-a="clips"]', card).hidden = !ready;
    const dl = $('[data-a="dl"]', card); dl.hidden = !ready; dl.href = `/media/video/${id}`;
    dl.textContent = ready ? `Download video (${mb(v.size)})` : "";
    $('[data-a="retry"]', card).hidden = v.status !== "failed";
    $('[data-a="watch"]', card).hidden = !v.preview || !$("video", card).hidden;
  });
}

async function videoAction(e, id, card) {
  const a = e.target.closest("[data-a]")?.dataset.a;
  if (!a || a === "dl") return;
  const err = $(".err", card);
  if (a === "clips") { const p = $(".panel", card); p.hidden = !p.hidden; if (!p.hidden) $("textarea", card).focus(); }
  if (a === "watch") { const vid = $("video", card); vid.src = `/media/preview/${id}.mp4`; vid.hidden = false;
    $('[data-a="mark"]', card).hidden = false; e.target.hidden = true; }
  if (a === "mark") { const t = $("textarea", card); const time = clock($("video", card).currentTime);
    t.value = (t.value.trim() ? t.value.trim() + "\\n" : "") + time; }
  if (a === "retry") { await api(`/api/videos/${id}/retry`).catch(x => alert(x.message)); refresh(); }
  if (a === "del" && confirm("Delete this saved video from the server? Clips already made stay.")) {
    await api(`/api/videos/${id}/delete`).catch(x => alert(x.message)); refresh(); }
  if (a === "make") {
    err.textContent = ""; e.target.disabled = true;
    try {
      await api("/api/add", {video: id, times: $("textarea", card).value, start: $(".start", card).value,
        shape: $(`input[name="s${id}"]:checked`, card).value});
      $("textarea", card).value = ""; $(".panel", card).hidden = true;
      refresh(); scrollTo({top: $("#jobs").offsetTop - 20, behavior: "smooth"});
    } catch (x) { err.textContent = x.message; }
    e.target.disabled = false;
  }
}

function renderJobs() {
  const box = $("#jobs");
  const ids = Object.keys(state.jobs).sort((a, b) => state.jobs[b].created - state.jobs[a].created);
  if (!ids.length) { box.innerHTML = '<p class="empty">No clips yet.</p>'; return; }
  const html = ids.map(id => {
    const j = state.jobs[id], v = state.videos[j.video] || {title: "(deleted video)"};
    const shape = j.shape === "original" ? "original shape" : "vertical 9:16";
    let body;
    if (j.status === "done") {
      const total = j.clips.reduce((n, c) => n + c.size, 0);
      body = `<div class="st ok">${j.clips.length} clip${j.clips.length > 1 ? "s" : ""}, ${j.width}×${j.height}, ${mb(total)} in all</div>` +
        j.clips.map((c, n) => `<div class="clip"><div><div>Part ${n + 1}</div><div class="small">${esc(c.start)} → ${esc(c.end)} · ${clock(c.duration)} · ${mb(c.size)}</div></div>
          <a class="btn dl" href="/media/clip/${id}/${encodeURIComponent(c.file)}">Download</a></div>`).join("") +
        `<div class="acts">${j.clips.length > 1 ? `<a class="btn" href="/media/zip/${id}">Download all (.zip)</a>` : ""}<button data-a="del">Delete clips</button></div>`;
    } else if (j.status === "failed") {
      body = `<div class="st bad">${esc(j.message)}</div><div class="acts"><button data-a="retry">Try again</button><button data-a="del">Delete</button></div>`;
    } else {
      const p = pct(j.progress);
      body = `<div class="st">${esc(j.status === "waiting" ? "Waiting its turn…" : nice(j.progress) || "Working…")}</div>` +
        (p !== null ? `<div class="bar"><i style="width:${p}%"></i></div>` : "");
    }
    return `<div class="card" data-id="${id}"><div class="title">${esc(v.title)}</div><div class="small">${shape}</div>${body}</div>`;
  }).join("");
  if (box.dataset.html !== html) { box.innerHTML = html; box.dataset.html = html; }
}
$("#jobs").addEventListener("click", async e => {
  const a = e.target.closest("[data-a]")?.dataset.a, id = e.target.closest("[data-id]")?.dataset.id;
  if (a === "del" && confirm("Delete these clips from the server?")) await api(`/api/jobs/${id}/delete`).catch(x => alert(x.message));
  if (a === "retry") await api(`/api/jobs/${id}/retry`).catch(x => alert(x.message));
  refresh();
});

let timer;
async function refresh() {
  clearTimeout(timer);
  try {
    const r = await fetch("/api/state");
    if (r.status === 401) return location.reload();
    state = await r.json();
    renderJobs(); renderVideos();
    $("#free").textContent = `· ${state.free_gb} GB free on the server`;
  } catch (e) {}
  const busy = Object.values(state.videos).concat(Object.values(state.jobs)).some(x => !["ready", "done", "failed"].includes(x.status));
  timer = setTimeout(refresh, busy ? 2000 : 15000);
}
refresh();
</script></body></html>"""
