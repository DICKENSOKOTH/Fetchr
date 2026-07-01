"""
main.py — Fetchr Production Server
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
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, Optional

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

executor = ThreadPoolExecutor(max_workers=10)

jobs:       Dict[str, dict] = {}
scheduled:  Dict[str, dict] = {}
rate_store: Dict[str, list] = {}

RATE_LIMIT  = 50
RATE_WINDOW = 3600


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


def safe_zip_name(name: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*]', '-', name).strip()
    return (cleaned[:80] or "download")


# ── Frontend path ─────────────────────────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_local   = os.path.join(BASE_DIR, "frontend")
_parent  = os.path.join(BASE_DIR, "..", "frontend")
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
                    "status":     "queued",
                    "title":      sched["url"],
                    "percent":    0,
                    "message":    "Queued (scheduled)",
                    "created_at": time.time(),
                    "url":        sched["url"],
                    "mode":       sched["mode"],
                    "speed":      0,
                    "track_info": "",
                    "files":      [],
                }
                asyncio.get_event_loop().run_in_executor(
                    executor, _run_download,
                    job_id, sched["url"],
                    sched["mode"], sched["quality"], sched["audio_fmt"]
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
        if result.returncode == 0:
            return {"status": "ok", "message": "yt-dlp updated successfully"}
        return {"status": "error", "message": result.stderr.strip()}
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


# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/files")
def list_files():
    try:
        files = sorted(os.listdir(DOWNLOAD_DIR))
    except Exception:
        files = []
    return {"files": files, "folder": DOWNLOAD_DIR}


@app.get("/storage")
def storage_info():
    return get_storage_info()


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


@app.get("/serve/{filepath:path}")
def serve_file(filepath: str):
    """Manual save link used in the Files tab. Streams one file, then deletes it."""
    safe      = os.path.normpath(filepath)
    full_path = os.path.abspath(os.path.join(DOWNLOAD_DIR, safe))

    if not full_path.startswith(os.path.abspath(DOWNLOAD_DIR)):
        raise HTTPException(status_code=400, detail="Invalid path.")
    if not os.path.exists(full_path):
        raise HTTPException(status_code=404, detail="File not found.")

    filename = os.path.basename(full_path)

    def file_iterator(path: str):
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

    encoded_name = urllib.parse.quote(filename)
    return StreamingResponse(
        file_iterator(full_path),
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{encoded_name}",
            "Content-Length":      str(os.path.getsize(full_path)),
        }
    )


@app.get("/serve-job/{job_id}")
def serve_job(job_id: str):
    """
    Auto-trigger endpoint. A pre-opened tab navigates here once a job finishes.
    Single file  -> streamed directly as an attachment.
    Multiple files (playlist/album) -> zipped in memory and streamed as one file.
    All source files are deleted from the server once the response finishes.
    """
    job = jobs.get(job_id)
    if not job:
        return HTMLResponse(
            "<body style='background:#0c0c0e;color:#f0f0f5;font-family:sans-serif;"
            "display:flex;align-items:center;justify-content:center;height:100vh'>"
            "<p>This download link has expired.</p></body>",
            status_code=404,
        )
    if job.get("status") != "done":
        return HTMLResponse(
            "<body style='background:#0c0c0e;color:#f0f0f5;font-family:sans-serif;"
            "display:flex;align-items:center;justify-content:center;height:100vh'>"
            "<p>Still processing — the original tab has the progress.</p></body>",
            status_code=409,
        )

    files = job.get("files") or []
    abs_paths = []
    for rel in files:
        safe = os.path.normpath(rel)
        full = os.path.abspath(os.path.join(DOWNLOAD_DIR, safe))
        if full.startswith(os.path.abspath(DOWNLOAD_DIR)) and os.path.exists(full):
            abs_paths.append(full)

    if not abs_paths:
        return HTMLResponse(
            "<body style='background:#0c0c0e;color:#f0f0f5;font-family:sans-serif;"
            "display:flex;align-items:center;justify-content:center;height:100vh'>"
            "<p>No files were produced for this download.</p></body>",
            status_code=404,
        )

    # ── Single file ──────────────────────────────────────────────────────────
    if len(abs_paths) == 1:
        path     = abs_paths[0]
        filename = os.path.basename(path)

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

        encoded = urllib.parse.quote(filename)
        return StreamingResponse(
            iterator(),
            media_type="application/octet-stream",
            headers={
                "Content-Disposition": f"attachment; filename*=UTF-8''{encoded}",
                "Content-Length":      str(os.path.getsize(path)),
            }
        )

    # ── Multiple files: zip on the fly ──────────────────────────────────────
    zip_name = safe_zip_name(job.get("title") or "fetchr-download") + ".zip"

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
            for path in abs_paths:
                try:
                    os.remove(path)
                except Exception:
                    pass
            if abs_paths:
                parent = os.path.dirname(abs_paths[0])
                try:
                    if parent != DOWNLOAD_DIR and os.path.isdir(parent) and not os.listdir(parent):
                        os.rmdir(parent)
                except Exception:
                    pass

    encoded_zip = urllib.parse.quote(zip_name)
    return StreamingResponse(
        zip_iterator(),
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{encoded_zip}"}
    )


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
            "status":     "queued",
            "title":      url,
            "percent":    0,
            "message":    "Queued",
            "created_at": time.time(),
            "url":        url,
            "mode":       mode,
            "speed":      0,
            "track_info": "",
            "files":      [],
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
        "url":       req.url.strip(),
        "mode":      req.mode,
        "quality":   req.quality,
        "audio_fmt": req.audio_fmt,
        "run_at":    req.run_at,
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
        track_info = d.get("track_info", "")

        jobs[job_id]["percent"]    = round(pct, 1)
        jobs[job_id]["speed"]      = speed
        jobs[job_id]["track_info"] = track_info

        if total and downloaded:
            jobs[job_id]["message"] = f"{downloaded/1_000_000:.1f} / {total/1_000_000:.1f} MB"
        elif downloaded:
            jobs[job_id]["message"] = f"{downloaded/1_000_000:.1f} MB"

    try:
        result = download(
            url,
            mode=mode,
            quality=quality,
            audio_fmt=audio_fmt,
            progress_hook=progress_hook,
        )
        if result.get("status") == "done":
            jobs[job_id].update({
                "status":     "done",
                "title":      result.get("title", url),
                "percent":    100,
                "message":    result.get("message", "Complete"),
                "type":       result.get("type", mode),
                "speed":      0,
                "track_info": "",
                "files":      result.get("files", []),
            })
        else:
            jobs[job_id].update({
                "status":     "error",
                "message":    result.get("message", "Download failed"),
                "percent":    0,
                "speed":      0,
                "track_info": "",
                "files":      [],
            })
    except Exception as e:
        jobs[job_id].update({
            "status":     "error",
            "message":    str(e),
            "percent":    0,
            "speed":      0,
            "track_info": "",
            "files":      [],
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