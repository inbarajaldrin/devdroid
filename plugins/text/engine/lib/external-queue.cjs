const fs = require('fs');

function createExternalQueue({ file, deliver, log, now = Date.now, maxAttempts = 120, maxAgeMs = 7 * 86400000, exhausted = async () => {}, concurrency = 4 }) {
  let pending;
  try { pending = JSON.parse(fs.readFileSync(file, 'utf8')); } catch (error) { if (error.code !== 'ENOENT') throw error; pending = []; }
  if (!Array.isArray(pending)) throw new Error('INVALID_QUEUE: expected pending delivery array');
  let draining = false;

  function persist() {
    const temp = `${file}.${process.pid}.tmp`;
    fs.writeFileSync(temp, JSON.stringify(pending, null, 2) + '\n', { mode: 0o600 });
    fs.renameSync(temp, file);
  }

  function enqueue(item) {
    if (pending.some((entry) => entry.id === item.id)) return false;
    if (pending.length >= 500) throw new Error('RETRY_CAPACITY: resolve the pending deliveries before adding more');
    pending.push({ ...item, queuedAt: now(), attempts: 0 });
    try { persist(); }
    catch (error) { pending.pop(); throw error; }
    return true;
  }

  async function drain() {
    if (draining) return;
    draining = true;
    try {
      const snapshot = [...pending]; let cursor = 0;
      async function worker() { while (cursor < snapshot.length) {
        const item = snapshot[cursor++];
        let result;
        try { result = await deliver(item.route, item.message); }
        catch (error) { result = { ok: false, err: String(error) }; }
        log('external_delivered', {
          id: item.id, jid: item.route.jid, uuid: item.route.threadId,
          kind: item.kind, ok: result.ok, err: result.err,
        });
        item.attempts = (item.attempts || 0) + 1;
        const expired = item.attempts >= maxAttempts || now() - (item.queuedAt ?? now()) >= maxAgeMs;
        if (result.ok || expired) pending.splice(pending.indexOf(item), 1);
        persist();
        if (!result.ok && expired) await exhausted(item, result);
      } }
      await Promise.all(Array.from({ length: Math.min(concurrency, snapshot.length) }, () => worker()));
    } finally {
      draining = false;
    }
  }

  return { enqueue, drain, count: () => pending.length };
}

module.exports = { createExternalQueue };
