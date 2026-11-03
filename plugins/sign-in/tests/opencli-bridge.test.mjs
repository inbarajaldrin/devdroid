import './environment.mjs';
import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { ensureExtension, extensionContext } from '../browser/opencli-bridge.mjs';

test('adopt the registered version without reloading a live extension', async () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'opencli-test-'));
  try {
    fs.writeFileSync(path.join(dir, 'manifest.json'), JSON.stringify({ version: '1.0.26' }));
    const calls = [];
    const send = async (method, params) => {
      calls.push([method, params]);
      return { extensions: [{ id: 'official', path: dir, enabled: true, version: '1.0.26' }] };
    };
    assert.equal(await ensureExtension(send, dir), 'official');
    assert.equal(calls.length, 1);
    const load = async (method, params) => method === 'Extensions.getExtensions'
      ? { extensions: [{ id: 'old', path: '/old-checkout', enabled: true, version: '1.0.22' }] }
      : (assert.deepEqual(params, { path: dir }), { id: 'official' });
    assert.equal(await ensureExtension(load, dir), 'official');
  } finally { fs.rmSync(dir, { recursive: true }); }
});

test('read context only from the configured extension, even with an old worker', async () => {
  const send = async (method, params, sessionId) => {
    if (method === 'Extensions.getExtensions') return { extensions: [
      { id: 'old', path: '/old-checkout', enabled: true },
      { id: 'official', path: '/official', enabled: true },
    ] };
    if (method === 'Target.getTargets') return { targetInfos: [
      { type: 'service_worker', url: 'chrome-extension://old/dist/background.js', targetId: 'old-worker' },
      { type: 'service_worker', url: 'chrome-extension://official/dist/background.js', targetId: 'new-worker' },
    ] };
    if (method === 'Target.attachToTarget') {
      assert.equal(params.targetId, 'new-worker');
      return { sessionId: 'worker-session' };
    }
    if (method === 'Target.detachFromTarget') return {};
    assert.equal(sessionId, 'worker-session');
    return { result: { value: { opencli_context_id_v1: 'new-context' } } };
  };
  assert.equal(await extensionContext(send, '/official'), 'new-context');
  assert.equal(await extensionContext(send, '/missing'), null);
});
