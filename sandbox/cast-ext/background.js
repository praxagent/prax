// MV3 service worker.  Two jobs:
//   1. Keep an offscreen document alive (that's where the signaling WS,
//      MediaStream, and MediaRecorder live — service workers can't hold
//      them because they get terminated when idle).
//   2. Respond to `request-stream` over a persistent port connection
//      from the offscreen document.  `chrome.tabs` and `chrome.tabCapture`
//      are service-worker-only in MV3, so the offscreen can't call them
//      directly.  A long-lived port (vs. one-shot chrome.runtime.sendMessage)
//      keeps this worker alive and reliably wakes it when idle.

const OFFSCREEN_URL = 'offscreen.html';

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

// Handle port connections from the offscreen document.  Using a port
// instead of chrome.runtime.sendMessage because (a) it's more reliable
// at waking a dormant SW and (b) holding the port open keeps this SW
// alive while capture is active.
chrome.runtime.onConnect.addListener((port) => {
  if (port.name !== 'cast') return;
  console.log('[prax-cast] port connected from offscreen');

  port.onMessage.addListener(async (msg) => {
    if (!msg || msg.type !== 'request-stream') return;
    try {
      const tabs = await chrome.tabs.query({ active: true, lastFocusedWindow: true });
      const tab = tabs[0];
      if (!tab || !tab.id) {
        port.postMessage({ type: 'stream-response', error: 'no active tab to capture' });
        return;
      }
      const streamId = await chrome.tabCapture.getMediaStreamId({ targetTabId: tab.id });
      port.postMessage({
        type: 'stream-response',
        streamId,
        tabUrl: tab.url,
        tabTitle: tab.title,
      });
    } catch (e) {
      port.postMessage({
        type: 'stream-response',
        error: e?.message || String(e),
      });
    }
  });

  port.onDisconnect.addListener(() => {
    console.log('[prax-cast] port disconnected');
  });
});
