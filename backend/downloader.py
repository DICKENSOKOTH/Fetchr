"""
downloader.py — Fetchr download engine
"""

import yt_dlp
import requests
import mimetypes
import os
import re
import shutil
from pathlib import Path
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
    "tiktok.com","vm.tiktok.com","vt.tiktok.com",
    "dailymotion.com","twitter.com","x.com", "redflix.co"
    "instagram.com","facebook.com","reddit.com",
    "streamable.com","rumble.com","odysee.com","bitchute.com",
]
VIDEO_PATH_HINTS = [".m3u8",".m3u","/stream","/watch","/video","/play"]

YOUTUBE_CLIENTS  = ["android", "ios", "tv_embedded", "web_creator"]
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


# ── Folder helpers ─────────────────────────────────────────────────────────────
def clean_folder_name(name: str) -> str:
    name = re.sub(r'^(album|playlist|ep|single)\s*[-:]\s*', '', name, flags=re.IGNORECASE)
    return name.strip()


def safe_folder(name: str) -> str:
    return re.sub(r'[<>:"/\\|?*]', '-', name).strip()


def resolve_folder(url: str, base_opts: dict) -> str:
    try:
        opts = {
            **base_opts,
            "quiet":       True,
            "noplaylist":  False,
            "playlistend": 1,
            "extractor_args": {"youtube": {"player_client": ["tv_embedded"], "js_runtimes": ["nodejs"]}},
        }
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)

        title  = info.get("playlist_title") or info.get("playlist") or info.get("title") or "Unknown"
        artist = (
            info.get("uploader") or info.get("channel") or
            info.get("artist")   or info.get("creator") or ""
        )

        if not artist and info.get("entries"):
            first = info["entries"][0]
            if first:
                artist = (
                    first.get("artist")   or first.get("uploader") or
                    first.get("channel")  or first.get("creator")  or ""
                )

        artist = re.sub(r'\s*-\s*Topic$', '', artist, flags=re.IGNORECASE).strip()
        title  = clean_folder_name(title)
        folder = f"{title} - {artist}" if artist and artist.lower() not in title.lower() else title
        return safe_folder(folder)
    except Exception:
        return "Unknown"


# ── Base yt-dlp options ────────────────────────────────────────────────────────
def _base_opts(progress_hook: Optional[Callable]) -> dict:
    COOKIES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cookies.txt")
    opts = {
        "noplaylist":                 False,
        "ignoreerrors":               True,
        "skip_unavailable_fragments": True,
        "quiet":                      True,
        "socket_timeout":             30,
        "retries":                    10,
        "fragment_retries":           10,
        "retry_sleep_functions": {
            "http":     lambda n: 2 ** n,
            "fragment": lambda n: 2 ** n,
        },
        "file_access_retries": 5,
        "js_runtimes":         {"node": {}},
        "remote_components": {"ejs:github"},
    }
    if os.path.exists(COOKIES):
        opts["cookiefile"] = COOKIES
    if PROXY:
        opts["proxy"] = PROXY
    if progress_hook:
        opts["progress_hooks"] = [progress_hook]
    return opts


# ── Video opts ─────────────────────────────────────────────────────────────────
def build_video_opts(
    quality: str,
    progress_hook: Optional[Callable],
    extra_extractor_args: Optional[dict] = None,
    folder: Optional[str] = None,
) -> dict:
    fmt       = VIDEO_FORMAT_MAP.get(quality, VIDEO_FORMAT_MAP["best"])
    container = "mkv" if quality in ("4k","1440p") else "mp4"
    out = (
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


# ── Audio opts ─────────────────────────────────────────────────────────────────
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
    out = (
    f"{DOWNLOAD_DIR}/{folder}/%(title).100s.%(ext)s"   # no index for TikTok flat folder
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


# ── Playlist result ────────────────────────────────────────────────────────────
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


# ── Progress hook wrapper that injects track info ──────────────────────────────
def _wrap_hook_with_track(progress_hook: Optional[Callable], playlist_index: list):
    """
    Wraps the progress hook to inject current track number into each update.
    playlist_index is a mutable list [current, total] updated by the postprocessor hook.
    """
    if not progress_hook:
        return None

    def wrapped(d: dict):
        if d.get("status") == "downloading":
            idx   = playlist_index[0]
            total = playlist_index[1]
            if total and idx:
                d["track_info"] = f"Track {idx} of {total}"
            else:
                d["track_info"] = ""
        progress_hook(d)

    return wrapped


# ── Platform downloaders ───────────────────────────────────────────────────────
def _make_ea(client: str) -> dict:
    return {"youtube": {"player_client": [client], "js_runtimes": ["nodejs"]}}


def download_youtube(url: str, mode: str, quality_or_fmt: str,
                     progress_hook: Optional[Callable] = None) -> dict:
    last_error = "Unknown error"
    for client in YOUTUBE_CLIENTS:
        try:
            ea     = _make_ea(client)
            probe  = build_video_opts(quality_or_fmt, None, ea)
            folder = resolve_folder(url, probe)

            # Probe total tracks for counter
            track_state = [None, None]
            try:
                with yt_dlp.YoutubeDL(opts) as ydl:
                    info = ydl.extract_info(url, download=True)
                    if info is None:
                        raise Exception("yt-dlp returned no data. The video may be private, deleted, or region-locked.")
                    return _playlist_result(info, mode)
            except Exception:
                pass

            # Count up as each track starts
            current = [0]
            original_hook = progress_hook

            def counting_hook(d: dict):
                if d.get("status") == "started" or (
                    d.get("status") == "downloading" and d.get("downloaded_bytes", 0) < 65536
                ):
                    pass
                if original_hook:
                    if track_state[1] and current[0]:
                        d["track_info"] = f"Track {current[0]} of {track_state[1]}"
                    original_hook(d)

            # Use postprocessor hook to detect track changes
            opts = (build_audio_opts(quality_or_fmt, counting_hook, ea, folder)
                    if mode == "audio" else
                    build_video_opts(quality_or_fmt, counting_hook, ea, folder))

            def pp_hook(d: dict):
                if d.get("status") == "started":
                    current[0] += 1
                    if progress_hook and track_state[1]:
                        progress_hook({
                            "status":     "downloading",
                            "percent":    0,
                            "track_info": f"Track {current[0]} of {track_state[1]}",
                        })

            opts["postprocessor_hooks"] = [pp_hook]

            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=True)
                result = _playlist_result(info, mode)
                return result
        except Exception as e:
            last_error = str(e)
            if "Unsupported URL" in last_error:
                break
    raise Exception(last_error)


TIKTOK_FOLDER = "TikTok"   # add this near the top with other constants

def download_tiktok(url, mode, quality_or_fmt, progress_hook=None):
    last_error = "Unknown error"
    for api_host in TIKTOK_API_HOSTS:
        try:
            ea = {
                "tiktok":  {"api_hostname": [api_host]},
                "youtube": {"js_runtimes": ["nodejs"]},
            }
            # All TikTok content goes into one shared folder, no per-video subfolders
            folder = TIKTOK_FOLDER

            if mode == "audio":
                opts = build_audio_opts(quality_or_fmt, progress_hook, ea, folder)
            else:
                opts = build_tiktok_video_opts(quality_or_fmt, progress_hook, ea, folder)

            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=True)
                if info is None:
                    raise Exception("yt-dlp returned no data. Video may be private or region-locked.")
                return _playlist_result(info, mode)
        except Exception as e:
            last_error = str(e)
            if any(x in last_error for x in [
                "Unsupported URL","account is private",
                "This video is unavailable","does not exist",
            ]):
                break
    raise Exception(last_error)

def build_tiktok_video_opts(
    quality: str,
    progress_hook: Optional[Callable],
    extra_extractor_args: Optional[dict] = None,
    folder: Optional[str] = None,
) -> dict:
    # TikTok doesn't support height-based format selection without impersonation.
    # Use a simpler format string that accepts whatever is available.
    out = (
        f"{DOWNLOAD_DIR}/{folder}/%(playlist_index)s - %(title).100s.%(ext)s"
        if folder else
        f"{DOWNLOAD_DIR}/%(title).100s.%(ext)s"
    )
    opts = _base_opts(progress_hook)
    opts.update({
        "outtmpl":             out,
        "format":              "bestvideo+bestaudio/best",  # no height filter
        "merge_output_format": "mp4",
        "remux_video":         "mp4",
    })
    if extra_extractor_args:
        opts["extractor_args"] = extra_extractor_args
    return opts

def download_generic_video(url: str, mode: str, quality_or_fmt: str,
                           progress_hook: Optional[Callable] = None) -> dict:
    ea     = {"youtube": {"js_runtimes": ["nodejs"]}}
    probe  = build_video_opts(quality_or_fmt, None, ea)
    folder = resolve_folder(url, probe)
    opts   = (build_audio_opts(quality_or_fmt, progress_hook, ea, folder)
              if mode == "audio" else
              build_video_opts(quality_or_fmt, progress_hook, ea, folder))
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
    headers  = {"User-Agent": "Mozilla/5.0", "Accept": "*/*", "Accept-Encoding": "identity"}
    proxies  = {"http": PROXY, "https": PROXY} if PROXY else None
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
                        "track_info":       "",
                    })
    return {"status": "done", "type": "file", "title": filename}


# ── Preview (metadata only, no download) ──────────────────────────────────────
def preview_url(url: str) -> dict:
    opts = {
        "quiet":       True,
        "noplaylist":  True,
        "playlistend": 1,
        "js_runtimes": {"node": {}},
        "extractor_args": {"youtube": {"player_client": ["tv_embedded"], "js_runtimes": ["nodejs"]}},
    }
    if PROXY:
        opts["proxy"] = PROXY
    COOKIES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cookies.txt")
    if os.path.exists(COOKIES):
        opts["cookiefile"] = COOKIES

    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)

    is_playlist = bool(info.get("entries"))
    entries     = list(info.get("entries") or [])
    count       = len(entries) if entries else 1

    return {
        "title":       info.get("title") or info.get("playlist_title") or "Unknown",
        "thumbnail":   info.get("thumbnail"),
        "duration":    info.get("duration"),
        "uploader":    info.get("uploader") or info.get("channel") or "",
        "is_playlist": is_playlist,
        "count":       count,
        "url":         url,
    }


# ── Storage info ───────────────────────────────────────────────────────────────
def get_storage_info() -> dict:
    files = []
    total_bytes = 0
    try:
        for name in sorted(os.listdir(DOWNLOAD_DIR)):
            path = os.path.join(DOWNLOAD_DIR, name)
            if os.path.isfile(path):
                size = os.path.getsize(path)
                total_bytes += size
                files.append({"name": name, "size": size})
    except Exception:
        pass

    disk  = shutil.disk_usage(DOWNLOAD_DIR)
    return {
        "files":       files,
        "total_bytes": total_bytes,
        "disk_free":   disk.free,
        "disk_total":  disk.total,
        "folder":      DOWNLOAD_DIR,
    }


# ── Main entry ─────────────────────────────────────────────────────────────────
def download(
    url: str,
    mode: str = "video",
    quality: str = "best",
    audio_fmt: str = "mp3",
    progress_hook: Optional[Callable] = None,
) -> dict:
    kind           = detect_type(url)
    if kind == "probe":
        kind       = probe_url(url)
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