// Local extension management only; never attaches to or changes a website tab.
import fs from 'node:fs';

export async function browserCDP(port, work) {
  const info = await (await fetch(`http://127.0.0.1:${port}/json/version`, {
    signal: AbortSignal.timeout(5000),
  })).json();
  const ws = new WebSocket(info.webSocketDebuggerUrl);
  const pending = new Map();
  let id = 0;
  try {
    await new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error('CDP connection timed out')), 5000);
      ws.onopen = () => { clearTimeout(timer); resolve(); };
      ws.onerror = () => { clearTimeout(timer); reject(new Error('CDP connection failed')); };
    });
    ws.onmessage = event => {
      const message = JSON.parse(event.data);
      const request = pending.get(message.id);
      if (!request) return;
      pending.delete(message.id);
      clearTimeout(request.timer);
      if (message.error) request.reject(new Error(message.error.message));
      else request.resolve(message.result);
    };
    return await work((method, params = {}, sessionId) => new Promise((resolve, reject) => {
      const requestId = ++id;
      const timer = setTimeout(() => {
        pending.delete(requestId);
        reject(new Error(`CDP ${method} timed out`));
      }, 5000);
      pending.set(requestId, { resolve, reject, timer });
      ws.send(JSON.stringify({ id: requestId, method, params, ...(sessionId ? { sessionId } : {}) }));
    }));
  } finally {
    for (const request of pending.values()) {
      clearTimeout(request.timer);
      request.reject(new Error('CDP connection closed'));
    }
    ws.close();
  }
}

export async function ensureExtension(send, extensionPath) {
  const manifest = JSON.parse(fs.readFileSync(`${extensionPath}/manifest.json`, 'utf8'));
  const { extensions } = await send('Extensions.getExtensions');
  const current = extensions.find(e => e.path === extensionPath && e.enabled && e.version === manifest.version);
  return current?.id ?? (await send('Extensions.loadUnpacked', { path: extensionPath })).id;
}

export async function extensionContext(send, extensionPath) {
  const { extensions } = await send('Extensions.getExtensions');
  const current = extensions.find(e => e.path === extensionPath && e.enabled);
  if (!current) return null;
  const { targetInfos } = await send('Target.getTargets');
  const worker = targetInfos.find(t => t.type === 'service_worker'
    && t.url === `chrome-extension://${current.id}/dist/background.js`);
  if (!worker) return null;
  const { sessionId } = await send('Target.attachToTarget', { targetId: worker.targetId, flatten: true });
  try {
    const { result, exceptionDetails } = await send('Runtime.evaluate', {
      expression: "chrome.storage.local.get(['opencli_context_id_v1','context_id','contextId'])",
      awaitPromise: true, returnByValue: true,
    }, sessionId);
    if (exceptionDetails) throw new Error('Cannot read OpenCLI context storage');
    const data = result.value || {};
    return data.opencli_context_id_v1 || data.context_id || data.contextId || null;
  } finally { await send('Target.detachFromTarget', { sessionId }); }
}
