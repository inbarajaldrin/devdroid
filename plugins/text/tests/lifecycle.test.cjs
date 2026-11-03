const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const net = require('node:net');
const { spawn, execFile } = require('node:child_process');
const { promisify } = require('node:util');
const { ensureSocketFree } = require('../engine/lib/socket.cjs');
const { write } = require('../engine/lib/store.cjs');
function temp(t) { const d = fs.mkdtempSync(path.join(os.tmpdir(), 'text-life-')); t.after(() => fs.rmSync(d, { recursive: true, force: true })); return d; }
test('ask dependency lookup uses override, sibling, plugin root, then user skills in that order', t => {
  const directory = temp(t);
  const { locateAsk } = require('../engine/lib/ask.cjs');
  const skill = path.join(directory, 'consumer/skills/text');
  const paths = [path.join(directory, 'override/ask.py'), path.resolve(skill, '../ask/scripts/ask.py'), path.join(directory, 'provider/skills/ask/scripts/ask.py'), path.join(directory, 'home/.claude/skills/ask/scripts/ask.py'), path.join(directory, 'dependency/skills/ask/scripts/ask.py')];
  for (const file of paths) { fs.mkdirSync(path.dirname(file), { recursive: true }); fs.writeFileSync(file, '# fake ask\n'); }
  write(path.join(directory, '.claude-plugin/marketplace.json'), { plugins: [{ name: 'session-tools', source: './dependency' }] });
  const env = { ASK_SCRIPT: paths[0], CLAUDE_PLUGIN_ROOT: path.join(directory, 'provider'), HOME: path.join(directory, 'home') };
  assert.equal(locateAsk(env, skill), paths[0]); delete env.ASK_SCRIPT;
  assert.equal(locateAsk(env, skill), paths[1]); fs.unlinkSync(paths[1]);
  assert.equal(locateAsk(env, skill), paths[2]); fs.unlinkSync(paths[2]);
  assert.equal(locateAsk(env, skill), paths[3]); fs.unlinkSync(paths[3]);
  assert.equal(locateAsk(env, skill), paths[4]); fs.unlinkSync(paths[4]);
  assert.throws(() => locateAsk(env, skill), { code: 'ASK_MISSING' });
  assert.throws(() => locateAsk({ ...env, ASK_SCRIPT: 'relative.py' }, skill), { code: 'ASK_MISSING' });
  assert.throws(() => locateAsk({ ...env, ASK_SCRIPT: directory }, skill), { code: 'ASK_MISSING' });
  assert.equal(fs.existsSync(path.resolve(__dirname, '../skills/ask')), false);
});
test('a normal installed runtime resolves its registered dependency before reading ancestor catalogs', t => {
  const directory = temp(t), home = path.join(directory, 'home');
  const { locateAsk } = require('../engine/lib/ask.cjs');
  const skill = path.join(home, '.devdroid/text/runtime/skills/text'); fs.mkdirSync(skill, { recursive: true });
  const installPath = path.join(home, '.claude/plugins/cache/fixture/session-tools/1');
  const script = path.join(installPath, 'skills/ask/scripts/ask.py'); write(script, {});
  fs.mkdirSync(path.join(directory, '.claude-plugin')); fs.writeFileSync(path.join(directory, '.claude-plugin/marketplace.json'), '{broken unrelated catalog');
  for (const entries of [[{ installPath }], { installPath }]) {
    write(path.join(home, '.claude/plugins/installed_plugins.json'), { version: 2, plugins: { 'session-tools@fixture': entries } });
    assert.equal(locateAsk({ HOME: home }, skill), script);
  }
});
test('a declared missing dependency refuses instead of borrowing one from an outer marketplace', t => {
  const directory = temp(t), outer = path.join(directory, 'outer'), inner = path.join(outer, 'inner');
  const { locateAsk } = require('../engine/lib/ask.cjs');
  write(path.join(outer, '.claude-plugin/marketplace.json'), { plugins: [{ name: 'session-tools', source: './dependency' }] });
  write(path.join(outer, 'dependency/skills/ask/scripts/ask.py'), {});
  write(path.join(inner, '.claude-plugin/marketplace.json'), { plugins: [{ name: 'session-tools', source: './missing' }] });
  assert.throws(() => locateAsk({ HOME: path.join(directory, 'empty-home') }, path.join(inner, 'plugins/text/skills/text')), { code: 'ASK_MISSING' });
});
test('a resolved marketplace dependency is not invalidated by a higher ancestor catalog', t => {
  const directory = temp(t), inner = path.join(directory, 'inner');
  const { locateAsk } = require('../engine/lib/ask.cjs');
  fs.mkdirSync(path.join(directory, '.claude-plugin')); fs.writeFileSync(path.join(directory, '.claude-plugin/marketplace.json'), '{broken unrelated catalog');
  write(path.join(inner, '.claude-plugin/marketplace.json'), { plugins: [{ name: 'session-tools', source: './dependency' }] });
  const script = path.join(inner, 'dependency/skills/ask/scripts/ask.py'); write(script, {});
  assert.equal(locateAsk({ HOME: path.join(directory, 'empty-home') }, path.join(inner, 'plugins/text/skills/text')), script);
});
test('stale socket from a killed fake server recovers; live and non-socket paths refuse', async t => {
  const dir = temp(t), socket = path.join(dir, 'fake.sock');
  const child = spawn(process.execPath, ['-e', "require('node:net').createServer().listen(process.argv[1],()=>process.stdout.write('ready'))", require('../engine/lib/socket.cjs').address(socket)]);
  t.after(() => { if (child.exitCode === null && child.signalCode === null) child.kill('SIGKILL'); });
  await new Promise((resolve, reject) => { child.stdout.once('data', resolve); child.once('error', reject); });
  const ended = new Promise(resolve => child.once('exit', resolve)); child.kill('SIGKILL'); await ended;
  assert.equal(fs.existsSync(socket), true); await ensureSocketFree(socket); assert.equal(fs.existsSync(socket), false);
  const server = net.createServer(); await new Promise(resolve => server.listen(require('../engine/lib/socket.cjs').address(socket), resolve)); t.after(() => server.close());
  await assert.rejects(ensureSocketFree(socket), { code: 'SERVICE_RUNNING' });
  const occupied = path.join(dir, 'file'); fs.writeFileSync(occupied, 'keep');
  await assert.rejects(ensureSocketFree(occupied), { code: 'SOCKET_PATH_OCCUPIED' }); assert.equal(fs.readFileSync(occupied, 'utf8'), 'keep');
});
test('daemon validates /ask before attempting to load or connect WhatsApp', async t => {
  const directory = temp(t);
  write(path.join(directory, 'config.json'), { host: 'test', homeHost: 'test', ownerChat: 'owner@lid', owners: ['owner@lid'], aiPrefix: 'AI: ' });
  await assert.rejects(promisify(execFile)(process.execPath, [path.resolve(__dirname, '../engine/daemon.cjs')], { env: { ...process.env, TEXT_HOME: directory, ASK_SCRIPT: '/missing/ask.py' } }), e => {
    assert.match(e.stderr, /ASK_MISSING/); assert.doesNotMatch(e.stderr, /Cannot find package 'baileys'/); return true;
  });
  assert.equal(fs.existsSync(path.join(directory, 'auth')), false); assert.equal(fs.existsSync(path.join(directory, 'engine.sock')), false);
});
test('installed daemon finds its recorded dependency without ASK_SCRIPT in the environment', async t => {
  const directory = temp(t), script = path.join(directory, 'independent-ask.py'); fs.writeFileSync(script, '# fake dependency\n');
  write(path.join(directory, 'config.json'), { host:'test',homeHost:'test',ownerChat:'owner@lid',owners:['owner@lid'],aiPrefix:'AI: ',askScript:script });
  const env = { ...process.env, TEXT_HOME:directory }; delete env.ASK_SCRIPT;
  await assert.rejects(promisify(execFile)(process.execPath,[path.resolve(__dirname,'../engine/daemon.cjs')],{env}), e => {
    assert.match(e.stderr,/INVALID_RULES/); assert.doesNotMatch(e.stderr,/ASK_MISSING|Cannot find package/); return true;
  });
});
test('secondary service account and concrete TEXT_HOME select the installed daemon without nesting accounts twice',async t=>{
  const root=temp(t),directory=path.join(root,'accounts/us'),runtime=path.join(directory,'runtime');
  fs.cpSync(path.resolve(__dirname,'..'),runtime,{recursive:true});
  write(path.join(directory,'config.json'),{host:'test',homeHost:'test',ownerChat:'owner@lid',owners:['owner@lid'],aiPrefix:'AI: ',account:'us',runtimeRoot:root});
  const env={...process.env,TEXT_HOME:directory,TEXT_ACCOUNT:'us',ASK_SCRIPT:'/missing/ask.py'};
  await assert.rejects(promisify(execFile)(process.execPath,[path.join(runtime,'engine/daemon.cjs')],{env}),e=>{
    assert.match(e.stderr,/ASK_MISSING/);assert.doesNotMatch(e.stderr,/CONFIG_REQUIRED|Cannot find package/);return true;
  });
  assert.equal(fs.existsSync(path.join(directory,'accounts')),false);assert.equal(fs.existsSync(path.join(directory,'auth')),false);
});
