/* Fetchr — app.js */

const WS_PROTO = location.protocol === 'https:' ? 'wss' : 'ws';
const WS_BASE  = `${WS_PROTO}://${location.host}`;

const S = {
  history:      JSON.parse(localStorage.getItem('fetchr_history') || '[]'),
  presets:      JSON.parse(localStorage.getItem('fetchr_presets') || '{}'),
  storageData:  [],
  socket:       null,
  downloading:  false,
  mode:         'video',
  quality:      '1080p',
  audioFmt:     'mp3',
  notifEnabled: false,
};

const el  = (id) => document.getElementById(id);
const els = {
  urlInput:          el('urlInput'),
  clearBtn:          el('clearBtn'),
  inspectBtn:        el('inspectBtn'),
  downloadBtn:       el('downloadBtn'),
  statusPanel:       el('statusPanel'),
  statusIconWrap:    el('statusIconWrap'),
  statusTitle:       el('statusTitle'),
  statusDetail:      el('statusDetail'),
  progressWrap:      el('progressWrap'),
  progressBar:       el('progressBar'),
  progressPct:       el('progressPct'),
  speedBadge:        el('speedBadge'),
  previewPanel:      el('previewPanel'),
  previewThumb:      el('previewThumb'),
  previewTitle:      el('previewTitle'),
  previewMeta:       el('previewMeta'),
  previewClose:      el('previewClose'),
  recentList:        el('recentList'),
  clearHistoryBtn:   el('clearHistoryBtn'),
  exportCsvBtn:      el('exportCsvBtn'),
  exportJsonBtn:     el('exportJsonBtn'),
  queueList:         el('queueList'),
  filesTableBody:    el('filesTableBody'),
  folderPath:        el('folderPath'),
  fileSearch:        el('fileSearch'),
  fileCount:         el('fileCount'),
  refreshBtn:        el('refreshBtn'),
  storageBarFill:    el('storageBarFill'),
  storageLabel:      el('storageLabel'),
  scheduledList:     el('scheduledList'),
  refreshScheduledBtn: el('refreshScheduledBtn'),
  scheduleInput:     el('scheduleInput'),
  scheduleBtn:       el('scheduleBtn'),
  clearScheduleBtn:  el('clearScheduleBtn'),
  summaryText:       el('summaryText'),
  serverDot:         el('serverDot'),
  serverLabel:       el('serverLabel'),
  updateBtn:         el('updateBtn'),
  notifBtn:          el('notifBtn'),
  presetSelect:      el('presetSelect'),
  savePresetBtn:     el('savePresetBtn'),
  deletePresetBtn:   el('deletePresetBtn'),
  blockVideo:        el('blockVideo'),
  blockAudio:        el('blockAudio'),
};

const ICONS = {
  idle:    `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><circle cx="12" cy="12" r="10"/><path d="M12 8v4l3 3"/></svg>`,
  loading: `<svg class="spinning" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M21 12a9 9 0 1 1-6.219-8.56"/></svg>`,
  done:    `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6 9 17l-5-5"/></svg>`,
  error:   `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="12" cy="12" r="10"/><path d="M12 8v4M12 16h.01"/></svg>`,
  video:   `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="7" width="15" height="10" rx="2"/><path d="m17 9 5-2v10l-5-2V9z"/></svg>`,
  audio:   `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M9 18V5l12-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="18" cy="16" r="3"/></svg>`,
  file:    `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>`,
};

const V_LABEL = { best:'Best available','4k':'4K','1440p':'1440p','1080p':'1080p HD','720p':'720p HD' };
const V_CONT  = { best:'MP4','4k':'MKV','1440p':'MKV','1080p':'MP4','720p':'MP4' };
const A_LABEL = { mp3:'MP3 · 320kbps',flac:'FLAC · Lossless',wav:'WAV · Lossless',opus:'Opus · HQ',aac:'AAC · HQ',alac:'ALAC · Apple Lossless' };

function esc(s) {
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}
function fmtBytes(b) {
  if (b < 1024)    return `${b} B`;
  if (b < 1024**2) return `${(b/1024).toFixed(1)} KB`;
  if (b < 1024**3) return `${(b/1024**2).toFixed(1)} MB`;
  return `${(b/1024**3).toFixed(2)} GB`;
}
function fmtSpeed(bps) {
  if (!bps || bps <= 0) return '';
  return `${fmtBytes(bps)}/s`;
}
function fmtDuration(secs) {
  if (!secs) return '';
  const h = Math.floor(secs/3600), m = Math.floor((secs%3600)/60), s = Math.floor(secs%60);
  if (h) return `${h}:${String(m).padStart(2,'0')}:${String(s).padStart(2,'0')}`;
  return `${m}:${String(s).padStart(2,'0')}`;
}

// ── Detect if a URL is likely a playlist ──────────────────────────────────────
function isPlaylistUrl(url) {
  const u = url.toLowerCase();
  return (
    u.includes('playlist?list=')          ||
    u.includes('/playlist/')              ||
    u.includes('music.youtube.com/playlist') ||
    // YouTube mix/auto-playlist but NOT a simple watch+list (single video)
    (u.includes('youtube') && u.includes('list=') && !u.includes('watch?v=') && !u.includes('youtu.be/'))
  );
}

// ── Navigation ─────────────────────────────────────────────────────────────────
function initNav() {
  document.querySelectorAll('.nav-item').forEach((btn) => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('.nav-item').forEach((b) => b.classList.remove('active'));
      document.querySelectorAll('.view').forEach((v) => v.classList.remove('active'));
      btn.classList.add('active');
      el(`view-${btn.dataset.view}`)?.classList.add('active');
      if (btn.dataset.view === 'files')     loadFiles();
      if (btn.dataset.view === 'scheduled') loadScheduled();
    });
  });
}

// ── Format selector ────────────────────────────────────────────────────────────
function setMode(mode) {
  S.mode = mode;
  el('modeVideo').classList.toggle('active', mode === 'video');
  el('modeAudio').classList.toggle('active', mode === 'audio');
  els.blockVideo.classList.toggle('hidden', mode !== 'video');
  els.blockAudio.classList.toggle('hidden', mode !== 'audio');
  updateSummary();
}
function setVideoQuality(val) {
  S.quality = val;
  ['best','4k','1440p','1080p','720p'].forEach((v) => el(`q-${v}`)?.classList.toggle('active', v === val));
  updateSummary();
}
function setAudioFmt(fmt) {
  S.audioFmt = fmt;
  ['mp3','flac','wav','opus','aac','alac'].forEach((f) => el(`a-${f}`)?.classList.toggle('active', f === fmt));
  updateSummary();
}
function updateSummary() {
  if (!els.summaryText) return;
  els.summaryText.textContent = S.mode === 'video'
    ? `Video · ${V_LABEL[S.quality]||S.quality} · ${V_CONT[S.quality]||'MP4'}`
    : `Audio · ${A_LABEL[S.audioFmt]||S.audioFmt.toUpperCase()}`;
}
function initFormatSelector() {
  el('modeVideo').onclick = () => setMode('video');
  el('modeAudio').onclick = () => setMode('audio');
  el('q-best').onclick    = () => setVideoQuality('best');
  el('q-4k').onclick      = () => setVideoQuality('4k');
  el('q-1440p').onclick   = () => setVideoQuality('1440p');
  el('q-1080p').onclick   = () => setVideoQuality('1080p');
  el('q-720p').onclick    = () => setVideoQuality('720p');
  el('a-mp3').onclick     = () => setAudioFmt('mp3');
  el('a-flac').onclick    = () => setAudioFmt('flac');
  el('a-wav').onclick     = () => setAudioFmt('wav');
  el('a-opus').onclick    = () => setAudioFmt('opus');
  el('a-aac').onclick     = () => setAudioFmt('aac');
  el('a-alac').onclick    = () => setAudioFmt('alac');
  updateSummary();
}

// ── Presets ────────────────────────────────────────────────────────────────────
function renderPresets() {
  const sel = els.presetSelect;
  sel.innerHTML = '<option value="">Load preset...</option>';
  Object.keys(S.presets).forEach((name) => {
    const opt = document.createElement('option');
    opt.value = name; opt.textContent = name;
    sel.appendChild(opt);
  });
}
function initPresets() {
  renderPresets();
  els.savePresetBtn.onclick = () => {
    const name = prompt('Preset name:'); if (!name) return;
    S.presets[name] = { mode: S.mode, quality: S.quality, audioFmt: S.audioFmt };
    localStorage.setItem('fetchr_presets', JSON.stringify(S.presets));
    renderPresets(); els.presetSelect.value = name;
  };
  els.deletePresetBtn.onclick = () => {
    const name = els.presetSelect.value; if (!name) return;
    delete S.presets[name];
    localStorage.setItem('fetchr_presets', JSON.stringify(S.presets));
    renderPresets();
  };
  els.presetSelect.onchange = () => {
    const name = els.presetSelect.value; if (!name || !S.presets[name]) return;
    const p = S.presets[name];
    setMode(p.mode);
    if (p.mode === 'video') setVideoQuality(p.quality); else setAudioFmt(p.audioFmt);
  };
}

// ── Notifications ──────────────────────────────────────────────────────────────
function initNotifications() {
  updateNotifBtn();
  els.notifBtn.onclick = async () => {
    if (!('Notification' in window)) { alert('Browser does not support notifications.'); return; }
    if (Notification.permission === 'granted') { S.notifEnabled = !S.notifEnabled; }
    else if (Notification.permission !== 'denied') {
      S.notifEnabled = (await Notification.requestPermission()) === 'granted';
    }
    updateNotifBtn();
  };
}
function updateNotifBtn() {
  const on = S.notifEnabled && Notification.permission === 'granted';
  els.notifBtn.classList.toggle('notif-on', on);
  els.notifBtn.classList.toggle('notif-off', !on);
}
function sendNotification(title, body) {
  if (!S.notifEnabled || Notification.permission !== 'granted') return;
  new Notification(`Fetchr: ${title}`, { body });
}

// ── Preview ────────────────────────────────────────────────────────────────────
function initPreview() {
  els.inspectBtn.onclick = async () => {
    const url = els.urlInput.value.trim(); if (!url) return;
    els.inspectBtn.disabled = true;
    try {
      const res  = await fetch(`/preview?url=${encodeURIComponent(url)}`);
      if (!res.ok) throw new Error('Could not fetch preview');
      const data = await res.json();
      els.previewThumb.src         = data.thumbnail || '';
      els.previewTitle.textContent = data.title || url;
      const parts = [];
      if (data.uploader)    parts.push(data.uploader);
      if (data.duration)    parts.push(fmtDuration(data.duration));
      if (data.is_playlist) parts.push(`${data.count} tracks`);
      els.previewMeta.textContent = parts.join(' · ');
      els.previewPanel.classList.remove('hidden');
    } catch { els.previewPanel.classList.add('hidden'); }
    finally  { els.inspectBtn.disabled = false; }
  };
  els.previewClose.onclick = () => els.previewPanel.classList.add('hidden');
}

// ── yt-dlp update ──────────────────────────────────────────────────────────────
function initUpdateBtn() {
  els.updateBtn.onclick = async () => {
    els.updateBtn.disabled = true; els.updateBtn.textContent = 'Updating...';
    try {
      const data = await fetch('/update-ytdlp', { method: 'POST' }).then((r) => r.json());
      els.updateBtn.textContent = data.status === 'ok' ? 'Updated!' : 'Failed';
    } catch { els.updateBtn.textContent = 'Error'; }
    setTimeout(() => {
      els.updateBtn.innerHTML = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M23 4v6h-6"/><path d="M1 20v-6h6"/><path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/></svg> Update yt-dlp`;
      els.updateBtn.disabled = false;
    }, 3000);
  };
}

// ── Schedule ───────────────────────────────────────────────────────────────────
function initSchedule() {
  els.scheduleBtn.onclick = async () => {
    const url = els.urlInput.value.trim(), dt = els.scheduleInput.value;
    if (!url || !dt) { alert('Enter a URL and pick a date/time.'); return; }
    const runAt = new Date(dt).getTime() / 1000;
    if (runAt <= Date.now() / 1000) { alert('Time must be in the future.'); return; }
    const res = await fetch('/schedule', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url, run_at: runAt, mode: S.mode, quality: S.quality, audio_fmt: S.audioFmt }),
    });
    if (res.ok) { els.scheduleInput.value = ''; alert(`Scheduled for ${new Date(runAt*1000).toLocaleString()}`); loadScheduled(); }
    else { const e = await res.json().catch(() => ({})); alert(e.detail || 'Failed to schedule.'); }
  };
  els.clearScheduleBtn.onclick = () => { els.scheduleInput.value = ''; };
}
async function loadScheduled() {
  try {
    const data = await fetch('/scheduled').then((r) => r.json());
    renderScheduled(data.scheduled || {});
  } catch { els.scheduledList.innerHTML = '<div class="empty-state">Could not load</div>'; }
}
function renderScheduled(scheduled) {
  const entries = Object.entries(scheduled);
  if (!entries.length) {
    els.scheduledList.innerHTML = `<div class="empty-state-block">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"><rect x="3" y="4" width="18" height="18" rx="2"/><path d="M16 2v4M8 2v4M3 10h18"/></svg>
      <p>No scheduled downloads</p></div>`;
    return;
  }
  els.scheduledList.innerHTML = entries.map(([id, s]) => `
    <div class="sched-item">
      <div class="sched-item-info">
        <p class="sched-item-url">${esc(s.url)}</p>
        <p class="sched-item-time">${new Date(s.run_at*1000).toLocaleString()}</p>
        <p class="sched-item-fmt">${s.mode==='audio'?s.audio_fmt.toUpperCase():s.quality+' '+s.mode}</p>
      </div>
      <button class="delete-btn" onclick="cancelScheduled('${id}')">Cancel</button>
    </div>`).join('');
}
async function cancelScheduled(id) {
  await fetch(`/scheduled/${id}`, { method: 'DELETE' }); loadScheduled();
}

// ── Server health ──────────────────────────────────────────────────────────────
async function checkServer() {
  try {
    const r = await fetch('/health', { signal: AbortSignal.timeout(3000) });
    if (r.ok) { els.serverDot.className='status-dot online'; els.serverLabel.textContent='Server online'; return true; }
  } catch {}
  els.serverDot.className='status-dot offline'; els.serverLabel.textContent='Server offline';
  return false;
}

// ── URL input ──────────────────────────────────────────────────────────────────
function initUrlInput() {
  els.urlInput.addEventListener('input', () => {
    els.clearBtn.classList.toggle('visible', els.urlInput.value.trim().length > 0);
    els.previewPanel.classList.add('hidden');
  });
  els.clearBtn.addEventListener('click', () => {
    els.urlInput.value = ''; els.clearBtn.classList.remove('visible');
    els.previewPanel.classList.add('hidden'); els.urlInput.focus();
  });
  els.urlInput.addEventListener('keydown', (e) => { if (e.key === 'Enter') startDownload(); });
  els.downloadBtn.addEventListener('click', startDownload);
}

// ── Status panel ───────────────────────────────────────────────────────────────
function setStatus(type, title, detail) {
  els.statusPanel.className    = `status-panel${type !== 'idle' ? ` is-${type}` : ''}`;
  els.statusIconWrap.innerHTML = ICONS[type] || ICONS.idle;
  els.statusTitle.textContent  = title;
  els.statusDetail.textContent = detail;
}
function showProgress(show) {
  els.progressWrap.style.display = show ? 'block' : 'none';
  if (!show) {
    els.progressBar.className = 'progress-bar'; els.progressBar.style.width = '0%';
    els.progressBar.style.background = ''; els.progressPct.textContent = ''; els.speedBadge.textContent = '';
  }
}
function setProgress(pct, indeterminate = false) {
  els.progressWrap.style.display = 'block';
  if (indeterminate) {
    els.progressBar.className = 'progress-bar indeterminate';
    els.progressBar.style.width = ''; els.progressPct.textContent = '';
  } else {
    els.progressBar.className = 'progress-bar';
    els.progressBar.style.width = `${Math.min(pct, 100)}%`;
    els.progressPct.textContent = `${Math.round(pct)}%`;
  }
}

// ── Download ───────────────────────────────────────────────────────────────────
// Two strategies:
//
// STREAM (single video or audio):
//   - Tab opened synchronously in click handler (trusted gesture)
//   - Tab navigates immediately to /stream-download?url=...
//   - Server runs yt-dlp | ffmpeg → StreamingResponse → browser saves file
//   - Nothing stored on server, file goes directly to device
//
// JOB (playlist / album):
//   - Tab opened synchronously in click handler
//   - POST /download to create job
//   - Tab navigates to /waiting/{job_id}
//   - /waiting page polls until done, then navigates to /serve-job/{job_id}
//   - serve-job streams then deletes the file
//
async function startDownload() {
  if (S.downloading) return;
  const raw = els.urlInput.value.trim();
  if (!raw) { els.urlInput.focus(); return; }

  const urls = raw.split(',').map((u) => u.trim()).filter(Boolean);
  for (const u of urls) {
    if (!u.startsWith('http://') && !u.startsWith('https://')) {
      setStatus('error', 'Invalid URL', `Not a valid URL: ${u}`); return;
    }
  }

  if (S.socket) { S.socket.close(); S.socket = null; }

  // ── Open one blank tab per URL synchronously ──────────────────────────────
  // This MUST happen here, inside the real click event, before any await.
  // Mobile browsers (iOS Safari, Chrome Android) only trust window.open
  // that originates from a genuine user gesture. Any tab opened after an
  // await is considered a popup and gets blocked.
  const preOpened = urls.map(() => {
    try { return window.open('', '_blank'); } catch { return null; }
  });

  S.downloading = true;
  els.downloadBtn.disabled = true;
  setStatus('loading', 'Starting…', urls.length > 1 ? `${urls.length} items` : urls[0]);
  setProgress(0, true);

  // ── Route each URL ────────────────────────────────────────────────────────
  const playlistUrls = urls.filter(isPlaylistUrl);
  const singleUrls   = urls.filter((u) => !isPlaylistUrl(u));

  // STREAM path — navigate pre-opened tabs directly to /stream-download
  singleUrls.forEach((url, i) => {
    const globalIdx = urls.indexOf(url);
    const win       = preOpened[globalIdx];
    const params    = new URLSearchParams({
      url,
      mode:      S.mode,
      quality:   S.quality,
      audio_fmt: S.audioFmt,
    });
    const target = `/stream-download?${params}`;
    if (win && !win.closed) {
      try { win.location.href = target; } catch { window.open(target, '_blank'); }
    } else {
      // Popup was blocked — last resort same-tab navigation with tiny delay
      setTimeout(() => window.open(target, '_blank'), i * 300);
    }
    addHistory({ url, title: url, status: 'done', type: S.mode });
  });

  if (singleUrls.length > 0 && playlistUrls.length === 0) {
    // All singles — UI feedback and done
    setTimeout(() => {
      setStatus('done', 'File sent to your device', 'Check your downloads folder');
      showProgress(false);
      S.downloading            = false;
      els.downloadBtn.disabled = false;
      sendNotification('Download started', 'Check your device downloads folder');
      renderHistory();
    }, 1200);
    return;
  }

  // JOB path — playlist(s) go through the server-side job queue
  if (playlistUrls.length === 0) return;

  try {
    const res = await fetch('/download', {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        url:       playlistUrls.join(','),
        mode:      S.mode,
        quality:   S.quality,
        audio_fmt: S.audioFmt,
      }),
    });

    if (res.status === 429) throw new Error('Rate limit reached. Please wait.');
    if (!res.ok) { const e = await res.json().catch(() => ({})); throw new Error(e.detail || `Server error ${res.status}`); }

    const data   = await res.json();
    const jobIds = data.job_ids || (data.job_id ? [data.job_id] : []);
    if (!jobIds.length) throw new Error('No jobs returned from server');

    jobIds.forEach((jobId, i) => {
      // Find the matching pre-opened window for this playlist URL
      const globalIdx = urls.indexOf(playlistUrls[i] || playlistUrls[0]);
      const win       = preOpened[globalIdx] || null;
      if (win && !win.closed) {
        try { win.location.href = `/waiting/${jobId}`; } catch {}
      } else {
        try { window.open(`/waiting/${jobId}`, '_blank'); } catch {}
      }
      const qId = addToQueue(playlistUrls[i] || jobId);
      setTimeout(() => connectWS(jobId, playlistUrls[i] || jobId, qId), i * 200);
    });

    if (jobIds.length > 1 || singleUrls.length > 0) {
      setStatus('loading', `Processing ${jobIds.length} playlist(s)`, 'See queue for progress');
      S.downloading            = false;
      els.downloadBtn.disabled = false;
    }

  } catch (err) {
    // Close any unused playlist windows
    preOpened.forEach((w, i) => {
      if (urls[i] && isPlaylistUrl(urls[i])) {
        try { w && !w.closed && w.close(); } catch {}
      }
    });
    setStatus('error', 'Failed', err.message);
    showProgress(false);
    S.downloading            = false;
    els.downloadBtn.disabled = false;
    checkServer();
  }
}

// ── WebSocket + poll (for playlist job progress in main page) ─────────────────
function connectWS(jobId, url, qId) {
  let ws;
  try { ws = new WebSocket(`${WS_BASE}/ws/${jobId}`); }
  catch { poll(jobId, url, qId); return; }
  S.socket     = ws;
  ws.onmessage = (e) => { try { onJobUpdate(JSON.parse(e.data), url, qId); } catch {} };
  ws.onerror   = ()  => { ws.close(); poll(jobId, url, qId); };
  ws.onclose   = ()  => { S.socket = null; };
}
function poll(jobId, url, qId) {
  const iv = setInterval(async () => {
    try {
      const r = await fetch(`/job/${jobId}`);
      if (!r.ok) { clearInterval(iv); return; }
      const job = await r.json();
      if (job.status === 'error' && job.message === 'Job not found') { clearInterval(iv); return; }
      onJobUpdate(job, url, qId);
      if (job.status === 'done' || job.status === 'error') clearInterval(iv);
    } catch { clearInterval(iv); }
  }, 800);
}
function onJobUpdate(job, url, qId) {
  if (job.status === 'downloading' || job.status === 'queued') {
    setStatus('loading', 'Downloading playlist…', job.message || '');
    els.speedBadge.textContent = fmtSpeed(job.speed);
    job.percent > 0 ? setProgress(job.percent) : setProgress(0, true);
    updateQueueProgress(qId, job.percent || 0, job.message || '', job.speed || 0, job.track_info || '');
  } else if (job.status === 'done') {
    setStatus('done', 'Playlist sent to your device', job.title || url);
    els.speedBadge.textContent = '';
    setProgress(100); setTimeout(() => showProgress(false), 1500);
    addHistory({ url, title: job.title || url, status: 'done', type: job.type || S.mode });
    finishQueueItem(qId, true, job.title);
    sendNotification('Download complete', job.title || url);
    S.downloading = false; els.downloadBtn.disabled = false;
  } else if (job.status === 'error') {
    setStatus('error', 'Download failed', job.message || 'Unknown error');
    els.speedBadge.textContent = '';
    els.progressBar.style.background = 'var(--red)';
    setProgress(100); setTimeout(() => showProgress(false), 2000);
    addHistory({ url, title: url, status: 'error', type: 'error' });
    finishQueueItem(qId, false, job.message);
    sendNotification('Download failed', job.message || url);
    S.downloading = false; els.downloadBtn.disabled = false;
  }
}

// ── History ────────────────────────────────────────────────────────────────────
function addHistory(entry) {
  entry.timestamp = Date.now(); S.history.unshift(entry);
  if (S.history.length > 50) S.history = S.history.slice(0, 50);
  localStorage.setItem('fetchr_history', JSON.stringify(S.history));
  renderHistory();
}
function renderHistory() {
  if (!S.history.length) { els.recentList.innerHTML = '<li class="empty-state">No downloads yet</li>'; return; }
  els.recentList.innerHTML = S.history.slice(0, 8).map((e) => {
    const isV = e.type==='video', isA = e.type==='audio', isErr = e.status==='error';
    const cls = isV?'type-video':isA?'type-audio':isErr?'type-error':'type-file';
    const ico = isV?ICONS.video:isA?ICONS.audio:isErr?ICONS.error:ICONS.file;
    const badge = e.status === 'done'
      ? '<span class="recent-item-badge done">Done</span>'
      : '<span class="recent-item-badge error">Failed</span>';
    const su = e.url.length > 60 ? e.url.slice(0, 60) + '...' : e.url;
    return `<li class="recent-item"><div class="recent-item-icon ${cls}">${ico}</div>
      <div class="recent-item-info"><p class="recent-item-title">${esc(e.title)}</p>
      <p class="recent-item-url">${esc(su)}</p></div>${badge}</li>`;
  }).join('');
}
function exportHistory(format) {
  if (!S.history.length) { alert('No history to export.'); return; }
  let content, mime, ext;
  if (format === 'csv') {
    const rows = [['Title','URL','Status','Type','Date']];
    S.history.forEach((e) => rows.push([
      `"${(e.title||'').replace(/"/g,'""')}"`,
      `"${(e.url||'').replace(/"/g,'""')}"`,
      e.status, e.type||'', new Date(e.timestamp||0).toISOString(),
    ]));
    content = rows.map((r) => r.join(',')).join('\n'); mime = 'text/csv'; ext = 'csv';
  } else {
    content = JSON.stringify(S.history, null, 2); mime = 'application/json'; ext = 'json';
  }
  const blob = new Blob([content], { type: mime }), a = document.createElement('a');
  a.href = URL.createObjectURL(blob); a.download = `fetchr-history.${ext}`; a.click();
  URL.revokeObjectURL(a.href);
}

// ── Queue ──────────────────────────────────────────────────────────────────────
let qN = 0;
function addToQueue(url) {
  const id = ++qN, sh = url.length > 52 ? url.slice(0,52) + '...' : url;
  el('queueList').querySelector('.empty-state-block')?.remove();
  const d = document.createElement('div');
  d.className = 'queue-item'; d.id = `qi-${id}`;
  d.innerHTML = `
    <div class="queue-item-header"><span class="queue-item-name">${esc(sh)}</span><span class="queue-item-pct" id="qp-${id}">Queued</span></div>
    <div class="queue-item-meta"><span class="queue-item-track" id="qt-${id}"></span><span class="queue-item-speed" id="qspd-${id}"></span></div>
    <div class="queue-item-msg" id="qm-${id}"></div>
    <div class="queue-item-progress"><div class="queue-item-bar indeterminate" id="qb-${id}"></div></div>`;
  els.queueList.prepend(d); return id;
}
function updateQueueProgress(id, pct, msg, speed, trackInfo) {
  const p=el(`qp-${id}`),b=el(`qb-${id}`),m=el(`qm-${id}`),t=el(`qt-${id}`),spd=el(`qspd-${id}`);
  if (!p) return;
  p.textContent = pct > 0 ? `${Math.round(pct)}%` : 'Downloading';
  if (m)   m.textContent   = msg || '';
  if (t)   t.textContent   = trackInfo || '';
  if (spd) spd.textContent = fmtSpeed(speed);
  if (pct > 0) { b.className = 'queue-item-bar'; b.style.width = `${Math.min(pct,100)}%`; }
  else b.className = 'queue-item-bar indeterminate';
}
function finishQueueItem(id, ok, label) {
  const p=el(`qp-${id}`),b=el(`qb-${id}`),m=el(`qm-${id}`),t=el(`qt-${id}`),spd=el(`qspd-${id}`);
  if (!p) return;
  p.textContent = ok ? 'Done' : 'Failed'; p.style.color = `var(--${ok ? 'green' : 'red'})`;
  b.className = 'queue-item-bar'; b.style.width = '100%'; b.style.background = `var(--${ok ? 'green' : 'red'})`;
  if (m && label) m.textContent = label;
  if (t)   t.textContent   = '';
  if (spd) spd.textContent = '';
  setTimeout(() => {
    el(`qi-${id}`)?.remove();
    if (!els.queueList.querySelector('.queue-item')) {
      els.queueList.innerHTML = `<div class="empty-state-block">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"><rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/></svg>
        <p>Queue is empty</p></div>`;
    }
  }, 5000);
}

// ── Files ──────────────────────────────────────────────────────────────────────
async function loadFiles() {
  els.filesTableBody.innerHTML = '<tr><td colspan="4" class="empty-state">Loading...</td></tr>';
  try {
    const data = await fetch('/storage').then((r) => r.json());
    S.storageData = data.files || []; els.folderPath.textContent = data.folder || '';
    renderStorage(data); renderFiles(S.storageData);
  } catch { els.filesTableBody.innerHTML = '<tr><td colspan="4" class="empty-state">Could not reach server</td></tr>'; }
}
function renderStorage(data) {
  const used = data.total_bytes||0, total = data.disk_total||1, free = data.disk_free||0;
  const pct  = Math.min(used/total*100, 100);
  els.storageBarFill.style.width       = `${pct}%`;
  els.storageLabel.textContent         = `${fmtBytes(used)} used · ${fmtBytes(free)} free`;
  els.storageBarFill.style.background  = pct > 85 ? 'var(--red)' : pct > 65 ? 'var(--accent)' : '';
}
function fileType(ext) {
  if (['mp4','mkv','webm','avi','mov','flv','wmv'].includes(ext))                return 'video';
  if (['mp3','flac','wav','opus','aac','m4a','ogg','alac'].includes(ext))        return 'audio';
  if (['jpg','jpeg','png','gif','webp','svg','bmp'].includes(ext))               return 'image';
  if (['pdf','docx','xlsx','pptx','txt','html','zip','rar','epub'].includes(ext)) return 'doc';
  return 'other';
}
function renderFiles(files) {
  els.fileCount.textContent = `${files.length} file${files.length !== 1 ? 's' : ''}`;
  if (!files.length) { els.filesTableBody.innerHTML = '<tr><td colspan="4" class="empty-state">No files yet</td></tr>'; return; }
  els.filesTableBody.innerHTML = files.map((f) => {
    const name  = f.name || f, size = f.size != null ? fmtBytes(f.size) : '';
    const parts = name.split('.'), ext = parts.length > 1 ? parts.pop().toLowerCase() : '';
    const type  = fileType(ext);
    return `<tr>
      <td class="filename">${esc(name)}</td>
      <td><span class="type-badge ${type}">${type}</span></td>
      <td class="size-cell">${size}</td>
      <td>
        <a class="download-btn" href="/serve/${encodeURIComponent(name)}" download>Save</a>
        <button class="delete-btn" onclick="deleteFile('${esc(name)}')">Delete</button>
      </td></tr>`;
  }).join('');
}
async function deleteFile(name) {
  if (!confirm(`Delete "${name}"?`)) return;
  try {
    const res = await fetch(`/files/${encodeURIComponent(name)}`, { method: 'DELETE' });
    if (res.ok) loadFiles();
    else { const e = await res.json().catch(() => ({})); alert(e.detail || 'Delete failed'); }
  } catch { alert('Could not reach server'); }
}
function initFileSearch() {
  els.fileSearch.addEventListener('input', () => {
    const q = els.fileSearch.value.toLowerCase();
    renderFiles(S.storageData.filter((f) => (f.name||f).toLowerCase().includes(q)));
  });
}

// ── Init ───────────────────────────────────────────────────────────────────────
function init() {
  initNav(); initUrlInput(); initFormatSelector(); initPresets();
  initPreview(); initNotifications(); initSchedule(); initUpdateBtn(); initFileSearch();
  els.refreshBtn.addEventListener('click', loadFiles);
  els.refreshScheduledBtn.addEventListener('click', loadScheduled);
  els.clearHistoryBtn.addEventListener('click', () => {
    if (!confirm('Clear all history?')) return;
    S.history = []; localStorage.removeItem('fetchr_history'); renderHistory();
  });
  els.exportCsvBtn.addEventListener('click',  () => exportHistory('csv'));
  els.exportJsonBtn.addEventListener('click', () => exportHistory('json'));
  renderHistory(); checkServer(); setInterval(checkServer, 20000);
  setStatus('idle', 'Ready', 'Waiting for a link');
}

document.addEventListener('DOMContentLoaded', init);