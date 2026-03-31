"""
downloader.py — Production download engine
Video qualities : best, 4k, 1440p, 1080p, 720p
Audio formats   : mp3, flac, wav, opus, aac, alac
"""

import yt_dlp
import requests
import mimetypes
import os
import re
from pathlib import Path
from bs4 import BeautifulSoup
from typing import Optional, Callable

# ── Paths ──────────────────────────────────────────────────────────────────────
DOWNLOAD_DIR = str(Path.home() / "Downloads" / "Fetchr")
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

PROXY: Optional[str] = os.environ.get("FETCHR_PROXY", None)

# ── Constants ──────────────────────────────────────────────────────────────────
DIRECT_EXTS = {
    "pdf","zip","rar","7z","tar","gz","bz2","exe","dmg","apk","msi","deb",
    "mp3","mp4","mkv","avi","mov","webm","flv","wmv","m4a","aac","ogg",
    "wav","flac","opus","jpg","jpeg","png","gif","webp","bmp",
    "docx","xlsx","pptx","doc","xls","ppt","csv","epub","mobi",
}

VIDEO_DOMAINS = [
    "youtube.com","youtu.be","vimeo.com","twitch.tv",
    "tiktok.com","vm.tiktok.com","vt.tiktok.com", "music.youtube.com", ""
    "dailymotion.com","twitter.com","x.com", "netflix.com","hulu.com",
    "disneyplus.com", "Redflix.com", "hbo.com","primevideo.com",
    "instagram.com","facebook.com","reddit.com", "soundcloud.com",
    "bandcamp.com","spotify.com", "youtube-music.com","apple.com/apple-music",
    "streamable.com","rumble.com","odysee.com","bitchute.com", "peertube.video",
]
VIDEO_PATH_HINTS = [".m3u8",".m3u","/stream","/watch","/video","/play"]

YOUTUBE_CLIENTS  = ["android","ios","tv_embedded","web_creator","web","embed","internal","mweb","music_video_app"]
TIKTOK_API_HOSTS = [
    "api22-normal-c-useast2a.tiktokv.com",
    "api19-normal-c-useast1a.tiktokv.com",
    "api16-normal-c-useast1a.tiktokv.com",
    "api16-normal-useast5.tiktokv.com",
]

VIDEO_FORMAT_MAP = {
    "best":  "bestvideo+bestaudio/best",
    "4k":    "bestvideo[height<=2160][vcodec^=vp9]+bestaudio/bestvideo[height<=2160][vcodec^=av01]+bestaudio/bestvideo[height<=2160]+bestaudio",
    "1440p": "bestvideo[height<=1440][vcodec^=vp9]+bestaudio/bestvideo[height<=1440][vcodec^=av01]+bestaudio/bestvideo[height<=1440]+bestaudio",
    "1080p": "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/bestvideo[height<=1080]+bestaudio",
    "720p":  "bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]/bestvideo[height<=720]+bestaudio",
}

AUDIO_FORMAT_MAP = {
    "mp3":  ("mp3",  "320", "mp3"),
    "flac": ("flac", "0",   "flac"),
    "wav":  ("wav",  "0",   "wav"),
    "opus": ("opus", "0",   "opus"),
    "aac":  ("aac",  "0",   "m4a"),
    "alac": ("alac", "0",   "m4a"),
}


# ── Type detection ─────────────────────────────────────────────────────────────
def detect_type(url: str) -> str:
    clean = url.split("?")[0].lower()
    ext   = clean.split(".")[-1]
    if ext in DIRECT_EXTS:
        return "file"
    for d in VIDEO_DOMAINS:
        if d in url.lower():
            return "video"
    for h in VIDEO_PATH_HINTS:
        if h in clean:
            return "video"
    return "probe"


def probe_url(url: str) -> str:
    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        r = requests.head(url, headers=headers, timeout=10, allow_redirects=True)
        if "filename" in r.headers.get("Content-Disposition", ""):
            return "file"
        ct = r.headers.get("Content-Type", "")
        if "text/html" in ct:
            return "ydlp"
        if any(t in ct for t in ["application/","video/","audio/","image/"]):
            return "file"
    except Exception:
        pass
    return "ydlp"


# ── Folder name helpers ────────────────────────────────────────────────────────
def clean_folder_name(name: str) -> str:
    """Strip prefixes like 'Album - ', 'Playlist - ' that YouTube Music adds."""
    name = re.sub(r'^(album|playlist|ep|single)\s*[-:]\s*', '', name, flags=re.IGNORECASE)
    return name.strip()


def safe_folder(name: str) -> str:
    """Remove characters that are illegal in folder names on Windows/Linux/Mac."""
    return re.sub(r'[<>:"/\\|?*]', '-', name).strip()


def resolve_folder(url: str, base_opts: dict) -> str:
    try:
        opts = {
            **base_opts,
            "quiet":       True,
            "noplaylist":  False,
            # Don't use extract_flat — it skips uploader/channel metadata
            "playlistend": 1,   # only fetch metadata for first entry, fast enough
        }
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)

        title  = info.get("playlist_title") or info.get("playlist") or info.get("title") or "Unknown"
        artist = (
            info.get("uploader") or
            info.get("channel") or
            info.get("artist") or
            info.get("creator") or
            ""
        )

        # If artist appears inside title already (some platforms do this), don't duplicate
        title  = clean_folder_name(title)
        folder = f"{title} by {artist}" if artist and artist.lower() not in title.lower() else title
        return safe_folder(folder)
    except Exception:
        return "Unknown"


# ── yt-dlp options builders ────────────────────────────────────────────────────
def _base_opts(progress_hook: Optional[Callable]) -> dict:
    opts = {
        "noplaylist":                 False,
        "ignoreerrors":               True,
        "skip_unavailable_fragments": True,
        "quiet":                      True,
        "socket_timeout":             30,
        "retries":                    10,
        "fragment_retries":           10,
        "retry_sleep_functions":      {
            "http":     lambda n: 2 ** n,
            "fragment": lambda n: 2 ** n,
        },
        "file_access_retries": 5,
    }
    if PROXY:
        opts["proxy"] = PROXY
    if progress_hook:
        opts["progress_hooks"] = [progress_hook]
    return opts


def build_video_opts(
    quality: str,
    progress_hook: Optional[Callable],
    extra_extractor_args: Optional[dict] = None,
    folder: Optional[str] = None,
) -> dict:
    fmt       = VIDEO_FORMAT_MAP.get(quality, VIDEO_FORMAT_MAP["best"])
    container = "mkv" if quality in ("4k", "1440p") else "mp4"
    out       = (
        f"{DOWNLOAD_DIR}/{folder}/%(playlist_index)s - %(title).100s.%(ext)s"
        if folder else
        f"{DOWNLOAD_DIR}/%(title).100s.%(ext)s"
    )
    opts = _base_opts(progress_hook)
    opts.update({
        "outtmpl":             out,
        "format":              fmt,
        "merge_output_format": container,
        "remux_video":         container,
    })
    if extra_extractor_args:
        opts["extractor_args"] = extra_extractor_args
    return opts


def build_audio_opts(
    fmt: str,
    progress_hook: Optional[Callable],
    extra_extractor_args: Optional[dict] = None,
    folder: Optional[str] = None,
) -> dict:
    codec, quality, _ = AUDIO_FORMAT_MAP.get(fmt, AUDIO_FORMAT_MAP["mp3"])
    out = (
        f"{DOWNLOAD_DIR}/{folder}/%(playlist_index)s - %(title).100s.%(ext)s"
        if folder else
        f"{DOWNLOAD_DIR}/%(title).100s.%(ext)s"
    )
    opts = _base_opts(progress_hook)
    opts.update({
        "outtmpl": out,
        "format":  "bestaudio/best",
        "postprocessors": [
            {
                "key":              "FFmpegExtractAudio",
                "preferredcodec":   codec,
                "preferredquality": quality,
            },
            {"key": "FFmpegMetadata"},
        ],
    })
    if extra_extractor_args:
        opts["extractor_args"] = extra_extractor_args
    return opts


# ── Playlist result inspector ──────────────────────────────────────────────────
def _playlist_result(info: dict, mode: str) -> dict:
    if not info.get("entries"):
        return {"status": "done", "type": mode, "title": info.get("title", "video")}

    entries   = list(info["entries"])
    total     = len(entries)
    succeeded = sum(1 for e in entries if e is not None)
    failed    = total - succeeded
    title     = info.get("title") or info.get("playlist_title") or "Playlist"

    if succeeded == 0:
        return {"status": "error", "message": f"All {total} items failed to download."}

    msg = f"Downloaded {succeeded}/{total}" + (f" — {failed} skipped" if failed else "")
    return {"status": "done", "type": mode, "title": title, "message": msg}


# ── Platform downloaders ───────────────────────────────────────────────────────
def download_youtube(
    url: str, mode: str, quality_or_fmt: str,
    progress_hook: Optional[Callable] = None,
) -> dict:
    last_error = "Unknown error"
    for client in YOUTUBE_CLIENTS:
        try:
            ea = {"youtube": {"player_client": [client]}}

            # Resolve clean folder name first
            probe = build_video_opts(quality_or_fmt, None, ea)
            folder = resolve_folder(url, probe)

            opts = (build_audio_opts(quality_or_fmt, progress_hook, ea, folder)
                    if mode == "audio" else
                    build_video_opts(quality_or_fmt, progress_hook, ea, folder))

            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=True)
                return _playlist_result(info, mode)
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
            ea = {"tiktok": {"api_hostname": [api_host]}}

            probe  = build_video_opts(quality_or_fmt, None, ea)
            folder = resolve_folder(url, probe)

            opts = (build_audio_opts(quality_or_fmt, progress_hook, ea, folder)
                    if mode == "audio" else
                    build_video_opts(quality_or_fmt, progress_hook, ea, folder))

            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=True)
                return _playlist_result(info, mode)
        except Exception as e:
            last_error = str(e)
            if any(x in last_error for x in [
                "Unsupported URL","account is private",
                "This video is unavailable","does not exist",
            ]):
                break
    raise Exception(last_error)


def download_generic_video(
    url: str, mode: str, quality_or_fmt: str,
    progress_hook: Optional[Callable] = None,
) -> dict:
    probe  = build_video_opts(quality_or_fmt, None)
    folder = resolve_folder(url, probe)

    opts = (build_audio_opts(quality_or_fmt, progress_hook, None, folder)
            if mode == "audio" else
            build_video_opts(quality_or_fmt, progress_hook, None, folder))

    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        return _playlist_result(info, mode)


# ── Direct file download ───────────────────────────────────────────────────────
def _get_filename(r: requests.Response, url: str) -> str:
    cd = r.headers.get("Content-Disposition", "")
    if "filename=" in cd:
        fname = cd.split("filename=")[-1].strip().strip('"\'').split(";")[0].strip()
        if fname:
            return fname
    fname = url.split("?")[0].rstrip("/").split("/")[-1]
    if not fname or "." not in fname:
        ct  = r.headers.get("Content-Type","").split(";")[0].strip()
        ext = mimetypes.guess_extension(ct) or ""
        ext = ext.replace(".jpe",".jpg")
        fname = (fname or "download") + ext
    return fname or "download"


def download_file(url: str, progress_hook: Optional[Callable] = None) -> dict:
    headers = {
        "User-Agent":      "Mozilla/5.0",
        "Accept":          "*/*",
        "Accept-Encoding": "identity",
    }
    proxies = {"http": PROXY, "https": PROXY} if PROXY else None
    r = requests.get(url, stream=True, headers=headers, timeout=60, proxies=proxies)
    r.raise_for_status()

    if "text/html" in r.headers.get("Content-Type",""):
        raise Exception("Server returned an HTML page instead of a file.")

    filename   = _get_filename(r, url)
    path       = os.path.join(DOWNLOAD_DIR, filename)
    total      = int(r.headers.get("Content-Length", 0))
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
    mode: str = "video",
    quality: str = "best",
    audio_fmt: str = "mp3",
    progress_hook: Optional[Callable] = None,
) -> dict:
    kind = detect_type(url)
    if kind == "probe":
        kind = probe_url(url)

    url_lower      = url.lower()
    quality_or_fmt = audio_fmt if mode == "audio" else quality

    if kind == "file":
        try:
            return download_file(url, progress_hook)
        except Exception as e:
            return {"status": "error", "message": str(e)}

    if kind in ("video", "ydlp"):
        try:
            if "youtu" in url_lower:
                return download_youtube(url, mode, quality_or_fmt, progress_hook)
            elif "tiktok.com" in url_lower:
                return download_tiktok(url, mode, quality_or_fmt, progress_hook)
            else:
                return download_generic_video(url, mode, quality_or_fmt, progress_hook)
        except Exception as e:
            try:
                return download_file(url, progress_hook)
            except Exception:
                return {"status": "error", "message": str(e)}

    return {"status": "error", "message": "Unrecognised URL."}