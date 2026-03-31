"""
downloader.py — Production download engine with format/quality selection

Video qualities : best, 4k, 1440p, 1080p, 720p
Audio formats   : mp3, flac, wav, opus, aac, alac
"""

import yt_dlp
import requests
import mimetypes
import os
from pathlib import Path
from bs4 import BeautifulSoup
from typing import Optional, Callable

# ── Download directory ─────────────────────────────────────────────────────────
DOWNLOAD_DIR = str(Path.home() / "Downloads" / "Fetchr")
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# ── Optional proxy ─────────────────────────────────────────────────────────────
PROXY: Optional[str] = os.environ.get("FETCHR_PROXY", None)

# ── Direct file extensions ─────────────────────────────────────────────────────
DIRECT_EXTS = {
    "pdf", "zip", "rar", "7z", "tar", "gz", "bz2",
    "exe", "dmg", "apk", "msi", "deb",
    "mp3", "mp4", "mkv", "avi", "mov", "webm", "flv", "wmv",
    "m4a", "aac", "ogg", "wav", "flac", "opus",
    "jpg", "jpeg", "png", "gif", "webp", "bmp",
    "docx", "xlsx", "pptx", "doc", "xls", "ppt",
    "csv", "epub", "mobi",
}

VIDEO_DOMAINS = [
    "youtube.com", "youtu.be", "vimeo.com", "twitch.tv", "stremio.com",
    "tiktok.com", "vm.tiktok.com", "vt.tiktok.com", "netflix.com",
    "dailymotion.com", "twitter.com", "x.com", "yotubemusic.com",
    "spotify.com", "soundcloud.com", "bandcamp.com", "redflix.com",
    "mixcloud.com","deezer.com", "music.apple.com", "music.youtube.com",
    "instagram.com", "facebook.com", "reddit.com", "linkedin.com",
    "streamable.com", "rumble.com", "odysee.com", "bitchute.com",
]
VIDEO_PATH_HINTS = [".m3u8", ".m3u", "/stream", "/watch", "/video", "/play"]

# YouTube player clients — android/ios bypass bot detection on server IPs
YOUTUBE_CLIENTS = ["android", "ios", "tv_embedded", "web_creator", "web", "embed", "mweb", "ytdl-raw", "ytmusic"]  # order matters for fallback

# TikTok API hostnames — rotating reduces "Unable to extract" errors
TIKTOK_API_HOSTS = [
    "api22-normal-c-useast2a.tiktokv.com",
    "api19-normal-c-useast1a.tiktokv.com",
    "api16-normal-c-useast1a.tiktokv.com",
    "api16-normal-useast5.tiktokv.com",
]

# ── Video quality → yt-dlp format string ──────────────────────────────────────
# Prefer mp4 video + m4a audio for best compatibility, fallback to any format.
# Heights are upper bounds so "1080p" also matches 1080p content.
VIDEO_FORMAT_MAP = {
    "best":  "bestvideo+bestaudio/best",
    "4k":    "bestvideo[height<=2160][vcodec^=vp9]+bestaudio/bestvideo[height<=2160][vcodec^=av01]+bestaudio/bestvideo[height<=2160]+bestaudio",
    "1440p": "bestvideo[height<=1440][vcodec^=vp9]+bestaudio/bestvideo[height<=1440][vcodec^=av01]+bestaudio/bestvideo[height<=1440]+bestaudio",
    "1080p": "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/bestvideo[height<=1080]+bestaudio",
    "720p":  "bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]/bestvideo[height<=720]+bestaudio",
}

# ── Audio codec config ─────────────────────────────────────────────────────────
# Each entry: (FFmpeg codec name, quality/bitrate string, output extension label)
# "quality" for lossy = bitrate kbps; for lossless = "0" (best/lossless)
# ALAC outputs inside .m4a container with Apple Lossless codec (NOT AAC/mp4a)
# Hi-Res FLAC/WAV = download highest bitrate source then convert losslessly
AUDIO_FORMAT_MAP = {
    "mp3":  ("mp3",  "320",  "mp3"),   # MP3 320kbps
    "flac": ("flac", "0",    "flac"),  # FLAC lossless
    "wav":  ("wav",  "0",    "wav"),   # WAV lossless
    "opus": ("opus", "0",    "opus"),  # Opus (kept as-is from source when possible)
    "aac":  ("aac",  "0",    "m4a"),   # AAC in M4A container
    "alac": ("alac", "0",    "m4a"),   # Apple Lossless in M4A container
}


# ── Type detection ─────────────────────────────────────────────────────────────
def detect_type(url: str) -> str:
    clean = url.split("?")[0].lower()
    ext = clean.split(".")[-1]
    if ext in DIRECT_EXTS:
        return "file"
    for domain in VIDEO_DOMAINS:
        if domain in url.lower():
            return "video"
    for hint in VIDEO_PATH_HINTS:
        if hint in clean:
            return "video"
    return "probe"


def probe_url(url: str) -> str:
    try:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        r = requests.head(url, headers=headers, timeout=10, allow_redirects=True)
        if "filename" in r.headers.get("Content-Disposition", ""):
            return "file"
        ct = r.headers.get("Content-Type", "")
        if "text/html" in ct:
            return "ydlp"
        if any(t in ct for t in ["application/", "video/", "audio/", "image/"]):
            return "file"
    except Exception:
        pass
    return "ydlp"


# ── Filename extraction ────────────────────────────────────────────────────────
def get_filename(response: requests.Response, url: str) -> str:
    cd = response.headers.get("Content-Disposition", "")
    if "filename=" in cd:
        fname = cd.split("filename=")[-1].strip().strip('"\'').split(";")[0].strip()
        if fname:
            return fname
    fname = url.split("?")[0].rstrip("/").split("/")[-1]
    if not fname or "." not in fname:
        ct = response.headers.get("Content-Type", "").split(";")[0].strip()
        ext = mimetypes.guess_extension(ct) or ""
        ext = ext.replace(".jpe", ".jpg")
        fname = (fname or "download") + ext
    return fname or "download"


# ── yt-dlp options builders ────────────────────────────────────────────────────
def build_video_opts(
    quality: str,
    progress_hook: Optional[Callable],
    extra_extractor_args: Optional[dict] = None,
) -> dict:
    fmt = VIDEO_FORMAT_MAP.get(quality, VIDEO_FORMAT_MAP["best"])
    container = "mkv" if quality in ("4k", "1440p") else "mp4"

    opts = {
        "outtmpl":                    f"{DOWNLOAD_DIR}/%(playlist_title,playlist,title)s by %(channel,uploader,artist,Unknown Artist)s/%(playlist_index)s - %(title).100s.%(ext)s",
        "format":                     fmt,
        "merge_output_format":        container,
        "remux_video":                container,
        "noplaylist":                 False,
        "ignoreerrors":               True,
        "skip_unavailable_fragments": True,
        "quiet":                      True,

        # Timeout + retry settings
        "socket_timeout":    30,       # seconds before a single socket op times out
        "retries":           10,       # retry each fragment/file up to 10 times
        "fragment_retries":  10,       # retry broken stream fragments 10 times
        "retry_sleep_functions": {
            "http":     lambda n: 2 ** n,   # exponential backoff: 1s, 2s, 4s, 8s...
            "fragment": lambda n: 2 ** n,
        },
        "file_access_retries": 5,
    }
    if extra_extractor_args:
        opts["extractor_args"] = extra_extractor_args
    if PROXY:
        opts["proxy"] = PROXY
    if progress_hook:
        opts["progress_hooks"] = [progress_hook]
    return opts


def build_audio_opts(
    fmt: str,
    progress_hook: Optional[Callable],
    extra_extractor_args: Optional[dict] = None,
) -> dict:
    codec, quality, _ = AUDIO_FORMAT_MAP.get(fmt, AUDIO_FORMAT_MAP["mp3"])

    opts = {
        "outtmpl":                    f"{DOWNLOAD_DIR}/%(playlist_title,playlist,title)s by %(channel,uploader,artist,Unknown Artist)s/%(playlist_index)s - %(title).100s.%(ext)s",
        "format":                     "bestaudio/best",
        "noplaylist":                 False,
        "ignoreerrors":               True,
        "skip_unavailable_fragments": True,
        "quiet":                      True,

        # Timeout + retry settings
        "socket_timeout":    30,
        "retries":           10,
        "fragment_retries":  10,
        "retry_sleep_functions": {
            "http":     lambda n: 2 ** n,
            "fragment": lambda n: 2 ** n,
        },
        "file_access_retries": 5,

        "postprocessors": [
            {
                "key":              "FFmpegExtractAudio",
                "preferredcodec":   codec,
                "preferredquality": quality,
            },
            {"key": "FFmpegMetadata"},
        ],
    }
    if extra_extractor_args:
        opts["extractor_args"] = extra_extractor_args
    if PROXY:
        opts["proxy"] = PROXY
    if progress_hook:
        opts["progress_hooks"] = [progress_hook]
    return opts


# ── Platform-specific downloaders ──────────────────────────────────────────────
def _ydl_run(opts: dict) -> dict:
    """Run yt-dlp with given opts, return result dict."""
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(ydl._ydl_info if hasattr(ydl, "_ydl_info") else None, download=False)


def _extract_playlist_result(info: dict, url: str) -> dict:
    """
    Inspects the yt-dlp info dict after a playlist download.
    Returns an honest result — partial if some entries failed.
    """
    # Single video — no playlist info
    if not info.get("entries"):
        return {"status": "done", "type": "video", "title": info.get("title", "video")}

    entries     = list(info["entries"])
    total       = len(entries)
    # yt-dlp sets entry to None when a video was skipped/failed
    succeeded   = sum(1 for e in entries if e is not None)
    failed      = total - succeeded
    title       = info.get("title") or info.get("playlist_title") or "Playlist"

    if succeeded == 0:
        return {"status": "error", "message": f"All {total} items failed to download."}

    if failed > 0:
        return {
            "status":  "done",
            "type":    "video",
            "title":   title,
            "message": f"Downloaded {succeeded}/{total} — {failed} skipped (unavailable or age-restricted)",
        }

    return {"status": "done", "type": "video", "title": title}


def download_youtube(
    url: str, mode: str, quality_or_fmt: str,
    progress_hook: Optional[Callable] = None,
) -> dict:
    last_error = "Unknown error"
    for client in YOUTUBE_CLIENTS:
        try:
            ea   = {"youtube": {"player_client": [client]}}
            opts = build_audio_opts(quality_or_fmt, progress_hook, ea) if mode == "audio" \
                   else build_video_opts(quality_or_fmt, progress_hook, ea)
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=True)
                result = _extract_playlist_result(info, url)
                result["type"] = mode
                return result
        except Exception as e:
            last_error = str(e)
            if "Unsupported URL" in last_error:
                break
    raise Exception(last_error)


def download_tiktok(
    url: str, mode: str, quality_or_fmt: str,
    progress_hook: Optional[Callable] = None,
) -> dict:
    last_error = "Unknown error"
    for api_host in TIKTOK_API_HOSTS:
        try:
            ea   = {"tiktok": {"api_hostname": [api_host]}}
            opts = build_audio_opts(quality_or_fmt, progress_hook, ea) if mode == "audio" \
                   else build_video_opts(quality_or_fmt, progress_hook, ea)
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=True)
                result = _extract_playlist_result(info, url)
                result["type"] = mode
                return result
        except Exception as e:
            last_error = str(e)
            if any(x in last_error for x in ["Unsupported URL", "account is private", "does not exist"]):
                break
    raise Exception(last_error)


def download_generic_video(
    url: str, mode: str, quality_or_fmt: str,
    progress_hook: Optional[Callable] = None,
) -> dict:
    opts = build_audio_opts(quality_or_fmt, progress_hook) if mode == "audio" \
           else build_video_opts(quality_or_fmt, progress_hook)
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        result = _extract_playlist_result(info, url)
        result["type"] = mode
        return result

# ── Direct file download ───────────────────────────────────────────────────────
def download_file(url: str, progress_hook: Optional[Callable] = None) -> dict:
    headers = {
        "User-Agent":      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept":          "*/*",
        "Accept-Encoding": "identity",
    }
    proxies = {"http": PROXY, "https": PROXY} if PROXY else None
    r = requests.get(url, stream=True, headers=headers, timeout=60, proxies=proxies)
    r.raise_for_status()

    ct = r.headers.get("Content-Type", "")
    if "text/html" in ct:
        raise Exception("Server returned an HTML page instead of a downloadable file.")

    filename = get_filename(r, url)
    path = os.path.join(DOWNLOAD_DIR, filename)
    total = int(r.headers.get("Content-Length", 0))
    downloaded = 0

    with open(path, "wb") as f:
        for chunk in r.iter_content(chunk_size=65536):
            if chunk:
                f.write(chunk)
                downloaded += len(chunk)
                if progress_hook and total:
                    progress_hook({
                        "status":           "downloading",
                        "downloaded_bytes": downloaded,
                        "total_bytes":      total,
                        "percent":          round(downloaded / total * 100, 1),
                    })

    return {"status": "done", "type": "file", "title": filename}


# ── Main entry ─────────────────────────────────────────────────────────────────
def download(
    url: str,
    mode: str = "video",            # "video" | "audio" | "file"
    quality: str = "best",          # video: best/4k/1440p/1080p/720p
    audio_fmt: str = "mp3",         # audio: mp3/flac/wav/opus/aac/alac
    progress_hook: Optional[Callable] = None,
) -> dict:
    kind = detect_type(url)
    if kind == "probe":
        kind = probe_url(url)

    url_lower = url.lower()

    # Always direct-download known file types regardless of mode
    if kind == "file":
        try:
            return download_file(url, progress_hook)
        except Exception as e:
            return {"status": "error", "message": str(e)}

    # Video or audio via yt-dlp
    if kind in ("video", "ydlp"):
        quality_or_fmt = audio_fmt if mode == "audio" else quality
        try:
            if "youtu" in url_lower:
                return download_youtube(url, mode, quality_or_fmt, progress_hook)
            elif "tiktok.com" in url_lower:
                return download_tiktok(url, mode, quality_or_fmt, progress_hook)
            else:
                return download_generic_video(url, mode, quality_or_fmt, progress_hook)
        except Exception as e:
            # Last resort: maybe it's a direct CDN link
            try:
                return download_file(url, progress_hook)
            except Exception:
                return {"status": "error", "message": str(e)}

    return {"status": "error", "message": "Unrecognised URL type."}