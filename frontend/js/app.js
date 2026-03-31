/* Fetchr — app.js */

const WS_PROTO = location.protocol === 'https:' ? 'wss' : 'ws';
const WS_BASE  = `${WS_PROTO}://${location.host}`;

// ── State ──────────────────────────────────────────────────────────────────────
const S = {
  history:       JSON.parse(localStorage.getItem('fetchr_history') || '[]'),
  allFiles:      [],
  socket:        null,
  downloading:   false,
  mode:          'video',   // sent to backend
  quality:       '1080p',   // sent to backend
  audioFmt:      'mp3',     // sent to backend
};

// ── Shorthand ──────────────────────────────────────────────────────────────────
const el  = (id) => document.getElementById(id);
const els = {
  urlInput:        el('urlInput'),
  clearBtn:        el('clearBtn'),
  downloadBtn:     el('downloadBtn'),
  statusPanel:     el('statusPanel'),
  statusIconWrap:  el('statusIconWrap'),
  statusTitle:     el('statusTitle'),
  statusDetail:    el('statusDetail'),
  progressWrap:    el('progressWrap'),
  progressBar:     el('progressBar'),
  progressPct:     el('progressPct'),
  recentList:      el('recentList'),
  clearHistoryBtn: el('clearHistoryBtn'),
  queueList:       el('queueList'),
  filesTableBody:  el('filesTableBody'),
  folderPath:      el('folderPath'),
  fileSearch:      el('fileSearch'),
  fileCount:       el('fileCount'),
  refreshBtn:      el('refreshBtn'),
  serverDot:       el('serverDot'),
  serverLabel:     el('serverLabel'),
  blockVideo:      el('blockVideo'),
  blockAudio:      el('blockAudio'),
  summaryText:     el('summaryText'),
};

// ── SVG icons ──────────────────────────────────────────────────────────────────
const ICONS = {
  idle:    `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><circle cx="12" cy="12" r="10"/><path d="M12 8v4l3 3"/></svg>`,
  loading: `<svg class="spinning" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M21 12a9 9 0 1 1-6.219-8.56"/></svg>`,
  done:    `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6 9 17l-5-5"/></svg>`,
  error:   `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="12" cy="12" r="10"/><path d="M12 8v4M12 16h.01"/></svg>`,
  video:   `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="7" width="15" height="10" rx="2"/><path d="m17 9 5-2v10l-5-2V9z"/></svg>`,
  audio:   `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M9 18V5l12-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="18" cy="16" r="3"/></svg>`,
  file:    `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>`,
};

// Summary labels
const V_LABEL = { best:'Best available', '4k':'4K', '1440p':'1440p', '1080p':'1080p HD', '720p':'720p HD' };
const V_CONT  = { best:'MP4', '4k':'MKV', '1440p':'MKV', '1080p':'MP4', '720p':'MP4' };
const A_LABEL = { mp3:'MP3 · 320kbps', flac:'FLAC · Lossless', wav:'WAV · Lossless', opus:'Opus · HQ', aac:'AAC · HQ', alac:'ALAC · Apple Lossless' };


// ── Navigation ─────────────────────────────────────────────────────────────────
function initNav() {
  document.querySelectorAll('.nav-item').forEach((btn) => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('.nav-item').forEach((b) => b.classList.remove('active'));
      document.querySelectorAll('.view').forEach((v) => v.classList.remove('active'));
      btn.classList.add('active');
      el(`view-${btn.dataset.view}`)?.classList.add('active');
      if (btn.dataset.view === 'files') loadFiles();
    });
  });
}

// ── Format selector ────────────────────────────────────────────────────────────
// Using direct getElementById references and onclick — the most reliable approach.
// No event delegation, no closest(), no child elements on buttons.

function setMode(mode) {
  S.mode = mode;

  // Update mode button visuals
  el('modeVideo').classList.toggle('active', mode === 'video');
  el('modeAudio').classList.toggle('active', mode === 'audio');

  // Show/hide quality vs format blocks
  if (mode === 'video') {
    els.blockVideo.classList.remove('hidden');
    els.blockAudio.classList.add('hidden');
  } else {
    els.blockVideo.classList.add('hidden');
    els.blockAudio.classList.remove('hidden');
  }

  updateSummary();
}

function setVideoQuality(val) {
  S.quality = val;

  // Clear all video quality pills then activate the right one
  ['best','4k','1440p','1080p','720p'].forEach((v) => {
    el(`q-${v}`)?.classList.toggle('active', v === val);
  });

  updateSummary();
}

function setAudioFmt(fmt) {
  S.audioFmt = fmt;

  // Clear all audio format pills then activate the right one
  ['mp3','flac','wav','opus','aac','alac'].forEach((f) => {
    el(`a-${f}`)?.classList.toggle('active', f === fmt);
  });

  updateSummary();
}

function updateSummary() {
  if (!els.summaryText) return;
  if (S.mode === 'video') {
    els.summaryText.textContent = `Video · ${V_LABEL[S.quality] || S.quality} · ${V_CONT[S.quality] || 'MP4'}`;
  } else {
    els.summaryText.textContent = `Audio · ${A_LABEL[S.audioFmt] || S.audioFmt.toUpperCase()}`;
  }
}

function initFormatSelector() {
  // Mode buttons
  el('modeVideo').onclick = () => setMode('video');
  el('modeAudio').onclick = () => setMode('audio');

  // Video quality pills — direct onclick per button
  el('q-best').onclick  = () => setVideoQuality('best');
  el('q-4k').onclick    = () => setVideoQuality('4k');
  el('q-1440p').onclick = () => setVideoQuality('1440p');
  el('q-1080p').onclick = () => setVideoQuality('1080p');
  el('q-720p').onclick  = () => setVideoQuality('720p');

  // Audio format pills — direct onclick per button
  el('a-mp3').onclick  = () => setAudioFmt('mp3');
  el('a-flac').onclick = () => setAudioFmt('flac');
  el('a-wav').onclick  = () => setAudioFmt('wav');
  el('a-opus').onclick = () => setAudioFmt('opus');
  el('a-aac').onclick  = () => setAudioFmt('aac');
  el('a-alac').onclick = () => setAudioFmt('alac');

  // Render initial state
  updateSummary();
}


// ── Server health ──────────────────────────────────────────────────────────────
async function checkServer() {
  try {
    const r = await fetch('/health', { signal: AbortSignal.timeout(3000) });
    if (r.ok) {
      els.serverDot.className    = 'status-dot online';
      els.serverLabel.textContent = 'Server online';
      return true;
    }
  } catch { /* ignore */ }
  els.serverDot.className    = 'status-dot offline';
  els.serverLabel.textContent = 'Server offline';
  return false;
}

// ── URL input ──────────────────────────────────────────────────────────────────
function initUrlInput() {
  els.urlInput.addEventListener('input', () => {
    els.clearBtn.classList.toggle('visible', els.urlInput.value.trim().length > 0);
  });
  els.clearBtn.addEventListener('click', () => {
    els.urlInput.value = '';
    els.clearBtn.classList.remove('visible');
    els.urlInput.focus();
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
    els.progressBar.className        = 'progress-bar';
    els.progressBar.style.width      = '0%';
    els.progressBar.style.background = '';
    els.progressPct.textContent      = '';
  }
}

function setProgress(pct, indeterminate = false) {
  els.progressWrap.style.display = 'block';
  if (indeterminate) {
    els.progressBar.className   = 'progress-bar indeterminate';
    els.progressBar.style.width = '';
    els.progressPct.textContent = '';
  } else {
    els.progressBar.className   = 'progress-bar';
    els.progressBar.style.width = `${Math.min(pct, 100)}%`;
    els.progressPct.textContent = `${Math.round(pct)}%`;
  }
}

// ── Download ───────────────────────────────────────────────────────────────────
async function startDownload() {
  if (S.downloading) return;

  const url = els.urlInput.value.trim();
  if (!url) { els.urlInput.focus(); return; }
  if (!url.startsWith('http://') && !url.startsWith('https://')) {
    setStatus('error', 'Invalid URL', 'Must start with http:// or https://');
    return;
  }

  if (S.socket) { S.socket.close(); S.socket = null; }

  S.downloading            = true;
  els.downloadBtn.disabled = true;
  setStatus('loading', 'Starting...', url);
  setProgress(0, true);

  const payload = { url, mode: S.mode, quality: S.quality, audio_fmt: S.audioFmt };
  console.log('[Fetchr] Sending:', payload);

  try {
    const res = await fetch('/download', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });

    if (res.status === 429) throw new Error('Rate limit reached. Please wait.');
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || `Server error ${res.status}`);
    }

    const { job_id } = await res.json();
    const qId = addToQueue(url);
    connectWS(job_id, url, qId);

  } catch (err) {
    setStatus('error', 'Failed', err.message);
    showProgress(false);
    S.downloading            = false;
    els.downloadBtn.disabled = false;
    checkServer();
  }
}

// ── WebSocket + polling fallback ───────────────────────────────────────────────
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
      onJobUpdate(job, url, qId);
      if (job.status === 'done' || job.status === 'error') clearInterval(iv);
    } catch { clearInterval(iv); }
  }, 800);
}

function onJobUpdate(job, url, qId) {
  if (job.status === 'downloading' || job.status === 'queued') {
    setStatus('loading', 'Downloading', job.message || '');
    job.percent > 0 ? setProgress(job.percent) : setProgress(0, true);
    queueProgress(qId, job.percent || 0, job.message || '');

  } else if (job.status === 'done') {
    setStatus('done', 'Download complete', job.title || url);
    setProgress(100);
    setTimeout(() => showProgress(false), 1500);
    addHistory({ url, title: job.title || url, status: 'done', type: job.type || S.mode });
    queueDone(qId, true, job.title);
    S.downloading            = false;
    els.downloadBtn.disabled = false;

  } else if (job.status === 'error') {
    setStatus('error', 'Download failed', job.message || 'Unknown error');
    els.progressBar.style.background = 'var(--red)';
    setProgress(100);
    setTimeout(() => showProgress(false), 2000);
    addHistory({ url, title: url, status: 'error', type: 'error' });
    queueDone(qId, false, job.message);
    S.downloading            = false;
    els.downloadBtn.disabled = false;
  }
}

// ── History ────────────────────────────────────────────────────────────────────
function addHistory(entry) {
  entry.timestamp = Date.now();
  S.history.unshift(entry);
  if (S.history.length > 50) S.history = S.history.slice(0, 50);
  localStorage.setItem('fetchr_history', JSON.stringify(S.history));
  renderHistory();
}

function renderHistory() {
  if (!S.history.length) {
    els.recentList.innerHTML = '<li class="empty-state">No downloads yet</li>';
    return;
  }
  els.recentList.innerHTML = S.history.slice(0, 8).map((e) => {
    const isV   = e.type === 'video';
    const isA   = e.type === 'audio';
    const isErr = e.status === 'error';
    const cls   = isV ? 'type-video' : isA ? 'type-audio' : isErr ? 'type-error' : 'type-file';
    const ico   = isV ? ICONS.video  : isA ? ICONS.audio  : isErr ? ICONS.error  : ICONS.file;
    const badge = e.status === 'done'
      ? '<span class="recent-item-badge done">Done</span>'
      : '<span class="recent-item-badge error">Failed</span>';
    const su = e.url.length > 60 ? e.url.slice(0, 60) + '...' : e.url;
    return `<li class="recent-item">
      <div class="recent-item-icon ${cls}">${ico}</div>
      <div class="recent-item-info">
        <p class="recent-item-title">${esc(e.title)}</p>
        <p class="recent-item-url">${esc(su)}</p>
      </div>${badge}</li>`;
  }).join('');
}

// ── Queue ──────────────────────────────────────────────────────────────────────
let qN = 0;

function addToQueue(url) {
  const id = ++qN;
  const sh = url.length > 52 ? url.slice(0, 52) + '...' : url;
  el('queueList').querySelector('.empty-state-block')?.remove();
  const d = document.createElement('div');
  d.className = 'queue-item'; d.id = `qi-${id}`;
  d.innerHTML = `<div class="queue-item-header">
    <span class="queue-item-name">${esc(sh)}</span>
    <span class="queue-item-status" id="qs-${id}">Queued</span>
  </div>
  <div class="queue-item-meta" id="qm-${id}"></div>
  <div class="queue-item-progress"><div class="queue-item-bar indeterminate" id="qb-${id}"></div></div>`;
  els.queueList.prepend(d);
  return id;
}

function queueProgress(id, pct, msg) {
  const s = el(`qs-${id}`), b = el(`qb-${id}`), m = el(`qm-${id}`);
  if (!s) return;
  s.textContent = pct > 0 ? `${Math.round(pct)}%` : 'Downloading';
  if (m) m.textContent = msg;
  if (pct > 0) { b.className = 'queue-item-bar'; b.style.width = `${Math.min(pct, 100)}%`; }
  else b.className = 'queue-item-bar indeterminate';
}

function queueDone(id, ok, label) {
  const s = el(`qs-${id}`), b = el(`qb-${id}`), m = el(`qm-${id}`);
  if (!s) return;
  s.textContent      = ok ? 'Done' : 'Failed';
  s.style.color      = `var(--${ok ? 'green' : 'red'})`;
  b.className        = 'queue-item-bar';
  b.style.width      = '100%';
  b.style.background = `var(--${ok ? 'green' : 'red'})`;
  if (m && label) m.textContent = label;
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
  els.filesTableBody.innerHTML = '<tr><td colspan="3" class="empty-state">Loading...</td></tr>';
  try {
    const d = await fetch('/files').then((r) => r.json());
    S.allFiles = d.files || [];
    els.folderPath.textContent = d.folder || '';
    renderFiles(S.allFiles);
  } catch {
    els.filesTableBody.innerHTML = '<tr><td colspan="3" class="empty-state">Could not reach server</td></tr>';
  }
}

function fileType(ext) {
  if (['mp4','mkv','webm','avi','mov','flv','wmv',].includes(ext))               return 'video';
  if (['mp3','flac','wav','opus','aac','m4a','ogg','alac'].includes(ext))       return 'audio';
  if (['jpg','jpeg','png','gif','webp','svg','bmp'].includes(ext))              return 'image';
  if (['pdf','docx','xlsx','pptx','txt','html','zip','rar','epub'].includes(ext)) return 'doc';
  return 'other';
}

function renderFiles(files) {
  els.fileCount.textContent = `${files.length} file${files.length !== 1 ? 's' : ''}`;
  if (!files.length) { els.filesTableBody.innerHTML = '<tr><td colspan="3" class="empty-state">No files yet</td></tr>'; return; }
  els.filesTableBody.innerHTML = files.map((name) => {
    const p = name.split('.');
    const x = p.length > 1 ? p.pop().toLowerCase() : '';
    const t = fileType(x);
    return `<tr><td class="filename">${esc(name)}</td><td><span class="type-badge ${t}">${t}</span></td><td><span class="ext-badge">${x || '—'}</span></td></tr>`;
  }).join('');
}

// ── Utility ────────────────────────────────────────────────────────────────────
function esc(s) {
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

// ── Init ───────────────────────────────────────────────────────────────────────
function init() {
  initNav();
  initUrlInput();
  initFormatSelector();

  els.fileSearch.addEventListener('input', () => {
    const q = els.fileSearch.value.toLowerCase();
    renderFiles(S.allFiles.filter((f) => f.toLowerCase().includes(q)));
  });

  els.refreshBtn.addEventListener('click', loadFiles);
  els.clearHistoryBtn.addEventListener('click', () => {
    S.history = [];
    localStorage.removeItem('fetchr_history');
    renderHistory();
  });

  renderHistory();
  checkServer();
  setInterval(checkServer, 20000);
  setStatus('idle', 'Ready', 'Waiting for a link');
}

document.addEventListener('DOMContentLoaded', init);