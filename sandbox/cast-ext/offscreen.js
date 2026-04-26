// Offscreen document — owns the signaling WebSocket, MediaStream, and
// MediaRecorder.  Capture is now driven from the service worker (which
// gets a real user invocation via Ctrl+Shift+K or the action icon in
// the Desktop tab).  This file just reacts to push messages from the
// SW and pumps WebM chunks to TeamWork.
//
// Signaling URL is rewritten at container-entry time (see
// sandbox/entrypoint.sh) so the hostname matches whatever Docker
// Compose service name is running TeamWork.
const WS_URL = 'ws://__PRAX_CAST_SIGNALING_HOST__/api/browser/cast/sandbox';

const CHUNK_INTERVAL_MS = 200;

let ws = null;
let reconnectTimer = null;
let recorder = null;
let stream = null;

let bgPort = null;

function log(...args) { console.log('[prax-cast]', ...args); }

function sendJson(obj) {
  if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(obj));
}

// ── Service-worker port ─────────────────────────────────────────────
// Held open so the SW stays alive and so we have a channel for it to
// push capture commands to us.
function ensureBgPort() {
  if (bgPort) return bgPort;
  bgPort = chrome.runtime.connect({ name: 'cast' });

  bgPort.onMessage.addListener(async (msg) => {
    if (!msg) return;
    if (msg.type === 'start-capture') {
      await beginCapture(msg.streamId, msg.tabUrl, msg.tabTitle);
    } else if (msg.type === 'stop-capture') {
      stopCapture('sw-requested');
    } else if (msg.type === 'cast-error') {
      sendJson({ type: 'error', error: msg.error });
    }
  });

  bgPort.onDisconnect.addListener(() => {
    const err = chrome.runtime.lastError?.message || 'disconnected';
    log('bg port closed:', err);
    bgPort = null;
    // Try again next tick so we recover from SW restarts.
    setTimeout(ensureBgPort, 1000);
  });

  return bgPort;
}

function notifyBg(type) {
  if (bgPort) {
    try { bgPort.postMessage({ type }); } catch { /* port may be closing */ }
  }
}

// ── Signaling WebSocket ─────────────────────────────────────────────
function connectWs() {
  if (reconnectTimer) { clearTimeout(reconnectTimer); reconnectTimer = null; }
  try {
    ws = new WebSocket(WS_URL);
  } catch (e) {
    log('WebSocket construct failed:', e);
    reconnectTimer = setTimeout(connectWs, 3000);
    return;
  }
  ws.binaryType = 'arraybuffer';
  ws.onopen = () => log('signaling connected');
  ws.onclose = () => {
    log('signaling closed — will reconnect');
    ws = null;
    if (recorder) stopCapture('signaling-closed');
    reconnectTimer = setTimeout(connectWs, 3000);
  };
  ws.onerror = () => { try { ws.close(); } catch {} };
  ws.onmessage = async (ev) => {
    if (typeof ev.data !== 'string') return;
    let msg;
    try { msg = JSON.parse(ev.data); } catch { return; }
    // The client (BrowserPanel) can request a stop at any time.  Start
    // requests are intentionally ignored here — capture is initiated only
    // from a real X11 user gesture (shortcut or action click).
    if (msg.type === 'stop') {
      stopCapture('client-requested');
    } else if (msg.type === 'start') {
      // Friendly hint so the panel can render an explanation.
      sendJson({
        type: 'awaiting-invocation',
        hint: 'Click the prax-cast icon in Chrome\'s toolbar (in the Desktop tab) to start capture.',
      });
    }
  };
}

// ── Capture lifecycle ──────────────────────────────────────────────
async function beginCapture(streamId, tabUrl, tabTitle) {
  if (recorder) {
    log('capture already running — ignoring duplicate start');
    return;
  }

  try {
    stream = await navigator.mediaDevices.getUserMedia({
      audio: { mandatory: { chromeMediaSource: 'tab', chromeMediaSourceId: streamId } },
      video: { mandatory: { chromeMediaSource: 'tab', chromeMediaSourceId: streamId } },
    });
  } catch (e) {
    sendJson({ type: 'error', error: `getUserMedia failed: ${e.message}` });
    return;
  }

  const mimeCandidates = [
    'video/webm;codecs=vp9,opus',
    'video/webm;codecs=vp8,opus',
    'video/webm',
  ];
  const mimeType = mimeCandidates.find((m) => MediaRecorder.isTypeSupported(m));
  if (!mimeType) {
    sendJson({ type: 'error', error: 'no supported WebM mime type' });
    stream.getTracks().forEach((t) => t.stop());
    stream = null;
    return;
  }

  sendJson({ type: 'meta', mimeType, tabUrl, tabTitle });

  recorder = new MediaRecorder(stream, { mimeType });
  recorder.ondataavailable = async (e) => {
    if (!e.data || e.data.size === 0) return;
    if (!ws || ws.readyState !== WebSocket.OPEN) return;
    const buf = await e.data.arrayBuffer();
    try { ws.send(buf); } catch (err) { log('ws.send failed:', err); }
  };
  recorder.onstop = () => {
    if (stream) stream.getTracks().forEach((t) => t.stop());
    stream = null;
    recorder = null;
    sendJson({ type: 'stopped' });
    notifyBg('capture-stopped');
  };
  recorder.onerror = (e) => {
    log('recorder error:', e);
    sendJson({ type: 'error', error: `recorder error: ${e.error?.message || e}` });
  };

  recorder.start(CHUNK_INTERVAL_MS);
  sendJson({ type: 'started', tabUrl, tabTitle });
  notifyBg('capture-started');
  log(`capture started — mime=${mimeType}, tab=${tabUrl}`);
}

function stopCapture(reason = 'unknown') {
  log(`stopping capture (${reason})`);
  if (recorder && recorder.state !== 'inactive') {
    recorder.stop();  // onstop notifies SW + WS
  } else {
    if (stream) stream.getTracks().forEach((t) => t.stop());
    stream = null;
    recorder = null;
    notifyBg('capture-stopped');
  }
}

// Boot
connectWs();
ensureBgPort();
