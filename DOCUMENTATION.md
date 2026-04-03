# Fetchr — Project Documentation

Version 1.0.0

---

## Table of Contents

1. Overview
2. Architecture
3. Tech Stack
4. Project Structure
5. Backend
   - main.py
   - downloader.py
   - Configuration
   - API Reference
6. Frontend
   - index.html
   - styles.css
   - app.js
7. Data Flow
8. Format and Quality System
9. Job and Queue System
10. Playlist and Album Handling
11. Error Handling
12. Security
13. Deployment

---

## 1. Overview

Fetchr is a self-hosted download manager that accepts any URL and downloads
the content to the server's local filesystem. It supports video, audio, direct
files, HLS/DASH streams, playlists, and albums from over 1000 platforms.

Users interact through a web interface that works on desktop, tablet, and mobile
without any app installation. The server does all downloading and processing.
Users receive real-time progress updates via WebSocket.

Core capabilities:
- Video downloads with quality selection from 720p to 4K
- Audio extraction and conversion to MP3, FLAC, WAV, Opus, AAC, or ALAC
- Full playlist and album downloads organized into named subfolders
- Multiple simultaneous downloads via an async job queue
- Real-time progress over WebSocket with automatic polling fallback
- Per-IP rate limiting
- Optional cookie-based authentication for age-restricted or bot-protected content

---

## 2. Architecture

```
Browser (any device)
        |
        | HTTP + WebSocket
        v
FastAPI Server (uvicorn)
        |
        |--- Static files (frontend HTML/CSS/JS)
        |--- POST /download  -> creates job, runs in thread pool
        |--- GET  /job/{id}  -> job status polling
        |--- WS   /ws/{id}   -> real-time progress stream
        |--- GET  /files     -> list downloaded files
        |
        v
Thread Pool (10 workers)
        |
        v
downloader.py
        |
        |--- yt-dlp (videos, audio, streams, playlists)
        |       |--- YouTube client rotation
        |       |--- TikTok API hostname rotation
        |       |--- ffmpeg (merge/remux/convert)
        |       |--- Node.js (YouTube signature solving)
        |
        |--- requests (direct file downloads)
        |
        v
Local filesystem (Downloads/Fetchr/)
```

The frontend uses relative URLs throughout so it runs identically on localhost
and any production domain without code changes.

---

## 3. Tech Stack

### Backend
- Python 3.10+
- FastAPI — async web framework and API
- uvicorn[standard] — ASGI server with WebSocket support
- yt-dlp[default] — download engine with yt-dlp-ejs for YouTube signature solving
- requests — direct HTTP file downloads
- BeautifulSoup4 — webpage title extraction
- Node.js — JavaScript runtime for YouTube challenge solving (external dependency)
- ffmpeg — audio conversion, video remuxing, stream merging (external dependency)

### Frontend
- Vanilla HTML5, CSS3, JavaScript (no frameworks, no build step)
- IBM Plex Mono and Syne fonts via Google Fonts
- WebSocket API for real-time progress
- localStorage for download history persistence

---

## 4. Project Structure

```
Fetchr/
├── README.md
├── DOCUMENTATION.md
├── backend/
│   ├── main.py              FastAPI application, routes, job management
│   ├── downloader.py        Download engine, format logic, folder naming
│   ├── requirements.txt     Python dependencies
│   └── cookies.txt          Optional YouTube auth cookies (user-provided)
└── frontend/
    ├── index.html           App shell, navigation, format selector UI
    ├── css/
    │   └── styles.css       Full design system, layout, components
    └── js/
        └── app.js           State management, WebSocket, format selection, UI logic
```

---

## 5. Backend

### main.py

The FastAPI application. Responsibilities:

- Serves the frontend static files
- Accepts download requests, validates input, applies rate limiting
- Creates job records and dispatches download tasks to the thread pool
- Streams job progress over WebSocket
- Exposes job polling endpoint as a fallback for environments where WebSocket
  is blocked by a proxy

#### Key components

**Job store**

Jobs are stored in a module-level dict:

```python
jobs: Dict[str, dict] = {}
```

Each job record:

```python
{
    "status":     "queued" | "downloading" | "done" | "error",
    "title":      str,
    "percent":    float,
    "message":    str,
    "created_at": float,
    "url":        str,
    "mode":       "video" | "audio",
}
```

Jobs older than 1 hour are cleaned up on each new request.

For multi-instance deployments, replace the dict with Redis.

**Rate limiting**

Each IP is allowed 10 downloads per hour. Tracked in memory with timestamps.
Returns HTTP 429 on breach. Replace with Redis for multi-instance.

**Thread pool**

Downloads run in a ThreadPoolExecutor with 10 workers so the event loop
stays unblocked. Each download job runs in its own thread.

**WebSocket handler**

Polls the job dict every 500ms and sends the current state to the connected
client. Handles disconnect gracefully. Falls silently if the connection is
already closed.

---

### downloader.py

The download engine. Responsibilities:

- URL type detection (video platform, direct file, unknown)
- Folder name resolution for playlists and albums
- yt-dlp options construction for video and audio modes
- Platform-specific download handlers (YouTube, TikTok, generic)
- Direct file download via requests
- Honest result reporting including partial playlist completions

#### URL Type Detection

```
detect_type(url)
    |
    |-- extension in DIRECT_EXTS?  -> "file"
    |-- domain in VIDEO_DOMAINS?   -> "video"
    |-- path hint in url?          -> "video"
    |-- else                       -> "probe"

probe_url(url)  [HEAD request]
    |
    |-- Content-Disposition has filename? -> "file"
    |-- Content-Type is text/html?        -> "ydlp"
    |-- Content-Type is binary?           -> "file"
    |-- else                              -> "ydlp"
```

#### Folder Name Resolution

Before downloading a playlist, resolve_folder() does a fast metadata-only
fetch (playlistend=1, no download) to get the playlist title and artist name.

The folder name is constructed as:
```
clean_folder_name(title) + " - " + artist
```

clean_folder_name() strips prefixes YouTube Music adds such as "Album - ",
"Playlist - ", "EP - ".

safe_folder() removes characters that are illegal in filesystem paths on
Windows, Mac, and Linux.

Single video downloads bypass folder creation and go directly to DOWNLOAD_DIR.

#### yt-dlp Options

**Video opts:**
- format: quality-specific format string with height ceiling
- merge_output_format: mp4 or mkv depending on quality
- remux_video: forces container even on pre-merged files
- js_runtimes: {"node": {}} — uses Node.js for signature solving
- remote_components: {"ejs": "github"} — fetches challenge solver on first run

**Audio opts:**
- format: bestaudio/best — always takes the highest quality source
- FFmpegExtractAudio postprocessor with the selected codec
- FFmpegMetadata postprocessor embeds title and artist tags

**Shared opts for all downloads:**
- ignoreerrors: True — skips failed items in playlists, continues rest
- retries: 10 with exponential backoff
- fragment_retries: 10 for broken HLS/DASH streams
- socket_timeout: 30 seconds per operation

#### YouTube Client Rotation

Tries clients in order until one succeeds:
```python
YOUTUBE_CLIENTS = ["android", "ios", "tv_embedded", "web_creator"]
```

Each client uses a different API path. If one is blocked or requires a token
the next is tried. The js_runtimes config applies to all clients.

#### TikTok API Hostname Rotation

TikTok serves video data from regional API endpoints. Rotating through them
reduces "Unable to extract webpage video data" errors:

```python
TIKTOK_API_HOSTS = [
    "api22-normal-c-useast2a.tiktokv.com",
    "api19-normal-c-useast1a.tiktokv.com",
    "api16-normal-c-useast1a.tiktokv.com",
    "api16-normal-useast5.tiktokv.com",
]
```

#### Playlist Result Inspection

After a playlist download, _playlist_result() inspects the info dict:

- yt-dlp sets failed entries to None in the entries list
- Counts succeeded vs failed
- Returns an honest message: "Downloaded 18/20 — 2 skipped"
- Returns error only if all items failed

---

### Configuration

**Environment variables:**

| Variable       | Description                              | Default |
|----------------|------------------------------------------|---------|
| FETCHR_PROXY   | Proxy URL for all downloads              | None    |

Proxy format:
```
http://user:pass@proxy-host:port
socks5://user:pass@proxy-host:port
```

**cookies.txt:**

Place a Netscape-format cookies file at backend/cookies.txt.
The server checks for its existence on every download and uses it if present.
If absent, downloads proceed without cookies.

**Download directory:**

```python
DOWNLOAD_DIR = Path.home() / "Downloads" / "Fetchr"
```

On Windows: C:\Users\USERNAME\Downloads\Fetchr
On Linux:   /home/username/Downloads/Fetchr

---

### API Reference

#### GET /health

Returns server status. Used by the frontend to show the online indicator.

Response:
```json
{ "status": "ok" }
```

---

#### POST /download

Start one or more download jobs.

Request body:
```json
{
  "url":       "https://... or comma-separated URLs",
  "mode":      "video",
  "quality":   "1080p",
  "audio_fmt": "mp3"
}
```

| Field     | Type   | Values                              | Default |
|-----------|--------|-------------------------------------|---------|
| url       | string | Any URL or comma-separated list     |         |
| mode      | string | video, audio                        | video   |
| quality   | string | best, 4k, 1440p, 1080p, 720p        | best    |
| audio_fmt | string | mp3, flac, wav, opus, aac, alac     | mp3     |

Response:
```json
{
  "job_ids": ["uuid1", "uuid2"],
  "count": 2
}
```

Errors:
- 400 Bad Request — invalid URL
- 429 Too Many Requests — rate limit exceeded

---

#### GET /job/{job_id}

Poll a job for its current state.

Response:
```json
{
  "status":   "downloading",
  "title":    "Song Title",
  "percent":  47.3,
  "message":  "23.1 MB / 48.9 MB",
  "url":      "https://...",
  "mode":     "audio",
  "type":     "audio"
}
```

Status values: queued, downloading, done, error

---

#### WebSocket /ws/{job_id}

Connect after receiving a job_id. The server sends the full job dict every 500ms
until status is done or error, then closes.

The frontend automatically falls back to polling /job/{job_id} every 800ms if
the WebSocket connection fails (common behind reverse proxies that do not support
WebSocket upgrades without explicit configuration).

---

#### GET /files

List all files in the download directory.

Response:
```json
{
  "files": ["filename.mp3", "filename.mp4"],
  "folder": "C:\\Users\\PC\\Downloads\\Fetchr"
}
```

---

## 6. Frontend

### index.html

Single-page application shell. Contains three views toggled by JavaScript:

- Download view — URL input, format selector, status panel, recent history
- Queue view — active and recently completed download jobs
- Files view — searchable table of all files in the download folder

No inline scripts or styles. All logic is in app.js and styles.css.

---

### styles.css

Full design system using CSS custom properties.

**Color palette:**

| Variable      | Value              | Usage                    |
|---------------|--------------------|--------------------------|
| --bg          | #0c0c0e            | Page background          |
| --bg-2        | #111116            | Cards and sidebar        |
| --bg-3        | #18181f            | Input backgrounds        |
| --accent      | #e8ff47            | Primary accent, buttons  |
| --blue        | #4fa8ff            | Progress, video mode     |
| --purple      | #b47fff            | Audio mode               |
| --green       | #3dffa0            | Success states           |
| --red         | #ff4f4f            | Error states             |

**Typography:**
- Syne — headings, navigation, buttons
- IBM Plex Mono — URLs, metadata, code, file names

**Responsive breakpoint:** 680px
Below this width the sidebar collapses to a top navigation bar and
single-column layout applies.

---

### app.js

State management and UI logic.

**Application state:**

```javascript
const S = {
  history:      [],       // persisted to localStorage
  allFiles:     [],       // from /files endpoint
  socket:       null,     // active WebSocket
  downloading:  false,    // download button lock
  mode:         'video',  // sent to backend
  quality:      '1080p',  // sent to backend
  audioFmt:     'mp3',    // sent to backend
};
```

**Format selector:**

Each format option has a unique element ID. Direct onclick handlers are attached
to each button individually — no event delegation — to avoid click target
ambiguity from child elements.

setMode(), setVideoQuality(), and setAudioFmt() each update state and toggle
the active class on the correct button using classList.toggle with a boolean
parameter, ensuring exactly one button is active at a time.

**Download flow:**

```
startDownload()
    |
    |-- Validate URL(s)
    |-- POST /download with mode, quality, audio_fmt
    |-- Receive job_ids array
    |-- For each job_id:
    |       addToQueue(url)
    |       connectWS(jobId, url, queueId)  [staggered by 200ms]
    |
connectWS()
    |
    |-- new WebSocket(ws://host/ws/{jobId})
    |-- onmessage -> handleJobUpdate()
    |-- onerror   -> poll() fallback
    |
onJobUpdate(job)
    |
    |-- downloading -> update status panel and queue card
    |-- done        -> mark complete, add to history, re-enable button
    |-- error       -> mark failed, add to history, re-enable button
```

**History:**

Last 50 downloads stored in localStorage under the key fetchr_history.
Rendered as a list of the 8 most recent entries in the Download view.
Cleared via the "Clear all" button.

**Server health:**

Polls /health every 20 seconds. Updates the sidebar status dot.
Uses AbortSignal.timeout(3000) to detect an unresponsive server quickly.

---

## 7. Data Flow

### Single video download

```
User pastes URL, selects 1080p MP4, clicks Download
    |
POST /download { url, mode: "video", quality: "1080p", audio_fmt: "mp3" }
    |
main.py creates job, dispatches _run_download to thread pool
    |
Returns { job_ids: ["abc-123"] }
    |
Frontend connects WebSocket ws://host/ws/abc-123
    |
_run_download calls downloader.download()
    |
detect_type() -> "video"
resolve_folder() -> "My Video" (single video, no subfolder)
build_video_opts("1080p") -> yt-dlp opts with format string
download_youtube() tries android client
    |
yt-dlp downloads, progress_hook fires every chunk
    |
jobs["abc-123"]["percent"] updates
    |
WebSocket sends updated job every 500ms
    |
Frontend updates progress bar
    |
Download complete -> jobs["abc-123"]["status"] = "done"
    |
WebSocket sends final state, closes
Frontend shows success, adds to history
```

### Playlist / album download

```
User pastes YouTube Music album URL, selects FLAC, clicks Download
    |
POST /download { url, mode: "audio", audio_fmt: "flac" }
    |
_run_download -> download_youtube()
    |
resolve_folder() does metadata probe (playlistend=1, no download)
    gets playlist_title: "WHAT HAPPENED TO THE STREETS"
    gets channel: "21 SAVAGE"
    returns folder: "WHAT HAPPENED TO THE STREETS - 21 SAVAGE"
    |
build_audio_opts("flac", folder="WHAT HAPPENED TO THE STREETS - 21 SAVAGE")
    outtmpl: Downloads/Fetchr/WHAT HAPPENED TO THE STREETS - 21 SAVAGE/%(playlist_index)s - %(title).100s.%(ext)s
    |
yt-dlp downloads all tracks, skips unavailable/age-restricted
progress_hook fires per track
    |
_playlist_result() inspects entries, counts None entries
returns "Downloaded 14/14" or "Downloaded 13/14 — 1 skipped"
    |
Frontend shows result message
```

---

## 8. Format and Quality System

### Video format strings

```python
VIDEO_FORMAT_MAP = {
    "best":  "bestvideo+bestaudio/best",
    "4k":    "bestvideo[height<=2160][vcodec^=vp9]+bestaudio/...",
    "1440p": "bestvideo[height<=1440][vcodec^=vp9]+bestaudio/...",
    "1080p": "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/...",
    "720p":  "bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]/...",
}
```

4K and 1440p prefer VP9 and AV1 codecs which produce smaller files at the same
quality. These codecs cannot be stored in MP4 so the container is MKV.
1080p and 720p prefer MP4+M4A for maximum device compatibility.

remux_video forces the final container even if yt-dlp finds a pre-merged file,
preventing format mismatches.

### Audio codec config

```python
AUDIO_FORMAT_MAP = {
    "mp3":  ("mp3",  "320", "mp3"),
    "flac": ("flac", "0",   "flac"),
    "wav":  ("wav",  "0",   "wav"),
    "opus": ("opus", "0",   "opus"),
    "aac":  ("aac",  "0",   "m4a"),
    "alac": ("alac", "0",   "m4a"),
}
```

Quality "0" for lossless formats means best/lossless. For MP3 it means 320kbps.
ALAC uses the Apple Lossless codec in an M4A container, distinct from AAC.
All audio downloads use bestaudio/best as the source to get the highest
available bitrate before conversion.

---

## 9. Job and Queue System

Each download request creates one job per URL. Jobs are stored in memory with
a UUID key. The thread pool runs up to 10 jobs concurrently.

The frontend creates one queue card per job immediately on receiving the
job_ids response. Each card shows the URL, current status, a progress message,
and an animated progress bar.

Queue cards auto-remove 5 seconds after completion or failure.
If the queue is then empty the empty state is restored.

For multi-instance production deployments where the job dict would not be shared
between processes, replace the in-memory dict and rate_store with Redis.

---

## 10. Playlist and Album Handling

ignoreerrors: True means yt-dlp continues to the next track if any track fails.
Tracks fail silently and yt-dlp sets their entry to None in the result.

_playlist_result() counts None entries and reports partial completions honestly
rather than returning a generic "done" status.

skip_unavailable_fragments: True handles HLS and DASH streams where individual
segments may be missing or return 404.

Retry behaviour:
- retries: 10 — each file retried up to 10 times
- fragment_retries: 10 — each stream segment retried up to 10 times
- retry_sleep_functions use exponential backoff: 1s, 2s, 4s, 8s, 16s...
- socket_timeout: 30 — connection attempts time out after 30 seconds
- file_access_retries: 5 — filesystem write retries

Playlist track numbering uses %(playlist_index)s in the output template so
tracks stay in album order regardless of download speed variation.

---

## 11. Error Handling

### Backend errors

All download functions catch exceptions and return a dict with status "error"
and a message. They never raise to the caller except in download_generic_video
which is wrapped by the main download() function.

The job runner (_run_download) wraps the entire call in try/except so a
completely unexpected error still sets the job to error status rather than
leaving it stuck at downloading.

### Frontend errors

The download button is disabled during downloads to prevent duplicate requests.
It is re-enabled on both success and error.

WebSocket errors silently fall back to polling so users are never blocked by
proxy or firewall issues.

"Job not found" responses from the WebSocket or polling endpoint are ignored
silently — this occurs when the WebSocket closes cleanly after a completed
job and the last poll arrives a fraction of a second later.

HTTP 429 from the rate limiter shows a user-readable message.
HTTP 400 for invalid URLs shows the validation message from the server.

---

## 12. Security

**Rate limiting:** 10 downloads per IP per hour prevents abuse.

**URL validation:** Only http:// and https:// URLs are accepted. The server
never executes the URL as a shell command — it is passed directly to the
yt-dlp Python API.

**HTML injection prevention:** All user-provided strings rendered in the
frontend pass through the esc() function which escapes &, <, >, and ".

**No file execution:** Downloaded files are written to disk only. The server
never opens or executes downloaded content.

**cookies.txt:** The file is read-only by the application. Store it with
appropriate filesystem permissions on the server (chmod 600).

**CORS:** Currently set to allow all origins for development convenience.
For production restrict this to your actual frontend domain in main.py:

```python
allow_origins=["https://yourdomain.com"]
```

**Static file serving:** The StaticFiles mount is added last after all API
routes to prevent it from intercepting API requests.

---

## 13. Deployment

### Minimum server requirements

- 1 CPU core
- 512 MB RAM (1 GB recommended for concurrent downloads)
- 10 GB disk space for downloads (scale as needed)
- Ubuntu 22.04 LTS or Debian 12 recommended

### Full deployment steps

Install system dependencies:
```
sudo apt update
sudo apt install python3 python3-pip python3-venv nodejs ffmpeg nginx -y
```

Create application directory:
```
sudo mkdir -p /var/www/fetchr
sudo chown $USER:$USER /var/www/fetchr
```

Upload project files to /var/www/fetchr and set up the environment:
```
cd /var/www/fetchr
python3 -m venv .venv
source .venv/bin/activate
cd backend
pip install "yt-dlp[default]" --upgrade
pip install -r requirements.txt
pip install gunicorn
```

Create systemd service at /etc/systemd/system/fetchr.service:
```
[Unit]
Description=Fetchr Download Manager
After=network.target

[Service]
User=www-data
WorkingDirectory=/var/www/fetchr/backend
Environment="FETCHR_PROXY=http://user:pass@proxy-host:port"
ExecStart=/var/www/fetchr/.venv/bin/gunicorn main:app -w 4 -k uvicorn.workers.UvicornWorker --bind 127.0.0.1:8080
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
```

Enable and start the service:
```
sudo systemctl daemon-reload
sudo systemctl enable fetchr
sudo systemctl start fetchr
sudo systemctl status fetchr
```

Configure nginx at /etc/nginx/sites-available/fetchr:
```
server {
    listen 80;
    server_name yourdomain.com;

    client_max_body_size 10M;

    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    }

    location /ws/ {
        proxy_pass http://127.0.0.1:8080;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_read_timeout 86400;
    }
}
```

Enable the site:
```
sudo ln -s /etc/nginx/sites-available/fetchr /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl reload nginx
```

Add HTTPS with Certbot:
```
sudo apt install certbot python3-certbot-nginx -y
sudo certbot --nginx -d yourdomain.com
```

View application logs:
```
sudo journalctl -u fetchr -f
```

Restart after code updates:
```
sudo systemctl restart fetchr
```

Update yt-dlp on the server:
```
cd /var/www/fetchr
source .venv/bin/activate
pip install "yt-dlp[default]" --upgrade
sudo systemctl restart fetchr
```
