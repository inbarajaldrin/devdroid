const { test } = require('node:test');
const assert = require('node:assert/strict');
const { validate, match, stamp, help } = require('../engine/lib/rules.cjs');
const { convert } = require('../engine/lib/convert.cjs');
const target = { kind: 'live', agent: 'claude', id: '11111111-1111-4111-8111-111111111111', host: 'mac' };
const rule = { chat: 'jobs@g.us', target, answerMode: 'draft', allow: ['friend@lid'], triggers: { keywords: ['@ai'], mention: true }, named: { codex: { ...target, agent: 'codex', host: 'box' } } };
const config = { host: 'mac', owners: ['owner@lid'], linkedJids: ['bot@lid'] };
const msg = { chat: rule.chat, sender: 'owner:2@lid', text: '@ai hello' };
const run = (m = {}, r = rule) => match({ version: 1, rules: r ? [r] : [] }, { ...msg, ...m }, config);
test('all wake triggers and strict keyword boundaries', () => {
  assert.equal(run().trigger, 'keyword');
  assert.equal(run({ text: '@aileen hi' }).kind, 'none');
  assert.equal(run({ text: 'hi', mentions: ['bot:3@lid'] }).trigger, 'mention');
  assert.equal(run({ text: '@codex hi' }).host, 'box');
  assert.equal(run({ text: 'chatter' }, { ...rule, triggers: { every: true } }).trigger, 'every');
  assert.equal(run({ text: 'chatter' }).reason, 'no-match');
});
test('swipe always targets exact original agent, including home/standing; no rule guesses', () => {
  for (const t of [target, { ...target, host: 'box', kind: 'standing', id: 'plan/47' }, { kind: 'home', agent: 'codex', homeKey:'fixture-choice', rolePrompt:'Instructions chosen in the fixture setup conversation.', permissions:{sandbox:'read-only',approvalPolicy:'on-request'}, host: 'box', cwd: '/project' }]) {
    const r = run({ text: '@codex hi', quotedOwn: true, quotedTarget: t, quotedText: stamp(t, 'plan/47', 'hello') }, null);
    assert.deepEqual(r.target, t);
  }
  assert.equal(run({ text: '@ai hello' }, null).kind, 'none');
  assert.equal(run({ quotedText: stamp(target, 'x', 'hi'), quotedOwn: false, text: 'hi' }).kind, 'none');
});
test('permissions, expiry, malformed rules refuse', () => {
  assert.equal(run({ sender: 'stranger@lid' }).reason, 'not-allowed');
  assert.equal(run({ sender: 'friend@lid' }).mode, 'draft');
  assert.equal(run({ sender: 'friend@lid' }, { ...rule, expiresAt: '2000-01-01T00:00:00Z' }).reason, 'expired');
  assert.equal(run({}, { ...rule, expiresAt: '2000-01-01T00:00:00Z' }).kind, 'deliver');
  assert.throws(() => run({}, { ...rule, expiresAt: 'tomorrow' }), { code: 'INVALID_EXPIRY' });
  assert.throws(() => validate({ version: 1, rules: [rule, rule] }), { code: 'INVALID_RULE' });
});
test('external chats require a chosen mode; owner and private defaults stay intact', () => {
  assert.equal(run().mode, 'draft');
  assert.equal(run({}, { ...rule, private: true, allow: [], answerMode: undefined }).mode, 'answer');
  assert.throws(() => run({}, { ...rule, private: true, answerMode: undefined }), { code: 'MODE_REQUIRED' });
  assert.throws(() => run({}, { ...rule, answerMode: undefined }), { code: 'MODE_REQUIRED' });
  assert.equal(run({ sender: 'friend@lid' }, { ...rule, private: true, answerMode: 'listen' }).mode, 'listen');
  for (const mode of ['answer', 'draft', 'listen', 'tell-me']) assert.equal(run({ sender: 'friend@lid' }, { ...rule, answerMode: mode }).mode, mode);
});
test('commands have separate access; instruction host and argv checked; help uses rule', () => {
  const r = { ...rule, commands: { '/usage': { type: 'script', host: 'box', argv: ['/usr/bin/uptime'], allow: ['friend@lid'] }, '/review': { type: 'instruction', host: 'mac', text: 'Review this project' } } };
  assert.equal(run({ text: '/usage now', sender: 'friend@lid' }, r).host, 'box');
  assert.equal(run({ text: '/review', sender: 'friend@lid' }, r).reason, 'command-not-allowed');
  assert.equal(run({ text: '/review' }, r).command.text, 'Review this project');
  assert.match(help(r), /\/usage/);
  assert.doesNotMatch(help(r, { shared: true }), /friend@lid|11111111|box|mac/);
  assert.equal(run({ text: '/help' }, null).kind, 'help');
  assert.throws(() => run({}, { ...rule, commands: { '/bad': { type: 'instruction', host: 'box', text: 'do' } } }), { code: 'INVALID_COMMAND' });
});
test('conversion preserves contacts, triggers, identities, expiry and maps legacy replies', () => {
  const old = { groupRoutes: [{ group: rule.chat, participants: ['friend@lid'], sessionId: target.id, trigger: { keyword: '@ai', mentionOwn: true }, expiresAt: '2099-01-01T00:00:00Z' }], routes: [{ jid: 'friend@lid', threadId: '22222222-2222-4222-8222-222222222222', expiresAt: '2099-01-01T00:00:00Z' }] };
  const converted = convert(old, 'mac');
  assert.equal(converted.bundle.rules.length, 2);
  assert.equal(converted.bundle.rules[0].answerMode, 'answer');
  assert.equal(converted.bundle.rules.find(r => r.chat === 'friend@lid').target.agent, 'codex');
  assert.equal(converted.bundle.rules.find(r => r.chat === 'friend@lid').style, 'plain');
  assert.equal(converted.bundle.rules.find(r => r.chat === rule.chat).style, 'stamped');
  assert.throws(() => convert({ groupRoutes: [old.groupRoutes[0], old.groupRoutes[0]] }, 'mac'), { code: 'CONVERSION_CONFLICT' });
  assert.throws(() => convert({ routes: [{ jid: 'friend@lid', threadId: target.id }] }, 'mac'), { code: 'CONVERSION_EXPIRY' });
});
