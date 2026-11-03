const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { install, doctor, pairRemote,installRemote,discover } = require('../scripts/setup.cjs');
const { write } = require('../engine/lib/store.cjs');
const config = { host: 'test', homeHost: 'test', ownerChat: 'owner@lid', owners: ['owner@lid'], aiPrefix: 'AI: ' };
test('M2: remote installation stages the plugin over stdin without needing a remote text entrance',async t=>{
  const staging=fs.mkdtempSync(path.join(os.tmpdir(),'text-remote-stage-'));t.after(()=>fs.rmSync(staging,{recursive:true,force:true}));
  assert.equal(typeof installRemote,'function');let got,bytes=0;const {EventEmitter}=require('node:events'),{PassThrough}=require('node:stream');
  const result=await installRemote({host:'peer.invalid',env:{TMPDIR:staging},config:{...config,host:'peer.invalid'},bundle:{version:1,rules:[]},execute:(cmd,args)=>{
    assert.ok(fs.readdirSync(staging).some(n=>n.startsWith('text-remote-install-')));
    got={cmd,args};const child=new EventEmitter();child.stdout=new PassThrough();child.stderr=new PassThrough();child.stdin=new PassThrough();child.stdin.on('data',data=>bytes+=data.length);child.stdin.on('finish',()=>{child.stdout.end('{"ok":true}');child.emit('close',0);});return child;
  }});assert.equal(result.ok,true);assert.equal(got.cmd,'ssh');assert.ok(!got.args.at(-1).includes('/bin/text-setup'));assert.ok(bytes>1000);
});
test('M2/discovery: inventory opens no SSH until a host is chosen, then reports both dependencies',async t=>{
  const home=fs.mkdtempSync(path.join(os.tmpdir(),'text-discover-'));t.after(()=>fs.rmSync(home,{recursive:true,force:true}));const calls=[];
  const execute=async(cmd,args)=>{calls.push([cmd,args]);return {stdout:cmd==='tailscale'?JSON.stringify({Peer:{fixture:{HostName:'peer.invalid'}}}):JSON.stringify({textInstalled:false,askInstalled:true})};};
  await discover(execute,{env:{HOME:home}});assert.equal(calls.filter(c=>c[0]==='ssh').length,0);
  const result=await discover(execute,{env:{HOME:home},selected:['peer.invalid']});assert.equal(result.hosts[0].textInstalled,false);assert.equal(result.hosts[0].askInstalled,true);
});
test('M1: upgrade applies an explicit replacement configuration',async t=>{
  const directory=fs.mkdtempSync(path.join(os.tmpdir(),'text-upgrade-'));t.after(()=>fs.rmSync(directory,{recursive:true,force:true}));const dependency=path.join(directory,'ask.py');fs.writeFileSync(dependency,'# fixture');
  const options={directory,config,bundle:{version:1,rules:[]},env:{ASK_SCRIPT:dependency},execute:async()=>({})};await install(options);await install({...options,upgrade:true,config:{...config,aiPrefix:'Assistant: '}});
  assert.equal(JSON.parse(fs.readFileSync(path.join(directory,'config.json'))).aiPrefix,'Assistant: ');
});
test('M9: missing runtime cannot make install overwrite state; customized source blocks an upgrade',async t=>{
  const directory=fs.mkdtempSync(path.join(os.tmpdir(),'text-preserve-'));t.after(()=>fs.rmSync(directory,{recursive:true,force:true}));const dependency=path.join(directory,'ask.py');fs.writeFileSync(dependency,'# fixture');
  const options={directory,config,bundle:{version:1,rules:[]},env:{ASK_SCRIPT:dependency},execute:async()=>({})};await install(options);
  const custom=path.join(directory,'runtime/engine/custom.cjs');fs.writeFileSync(custom,'module.exports="user change";');
  await assert.rejects(install({...options,upgrade:true}),{code:'CUSTOMIZED_RUNTIME'});assert.equal(fs.readFileSync(custom,'utf8'),'module.exports="user change";');
  fs.rmSync(path.join(directory,'runtime'),{recursive:true});const before=fs.readFileSync(path.join(directory,'config.json'),'utf8');
  await assert.rejects(install(options),{code:'ALREADY_INSTALLED'});assert.equal(fs.readFileSync(path.join(directory,'config.json'),'utf8'),before);
});
test('install isolated source runtime; npm only at destination; no service activation', async t => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'text-install-')); t.after(() => fs.rmSync(directory, { recursive: true, force: true }));
  const dependency = path.join(directory, 'external/ask.py'); fs.mkdirSync(path.dirname(dependency)); fs.writeFileSync(dependency, '# independent ask dependency\n');
  const calls = [];
  const result = await install({ directory, config, bundle: { version: 1, rules: [] }, env: { ASK_SCRIPT: dependency }, execute: async (...args) => { calls.push(args); return {}; } });
  assert.equal(result.serviceInstalled, false); assert.equal(result.linked, false);
  assert.equal(calls.length, 1); assert.equal(calls[0][0], 'npm'); assert.ok(calls[0][2].cwd.startsWith(directory));
  assert.equal(fs.existsSync(path.join(directory, 'runtime/skills/ask')), false);
  assert.ok(fs.existsSync(path.join(directory, 'runtime/engine/baileys/package-lock.json')));
  const installedAsk = require(path.join(directory, 'runtime/engine/lib/ask.cjs'));
  assert.equal(installedAsk.locateAsk({ ASK_SCRIPT: dependency }), dependency);
  assert.match(fs.readFileSync(path.join(directory, 'runtime/bin/text-relay'), 'utf8'), new RegExp(process.execPath.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')));
  assert.match(fs.readFileSync(path.join(directory, 'service/com.devdroid.text.plist'), 'utf8'), /TEXT_HOME/);
  assert.match(fs.readFileSync(path.join(directory, 'service/devdroid-text.service'), 'utf8'), /ExecStart=/);
  assert.equal(JSON.parse(fs.readFileSync(path.join(directory, 'config.json'))).askScript, dependency);
  assert.match(fs.readFileSync(path.join(directory, 'service/com.devdroid.text.plist'), 'utf8'), /ThrottleInterval/);
  assert.ok(!fs.readFileSync(path.join(directory, 'service/devdroid-text.service'), 'utf8').includes('ASK_SCRIPT='));
  await assert.rejects(install({ directory, config, bundle: { version: 1, rules: [] } }), { code: 'ALREADY_INSTALLED' });
});
test('install refuses a missing ask dependency before installing or writing runtime state', async t => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'text-dependency-')); t.after(() => fs.rmSync(directory, { recursive: true, force: true }));
  await assert.rejects(install({ directory: path.join(directory, 'runtime-root'), config, bundle: { version: 1, rules: [] }, env: { ASK_SCRIPT: path.join(directory, 'missing.py') }, execute: async () => assert.fail('npm must not run') }), { code: 'ASK_MISSING' });
  assert.equal(fs.existsSync(path.join(directory, 'runtime-root')), false);
});
test('doctor never sends; reports absent round trip instead of claiming success', async t => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'text-doctor-')); t.after(() => fs.rmSync(directory, { recursive: true, force: true }));
  const dependency=path.join(directory,'ask.py');fs.writeFileSync(dependency,['--outcome','--exact','--mid-turn','--worker-receipt','--worker-stream','--worker-input'].map(flag=>'p.add_argument('+JSON.stringify(flag)+')').join('\n'));
  const execute=async(cmd,args)=>{if(cmd==='launchctl')return {stdout:'state = running'};if(cmd==='systemctl')return {stdout:'ActiveState=active\nSubState=running\n'};return {};};
  await install({directory,config,bundle:{version:1,rules:[]},env:{ASK_SCRIPT:dependency},execute});
  for(const name of ['baileys','pino','qrcode-terminal','sharp','link-preview-js']){const folder=path.join(directory,'runtime/engine/baileys/node_modules',name);fs.mkdirSync(folder,{recursive:true});fs.writeFileSync(path.join(folder,'index.js'),'module.exports={};');}
  write(path.join(directory, 'auth/creds.json'), { me: { id: 'bot' }, account: { details: 'signed' } });
  const call = async (_socket, req) => { assert.deepEqual(req, { cmd: 'ping' }); return { ok: true, ready: true }; };
  const absent = await doctor({ directory, call, execute,env:{HOME:directory} }); assert.equal(absent.ok, false); assert.equal(absent.checks.roundTrip, 'not-verified');
  write(path.join(directory, 'state.json'), { deliveries: { d: { replyTo: 'phone-reply', receipt: { status: 'delivered' }, responseReceipt: { ok: true, id: 'answer' } } }, sent: { answer: { status: 'sent' } } });
  assert.equal((await doctor({ directory, call, execute,env:{HOME:directory}, roundTripId: 'phone-reply' })).ok, true);
  write(path.join(directory,'state.json'),{deliveries:{d:{replyTo:'phone-reply',receipt:{status:'accepted'},responseReceipt:{ok:true,id:'answer',verifiedBy:'inspection'}}},sent:{answer:{status:'sent'}}});
  assert.equal((await doctor({directory,call,execute,env:{HOME:directory},roundTripId:'phone-reply'})).checks.roundTrip,false);
});
test('remote pairing starts only the named runtime link mode and streams code; never run here', () => {
  let got;
  pairRemote('box', '0'.repeat(11), (...args) => { got = args; return {stdin:{end(){}}}; });
  assert.equal(got[0], 'ssh'); assert.equal(got[1].at(-2), 'box'); assert.match(got[1].at(-1), /text-setup.*link --phone-stdin/);
  assert.deepEqual(got[2].stdio, ['pipe','inherit','inherit']);
  assert.throws(() => pairRemote('box;bad', '0'.repeat(11)), { code: 'PAIR_ARGUMENT' });
});
test('L5: pairing phone digits travel over stdin and never appear in SSH argv',()=>{
  let args,input;const phone='0'.repeat(11);pairRemote('peer.invalid',phone,(_cmd,argv,options)=>{args=argv;assert.deepEqual(options.stdio,['pipe','inherit','inherit']);return {stdin:{end:value=>input=value}};},'us');
  assert.ok(!args.join(' ').includes(phone));assert.match(args.at(-1),/--phone-stdin --account us/);assert.equal(input,phone+'\n');
});

test('link accepts --window and refuses it with --phone-stdin', () => {
  const run=args=>require('node:child_process').spawnSync(process.execPath,[path.join(__dirname,'../scripts/setup.cjs'),'link',...args],{input:'',encoding:'utf8',env:{...process.env,TEXT_HOME:fs.mkdtempSync(path.join(os.tmpdir(),'text-link-'))}});
  assert.doesNotMatch(run(['--window']).stderr,/OPTION_NOT_APPLICABLE/);
  assert.match(run(['--window','--phone-stdin']).stderr,/PAIR_ARGUMENT/);
});

test('a config without a label gets the platform label',()=>{
  const {configOK}=require('../scripts/setup.cjs');
  const c=configOK({host:'mac',homeHost:'mac',ownerChat:'owner@lid',owners:['owner@lid']});assert.equal(c.aiPrefix,'devdroid — ');
  assert.equal(configOK({host:'mac',homeHost:'mac',ownerChat:'owner@lid',owners:['owner@lid'],aiPrefix:'Mine: '}).aiPrefix,'Mine: ');
});
