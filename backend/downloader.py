"""
downloader.py — Fetchr download engine
All download functions return 'files' list of relative paths so the server
can stream them to the browser and delete them afterward.
"""

import yt_dlp
import requests
import mimetypes
import os
import re
import shutil
from pathlib import Path
from typing import Optional, Callable

#Paths ─
DOWNLOAD_DIR = str(Path.home() / "Downloads" / "Fetchr")
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

PROXY: Optional[str] = os.environ.get("FETCHR_PROXY", None)

#Shared folder names 
TIKTOK_FOLDER          = "TikTok"
YOUTUBE_SINGLES_FOLDER = "YouTube"

#Constants 
DIRECT_EXTS = {
    "pdf","zip","rar","7z","tar","gz","bz2","exe","dmg","apk","msi","deb",
    "mp3","mp4","mkv","avi","mov","webm","flv","wmv","m4a","aac","ogg",
    "wav","flac","opus","jpg","jpeg","png","gif","webp","bmp",
    "docx","xlsx","pptx","doc","xls","ppt","csv","epub","mobi",
}

VIDEO_DOMAINS = [
    "youtube.com","youtu.be","vimeo.com","twitch.tv",
    "tiktok.com","vm.tiktok.com","vt.tiktok.com",
    "dailymotion.com","twitter.com","x.com",
    "instagram.com","facebook.com","reddit.com",
    "streamable.com","rumble.com","odysee.com","bitchute.com",
]
VIDEO_PATH_HINTS = [".m3u8",".m3u","/stream","/watch","/video","/play"]

YOUTUBE_CLIENTS_NO_COOKIES   = ["tv_embedded","web_creator","android","ios"]
YOUTUBE_CLIENTS_WITH_COOKIES = ["tv_embedded","web","web_creator","mweb"]

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


#Helpers
def _cookies_path() -> str:
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "cookies.txt")


def _has_cookies() -> bool:
    return os.path.exists(_cookies_path())


def _youtube_clients() -> list:
    return YOUTUBE_CLIENTS_WITH_COOKIES if _has_cookies() else YOUTUBE_CLIENTS_NO_COOKIES


#Type detection 
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


#Folder name helpers 
def clean_folder_name(name: str) -> str:
    name = re.sub(r'^(album|playlist|ep|single)\s*[-:]\s*', '', name, flags=re.IGNORECASE)
    return name.strip()


def safe_folder(name: str) -> str:
    return re.sub(r'[<>:"/\\|?*]', '-', name).strip()


def build_playlist_folder(info: dict) -> str:
    title  = info.get("playlist_title") or info.get("playlist") or info.get("title") or "Unknown"
    artist = (
        info.get("uploader") or info.get("channel") or
        info.get("artist")   or info.get("creator") or ""
    )
    if not artist and info.get("entries"):
        first = next((e for e in info["entries"] if e), None)
        if first:
            artist = (
                first.get("artist") or first.get("uploader") or
                first.get("channel") or first.get("creator") or ""
            )
    artist = re.sub(r'\s*-\s*Topic$', '', artist, flags=re.IGNORECASE).strip()
    title  = clean_folder_name(title)
    folder = f"{title} - {artist}" if artist and artist.lower() not in title.lower() else title
    return safe_folder(folder)


#Base yt-dlp options 
def _base_opts(progress_hook: Optional[Callable]) -> dict:
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
        "remote_components":   {"ejs:github"},
    }
    if _has_cookies():
        opts["cookiefile"] = _cookies_path()
    if PROXY:
        opts["proxy"] = PROXY
    if progress_hook:
        opts["progress_hooks"] = [progress_hook]
    return opts


#Video opts
def build_video_opts(
    quality: str,
    progress_hook: Optional[Callable],
    extra_extractor_args: Optional[dict] = None,
    folder: Optional[str] = None,
    single: bool = False,
) -> dict:
    fmt       = VIDEO_FORMAT_MAP.get(quality, VIDEO_FORMAT_MAP["best"])
    container = "mkv" if quality in ("4k","1440p") else "mp4"
    if folder and not single:
        out = f"{DOWNLOAD_DIR}/{folder}/%(playlist_index)s - %(title).100s.%(ext)s"
    elif folder and single:
        out = f"{DOWNLOAD_DIR}/{folder}/%(title).100s.%(ext)s"
    else:
        out = f"{DOWNLOAD_DIR}/%(title).100s.%(ext)s"
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


#Audio opts
def build_audio_opts(
    fmt: str,
    progress_hook: Optional[Callable],
    extra_extractor_args: Optional[dict] = None,
    folder: Optional[str] = None,
    single: bool = False,
) -> dict:
    codec, quality, _ = AUDIO_FORMAT_MAP.get(fmt, AUDIO_FORMAT_MAP["mp3"])
    if folder and not single:
        out = f"{DOWNLOAD_DIR}/{folder}/%(playlist_index)s - %(title).100s.%(ext)s"
    elif folder and single:
        out = f"{DOWNLOAD_DIR}/{folder}/%(title).100s.%(ext)s"
    else:
        out = f"{DOWNLOAD_DIR}/%(title).100s.%(ext)s"
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


#TikTok video opts 
def build_tiktok_video_opts(
    quality: str,
    progress_hook: Optional[Callable],
    extra_extractor_args: Optional[dict] = None,
    folder: Optional[str] = None,
) -> dict:
    out = (
        f"{DOWNLOAD_DIR}/{folder}/%(title).100s.%(ext)s"
        if folder else
        f"{DOWNLOAD_DIR}/%(title).100s.%(ext)s"
    )
    opts = _base_opts(progress_hook)
    opts.update({
        "outtmpl":             out,
        "format":              "bestvideo+bestaudio/best",
        "merge_output_format": "mp4",
        "remux_video":         "mp4",
    })
    if extra_extractor_args:
        opts["extractor_args"] = extra_extractor_args
    return opts


#extractor_args builder 
def _make_ea(client: str) -> dict:
    return {"youtube": {"player_client": [client], "js_runtimes": ["nodejs"]}}


#Collect finished files via postprocessor hook 
def _make_collector(collected: list):
    """Returns a postprocessor hook that records final output filenames."""
    def _pp_hook(d: dict):
        if d.get("status") == "finished" and d.get("filename"):
            rel = os.path.relpath(d["filename"], DOWNLOAD_DIR)
            if rel not in collected:
                collected.append(rel)
    return _pp_hook


#Playlist result inspector 
def _playlist_result(info: Optional[dict], mode: str, collected: list) -> dict:
    if info is None:
        return {"status": "error", "message": "yt-dlp returned no data. Content may be private, deleted, or region-locked."}

    files = collected[:]  # snapshot at result time

    if not info.get("entries"):
        return {
            "status": "done",
            "type":   mode,
            "title":  info.get("title", "video"),
            "files":  files,
        }

    entries   = list(info["entries"])
    total     = len(entries)
    succeeded = sum(1 for e in entries if e is not None)
    failed    = total - succeeded
    title     = info.get("title") or info.get("playlist_title") or "Playlist"

    if succeeded == 0:
        return {"status": "error", "message": f"All {total} items failed to download."}

    msg = f"Downloaded {succeeded}/{total}" + (f" — {failed} skipped" if failed else "")
    return {
        "status":  "done",
        "type":    mode,
        "title":   title,
        "message": msg,
        "files":   files,
    }


#YouTube download 
def download_youtube(url: str, mode: str, quality_or_fmt: str,
                     progress_hook: Optional[Callable] = None) -> dict:
    clients    = _youtube_clients()
    last_error = "Unknown error"

    for client in clients:
        try:
            ea = _make_ea(client)

            # Fast metadata probe — single vs playlist decision
            probe_opts = {
                **_base_opts(None),
                "quiet":          True,
                "noplaylist":     False,
                "playlistend":    1,
                "extractor_args": ea,
            }

            folder    = YOUTUBE_SINGLES_FOLDER
            is_single = True
            try:
                with yt_dlp.YoutubeDL(probe_opts) as ydl:
                    meta = ydl.extract_info(url, download=False)
                if meta:
                    is_playlist = bool(meta.get("entries")) or meta.get("_type") == "playlist"
                    if is_playlist:
                        is_single = False
                        folder    = build_playlist_folder(meta)
            except Exception:
                pass

            # Track counter
            track_total   = [None]
            track_current = [0]

            if not is_single:
                try:
                    flat_opts = {**probe_opts, "extract_flat": True, "playlistend": 9999}
                    with yt_dlp.YoutubeDL(flat_opts) as ydl:
                        flat = ydl.extract_info(url, download=False)
                    if flat and flat.get("entries"):
                        track_total[0] = len(list(flat["entries"]))
                except Exception:
                    pass

            original_hook = progress_hook

            def counting_hook(d: dict):
                if original_hook:
                    if track_total[0] and track_current[0]:
                        d["track_info"] = f"Track {track_current[0]} of {track_total[0]}"
                    else:
                        d.setdefault("track_info", "")
                    original_hook(d)

            collected = []

            def pp_hook(d: dict):
                # Increment only on actual file — not on every postprocessor step
                if d.get("status") == "started" and "filename" in d:
                    track_current[0] += 1
                    if original_hook and track_total[0]:
                        original_hook({
                            "status":     "downloading",
                            "percent":    0,
                            "track_info": f"Track {track_current[0]} of {track_total[0]}",
                        })
                # Collect final output path
                if d.get("status") == "finished" and d.get("filename"):
                    rel = os.path.relpath(d["filename"], DOWNLOAD_DIR)
                    if rel not in collected:
                        collected.append(rel)

            opts = (
                build_audio_opts(quality_or_fmt, counting_hook, ea, folder, single=is_single)
                if mode == "audio" else
                build_video_opts(quality_or_fmt, counting_hook, ea, folder, single=is_single)
            )
            opts["postprocessor_hooks"] = [pp_hook]

            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=True)
                return _playlist_result(info, mode, collected)

        except Exception as e:
            last_error = str(e)
            if "Unsupported URL" in last_error:
                break

    raise Exception(last_error)


#TikTok download 
def download_tiktok(url: str, mode: str, quality_or_fmt: str,
                    progress_hook: Optional[Callable] = None) -> dict:
    last_error = "Unknown error"
    for api_host in TIKTOK_API_HOSTS:
        try:
            ea = {
                "tiktok":  {"api_hostname": [api_host]},
                "youtube": {"js_runtimes": ["nodejs"]},
            }
            folder    = TIKTOK_FOLDER
            collected = []

            opts = (
                build_audio_opts(quality_or_fmt, progress_hook, ea, folder, single=True)
                if mode == "audio" else
                build_tiktok_video_opts(quality_or_fmt, progress_hook, ea, folder)
            )
            opts["postprocessor_hooks"] = [_make_collector(collected)]

            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=True)
                return _playlist_result(info, mode, collected)

        except Exception as e:
            last_error = str(e)
            if any(x in last_error for x in [
                "Unsupported URL","account is private",
                "This video is unavailable","does not exist",
            ]):
                break
    raise Exception(last_error)


#Generic video download 
def download_generic_video(url: str, mode: str, quality_or_fmt: str,
                           progress_hook: Optional[Callable] = None) -> dict:
    ea        = {"youtube": {"js_runtimes": ["nodejs"]}}
    collected = []
    opts      = (
        build_audio_opts(quality_or_fmt, progress_hook, ea, None, single=True)
        if mode == "audio" else
        build_video_opts(quality_or_fmt, progress_hook, ea, None, single=True)
    )
    opts["postprocessor_hooks"] = [_make_collector(collected)]

    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        return _playlist_result(info, mode, collected)


#Direct file download
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
    headers = {"User-Agent": "Mozilla/5.0", "Accept": "*/*", "Accept-Encoding": "identity"}
    proxies = {"http": PROXY, "https": PROXY} if PROXY else None
    r = requests.get(url, stream=True, headers=headers, timeout=60, proxies=proxies)
    r.raise_for_status()
    if "text/html" in r.headers.get("Content-Type", ""):
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
    return {"status": "done", "type": "file", "title": filename, "files": [filename]}


#Preview
def preview_url(url: str) -> dict:
    opts = {
        "quiet":         True,
        "noplaylist":    False,
        "playlistend":   1,
        "js_runtimes":   {"node": {}},
        "remote_components": {"ejs:github"},
        "extractor_args": {
            "youtube": {"player_client": ["web"], "js_runtimes": ["nodejs"]},
        },
    }
    if _has_cookies():
        opts["cookiefile"] = _cookies_path()
    if PROXY:
        opts["proxy"] = PROXY
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
    if info is None:
        raise Exception("Could not fetch preview.")
    entries = list(info.get("entries") or [])
    return {
        "title":       info.get("title") or info.get("playlist_title") or "Unknown",
        "thumbnail":   info.get("thumbnail"),
        "duration":    info.get("duration"),
        "uploader":    info.get("uploader") or info.get("channel") or "",
        "is_playlist": bool(entries),
        "count":       len(entries) if entries else 1,
        "url":         url,
    }


#Storage info 
def get_storage_info() -> dict:
    files = []
    total_bytes = 0
    try:
        for root, dirs, filenames in os.walk(DOWNLOAD_DIR):
            for name in sorted(filenames):
                path = os.path.join(root, name)
                if os.path.isfile(path):
                    size = os.path.getsize(path)
                    total_bytes += size
                    rel = os.path.relpath(path, DOWNLOAD_DIR)
                    files.append({"name": rel, "size": size})
    except Exception:
        pass
    files.sort(key=lambda f: f["name"].lower())
    disk = shutil.disk_usage(DOWNLOAD_DIR)
    return {
        "files":       files,
        "total_bytes": total_bytes,
        "disk_free":   disk.free,
        "disk_total":  disk.total,
        "folder":      DOWNLOAD_DIR,
    }


#Main entry
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
