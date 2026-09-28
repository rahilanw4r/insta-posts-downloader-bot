import os
import re
import shutil
import tempfile
import threading
import uuid
import zipfile
from pathlib import Path
from flask import Flask, jsonify, render_template_string, request, send_file, abort
import instaloader

app = Flask(__name__)
JOBS = {}
LOCK = threading.Lock()
ROOT = Path(tempfile.gettempdir()) / "profile_archives"
ROOT.mkdir(exist_ok=True)
USERNAME_RE = re.compile(r"^[A-Za-z0-9._]{1,30}$")

PAGE = """<!doctype html>
<html lang="en"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Profile Archive Downloader</title>
<style>
:root{color-scheme:dark}*{box-sizing:border-box}body{margin:0;background:#101114;color:#f4f4f5;font:16px system-ui;min-height:100vh;display:grid;place-items:center;padding:20px}
main{width:min(560px,100%);background:#1a1c21;border:1px solid #30333b;border-radius:18px;padding:28px}
h1{font-size:25px;margin:0 0 8px}p{color:#b7bbc5;line-height:1.5}.field{display:flex;gap:8px;margin:22px 0}
input{min-width:0;flex:1;padding:14px;border-radius:10px;border:1px solid #454955;background:#111318;color:white;font:inherit}
button,.download{border:0;border-radius:10px;background:#f4f4f5;color:#111318;padding:14px 17px;font:600 15px system-ui;cursor:pointer;text-decoration:none}
button:disabled{opacity:.5;cursor:wait}.note{font-size:13px;color:#9da3af}.status{margin-top:18px;padding:14px;background:#111318;border-radius:10px;min-height:48px;white-space:pre-wrap}.download{display:inline-block;margin-top:12px}
</style><main><h1>Profile Archive Downloader</h1>
<p>Enter a public Instagram username to collect its accessible posts into ZIP archive(s), then save them to your device.</p>
<form id="form"><div class="field"><input id="username" placeholder="username or instagram.com/username" required maxlength="100"><button id="go">Download all</button></div></form>
<div class="status" id="status" aria-live="polite">Ready. No Instagram password needed.</div><p class="note">Only public content you have permission to save. Instagram may throttle or block automated access; full-profile downloads are not guaranteed. Large profiles may take a while.</p></main>
<script>
const form=document.querySelector('#form'), input=document.querySelector('#username'), go=document.querySelector('#go'), statusBox=document.querySelector('#status');
async function readJson(response){
 const raw=await response.text();
 try{return JSON.parse(raw)}catch(_){throw Error("Server returned a non-JSON response (HTTP "+response.status+"). Check that Flask is running and open the forwarded port 8080 URL, not the GitHub preview. "+raw.slice(0,180))}
}
form.addEventListener('submit',async e=>{e.preventDefault();go.disabled=true;statusBox.textContent='Starting…';
try{const r=await fetch('/api/jobs',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({username:input.value})});const data=await readJson(r);if(!r.ok)throw Error(data.error||'Could not start job');poll(data.id)}catch(err){statusBox.textContent=err.message;go.disabled=false}});
async function poll(id){try{const r=await fetch('/api/jobs/'+id);const j=await readJson(r);if(!r.ok)throw Error(j.error||'Status check failed');statusBox.textContent=j.message||j.status;
if(j.status==='done'){statusBox.innerHTML='Archive ready. <a class="download" href="/api/jobs/'+id+'/download">Save ZIP to device</a>';go.disabled=false;return}
if(j.status==='error'){go.disabled=false;return}setTimeout(()=>poll(id),2500)}catch(err){statusBox.textContent=err.message;go.disabled=false}}
</script></html>"""

@app.get("/")
def home():
    return render_template_string(PAGE)

def normalize_username(value):
    value = (value or "").strip()
    value = re.sub(r"^https?://(www\.)?instagram\.com/", "", value, flags=re.I)
    value = value.split("?")[0].strip("/")
    value = value.lstrip("@")
    if not USERNAME_RE.fullmatch(value):
        raise ValueError("Enter a valid Instagram username or profile URL.")
    return value

def run_job(job_id, username):
    work = ROOT / job_id
    work.mkdir(parents=True, exist_ok=True)
    try:
        loader = instaloader.Instaloader(
            dirname_pattern=str(work / "{target}"),
            filename_pattern="{date_utc}_UTC_{shortcode}",
            download_pictures=True, download_videos=True,
            download_video_thumbnails=False, download_geotags=False,
            save_metadata=False, compress_json=False, post_metadata_txt_pattern=""
        )
        profile = instaloader.Profile.from_username(loader.context, username)
        if not profile.is_private:
            with LOCK:
                JOBS[job_id]["message"] = "Profile found. Downloading accessible posts…"
            count = 0
            for post in profile.get_posts():
                loader.download_post(post, target=profile.username)
                count += 1
                with LOCK:
                    JOBS[job_id]["message"] = f"Downloaded {count} post(s)…"
        else:
            raise RuntimeError("This profile is private. Only public profiles are supported.")
        zip_path = ROOT / f"{job_id}.zip"
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for file in work.rglob("*"):
                if file.is_file():
                    zf.write(file, file.relative_to(work))
        if count == 0:
            raise RuntimeError("No accessible posts were found.")
        with LOCK:
            JOBS[job_id].update(status="done", message=f"Finished: {count} post(s) archived.", zip_path=str(zip_path))
    except Exception as exc:
        with LOCK:
            JOBS[job_id].update(status="error", message=f"Download stopped: {str(exc)[:350]}")
        shutil.rmtree(work, ignore_errors=True)

@app.post("/api/jobs")
def create_job():
    try:
        username = normalize_username((request.get_json(silent=True) or {}).get("username"))
    except ValueError as exc:
        return jsonify(error=str(exc)), 400
    job_id = uuid.uuid4().hex
    with LOCK:
        JOBS[job_id] = {"status":"running", "message":"Queued…", "zip_path":None}
    threading.Thread(target=run_job, args=(job_id, username), daemon=True).start()
    return jsonify(id=job_id), 202

@app.get("/api/jobs/<job_id>")
def job_status(job_id):
    with LOCK:
        job = JOBS.get(job_id)
    if not job:
        return jsonify(error="Job not found or server restarted."), 404
    return jsonify(status=job["status"], message=job["message"])

@app.get("/api/jobs/<job_id>/download")
def download(job_id):
    with LOCK:
        job = JOBS.get(job_id)
    if not job or job["status"] != "done" or not job["zip_path"]:
        abort(404)
    path = Path(job["zip_path"])
    if not path.is_file():
        abort(404)
    return send_file(path, as_attachment=True, download_name=f"instagram_{job_id}.zip")

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8080")), debug=False)
