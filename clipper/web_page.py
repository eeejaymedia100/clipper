"""The clipper website's one page, kept here so the package needs no data files."""

PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="theme-color" content="#111214"><title>Clipper</title>
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E%3Crect width='32' height='32' rx='8' fill='%23ff5a36'/%3E%3Cpath d='M11 9v14l11-7z' fill='white'/%3E%3C/svg%3E">
<style>
:root{--bg:#111214;--card:#1b1d21;--line:#2c2f35;--text:#f1f1f3;--dim:#9a9ca3;--accent:#ff5a36;--ok:#39c47a;--bad:#ff6b6b}
*{box-sizing:border-box}[hidden]{display:none!important}
body{margin:0;background:var(--bg);color:var(--text);font:16px/1.45 system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
main{max-width:640px;margin:0 auto;padding:16px 16px 64px}
h1{font-size:22px;margin:8px 0 14px}h2{font-size:16px;margin:26px 0 10px;color:var(--dim);font-weight:600}
.card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:14px;margin-bottom:12px}
label{display:block;font-size:14px;color:var(--dim);margin:12px 0 6px}
input{width:100%;background:#0d0e10;color:var(--text);border:1px solid var(--line);border-radius:10px;padding:12px;font:inherit;font-size:17px}
input.time{font-family:ui-monospace,Menlo,Consolas,monospace;text-align:center}
input:focus{outline:2px solid var(--accent);border-color:transparent}
.row{display:flex;gap:10px}.row>*{flex:1}
button,.btn{display:inline-flex;align-items:center;justify-content:center;border:0;border-radius:10px;padding:11px 14px;font-family:inherit;font-weight:600;font-size:15px;line-height:1.2;cursor:pointer;text-decoration:none;color:var(--text);background:#2a2d33;min-height:44px}
button.main{background:var(--accent);color:#fff;width:100%;margin-top:14px;font-size:16px;min-height:50px}
button:disabled{opacity:.5}
.btn.dl{background:var(--accent);color:#fff}
.title{font-weight:650;font-size:17px;word-break:break-word}
.st{font-size:14px;color:var(--dim);margin-top:4px;word-break:break-word}
.st.bad,.err{color:var(--bad)}.st.ok{color:var(--ok)}
.err{font-size:14px;margin-top:10px;min-height:1em}
.bar{position:relative;height:10px;background:#2a2d33;border-radius:6px;margin-top:10px;overflow:hidden}
.bar i{position:absolute;top:0;bottom:0;background:var(--accent)}
.bar.done i{background:var(--ok)}
.clip{display:flex;align-items:center;gap:10px;justify-content:space-between;border-top:1px solid var(--line);padding:12px 0}
.clip:first-child{border-top:0}
.small{font-size:13px;color:var(--dim)}
video{width:100%;border-radius:10px;background:#000;margin-top:12px;max-height:50vh}
.marks{display:flex;gap:8px;margin-top:8px}.marks button{flex:1}
.pick{display:flex;justify-content:space-between;align-items:center;gap:10px;width:100%;text-align:left;background:var(--card);border:1px solid var(--line);margin-bottom:8px;font-weight:500}
.pick.on{border-color:var(--accent)}
.x{background:none;color:var(--dim);min-height:36px;padding:6px 8px;font-weight:500}
</style></head><body><main>
<h1>Clipper</h1>

<form class="card" id="add">
  <label for="url" style="margin-top:0">Paste a video link</label>
  <input id="url" inputmode="url" placeholder="https://..." autocomplete="off">
  <button class="main" id="get">Get video</button>
  <div class="err" id="adderr"></div>
</form>

<section id="current" hidden>
  <div class="card">
    <div class="title" id="vtitle"></div>
    <div class="st" id="vstatus"></div>
    <div class="bar" id="vbar"></div>
    <div id="vready" hidden>
      <button id="watch" style="margin-top:12px;width:100%">Watch preview to find the times</button>
      <div class="small" id="nopreview" style="margin-top:10px">A small preview for finding the times is on its way. You can type the times now.</div>
      <video id="player" controls playsinline preload="none" hidden></video>
      <div class="marks" id="marks" hidden><button id="markstart">Start here</button><button id="markend">End here</button></div>
      <div class="row">
        <div><label for="start">Start</label><input class="time" id="start" placeholder="0:00" inputmode="decimal"></div>
        <div><label for="end">End</label><input class="time" id="end" placeholder="end" inputmode="decimal"></div>
      </div>
      <button class="main" id="make">Make 9:16 clip</button>
      <div class="err" id="cliperr"></div>
    </div>
    <div id="vfailed" hidden style="margin-top:10px"><button id="vretry">Try again</button></div>
  </div>
  <div class="card" id="clips" hidden></div>
  <div style="text-align:right"><button class="x" id="vdelete">Delete this video from the server</button></div>
</section>

<div id="others" hidden><h2>Saved videos</h2><div id="list"></div></div>
</main>
<script>
const $ = s => document.querySelector(s);
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const mb = n => n >= 1e9 ? (n / 1e9).toFixed(2) + " GB" : Math.max(0.1, n / 1e6).toFixed(1) + " MB";
const clock = s => { s = Math.round(s); const h = Math.floor(s / 3600), m = Math.floor(s % 3600 / 60), x = String(s % 60).padStart(2, "0");
  return h ? `${h}:${String(m).padStart(2, "0")}:${x}` : `${m}:${x}`; };
const pct = t => { const m = String(t || "").match(/(\\d+)%/); return m ? +m[1] : null; };
const step = t => { const m = String(t || "").match(/(finding faces|encoding|downloading \\w+)\\s+(\\d+)%/);
  return m ? `${m[1][0].toUpperCase() + m[1].slice(1)} ${m[2]}%` : String(t || "").trim(); };
let state = {videos: {}, jobs: {}}, current = null, shownFor = null;
try { current = localStorage.getItem("clipper.video"); } catch (e) {}

async function api(path, body) {
  const r = await fetch(path, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body || {})});
  const data = await r.json().catch(() => ({error: "The server didn't answer properly."}));
  if (!r.ok) throw new Error(data.error || "Something went wrong.");
  return data;
}
function choose(id) {
  current = id; shownFor = null;
  try { localStorage.setItem("clipper.video", id); } catch (e) {}
  render();
}

$("#add").addEventListener("submit", async e => {
  e.preventDefault();
  $("#adderr").textContent = ""; $("#get").disabled = true;
  try { const {video} = await api("/api/video", {url: $("#url").value}); $("#url").value = ""; await load(); choose(video); }
  catch (x) { $("#adderr").textContent = x.message; }
  $("#get").disabled = false;
});

const clipsOf = id => Object.entries(state.jobs).filter(([, j]) => j.video === id).sort((a, b) => a[1].part - b[1].part);
// the parts of the video already clipped (or being clipped), merged
function covered(id) {
  const spans = clipsOf(id).filter(([, j]) => j.status !== "failed").map(([, j]) => [j.start, j.end]).sort((a, b) => a[0] - b[0]);
  const out = [];
  for (const [a, b] of spans) { const last = out[out.length - 1]; if (last && a <= last[1] + 0.5) last[1] = Math.max(last[1], b); else out.push([a, b]); }
  return out;
}
const nextStart = id => { const c = covered(id), d = state.videos[id].duration;
  const s = c.length && c[0][0] <= 0.5 ? c[0][1] : 0; return s >= d - 0.5 ? 0 : s; };

function render() {
  const ids = Object.keys(state.videos).sort((a, b) => state.videos[b].added - state.videos[a].added);
  if (!state.videos[current]) current = ids[0] || null;
  $("#current").hidden = !current;
  if (current) renderCurrent(current);
  const others = ids.filter(id => id !== current);
  $("#others").hidden = !others.length;
  $("#list").innerHTML = others.map(id => {
    const v = state.videos[id], done = v.duration ? covered(id).reduce((n, [a, b]) => n + b - a, 0) / v.duration : 0;
    const note = v.status === "ready" ? (done >= 0.995 ? "all clipped" : `${Math.round(done * 100)}% clipped`) : v.status;
    return `<button class="pick" data-id="${id}"><span>${esc(v.title)}</span><span class="small">${note}</span></button>`;
  }).join("");
}
$("#list").addEventListener("click", e => { const b = e.target.closest("[data-id]"); if (b) { choose(b.dataset.id); scrollTo({top: 0, behavior: "smooth"}); } });

function renderCurrent(id) {
  const v = state.videos[id];
  $("#vtitle").textContent = v.title;
  const ready = v.status === "ready";
  $("#vready").hidden = !ready; $("#vfailed").hidden = v.status !== "failed";
  const st = $("#vstatus"), bar = $("#vbar");
  bar.className = "bar";
  if (ready) {
    const spans = covered(id), done = spans.reduce((n, [a, b]) => n + b - a, 0), all = done >= v.duration - 0.5;
    st.className = "st " + (all ? "ok" : "");
    st.textContent = all ? `Whole video clipped (${clock(v.duration)})` : `${clock(done)} of ${clock(v.duration)} clipped · saved on the server`;
    bar.classList.toggle("done", all);
    bar.innerHTML = spans.map(([a, b]) => `<i style="left:${a / v.duration * 100}%;width:${(b - a) / v.duration * 100}%"></i>`).join("");
    $("#watch").hidden = !v.preview || !$("#player").hidden;
    $("#nopreview").hidden = !!v.preview;
    if (shownFor !== id) {           // a new video: fresh inputs, start where the last clip ended
      shownFor = id;
      $("#player").hidden = $("#marks").hidden = true; $("#player").removeAttribute("src");
      $("#end").value = ""; $("#cliperr").textContent = "";
      const s = nextStart(id); $("#start").value = s ? clock(s) : "";
    }
  } else if (v.status === "failed") {
    st.className = "st bad"; st.textContent = v.message; bar.innerHTML = "";
  } else {
    st.className = "st";
    st.textContent = v.status === "waiting" ? "Waiting its turn…" : step(v.progress) || "Downloading on the server…";
    const p = pct(v.progress);
    bar.innerHTML = p === null ? "" : `<i style="left:0;width:${p}%"></i>`;
  }
  const clips = clipsOf(id);
  $("#clips").hidden = !clips.length;
  const html = clips.map(([jid, j]) => {
    let right, note = `${clock(j.start)} → ${clock(j.end)}`;
    if (j.status === "done") { note += ` · ${mb(j.size)}`; right = `<a class="btn dl" href="/media/clip/${jid}">Download</a>`; }
    else if (j.status === "failed") { note = esc(j.message); right = `<button data-a="retry" data-id="${jid}">Try again</button>`; }
    else { note += " · " + esc(j.status === "waiting" ? "waiting its turn" : step(j.progress) || "working"); right = ""; }
    return `<div class="clip"><div><div>Part ${j.part}</div><div class="small${j.status === "failed" ? " err" : ""}">${note}</div></div>${right}</div>`;
  }).join("");
  if ($("#clips").dataset.html !== html) { $("#clips").innerHTML = html; $("#clips").dataset.html = html; }
}

$("#watch").addEventListener("click", () => {
  const p = $("#player"); p.src = `/media/preview/${current}.mp4`; p.hidden = false; $("#marks").hidden = false; $("#watch").hidden = true;
  const s = $("#start").value.split(":").reduce((n, x) => n * 60 + Number(x), 0); if (s > 0) p.currentTime = s;
});
$("#markstart").addEventListener("click", () => $("#start").value = clock($("#player").currentTime));
$("#markend").addEventListener("click", () => $("#end").value = clock($("#player").currentTime));
$("#make").addEventListener("click", async () => {
  $("#cliperr").textContent = ""; $("#make").disabled = true;
  try {
    await api("/api/clip", {video: current, start: $("#start").value, end: $("#end").value});
    await load();
    const s = nextStart(current); $("#start").value = s ? clock(s) : ""; $("#end").value = "";
  } catch (x) { $("#cliperr").textContent = x.message; }
  $("#make").disabled = false;
});
$("#clips").addEventListener("click", async e => {
  const b = e.target.closest("[data-a]"); if (!b) return;
  await api(`/api/jobs/${b.dataset.id}/retry`).catch(x => alert(x.message)); load();
});
$("#vretry").addEventListener("click", async () => { await api(`/api/videos/${current}/retry`).catch(x => alert(x.message)); load(); });
$("#vdelete").addEventListener("click", async () => {
  if (!confirm("Delete this video and its clips from the server? Clips you've already downloaded stay on your phone.")) return;
  try { await api(`/api/videos/${current}/delete`); current = null; shownFor = null; load(); } catch (x) { alert(x.message); }
});

let timer;
async function load() {
  clearTimeout(timer);
  try { const r = await fetch("/api/state"); state = await r.json(); render(); } catch (e) {}
  const busy = [...Object.values(state.videos), ...Object.values(state.jobs)].some(x => !["ready", "done", "failed"].includes(x.status) || (x.status === "ready" && x.preview === false));
  timer = setTimeout(load, busy ? 2000 : 15000);
}
load();
</script></body></html>"""
