const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { Ask } = require('../engine/lib/ask.cjs');
const { Engine } = require('../engine/lib/engine.cjs');
const { write } = require('../engine/lib/store.cjs');
test('engine invokes real Python fake /ask process; home survives engine restart', async t => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'text-process-')); t.after(() => fs.rmSync(directory, { recursive: true, force: true }));
  const script = path.join(directory, 'fake-ask.py');
  fs.writeFileSync(script, `import sys,json\nfrom pathlib import Path\na=sys.argv[1:]\np=Path(__file__).parent\nwith (p/'calls.jsonl').open('a') as f: f.write(json.dumps({'args':a,'prompt':Path(a[a.index('--prompt-file')+1]).read_text()})+'\\n')\nPath(a[a.index('--metadata')+1]).write_text(json.dumps({'threadId':'11111111-1111-4111-8111-111111111111','status':'accepted'}))\nPath(a[a.index('--outcome')+1]).write_text(json.dumps({'version':1,'kind':'accepted','code':'ENQUEUED'}))\n`);
  const rulesFile = path.join(directory, 'rules.json');
  write(rulesFile, { version: 1, rules: [{ chat: 'project@g.us', private: true, target: { kind: 'home', agent: 'codex', homeKey:'fixture-choice', rolePrompt:'Instructions chosen in the fixture setup conversation.', permissions:{sandbox:'read-only',approvalPolicy:'on-request'}, host: 'test', cwd: '/project' }, triggers: { every: true } }] });
  const options = { config: { host: 'test', homeHost: 'test', ownerChat: 'owner@lid', owners: ['owner@lid'], aiPrefix: 'AI: ' }, rulesFile,
    stateFile: path.join(directory, 'state.json'), wa: { messageId:require('./native-id.cjs').nativeId, resolve: async c => c, send: async (_c, _t, opts) => ({ key: { id: opts.id } }) }, ask: new Ask({ script, directory }) };
  await new Engine(options).receive({ id: 'one', chat: 'project@g.us', senders: ['owner@lid'], text: '`$HOME` $(literal)' });
  await new Engine(options).receive({ id: 'two', chat: 'project@g.us', senders: ['owner@lid'], text: 'follow up' });
  const calls = fs.readFileSync(path.join(directory, 'calls.jsonl'), 'utf8').trim().split('\n').map(JSON.parse);
  assert.equal(calls.length, 2); assert.ok(calls[0].args.includes('--start')); assert.ok(!calls[1].args.includes('--start'));
  assert.ok(calls[1].args.includes('11111111-1111-4111-8111-111111111111')); assert.match(calls[0].prompt, /\$\(literal\)/);
  const restarted = new Engine(options);
  const notification = await restarted.request({ cmd: 'send', chat: 'owner@lid', message: 'done', target: { kind: 'live', agent: 'codex', host: 'test', id: '11111111-1111-4111-8111-111111111111' } });
  assert.equal(restarted.state.sent[notification.id].target.kind, 'home');
});
