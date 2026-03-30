"""
main.py — Production FastAPI server
"""

import os
import uuid
import asyncio
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel

from downloader import download, DOWNLOAD_DIR

app = FastAPI(title="Fetchr", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

executor    = ThreadPoolExecutor(max_workers=10)
jobs:       Dict[str, dict] = {}
rate_store: Dict[str, list] = {}

RATE_LIMIT  = 10
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
    for jid in [k for k, v in jobs.items() if v.get("created_at", 0) < cutoff]:
        del jobs[jid]


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_local   = os.path.join(BASE_DIR, "frontend")
_parent  = os.path.join(BASE_DIR, "..", "frontend")
FRONTEND_DIR = os.path.abspath(_local if os.path.exists(_local) else _parent)

print(f"Frontend  : {FRONTEND_DIR}")
print(f"Downloads : {DOWNLOAD_DIR}")


class DownloadRequest(BaseModel):
    url:       str
    mode:      str = "video"   # "video" | "audio"
    quality:   str = "best"    # video quality: best/4k/1440p/1080p/720p
    audio_fmt: str = "mp3"     # audio format: mp3/flac/wav/opus/aac/alac


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


@app.post("/download")
async def start_download(req: DownloadRequest, request: Request):
    ip = request.client.host
    if not check_rate_limit(ip):
        raise HTTPException(status_code=429, detail="Rate limit reached. Try again later.")

    url = req.url.strip()
    if not url.startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="Invalid URL.")

    # Validate mode / quality / audio_fmt
    valid_modes      = {"video", "audio"}
    valid_qualities  = {"best", "4k", "1440p", "1080p", "720p"}
    valid_audio_fmts = {"mp3", "flac", "wav", "opus", "aac", "alac"}

    mode      = req.mode      if req.mode      in valid_modes      else "video"
    quality   = req.quality   if req.quality   in valid_qualities  else "best"
    audio_fmt = req.audio_fmt if req.audio_fmt in valid_audio_fmts else "mp3"

    job_id = str(uuid.uuid4())
    jobs[job_id] = {
        "status":     "queued",
        "title":      url,
        "percent":    0,
        "message":    "Queued",
        "created_at": time.time(),
        "url":        url,
        "mode":       mode,
    }

    asyncio.get_event_loop().run_in_executor(
        executor, _run_download, job_id, url, mode, quality, audio_fmt
    )
    clean_old_jobs()
    return {"job_id": job_id}


def _run_download(job_id: str, url: str, mode: str, quality: str, audio_fmt: str):
    jobs[job_id]["status"]  = "downloading"
    jobs[job_id]["message"] = "Starting..."

    def progress_hook(d: dict):
        pct        = d.get("percent", 0) or 0
        downloaded = d.get("downloaded_bytes", 0) or 0
        total      = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
        jobs[job_id]["percent"] = pct
        if total and downloaded:
            jobs[job_id]["message"] = (
                f"{downloaded/1_000_000:.1f} MB / {total/1_000_000:.1f} MB"
            )
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
                "status":  "done",
                "title":   result.get("title", url),
                "percent": 100,
                "message": "Complete",
                "type":    result.get("type", mode),
            })
        else:
            jobs[job_id].update({
                "status":  "error",
                "message": result.get("message", "Download failed"),
                "percent": 0,
            })
    except Exception as e:
        jobs[job_id].update({
            "status":  "error",
            "message": str(e),
            "percent": 0,
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
    finally:
        await websocket.close()


@app.get("/")
def root():
    return FileResponse(os.path.join(FRONTEND_DIR, "index.html"))


app.mount("/", StaticFiles(directory=FRONTEND_DIR), name="static")