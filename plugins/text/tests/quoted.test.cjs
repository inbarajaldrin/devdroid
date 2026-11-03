const test = require('node:test');
const assert = require('node:assert/strict');
const { stripFrame, snippet, quotedSummary } = require('../engine/lib/quoted.cjs');

const framed = '`example-agent`\n\nStatus image, redone: one run end to end.\n\n_00000000-aaaa-4bbb-8ccc-dddddddddddd_';

test('the routing frame is stripped from a quoted agent message', () => {
  assert.equal(stripFrame(framed), 'Status image, redone: one run end to end.');
  assert.equal(stripFrame('`codex`\n_Thread title_\n\nbody\n\n_01a166c5-7abb-7f32-a061-a5675c9141e5_'), 'body');
  assert.equal(stripFrame('plain text from a person'), 'plain text from a person');
});

test('a long quote is cut to one short line', () => {
  const s = snippet('word '.repeat(80));
  assert.ok(s.length <= 140 && s.endsWith('…'));
});

test('an indexed send names its kind, time, opening text and file', () => {
  const ts = new Date('2026-10-23T18:05:00').getTime();
  const line = quotedSummary({ hit: { kind: 'IMAGE', ts, text: 'Status image, redone', file: '/tmp/process.png' } });
  assert.equal(line, '[Replying to your image sent 18:05: "Status image, redone" (file: /tmp/process.png)]');
  assert.match(quotedSummary({ hit: { kind: 'TEXT', ts, text: 'hello' } }), /^\[Replying to your message sent 18:05: "hello"\]$/);
});

test('without an index hit it falls back to the quoted kind and text', () => {
  assert.equal(quotedSummary({ quotedType: 'imageMessage', quotedText: framed, fromAgent: true }),
    '[Replying to your image: "Status image, redone: one run end to end."]');
  assert.equal(quotedSummary({ quotedType: 'conversation', quotedText: 'see you at 5' }), '[Replying to a message: "see you at 5"]');
  assert.equal(quotedSummary({}), '');
});

test('a reply can quote a remembered message from its key and text', () => {
  const { quotedStub } = require('../engine/lib/quoted.cjs');
  const key = { remoteJid: 'fixture-group@g.us', id: 'ABC123', fromMe: false, participant: 'fixture-sender@lid' };
  assert.deepEqual(quotedStub(key, 'switch photo', 'image'), { key, message: { conversation: 'switch photo' } });
  assert.deepEqual(quotedStub(key, '', 'image').message, { conversation: '[image]' });
  assert.equal(quotedStub({ id: 'x' }, 'no chat'), null);
});

// Quoted context: nothing when the agent still has its message, the FULL text when it does not.
{
  const { test } = require('node:test');
  const assert = require('node:assert');
  const { quotedBlock } = require('../engine/lib/quoted.cjs');
  const long = 'Line one of a long agent message. '.repeat(20).trim();
  const hit = { kind: 'text', text: '`example-session`\n\n' + long + '\n\n_00000000-aaaa-4bbb-8ccc-eeeeeeeeeeee_', ts: Date.parse('2026-10-24T18:31:00Z') };
  test('latest own message, after the last compaction: no block', () => {
    assert.strictEqual(quotedBlock({ hit, compactedAt: Date.parse('2026-10-20T06:09:38Z'), latest: true }), '');
  });
  test('older own message, after the last compaction: one short pointer line, not the full text', () => {
    const b = quotedBlock({ hit, compactedAt: Date.parse('2026-10-20T06:09:38Z'), latest: false });
    assert.ok(b.startsWith('[Replying to your message sent')); assert.ok(b.includes('…')); assert.ok(!b.includes('\n'));
  });
  test('own message sent before the last compaction: full text, frame stripped, not truncated', () => {
    const b = quotedBlock({ hit, compactedAt: Date.parse('2026-10-25T00:00:00Z') });
    assert.ok(b.includes(long)); assert.ok(!b.includes('…')); assert.ok(!b.includes('00000000-aaaa-4bbb')); assert.ok(b.includes('before your context was compacted'));
  });
  test('receiver transcript unknown (null): full text, to be safe', () => {
    assert.ok(quotedBlock({ hit, compactedAt: null }).includes(long));
  });
  test("someone else's message: full text", () => {
    const b = quotedBlock({ hit: null, quotedType: 'conversation', quotedText: 'check this posting ' + long, fromAgent: false, compactedAt: 0 });
    assert.ok(b.startsWith('[Replying to a message:]')); assert.ok(b.includes(long));
  });
}
