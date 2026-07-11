"""
main.py — Fetchr Production Server
Architecture: yt-dlp + ffmpeg pipe → StreamingResponse → browser → device.
Zero disk writes for streaming downloads.
"""

import os
import re
import io
import uuid
import zipfile
import asyncio
import time
import subprocess
import sys
import shlex
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, Optional

import yt_dlp

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, StreamingResponse, HTMLResponse
from pydantic import BaseModel

from backend.downloader import (
    download, preview_url, get_storage_info,
    DOWNLOAD_DIR
)

# ── App ───────────────────────────────────────────────────────────────────────
app = FastAPI(title="Fetchr", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

executor    = ThreadPoolExecutor(max_workers=10)
jobs:       Dict[str, dict] = {}
scheduled:  Dict[str, dict] = {}
rate_store: Dict[str, list] = {}

RATE_LIMIT  = 50
RATE_WINDOW = 3600

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def check_rate_limit(ip: str) -> bool:
    now = time.time()
    ts  = [t for t in rate_store.get(ip, []) if now - t < RATE_WINDOW]
    if len(ts) >= RATE_LIMIT:
        return False
    ts.append(now)
    rate_store[ip] = ts
    return True


def clean_old_jobs():
    cutoff = time.time() - 3600
    for jid in [k for k, v in list(jobs.items()) if v.get("created_at", 0) < cutoff]:
        del jobs[jid]


def safe_name(name: str, max_len: int = 100) -> str:
    return (re.sub(r'[<>:"/\\|?*]', '-', name).strip()[:max_len] or "download")


# ── Frontend path ─────────────────────────────────────────────────────────────
_local  = os.path.join(BASE_DIR, "frontend")
_parent = os.path.join(BASE_DIR, "..", "frontend")
FRONTEND_DIR = os.path.abspath(_local if os.path.exists(_local) else _parent)

print(f"Frontend  : {FRONTEND_DIR}")
print(f"Downloads : {DOWNLOAD_DIR}")


# ── Background tasks ──────────────────────────────────────────────────────────
@app.on_event("startup")
async def startup():
    asyncio.create_task(scheduler_loop())
    asyncio.create_task(auto_update_loop())


async def scheduler_loop():
    while True:
        await asyncio.sleep(30)
        now = time.time()
        for jid, sched in list(scheduled.items()):
            if sched["run_at"] <= now:
                del scheduled[jid]
                job_id = str(uuid.uuid4())
                jobs[job_id] = {
                    "status": "queued", "title": sched["url"], "percent": 0,
                    "message": "Queued (scheduled)", "created_at": time.time(),
                    "url": sched["url"], "mode": sched["mode"],
                    "speed": 0, "track_info": "", "files": [],
                }
                asyncio.get_event_loop().run_in_executor(
                    executor, _run_download,
                    job_id, sched["url"], sched["mode"], sched["quality"], sched["audio_fmt"]
                )


async def auto_update_loop():
    while True:
        await asyncio.sleep(86400)
        _run_ytdlp_update()


def _run_ytdlp_update() -> dict:
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", "yt-dlp[default]", "--upgrade", "--quiet"],
            capture_output=True, text=True, timeout=120
        )
        return {"status": "ok" if result.returncode == 0 else "error",
                "message": "Updated" if result.returncode == 0 else result.stderr.strip()}
    except Exception as e:
        return {"status": "error", "message": str(e)}


# ── Models ────────────────────────────────────────────────────────────────────
class DownloadRequest(BaseModel):
    url:       str
    mode:      str = "video"
    quality:   str = "best"
    audio_fmt: str = "mp3"

class ScheduleRequest(BaseModel):
    url:       str
    mode:      str = "video"
    quality:   str = "best"
    audio_fmt: str = "mp3"
    run_at:    float


# ── Streaming pipeline helpers ─────────────────────────────────────────────────
def _cookies_arg() -> list:
    p = os.path.join(BASE_DIR, "cookies.txt")
    return ["--cookies", p] if os.path.exists(p) else []


def _proxy_arg() -> list:
    p = os.environ.get("FETCHR_PROXY", "")
    return ["--proxy", p] if p else []


MIME = {
    "mp3":"audio/mpeg","flac":"audio/flac","wav":"audio/wav","opus":"audio/ogg",
    "aac":"audio/aac","m4a":"audio/mp4","alac":"audio/mp4",
    "mp4":"video/mp4","mkv":"video/x-matroska","webm":"video/webm",
}

AUDIO_FFMPEG = {
    "mp3":  ["-f","mp3","-ab","320k","-vn"],
    "flac": ["-f","flac","-vn"],
    "wav":  ["-f","wav","-vn"],
    "opus": ["-f","opus","-vn"],
    "aac":  ["-f","adts","-vn"],
    "m4a":  ["-f","ipod","-vn"],
    "alac": ["-f","ipod","-acodec","alac","-vn"],
}


def _build_stream_cmd(url: str, mode: str, quality: str, audio_fmt: str) -> tuple:
    """
    Build the command that pipes everything to stdout with zero disk I/O.

    For audio:
        yt-dlp -o - bestaudio | ffmpeg -i pipe:0 -f mp3 320k pipe:1

    For video:
        yt-dlp -o - bestvideo+bestaudio | (ffmpeg merges on the fly) pipe:1

    yt-dlp's --downloader ffmpeg with -o - handles the piping natively.
    """
    base = ["yt-dlp"] + _cookies_arg() + _proxy_arg() + ["--no-playlist", "--no-warnings"]

    if mode == "audio":
        ffargs  = AUDIO_FFMPEG.get(audio_fmt, AUDIO_FFMPEG["mp3"])
        ext     = audio_fmt if audio_fmt != "alac" else "m4a"
        mime    = MIME.get(ext, "audio/mpeg")
        dl_args = ["-f", "bestaudio/best",
                   "--downloader", "ffmpeg",
                   "--downloader-args", "ffmpeg_i:" + " ".join(ffargs) + " pipe:1 -y",
                   "-o", "-", url]
    else:
        h_map   = {"best": None, "4k": 2160, "1440p": 1440, "1080p": 1080, "720p": 720}
        h       = h_map.get(quality)
        fmt     = (f"bestvideo[height<={h}]+bestaudio/best[height<={h}]"
                   if h else "bestvideo+bestaudio/best")
        cont    = "mkv" if quality in ("4k", "1440p") else "mp4"
        ext     = cont
        mime    = MIME.get(cont, "video/mp4")
        dl_args = ["-f", fmt,
                   "--downloader", "ffmpeg",
                   "--downloader-args", f"ffmpeg_i:-f {cont} pipe:1 -y",
                   "-o", "-", url]

    return base + dl_args, ext, mime


def _quick_title(url: str) -> str:
    """Fetch just the title with a short timeout."""
    try:
        opts = {
            "quiet": True, "noplaylist": True,
            "js_runtimes": {"node": {}},
            "remote_components": {"ejs:github"},
        }
        cp = os.path.join(BASE_DIR, "cookies.txt")
        if os.path.exists(cp):
            opts["cookiefile"] = cp
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
        return (info or {}).get("title") or "download"
    except Exception:
        return "download"


# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/stream-download")
async def stream_download(
    request:   Request,
    url:       str,
    mode:      str = "audio",
    quality:   str = "best",
    audio_fmt: str = "mp3",
):
    """
    Zero-storage streaming download.

    Flow:
      browser tab → GET /stream-download?url=...
      server: yt-dlp -o - URL | ffmpeg → pipe → StreamingResponse
      browser: receives Content-Disposition: attachment → saves file to device
      server: no file written to disk at any point
    """
    ip = request.client.host
    if not check_rate_limit(ip):
        raise HTTPException(status_code=429, detail="Rate limit reached.")

    if not url.startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="Invalid URL.")

    mode      = mode      if mode      in {"video","audio"}                        else "audio"
    quality   = quality   if quality   in {"best","4k","1440p","1080p","720p"}     else "best"
    audio_fmt = audio_fmt if audio_fmt in {"mp3","flac","wav","opus","aac","m4a","alac"} else "mp3"

    # Fetch title in background (non-blocking, short timeout)
    loop  = asyncio.get_event_loop()
    title = "download"
    try:
        title = await asyncio.wait_for(
            loop.run_in_executor(executor, _quick_title, url),
            timeout=15,
        )
    except Exception:
        pass

    cmd, ext, mime = _build_stream_cmd(url, mode, quality, audio_fmt)
    filename     = f"{safe_name(title)}.{ext}"
    encoded_name = urllib.parse.quote(filename)

    def stream_proc():
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        try:
            while True:
                chunk = proc.stdout.read(65536)
                if not chunk:
                    break
                yield chunk
        finally:
            try:
                proc.kill()
                proc.wait(timeout=5)
            except Exception:
                pass

    return StreamingResponse(
        stream_proc(),
        media_type=mime,
        headers={
            "Content-Disposition":    f"attachment; filename*=UTF-8''{encoded_name}",
            "X-Content-Type-Options": "nosniff",
            "Cache-Control":          "no-store",
        },
    )


@app.get("/waiting/{job_id}")
def waiting_page(job_id: str):
    """Waiting page for job-based downloads (playlists, fallback)."""
    html = f"""<!DOCTYPE html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Preparing download…</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
     background:#0c0c0e;color:#f0f0f5;
     display:flex;align-items:center;justify-content:center;min-height:100vh;text-align:center;padding:24px}}
.card{{max-width:320px;width:100%}}
.spinner{{width:40px;height:40px;border:3px solid #2a2a35;border-top-color:#e8ff47;
          border-radius:50%;margin:0 auto 20px;animation:spin 1s linear infinite}}
@keyframes spin{{to{{transform:rotate(360deg)}}}}
h2{{font-size:17px;font-weight:700;margin-bottom:8px}}
p{{font-size:13px;opacity:.55;line-height:1.5}}
.bar-wrap{{margin-top:20px;height:3px;background:#2a2a35;border-radius:2px;overflow:hidden}}
.bar{{height:100%;background:#e8ff47;border-radius:2px;width:0%;transition:width .4s}}
.pct{{font-family:monospace;font-size:12px;color:#e8ff47;margin-top:8px}}
.trk{{font-family:monospace;font-size:11px;color:#b47fff;margin-top:4px;min-height:16px}}
.done{{color:#3dffa0}}.err{{color:#ff4f4f}}
</style></head>
<body><div class="card">
  <div class="spinner" id="sp"></div>
  <h2 id="msg">Preparing your download…</h2>
  <p id="sub">Processing on server. Your file will save automatically.</p>
  <div class="bar-wrap" id="bw" style="display:none"><div class="bar" id="bar"></div></div>
  <p class="pct" id="pct"></p><p class="trk" id="trk"></p>
</div>
<script>
const JOB="{job_id}";let attempts=0;
const sp=document.getElementById('sp'),msg=document.getElementById('msg'),
      sub=document.getElementById('sub'),bw=document.getElementById('bw'),
      bar=document.getElementById('bar'),pct=document.getElementById('pct'),
      trk=document.getElementById('trk');
async function check(){{
  attempts++;
  if(attempts>360){{sp.style.display='none';msg.textContent='Timed out.';sub.textContent='Please try again.';return;}}
  try{{
    const r=await fetch('/job/'+JOB);
    if(!r.ok){{setTimeout(check,1000);return;}}
    const job=await r.json();
    if(job.status==='done'){{
      sp.style.display='none';msg.className='done';
      msg.textContent='Saving to your device…';
      sub.textContent='Check your downloads. You can close this tab.';
      bw.style.display='none';pct.textContent='';trk.textContent='';
      window.location.href='/serve-job/'+JOB;
    }}else if(job.status==='error'){{
      sp.style.display='none';msg.className='err';
      msg.textContent='Download failed';sub.textContent=job.message||'Please try again.';
    }}else{{
      if(job.percent>0){{bw.style.display='block';bar.style.width=Math.min(job.percent,100)+'%';pct.textContent=Math.round(job.percent)+'%';}}
      if(job.track_info)trk.textContent=job.track_info;
      if(job.message)sub.textContent=job.message;
      setTimeout(check,1000);
    }}
  }}catch(e){{setTimeout(check,2000);}}
}}
check();
</script></body></html>"""
    return HTMLResponse(html)


@app.get("/serve-job/{job_id}")
def serve_job(job_id: str):
    """Serve completed job files (playlist/album fallback)."""
    job = jobs.get(job_id)
    if not job or job.get("status") != "done":
        return HTMLResponse(
            "<body style='background:#0c0c0e;color:#f0f0f5;font-family:sans-serif;"
            "display:flex;align-items:center;justify-content:center;height:100vh'>"
            "<p>File not available — it may have already been downloaded.</p></body>",
            status_code=404,
        )

    files = job.get("files") or []
    abs_paths = []
    for rel in files:
        full = os.path.abspath(os.path.join(DOWNLOAD_DIR, os.path.normpath(rel)))
        if full.startswith(os.path.abspath(DOWNLOAD_DIR)) and os.path.exists(full):
            abs_paths.append(full)

    if not abs_paths:
        return HTMLResponse(
            "<body style='background:#0c0c0e;color:#f0f0f5;font-family:sans-serif;"
            "display:flex;align-items:center;justify-content:center;height:100vh'>"
            "<p>File not found — may have already been downloaded.</p></body>",
            status_code=404,
        )

    if len(abs_paths) == 1:
        path = abs_paths[0]
        ext  = path.rsplit(".", 1)[-1].lower() if "." in path else ""
        mime = MIME.get(ext, "application/octet-stream")
        encoded = urllib.parse.quote(os.path.basename(path))

        def iterator():
            try:
                with open(path, "rb") as f:
                    while True:
                        chunk = f.read(65536)
                        if not chunk:
                            break
                        yield chunk
            finally:
                try:
                    os.remove(path)
                    parent = os.path.dirname(path)
                    if parent != DOWNLOAD_DIR and os.path.isdir(parent) and not os.listdir(parent):
                        os.rmdir(parent)
                except Exception:
                    pass

        return StreamingResponse(iterator(), media_type=mime, headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{encoded}",
            "Content-Length": str(os.path.getsize(path)),
        })

    # Multiple files → zip
    zip_name = safe_name(job.get("title") or "fetchr-download") + ".zip"

    def zip_iterator():
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for path in abs_paths:
                zf.write(path, os.path.basename(path))
        buf.seek(0)
        try:
            while True:
                chunk = buf.read(65536)
                if not chunk:
                    break
                yield chunk
        finally:
            for p in abs_paths:
                try:
                    os.remove(p)
                except Exception:
                    pass
            try:
                parent = os.path.dirname(abs_paths[0])
                if parent != DOWNLOAD_DIR and os.path.isdir(parent) and not os.listdir(parent):
                    os.rmdir(parent)
            except Exception:
                pass

    return StreamingResponse(zip_iterator(), media_type="application/zip", headers={
        "Content-Disposition": f"attachment; filename*=UTF-8''{urllib.parse.quote(zip_name)}"
    })


@app.get("/serve/{filepath:path}")
def serve_file(filepath: str):
    """Manual save link in the Files tab."""
    safe = os.path.normpath(filepath)
    full = os.path.abspath(os.path.join(DOWNLOAD_DIR, safe))
    if not full.startswith(os.path.abspath(DOWNLOAD_DIR)):
        raise HTTPException(status_code=400, detail="Invalid path.")
    if not os.path.exists(full):
        raise HTTPException(status_code=404, detail="File not found.")
    ext     = full.rsplit(".", 1)[-1].lower() if "." in full else ""
    mime    = MIME.get(ext, "application/octet-stream")
    encoded = urllib.parse.quote(os.path.basename(full))

    def it():
        try:
            with open(full, "rb") as f:
                while True:
                    chunk = f.read(65536)
                    if not chunk:
                        break
                    yield chunk
        finally:
            try:
                os.remove(full)
                parent = os.path.dirname(full)
                if parent != DOWNLOAD_DIR and os.path.isdir(parent) and not os.listdir(parent):
                    os.rmdir(parent)
            except Exception:
                pass

    return StreamingResponse(it(), media_type=mime, headers={
        "Content-Disposition": f"attachment; filename*=UTF-8''{encoded}",
        "Content-Length": str(os.path.getsize(full)),
    })


@app.get("/storage")
def storage_info():
    return get_storage_info()


@app.get("/files")
def list_files():
    try:
        files = sorted(os.listdir(DOWNLOAD_DIR))
    except Exception:
        files = []
    return {"files": files, "folder": DOWNLOAD_DIR}


@app.delete("/files/{filename:path}")
def delete_file(filename: str):
    safe = os.path.normpath(filename)
    path = os.path.abspath(os.path.join(DOWNLOAD_DIR, safe))
    if not path.startswith(os.path.abspath(DOWNLOAD_DIR)):
        raise HTTPException(status_code=400, detail="Invalid path.")
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="File not found")
    try:
        os.remove(path)
        return {"status": "deleted", "file": filename}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/preview")
async def preview(url: str):
    try:
        loop   = asyncio.get_event_loop()
        result = await loop.run_in_executor(executor, preview_url, url)
        return result
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/download")
async def start_download(req: DownloadRequest, request: Request):
    """Job-based download — used for playlists and as fallback."""
    ip = request.client.host
    if not check_rate_limit(ip):
        raise HTTPException(status_code=429, detail="Rate limit reached. Try again later.")

    urls = [u.strip() for u in req.url.split(",") if u.strip()]
    if not urls:
        raise HTTPException(status_code=400, detail="No valid URLs provided.")
    for url in urls:
        if not url.startswith(("http://", "https://")):
            raise HTTPException(status_code=400, detail=f"Invalid URL: {url}")

    mode      = req.mode      if req.mode      in {"video","audio"}                        else "video"
    quality   = req.quality   if req.quality   in {"best","4k","1440p","1080p","720p"}     else "best"
    audio_fmt = req.audio_fmt if req.audio_fmt in {"mp3","flac","wav","opus","aac","alac"} else "mp3"

    job_ids = []
    for url in urls:
        job_id = str(uuid.uuid4())
        jobs[job_id] = {
            "status": "queued", "title": url, "percent": 0,
            "message": "Queued", "created_at": time.time(),
            "url": url, "mode": mode, "speed": 0, "track_info": "", "files": [],
        }
        asyncio.get_event_loop().run_in_executor(
            executor, _run_download, job_id, url, mode, quality, audio_fmt
        )
        job_ids.append(job_id)

    clean_old_jobs()
    return {"job_ids": job_ids, "count": len(job_ids)}


@app.post("/schedule")
async def schedule_download(req: ScheduleRequest, request: Request):
    ip = request.client.host
    if not check_rate_limit(ip):
        raise HTTPException(status_code=429, detail="Rate limit reached.")
    if not req.url.strip().startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="Invalid URL.")
    if req.run_at <= time.time():
        raise HTTPException(status_code=400, detail="Scheduled time must be in the future.")
    sched_id = str(uuid.uuid4())
    scheduled[sched_id] = {
        "url": req.url.strip(), "mode": req.mode,
        "quality": req.quality, "audio_fmt": req.audio_fmt, "run_at": req.run_at,
    }
    return {"sched_id": sched_id, "run_at": req.run_at}


@app.get("/scheduled")
def list_scheduled():
    return {"scheduled": scheduled}


@app.delete("/scheduled/{sched_id}")
def cancel_scheduled(sched_id: str):
    if sched_id not in scheduled:
        raise HTTPException(status_code=404, detail="Scheduled job not found")
    del scheduled[sched_id]
    return {"status": "cancelled"}


@app.post("/update-ytdlp")
async def update_ytdlp():
    loop   = asyncio.get_event_loop()
    result = await loop.run_in_executor(executor, _run_ytdlp_update)
    return result


def _run_download(job_id: str, url: str, mode: str, quality: str, audio_fmt: str):
    jobs[job_id]["status"]  = "downloading"
    jobs[job_id]["message"] = "Starting..."

    def progress_hook(d: dict):
        pct        = d.get("percent", 0) or 0
        downloaded = d.get("downloaded_bytes", 0) or 0
        total      = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
        speed      = d.get("speed", 0) or 0
        jobs[job_id]["percent"]    = round(pct, 1)
        jobs[job_id]["speed"]      = speed
        jobs[job_id]["track_info"] = d.get("track_info", "")
        if total and downloaded:
            jobs[job_id]["message"] = f"{downloaded/1_000_000:.1f} / {total/1_000_000:.1f} MB"
        elif downloaded:
            jobs[job_id]["message"] = f"{downloaded/1_000_000:.1f} MB"

    try:
        result = download(url, mode=mode, quality=quality, audio_fmt=audio_fmt, progress_hook=progress_hook)
        if result.get("status") == "done":
            jobs[job_id].update({
                "status": "done", "title": result.get("title", url),
                "percent": 100, "message": result.get("message", "Complete"),
                "type": result.get("type", mode),
                "speed": 0, "track_info": "",
                "files": result.get("files", []),
            })
        else:
            jobs[job_id].update({
                "status": "error", "message": result.get("message", "Download failed"),
                "percent": 0, "speed": 0, "track_info": "", "files": [],
            })
    except Exception as e:
        jobs[job_id].update({
            "status": "error", "message": str(e),
            "percent": 0, "speed": 0, "track_info": "", "files": [],
        })


@app.get("/job/{job_id}")
def get_job(job_id: str):
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@app.websocket("/ws/{job_id}")
async def job_ws(websocket: WebSocket, job_id: str):
    await websocket.accept()
    try:
        while True:
            job = jobs.get(job_id)
            if not job:
                await websocket.send_json({"status": "error", "message": "Job not found"})
                break
            await websocket.send_json(job)
            if job["status"] in ("done", "error"):
                break
            await asyncio.sleep(0.5)
    except WebSocketDisconnect:
        pass
    except RuntimeError:
        pass
    finally:
        try:
            await websocket.close()
        except Exception:
            pass


@app.get("/")
def root():
    return FileResponse(os.path.join(FRONTEND_DIR, "index.html"))


app.mount("/", StaticFiles(directory=FRONTEND_DIR), name="static")