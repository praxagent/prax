// MV3 service worker.  Three jobs:
//   1. Keep an offscreen document alive (that's where the signaling WS,
//      MediaStream, and MediaRecorder live).
//   2. React to user invocation — keyboard shortcut OR action-icon click.
//      Both are real X11 events (when the user is sitting in the Desktop
//      tab via noVNC), which Chromium treats as trusted gestures.  CDP-
//      synthesized events from the BrowserPanel side don't qualify.
//   3. Capture the active tab's MediaStream id and PUSH it to the
//      offscreen via the persistent port.

const OFFSCREEN_URL = 'offscreen.html';

let casting = false;          // SW is the source of truth for capture state
let castPort = null;          // port to the offscreen document (single connection)

async function ensureOffscreen() {
  try {
    const ctxs = await chrome.runtime.getContexts({ contextTypes: ['OFFSCREEN_DOCUMENT'] });
    if (ctxs.length > 0) return;
    await chrome.offscreen.createDocument({
      url: OFFSCREEN_URL,
      reasons: ['USER_MEDIA'],
      justification: 'Capture active tab audio+video for remote viewing.',
    });
  } catch (e) {
    console.error('[prax-cast] ensureOffscreen failed:', e);
  }
}

chrome.runtime.onInstalled.addListener(ensureOffscreen);
chrome.runtime.onStartup.addListener(ensureOffscreen);
ensureOffscreen();

// Persistent port from the offscreen document.  The offscreen opens it
// on load and holds it open — so we always have a channel to push the
// streamId when the user fires a capture.
chrome.runtime.onConnect.addListener((port) => {
  if (port.name !== 'cast') return;
  console.log('[prax-cast] offscreen port connected');
  castPort = port;

  port.onDisconnect.addListener(() => {
    console.log('[prax-cast] offscreen port disconnected');
    if (castPort === port) castPort = null;
    casting = false;
  });

  // Offscreen reports its lifecycle so we can stay in sync.
  port.onMessage.addListener((msg) => {
    if (!msg) return;
    if (msg.type === 'capture-started') casting = true;
    else if (msg.type === 'capture-stopped') casting = false;
  });
});

async function startCast() {
  if (casting) return;
  if (!castPort) {
    console.warn('[prax-cast] no offscreen port — re-ensuring');
    await ensureOffscreen();
    if (!castPort) return;
  }

  let tab;
  try {
    [tab] = await chrome.tabs.query({ active: true, lastFocusedWindow: true });
  } catch (e) {
    castPort.postMessage({ type: 'cast-error', error: `tabs.query failed: ${e.message}` });
    return;
  }
  if (!tab || !tab.id) {
    castPort.postMessage({ type: 'cast-error', error: 'no active tab to capture' });
    return;
  }
  if (/^chrome(-extension)?:\/\//.test(tab.url || '')) {
    castPort.postMessage({ type: 'cast-error', error: `cannot capture ${tab.url} — chrome internal pages are blocked` });
    return;
  }

  let streamId;
  try {
    streamId = await chrome.tabCapture.getMediaStreamId({ targetTabId: tab.id });
  } catch (e) {
    castPort.postMessage({ type: 'cast-error', error: `getMediaStreamId failed: ${e.message}` });
    return;
  }

  castPort.postMessage({
    type: 'start-capture',
    streamId,
    tabUrl: tab.url,
    tabTitle: tab.title,
  });
  // `casting = true` is set when the offscreen confirms `capture-started`.
}

function stopCast() {
  if (!castPort) return;
  castPort.postMessage({ type: 'stop-capture' });
}

function toggleCast() {
  if (casting) stopCast(); else startCast();
}

// User invocation paths.  Both must come from a real (untrusted-by-CDP)
// gesture for chrome.tabCapture to grant access.
chrome.commands.onCommand.addListener((cmd) => {
  if (cmd === 'toggle-cast') toggleCast();
});

chrome.action.onClicked.addListener(() => {
  toggleCast();
});
