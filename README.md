# Fetchr

A production-grade download manager for videos, audio, playlists, files, and streams.
Built with FastAPI, WebSockets, and yt-dlp.

---

## Project Structure

```
Fetchr/
├── backend/
│   ├── main.py
│   ├── downloader.py
│   ├── requirements.txt
│   └── cookies.txt          (optional, see Cookie Authentication section)
└── frontend/
    ├── index.html
    ├── css/
    │   └── styles.css
    └── js/
        └── app.js
```

---

## System Requirements

- Python 3.10 or higher
- Node.js (required for YouTube signature solving)
- ffmpeg (required for audio conversion and video remuxing)

---

## Installation

### 1. Install Python

Download from https://python.org and install.
Check "Add Python to PATH" during installation on Windows.

### 2. Install Node.js

Windows:
```
winget install OpenJS.NodeJS
```

Ubuntu / Debian:
```
sudo apt install nodejs -y
```

Close and reopen your terminal after installation. Verify:
```
node --version
```

### 3. Install ffmpeg

Windows:
```
winget install Gyan.FFmpeg
```

Ubuntu / Debian:
```
sudo apt install ffmpeg -y
```

Close and reopen your terminal after installation. Verify:
```
ffmpeg -version
```

### 4. Create a virtual environment

```
cd C:\Users\PC\Desktop\Fetchr
python -m venv .venv
```

### 5. Activate the virtual environment

Windows:
```
.venv\Scripts\activate
```

Mac / Linux:
```
source .venv/bin/activate
```

### 6. Install Python dependencies

```
cd backend
pip install "yt-dlp[default]" --upgrade
pip install -r requirements.txt
pip install curl_cffi --upgrade
```

The yt-dlp[default] install includpip install curl_cffi --upgradees the yt-dlp-ejs component which is required
alongside Node.js for YouTube signature solving. Installing plain yt-dlp without
[default] will cause all YouTube downloads to fail.

---

## Running the Server

Always activate the virtual environment first, then run from inside the backend folder:

```
cd C:\Users\PC\Desktop\Fetchr
.venv\Scripts\activate
cd backend
uvicorn main:app --reload --port 8080
```

Open your browser and navigate to:
```
http://127.0.0.1:8080
```

---

## Updating yt-dlp

yt-dlp updates frequently to keep up with platform changes. Update regularly:

```
pip install "yt-dlp[default]" --upgrade
```

---

## Cookie Authentication

On some home or residential networks YouTube requires a logged-in session to download.
This is solved by exporting your browser cookies once.

### How to export cookies

1. Open Microsoft Edge or Firefox (do not use Chrome, it locks its database)
2. Log into youtube.com
3. Install the "Get cookies.txt LOCALLY" extension from the browser extension store
4. While on youtube.com click the extension icon
5. Select Netscape format and click Export
6. Save the file as cookies.txt
7. Place cookies.txt inside the backend/ folder

The server detects the file automatically on every request.
Re-export every few weeks as cookies expire.

---

## Supported Content

- YouTube videos, playlists, and YouTube Music albums
- TikTok videos
- Vimeo, Twitter/X, Instagram, Facebook, Reddit, Rumble, Dailymotion
- HLS and DASH streams (.m3u8)
- Direct file links (PDF, ZIP, MP3, MP4, DOCX, images, and more)
- Any site supported by yt-dlp (1000+ platforms)

---

## Video Quality Options

| Option         | Description                  | Container |
|----------------|------------------------------|-----------|
| Best available | Highest quality on the source | MP4       |
| 4K             | Up to 2160p, prefers VP9/AV1 | MKV       |
| 1440p          | Up to 1440p, prefers VP9/AV1 | MKV       |
| 1080p HD       | Up to 1080p                  | MP4       |
| 720p HD        | Up to 720p                   | MP4       |

---

## Audio Format Options

| Format | Type     | Quality         |
|--------|----------|-----------------|
| MP3    | Lossy    | 320 kbps        |
| FLAC   | Lossless | Source quality  |
| WAV    | Lossless | Source quality  |
| Opus   | Lossy    | High quality    |
| AAC    | Lossy    | High quality    |
| ALAC   | Lossless | Apple Lossless  |

---

## Download Folder Structure

Files are saved to:
```
C:\Users\PC\Downloads\Fetchr\
```

Single videos and files go directly into this folder.
Playlists and albums are organised into named subfolders:

```
Fetchr/
├── WHAT HAPPENED TO THE STREETS - 21 SAVAGE/
│   ├── 01 - track name.flac
│   ├── 02 - track name.flac
│   └── ...
├── Thriller - Michael Jackson/
│   ├── 01 - track name.mp3
│   └── ...
└── single-video.mp4
```

---

## Multiple URL Downloads

Paste multiple URLs separated by commas into the input field:

```
https://first-url.com, https://second-url.com, https://third-url.com
```

Each URL is processed as a separate job with its own queue card and progress bar.

---

## Proxy Configuration

If YouTube blocks your server IP, configure a residential proxy:

Windows:
```
set FETCHR_PROXY=http://user:pass@proxy-host:port
```

Mac / Linux:
```
export FETCHR_PROXY=http://user:pass@proxy-host:port
```

Set this before starting the server. The proxy applies to all downloads automatically.
Residential proxies are available from providers such as Webshare.io starting at $5/month.

---

## Deploying to a Production Server (Ubuntu / Debian)

### Install system dependencies

```
sudo apt update
sudo apt install python3 python3-pip python3-venv nodejs ffmpeg -y
```

### Set up the project

```
cd /var/www/fetchr
python3 -m venv .venv
source .venv/bin/activate
cd backend
pip install "yt-dlp[default]" --upgrade
pip install -r requirements.txt
pip install curl_cffi --upgrade
```

### Run with gunicorn

```
pip install gunicorn
gunicorn main:app -w 4 -k uvicorn.workers.UvicornWorker --bind 0.0.0.0:8080
```

### Run as a systemd service

Create the file /etc/systemd/system/fetchr.service:

```
[Unit]
Description=Fetchr Download Manager
After=network.target

[Service]
User=www-data
WorkingDirectory=/var/www/fetchr/backend
Environment="FETCHR_PROXY=http://user:pass@proxy-host:port"
ExecStart=/var/www/fetchr/.venv/bin/gunicorn main:app -w 4 -k uvicorn.workers.UvicornWorker --bind 0.0.0.0:8080
Restart=always

[Install]
WantedBy=multi-user.target
```

Enable and start:
```
sudo systemctl daemon-reload
sudo systemctl enable fetchr
sudo systemctl start fetchr
sudo systemctl status fetchr
```

### nginx reverse proxy with WebSocket support

```
server {
    listen 80;
    server_name yourdomain.com;

    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }

    location /ws/ {
        proxy_pass http://127.0.0.1:8080;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
    }
}
```

---

## Rate Limiting

Each IP address is limited to 10 downloads per hour.
Exceeding this returns HTTP 429 with a clear message.

---

## API Endpoints

| Method    | Endpoint        | Description                              |
|-----------|-----------------|------------------------------------------|
| GET       | /               | Serves the frontend                      |
| GET       | /health         | Server health check                      |
| POST      | /download       | Start a download job                     |
| GET       | /job/{job_id}   | Poll job status                          |
| WebSocket | /ws/{job_id}    | Real-time job progress stream            |
| GET       | /files          | List all downloaded files                |

### POST /download request body

```json
{
  "url": "https://...",
  "mode": "video",
  "quality": "1080p",
  "audio_fmt": "mp3"
}
```

Valid values:
- mode: video, audio
- quality: best, 4k, 1440p, 1080p, 720p
- audio_fmt: mp3, flac, wav, opus, aac, alac

Multiple URLs can be separated by commas in the url field.

---

## Troubleshooting

### Signature solving failed / No JavaScript runtime

Verify Node.js is installed:
```
node --version
```

Reinstall yt-dlp with the required extras:
```
pip install "yt-dlp[default]" --upgrade
```

### ffmpeg not found

Verify ffmpeg is on your PATH:
```
ffmpeg -version
```

If not found after installation, close the terminal completely and reopen it.

### Could not copy Chrome cookie database / DPAPI error

Chrome locks its cookie database while running and Windows encrypts it with DPAPI.
Use Edge or Firefox to export cookies.txt instead.

### All playlist items failed to download

Your IP is being flagged by YouTube. Solutions in order:
1. Export cookies.txt from a logged-in Edge or Firefox session
2. Connect to a VPN (ProtonVPN has a free tier)
3. Set FETCHR_PROXY with a residential proxy

### WebSocket 404 or upgrade error

Make sure you installed uvicorn with the standard extras:
```
pip install "uvicorn[standard]"
```

This is included in requirements.txt but verify it is installed in your active venv.

### TikTok video not available

The video may be deleted, private, or region-restricted.
Test with a different public TikTok video to confirm the extractor is working.

---

## License

MIT