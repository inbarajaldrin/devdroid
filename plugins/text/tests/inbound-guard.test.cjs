const test = require('node:test');
const assert = require('node:assert/strict');
const { createInboundGuard } = require('../engine/lib/inbound-guard.cjs');

test('single-number fromMe input routes unless it is a daemon send echo', () => {
  const guard = createInboundGuard(new Set());
  const typed = { key: { id: 'USER_TYPED', fromMe: true } };
  assert.equal(guard.isOwn(typed, 'self-chat'), true);
  assert.equal(guard.consumeDaemonSend(typed.key.id), false);

  guard.noteDaemonSend('DAEMON_SENT');
  const echo = { key: { id: 'DAEMON_SENT', fromMe: true } };
  assert.equal(guard.isOwn(echo, 'self-chat'), true);
  assert.equal(guard.consumeDaemonSend(echo.key.id), true);
  assert.equal(guard.consumeDaemonSend(echo.key.id), false);
});
test('a message one account sent is never read back by another account sharing the group', () => {
  const fs = require('node:fs'), os = require('node:os'), path = require('node:path');
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'text-guard-')), file = path.join(dir, 'engine-sends.log');
  const { createInboundGuard } = require('../engine/lib/inbound-guard.cjs');
  const main = createInboundGuard(new Set(), file), us = createInboundGuard(new Set(), file);
  us.noteDaemonSend('SENT-BY-US');
  assert.equal(main.consumeDaemonSend('SENT-BY-US'), true);
  assert.equal(main.consumeDaemonSend('TYPED-BY-OWNER'), false);
  fs.rmSync(dir, { recursive: true, force: true });
});
