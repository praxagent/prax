// Offscreen document — owns the signaling WebSocket, the captured
// MediaStream, and the MediaRecorder.  Keeps the signaling WS connected
// at all times and reacts to `{type:"start"}` / `{type:"stop"}` from
// the client peer.  When capturing, muxes the tab's audio+video into
// WebM chunks and ships them as binary WS frames.
//
// Signaling URL is rewritten at container-entry time (see
// sandbox/entrypoint.sh) so the hostname matches whatever Docker Compose
// service name is running TeamWork.  The default is the development
// compose project name.
const WS_URL = 'ws://__PRAX_CAST_SIGNALING_HOST__/api/browser/cast/sandbox';

const CHUNK_INTERVAL_MS = 200;

let ws = null;
let reconnectTimer = null;
let recorder = null;
let stream = null;

function log(...args) { console.log('[prax-cast]', ...args); }

function sendJson(obj) {
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify(obj));
  }
}

function connect() {
  if (reconnectTimer) { clearTimeout(reconnectTimer); reconnectTimer = null; }
  try {
    ws = new WebSocket(WS_URL);
  } catch (e) {
    log('WebSocket construct failed:', e);
    reconnectTimer = setTimeout(connect, 3000);
    return;
  }
  ws.binaryType = 'arraybuffer';
  ws.onopen = () => log('signaling connected');
  ws.onclose = () => {
    log('signaling closed — will reconnect');
    ws = null;
    // If we were mid-stream, stop the recorder; the client is gone.
    if (recorder) stopCapture();
    reconnectTimer = setTimeout(connect, 3000);
  };
  ws.onerror = () => { try { ws.close(); } catch {} };
  ws.onmessage = async (ev) => {
    if (typeof ev.data !== 'string') return;  // no binary from client side
    let msg;
    try { msg = JSON.parse(ev.data); } catch { return; }
    if (msg.type === 'start') {
      await startCapture();
    } else if (msg.type === 'stop') {
      stopCapture();
    }
  };
}

// Persistent port to the service worker.  Held open for the life of
// the offscreen document so the SW stays alive (a connected port
// disables its 30s idle timeout) and so we don't race the SW's lazy
// startup on the first capture attempt.
let bgPort = null;
let pendingStreamResolve = null;

function ensureBgPort() {
  if (bgPort) return bgPort;
  bgPort = chrome.runtime.connect({ name: 'cast' });
  bgPort.onMessage.addListener((msg) => {
    if (msg?.type === 'stream-response' && pendingStreamResolve) {
      const resolve = pendingStreamResolve;
      pendingStreamResolve = null;
      resolve(msg);
    }
  });
  bgPort.onDisconnect.addListener(() => {
    const err = chrome.runtime.lastError?.message || 'disconnected';
    log('bg port closed:', err);
    bgPort = null;
    if (pendingStreamResolve) {
      const resolve = pendingStreamResolve;
      pendingStreamResolve = null;
      resolve({ error: `bg port: ${err}` });
    }
  });
  return bgPort;
}

async function requestStreamFromBg(timeoutMs = 5000) {
  const port = ensureBgPort();
  return new Promise((resolve) => {
    pendingStreamResolve = resolve;
    try {
      port.postMessage({ type: 'request-stream' });
    } catch (e) {
      pendingStreamResolve = null;
      resolve({ error: `postMessage failed: ${e.message}` });
      return;
    }
    setTimeout(() => {
      if (pendingStreamResolve === resolve) {
        pendingStreamResolve = null;
        resolve({ error: 'timeout waiting for service worker' });
      }
    }, timeoutMs);
  });
}

async function startCapture() {
  if (recorder) return;  // already running; ignore duplicate start

  // `chrome.tabs` and `chrome.tabCapture` aren't exposed to offscreen
  // documents — ask the service worker to look them up via the port.
  const resp = await requestStreamFromBg();
  if (!resp || resp.error) {
    sendJson({ type: 'error', error: `stream request failed: ${resp?.error || 'no response'}` });
    return;
  }
  const { streamId, tabUrl, tabTitle } = resp;

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
  };
  recorder.onerror = (e) => {
    log('recorder error:', e);
    sendJson({ type: 'error', error: `recorder error: ${e.error?.message || e}` });
  };

  recorder.start(CHUNK_INTERVAL_MS);
  sendJson({ type: 'started' });
  log(`capture started — mime=${mimeType}, tab=${tabUrl}`);
}

function stopCapture() {
  if (recorder && recorder.state !== 'inactive') {
    recorder.stop();  // onstop will clean up
  } else {
    if (stream) stream.getTracks().forEach((t) => t.stop());
    stream = null;
    recorder = null;
  }
}

connect();
// Eagerly open the port to the service worker so it's awake and ready
// before the first Cast click — avoids a cold-start race where the SW
// is dormant when the user triggers capture.
ensureBgPort();
