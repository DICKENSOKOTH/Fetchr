"""
main.py — Fetchr Production Server

Streaming pipeline (single files):
  yt-dlp Python API → CDN URL + real title
  → ffmpeg (conversion/merge) OR requests (direct proxy)
  → StreamingResponse → browser → device Downloads folder
  Zero disk writes.

Job pipeline (playlists/albums):
  yt-dlp downloads to server → /waiting page polls → /serve-job streams + deletes
"""

import os
import re
import io
import uuid
import zipfile
import asyncio
import time
import subprocess
import shutil
import sys
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, Optional

import yt_dlp
import requests as req_lib

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, StreamingResponse, HTMLResponse
from pydantic import BaseModel

from backend.downloader import (
    download, preview_url, get_storage_info, DOWNLOAD_DIR,
    _youtube_clients, _make_ea, _has_cookies, _cookies_path,
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
BASE_DIR    = os.path.dirname(os.path.abspath(__file__))

# MIME types for response headers
MIME = {
    "mp3": "audio/mpeg",   "flac": "audio/flac",   "wav": "audio/wav",
    "opus": "audio/ogg",   "aac": "audio/aac",      "m4a": "audio/mp4",
    "alac": "audio/mp4",   "mp4": "video/mp4",      "mkv": "video/x-matroska",
    "webm": "video/webm",
}

# ffmpeg output args for each audio format
AUDIO_CODEC = {
    "mp3":  ["-f", "mp3",  "-ab", "320k", "-vn"],
    "flac": ["-f", "flac", "-vn"],
    "wav":  ["-f", "wav",  "-vn"],
    "opus": ["-f", "opus", "-vn"],
    "aac":  ["-f", "adts", "-vn"],
    "m4a":  ["-f", "ipod", "-vn"],
    "alac": ["-f", "ipod", "-acodec", "alac", "-vn"],
}


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


def safe_name(s: str, n: int = 100) -> str:
    return (re.sub(r'[<>:"/\\|?*\x00-\x1f]', '-', str(s)).strip()[:n] or "download")


def esc_html(s: str) -> str:
    return str(s).replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")


# ── Frontend path ─────────────────────────────────────────────────────────────
_local  = os.path.join(BASE_DIR, "frontend")
_parent = os.path.join(BASE_DIR, "..", "frontend")
FRONTEND_DIR = os.path.abspath(_local if os.path.exists(_local) else _parent)
print(f"Frontend  : {FRONTEND_DIR}")
print(f"Downloads : {DOWNLOAD_DIR}")


# ── ffmpeg discovery ──────────────────────────────────────────────────────────
def _find_ffmpeg() -> Optional[str]:
    """
    Find ffmpeg binary.
    1. System PATH (Render/Ubuntu usually has it if apt-installed)
    2. imageio-ffmpeg portable static binary (fallback, no apt needed)
    """
    found = shutil.which("ffmpeg")
    if found:
        return found
    try:
        import imageio_ffmpeg          # type: ignore
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


# ── yt-dlp stream info extraction (Python API, no subprocess) ─────────────────
def _extract_stream_info(url: str, mode: str, quality: str, audio_fmt: str) -> dict:
    """
    Use the yt-dlp Python API to extract direct CDN URL(s) and the real title.
    No subprocess. No PATH dependency. No disk writes.

    Returns one of:
      { title, source_url, [audio_url], headers, [audio_headers],
        native_ext, target_ext, needs_ffmpeg, needs_merge }
    OR
      { error: str }
    """
    proxy = os.environ.get("FETCHR_PROXY")

    if mode == "audio":
        fmt = "bestaudio[ext=m4a]/bestaudio[ext=webm]/bestaudio/best"
    else:
        h_map = {"best": None, "4k": 2160, "1440p": 1440, "1080p": 1080, "720p": 720}
        h     = h_map.get(quality)
        if h:
            fmt = (f"bestvideo[height<={h}][ext=mp4]+bestaudio[ext=m4a]"
                   f"/bestvideo[height<={h}]+bestaudio/best[height<={h}]")
        else:
            fmt = "bestvideo[ext=mp4]+bestaudio[ext=m4a]/bestvideo+bestaudio/best"

    clients  = _youtube_clients() if "youtu" in url.lower() else [None]
    last_err = "Could not extract stream info"

    for client in clients:
        try:
            ea   = _make_ea(client) if client else {}
            opts: dict = {
                "quiet":             True,
                "noplaylist":        True,
                "format":            fmt,
                "js_runtimes":       {"node": {}},
                "remote_components": {"ejs:github"},
            }
            if ea:
                opts["extractor_args"] = ea
            if _has_cookies():
                opts["cookiefile"] = _cookies_path()
            if proxy:
                opts["proxy"] = proxy

            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=False)

            if not info:
                last_err = "yt-dlp returned no info"
                continue

            # If yt-dlp returned a playlist despite noplaylist=True, take first entry
            if info.get("_type") == "playlist" or info.get("entries"):
                entries = list(info.get("entries") or [])
                if entries and entries[0]:
                    info = entries[0]
                else:
                    last_err = "No entries in playlist"
                    continue

            title = info.get("title") or "download"
            all_formats = info.get("formats") or []

            # ── Audio mode ────────────────────────────────────────────────────
            if mode == "audio":
                # Audio-only streams (no video codec)
                audio_only = sorted(
                    [f for f in all_formats
                     if f.get("acodec") not in (None, "none", "")
                     and f.get("vcodec") in (None, "none", "")
                     and f.get("url")],
                    key=lambda f: (f.get("abr") or f.get("tbr") or 0),
                    reverse=True,
                )

                # Fallback: top-level URL (combined stream, ffmpeg will strip video)
                if not audio_only and info.get("url"):
                    audio_only = [{
                        "url":          info["url"],
                        "ext":          info.get("ext") or "m4a",
                        "http_headers": info.get("http_headers") or {},
                    }]

                if not audio_only:
                    last_err = "No audio streams found"
                    continue

                best       = audio_only[0]
                native_ext = best.get("ext") or "m4a"

                # Formats that need ffmpeg conversion (not natively available from YouTube)
                needs_conv = audio_fmt in {"mp3", "flac", "wav", "alac"}
                if needs_conv:
                    target_ext = audio_fmt if audio_fmt != "alac" else "m4a"
                else:
                    # m4a, aac, opus — serve the native stream as-is
                    target_ext = native_ext

                return {
                    "title":        title,
                    "source_url":   best["url"],
                    "headers":      best.get("http_headers") or {},
                    "native_ext":   native_ext,
                    "target_ext":   target_ext,
                    "needs_ffmpeg": needs_conv,
                    "needs_merge":  False,
                }

            # ── Video mode ────────────────────────────────────────────────────
            else:
                # Check if yt-dlp selected separate video+audio streams (needs merging)
                req_fmts = info.get("requested_formats") or []

                if len(req_fmts) >= 2:
                    # Two streams: merge via ffmpeg
                    v_fmt = next(
                        (f for f in req_fmts if f.get("vcodec") not in (None, "none", "")), None
                    )
                    a_fmt = next(
                        (f for f in req_fmts
                         if f.get("acodec") not in (None, "none", "")
                         and f.get("vcodec") in (None, "none", "")), None
                    )
                    if v_fmt and a_fmt and v_fmt.get("url") and a_fmt.get("url"):
                        container = "mkv" if quality in ("4k", "1440p") else "mp4"
                        return {
                            "title":         title,
                            "source_url":    v_fmt["url"],       # video stream
                            "audio_url":     a_fmt["url"],       # audio stream
                            "headers":       v_fmt.get("http_headers") or {},
                            "audio_headers": a_fmt.get("http_headers") or {},
                            "native_ext":    container,
                            "target_ext":    container,
                            "needs_ffmpeg":  True,
                            "needs_merge":   True,
                        }

                # Pre-merged or single stream
                # Check req_fmts first, then top-level url
                source_url = None
                ext        = "mp4"
                headers    = {}

                if req_fmts:
                    source_url = req_fmts[0].get("url")
                    ext        = req_fmts[0].get("ext") or "mp4"
                    headers    = req_fmts[0].get("http_headers") or {}

                if not source_url:
                    source_url = info.get("url") or ""
                    ext        = info.get("ext") or "mp4"
                    headers    = info.get("http_headers") or {}

                if not source_url:
                    last_err = "No video URL found"
                    continue

                return {
                    "title":        title,
                    "source_url":   source_url,
                    "headers":      headers,
                    "native_ext":   ext,
                    "target_ext":   ext,
                    "needs_ffmpeg": False,
                    "needs_merge":  False,
                }

        except Exception as exc:
            last_err = str(exc)
            if client is None:
                break
            continue

    return {"error": last_err}


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
        _do_ytdlp_update()


def _do_ytdlp_update() -> dict:
    try:
        r = subprocess.run(
            [sys.executable, "-m", "pip", "install", "yt-dlp[default]", "--upgrade", "--quiet"],
            capture_output=True, text=True, timeout=120,
        )
        return {"status": "ok" if r.returncode == 0 else "error",
                "message": "Updated" if r.returncode == 0 else r.stderr.strip()}
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

    For single audio/video:
      1. yt-dlp Python API extracts real CDN URL + real title (no subprocess)
      2a. Audio needing conversion: ffmpeg -i CDN_URL -f mp3 pipe:1 → StreamingResponse
      2b. Audio native / video pre-merged: requests proxies CDN → StreamingResponse
      2c. Video needing merge: ffmpeg -i video_url -i audio_url -f mp4 pipe:1 → StreamingResponse
      3. Browser saves file with correct title and format to device Downloads folder
      4. Nothing written to server disk
    """
    ip = request.client.host
    if not check_rate_limit(ip):
        raise HTTPException(status_code=429, detail="Rate limit reached.")

    if not url.startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="Invalid URL.")

    mode      = mode      if mode      in {"video","audio"}                              else "audio"
    quality   = quality   if quality   in {"best","4k","1440p","1080p","720p"}           else "best"
    audio_fmt = audio_fmt if audio_fmt in {"mp3","flac","wav","opus","aac","m4a","alac"} else "mp3"

    # Extract CDN URL + title using yt-dlp Python API in thread pool
    loop = asyncio.get_event_loop()
    info = await loop.run_in_executor(
        executor,
        lambda: _extract_stream_info(url, mode, quality, audio_fmt),
    )

    if info.get("error"):
        return HTMLResponse(
            "<body style='background:#0c0c0e;color:#ff4f4f;font-family:sans-serif;"
            "display:flex;align-items:center;justify-content:center;height:100vh;margin:0'>"
            f"<div><p style='font-size:16px;margin-bottom:8px'>Download error</p>"
            f"<p style='opacity:.6;font-size:13px'>{esc_html(info['error'])}</p></div></body>",
            status_code=400,
        )

    title        = info.get("title", "download")
    source_url   = info["source_url"]
    target_ext   = info["target_ext"]
    needs_ffmpeg = info.get("needs_ffmpeg", False)
    needs_merge  = info.get("needs_merge", False)
    cdn_headers  = info.get("headers") or {}

    # Real filename with real title and chosen format
    filename     = f"{safe_name(title)}.{target_ext}"
    encoded_name = urllib.parse.quote(filename)
    mime         = MIME.get(target_ext, "application/octet-stream")

    resp_headers = {
        "Content-Disposition":    f"attachment; filename*=UTF-8''{encoded_name}",
        "X-Content-Type-Options": "nosniff",
        "Cache-Control":          "no-store",
    }

    ffmpeg = _find_ffmpeg()

    # ── Path A: Video merge (two separate streams → one file) ─────────────────
    if needs_merge and ffmpeg:
        audio_url     = info.get("audio_url", "")
        audio_headers = info.get("audio_headers") or {}

        # Build header args for both inputs
        def _hdr_args(h: dict) -> list:
            args = []
            for k, v in h.items():
                args += ["-headers", f"{k}: {v}\r\n"]
            return args

        container  = target_ext
        vcodec     = "copy"
        acodec     = "aac" if container == "mp4" else "copy"
        fmt_args   = ["-f", container]

        cmd = (
            [ffmpeg, "-loglevel", "error"]
            + _hdr_args(cdn_headers)   + ["-i", source_url]
            + _hdr_args(audio_headers) + ["-i", audio_url]
            + ["-c:v", vcodec, "-c:a", acodec]
            + fmt_args + ["pipe:1", "-y"]
        )

        def merge_stream():
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            try:
                while True:
                    chunk = proc.stdout.read(65536)
                    if not chunk:
                        break
                    yield chunk
                err = proc.stderr.read()
                if proc.returncode and err:
                    print(f"[ffmpeg merge] {err.decode(errors='replace')}", flush=True)
            finally:
                try:
                    proc.kill(); proc.wait(timeout=5)
                except Exception:
                    pass

        return StreamingResponse(merge_stream(), media_type=mime, headers=resp_headers)

    # ── Path B: Audio conversion via ffmpeg ───────────────────────────────────
    if needs_ffmpeg and ffmpeg:
        codec_args  = AUDIO_CODEC.get(audio_fmt, AUDIO_CODEC["mp3"])

        # Pass CDN headers to ffmpeg so YouTube doesn't reject the request
        header_args = []
        for k, v in cdn_headers.items():
            header_args += ["-headers", f"{k}: {v}\r\n"]

        cmd = (
            [ffmpeg, "-loglevel", "error"]
            + header_args
            + ["-i", source_url]
            + codec_args
            + ["pipe:1", "-y"]
        )

        def audio_conv_stream():
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            try:
                while True:
                    chunk = proc.stdout.read(65536)
                    if not chunk:
                        break
                    yield chunk
                err = proc.stderr.read()
                if proc.returncode and err:
                    print(f"[ffmpeg audio] {err.decode(errors='replace')}", flush=True)
            finally:
                try:
                    proc.kill(); proc.wait(timeout=5)
                except Exception:
                    pass

        return StreamingResponse(audio_conv_stream(), media_type=mime, headers=resp_headers)

    # ── Path C: Direct proxy (native format, no conversion needed) ────────────
    # Also used as fallback when ffmpeg is missing
    if needs_ffmpeg and not ffmpeg:
        # ffmpeg not available — serve native format instead of 0 bytes
        native_ext   = info.get("native_ext", target_ext)
        filename     = f"{safe_name(title)}.{native_ext}"
        encoded_name = urllib.parse.quote(filename)
        mime         = MIME.get(native_ext, "application/octet-stream")
        resp_headers["Content-Disposition"] = f"attachment; filename*=UTF-8''{encoded_name}"
        print("[warn] ffmpeg unavailable — serving native format", flush=True)

    proxy   = os.environ.get("FETCHR_PROXY")
    proxies = {"http": proxy, "https": proxy} if proxy else None

    req_hdrs = {
        "User-Agent":      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept":          "*/*",
        "Accept-Encoding": "identity",
        "Range":           "bytes=0-",  # Some CDNs require explicit range
    }
    req_hdrs.update(cdn_headers)

    def proxy_stream():
        try:
            r = req_lib.get(
                source_url, stream=True,
                headers=req_hdrs, timeout=180, proxies=proxies,
            )
            r.raise_for_status()
            for chunk in r.iter_content(chunk_size=65536):
                if chunk:
                    yield chunk
        except Exception as exc:
            print(f"[proxy_stream] {exc}", flush=True)

    return StreamingResponse(proxy_stream(), media_type=mime, headers=resp_headers)


@app.get("/waiting/{job_id}")
def waiting_page(job_id: str):
    """Self-polling waiting page for playlist/album downloads."""
    html = f"""<!DOCTYPE html><html><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Preparing download…</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#0c0c0e;
     color:#f0f0f5;display:flex;align-items:center;justify-content:center;
     min-height:100vh;text-align:center;padding:24px}}
.card{{max-width:320px;width:100%}}
.sp{{width:40px;height:40px;border:3px solid #2a2a35;border-top-color:#e8ff47;
     border-radius:50%;margin:0 auto 20px;animation:spin 1s linear infinite}}
@keyframes spin{{to{{transform:rotate(360deg)}}}}
h2{{font-size:17px;font-weight:700;margin-bottom:8px}}
p{{font-size:13px;opacity:.55;line-height:1.5}}
.bw{{margin-top:20px;height:3px;background:#2a2a35;border-radius:2px;overflow:hidden}}
.bar{{height:100%;background:#e8ff47;border-radius:2px;width:0%;transition:width .4s}}
.pct{{font-family:monospace;font-size:12px;color:#e8ff47;margin-top:8px}}
.trk{{font-family:monospace;font-size:11px;color:#b47fff;margin-top:4px;min-height:16px}}
.done{{color:#3dffa0}}.err{{color:#ff4f4f}}
</style></head>
<body><div class="card">
<div class="sp" id="sp"></div>
<h2 id="msg">Preparing your download…</h2>
<p id="sub">Processing on server. File will save to your device automatically.</p>
<div class="bw" id="bw" style="display:none"><div class="bar" id="bar"></div></div>
<p class="pct" id="pct"></p><p class="trk" id="trk"></p>
</div><script>
const JOB="{job_id}";let att=0;
const sp=document.getElementById('sp'),msg=document.getElementById('msg'),
      sub=document.getElementById('sub'),bw=document.getElementById('bw'),
      bar=document.getElementById('bar'),pct=document.getElementById('pct'),
      trk=document.getElementById('trk');
async function check(){{
  att++;
  if(att>360){{sp.style.display='none';msg.textContent='Timed out.';sub.textContent='Please try again.';return;}}
  try{{
    const r=await fetch('/job/'+JOB);
    if(!r.ok){{setTimeout(check,1000);return;}}
    const j=await r.json();
    if(j.status==='done'){{
      sp.style.display='none';msg.className='done';
      msg.textContent='Saving to your device…';
      sub.textContent='Check your downloads. You can close this tab.';
      bw.style.display='none';pct.textContent='';trk.textContent='';
      window.location.href='/serve-job/'+JOB;
    }}else if(j.status==='error'){{
      sp.style.display='none';msg.className='err';
      msg.textContent='Download failed';sub.textContent=j.message||'Please try again.';
    }}else{{
      if(j.percent>0){{bw.style.display='block';bar.style.width=Math.min(j.percent,100)+'%';pct.textContent=Math.round(j.percent)+'%';}}
      if(j.track_info)trk.textContent=j.track_info;
      if(j.message)sub.textContent=j.message;
      setTimeout(check,1000);
    }}
  }}catch(e){{setTimeout(check,2000);}}
}}
check();
</script></body></html>"""
    return HTMLResponse(html)


@app.get("/serve-job/{job_id}")
def serve_job(job_id: str):
    """Stream completed job files to the browser and delete them from the server."""
    job = jobs.get(job_id)
    if not job or job.get("status") != "done":
        return HTMLResponse(
            "<body style='background:#0c0c0e;color:#f0f0f5;font-family:sans-serif;"
            "display:flex;align-items:center;justify-content:center;height:100vh'>"
            "<p>File not available — it may have already been downloaded.</p></body>",
            status_code=404,
        )

    files     = job.get("files") or []
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

    # Single file — stream directly
    if len(abs_paths) == 1:
        path = abs_paths[0]
        ext  = path.rsplit(".", 1)[-1].lower() if "." in path else ""
        enc  = urllib.parse.quote(os.path.basename(path))

        def it():
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

        return StreamingResponse(it(), media_type=MIME.get(ext, "application/octet-stream"), headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{enc}",
            "Content-Length":      str(os.path.getsize(path)),
        })

    # Multiple files — zip and stream
    zname = safe_name(job.get("title") or "fetchr-download") + ".zip"

    def zit():
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for p in abs_paths:
                zf.write(p, os.path.basename(p))
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

    return StreamingResponse(zit(), media_type="application/zip", headers={
        "Content-Disposition": f"attachment; filename*=UTF-8''{urllib.parse.quote(zname)}"
    })


@app.get("/serve/{filepath:path}")
def serve_file(filepath: str):
    """Manual Save link in the Files tab."""
    safe = os.path.normpath(filepath)
    full = os.path.abspath(os.path.join(DOWNLOAD_DIR, safe))
    if not full.startswith(os.path.abspath(DOWNLOAD_DIR)):
        raise HTTPException(status_code=400, detail="Invalid path.")
    if not os.path.exists(full):
        raise HTTPException(status_code=404, detail="File not found.")
    ext = full.rsplit(".", 1)[-1].lower() if "." in full else ""
    enc = urllib.parse.quote(os.path.basename(full))

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

    return StreamingResponse(it(), media_type=MIME.get(ext, "application/octet-stream"), headers={
        "Content-Disposition": f"attachment; filename*=UTF-8''{enc}",
        "Content-Length":      str(os.path.getsize(full)),
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
    """Job-based download — used for playlists/albums."""
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
    result = await loop.run_in_executor(executor, _do_ytdlp_update)
    return result


def _run_download(job_id: str, url: str, mode: str, quality: str, audio_fmt: str):
    jobs[job_id]["status"]  = "downloading"
    jobs[job_id]["message"] = "Starting..."

    def progress_hook(d: dict):
        pct        = d.get("percent", 0) or 0
        downloaded = d.get("downloaded_bytes", 0) or 0
        total      = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
        jobs[job_id]["percent"]    = round(pct, 1)
        jobs[job_id]["speed"]      = d.get("speed", 0) or 0
        jobs[job_id]["track_info"] = d.get("track_info", "")
        if total and downloaded:
            jobs[job_id]["message"] = f"{downloaded/1_000_000:.1f} / {total/1_000_000:.1f} MB"
        elif downloaded:
            jobs[job_id]["message"] = f"{downloaded/1_000_000:.1f} MB"

    try:
        result = download(
            url, mode=mode, quality=quality,
            audio_fmt=audio_fmt, progress_hook=progress_hook,
        )
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