const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { createExternalQueue } = require('../engine/lib/external-queue.cjs');

test('failed Codex delivery survives restart and is retried in order', async () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'wa-external-queue-'));
  const file = path.join(dir, 'pending.json');
  const route = { jid: 'fixture@s.whatsapp.net', threadId: '12345678-1234-4234-8234-123456789abc' };
  const delivered = [];
  let fail = true;
  try {
    const makeQueue = () => createExternalQueue({
      file,
      deliver: async (_route, message) => {
        if (fail) return { ok: false, err: 'Codex busy' };
        delivered.push(message);
        return { ok: true };
      },
      log: () => {},
    });
    const first = makeQueue();
    assert.equal(first.enqueue({ id: 'one', route, kind: 'text', message: 'first' }), true);
    assert.equal(first.enqueue({ id: 'one', route, kind: 'text', message: 'first' }), false);
    assert.equal(first.enqueue({ id: 'two', route, kind: 'image', message: 'second' }), true);
    await first.drain();
    assert.equal(first.count(), 2);

    fail = false;
    const restarted = makeQueue();
    await restarted.drain();
    assert.deepEqual(delivered, ['first', 'second']);
    assert.equal(restarted.count(), 0);
    assert.deepEqual(JSON.parse(fs.readFileSync(file, 'utf8')), []);
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});
