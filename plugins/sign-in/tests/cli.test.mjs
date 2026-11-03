import './environment.mjs';
import test from 'node:test';import assert from 'node:assert/strict';import fs from 'node:fs';import path from 'node:path';import {execFile} from 'node:child_process';import {promisify} from 'node:util';
import {ROOT,read,atomicJSON} from '../lib/core.mjs';import {fakeCLI} from './support.mjs';
const exec=promisify(execFile);
async function invoke(f,args,patch={}){try{const r=await exec(process.execPath,[path.join(ROOT,'scripts/sign-in.mjs'),'--accounts',f.file,...args],{env:{...f.env,...patch}});return {ok:true,data:JSON.parse(r.stdout).data};}catch(e){return {ok:false,error:JSON.parse(e.stderr)};}}
test('CLI list/permission are local; QR and free-text grants are not accepted',async t=>{
  const f=fakeCLI(t);assert.equal((await invoke(f,['list'])).data.length,1);const p=await invoke(f,['permission','work','--site','handshake','--action','edit']);assert.equal(p.data.needsUserOK,true);assert.equal(p.data.advisory,true);
  assert.equal((await invoke(f,['open','work','--qr','/tmp/image'])).error.code,'USAGE');assert.equal((await invoke(f,['grant','work','--site','handshake','--action','edit','--resource','profile'])).error.code,'HUMAN_TERMINAL_REQUIRED');assert.equal(f.calls().length,0);
});
test('CLI check uses its own random lease, settles identity and releases it',async t=>{
  const f=fakeCLI(t),r=await invoke(f,['check','work']);assert.equal(r.ok,true);assert.equal(r.data.status,'signed-in');assert.equal(JSON.stringify(r).includes('token'),false);
  assert.ok(f.calls().findIndex(c=>c.args.includes('bridge-up'))<f.calls().findIndex(c=>c.args.includes('browser')));
  const commands=f.calls().filter(c=>c.args.includes('browser'));assert.ok(commands.length>=4);assert.equal(commands.at(-1).args.at(-1),'close');assert.equal(commands.at(-1).ownedCleanup,'1');assert.ok(commands.every(c=>c.args[c.args.indexOf('--session')+1].startsWith('check-')));
});
test('signed out refuses without credential actions and closes only its check lease',async t=>{
  const f=fakeCLI(t),r=await invoke(f,['check','work'],{OBSERVED:JSON.stringify({state:'signed-out',identity:{cookie:'never-print'}})});assert.equal(r.error.code,'SIGNED_OUT');assert.equal(JSON.stringify(r).includes('never-print'),false);const calls=f.calls();assert.ok(!calls.some(c=>c.args.some(v=>['fill','type','keys'].includes(v))));assert.equal(calls.filter(c=>c.args.includes('browser')).at(-1).args.at(-1),'close');
});
test('propose checks identity and leaves both global instructions byte-identical',async t=>{
  const f=fakeCLI(t),files=['.claude/CLAUDE.md','.codex/AGENTS.md'].map(n=>path.join(f.dir,n)),text='Existing instructions\nSigned-in sites (use /sign-in): youtube\n';
  for(const file of files){fs.mkdirSync(path.dirname(file),{recursive:true});fs.writeFileSync(file,text);}
  const r=await invoke(f,['propose','work']);assert.equal(r.ok,true);assert.equal(r.data.written,false);for(const file of files)assert.equal(fs.readFileSync(file,'utf8'),text);
});
test('remote drive delegates to the holder, never uses the laptop context locally',async t=>{
  const f=fakeCLI(t),d=read(f.file);d.accounts.work.machine.id='f'.repeat(64);d.accounts.work.machine.ssh='holder';d.accounts.work.machine.cli='/remote/sign-in.mjs';atomicJSON(f.file,d);
  const r=await invoke(f,['drive','work','--site','handshake','--','browser','--session','task-read','extract']);assert.equal(r.ok,true);assert.equal(r.data.trust,'untrusted');assert.equal(r.data.data,'synthetic remote text');const calls=f.calls();assert.equal(calls.length,1);assert.ok(calls[0].args.at(-1).includes("'--on-holder'"));assert.ok(calls[0].args.at(-1).includes("'drive'"));assert.ok(calls[0].args.at(-1).includes("'extract'"));
});
test('delegated scalar results retain their type inside the verified holder envelope',async t=>{
  const f=fakeCLI(t),r=await invoke(f,['--on-holder',f.a.machine.id,'drive','work','--site','handshake','--','browser','--session','task-read','extract']);
  assert.equal(r.ok,true);assert.equal(r.data.holdingMachine,f.a.machine.id);assert.equal(r.data.result.trust,'untrusted');assert.equal(r.data.result.data,'synthetic extracted text');
});
test('local sign-in gets an acknowledged keeper and a distinct human session',async t=>{
  const f=fakeCLI(t),r=await invoke(f,['open','work']);assert.equal(r.ok,true);assert.ok(r.data.session.startsWith('login-'));assert.ok(r.data.keeperPid>0);
  const calls=f.calls();assert.ok(calls.some(c=>c.bin===f.bins.keeper&&c.args[0]==='sign-in'));assert.ok(calls.some(c=>c.args.includes('show')));assert.ok(!calls.some(c=>c.args.includes('close')));
});
test('remote sign-in creates an expiring loopback tunnel with an acknowledged watchdog',async t=>{
  const f=fakeCLI(t),d=read(f.file);d.accounts.work.machine.id='f'.repeat(64);d.accounts.work.machine.ssh='holder';d.accounts.work.machine.cli='/remote/sign-in.mjs';atomicJSON(f.file,d);
  const before=Date.now(),r=await invoke(f,['open','work','--ttl','60']);assert.equal(r.ok,true);assert.equal(r.data.inspect,'chrome://inspect/#devices');assert.ok(Date.parse(r.data.expiresAt)>=before+60000);
  const calls=f.calls(),tunnel=calls.find(c=>c.args.includes('-L'));assert.ok(tunnel.args[tunnel.args.indexOf('-L')+1].endsWith(`:127.0.0.1:${f.a.bridge.port}`));assert.ok(calls.findIndex(c=>c.bin===f.bins.keeper)<calls.indexOf(tunnel));assert.ok(calls.some(c=>c.bin===f.bins.keeper&&c.args[0]==='tunnel'));assert.deepEqual(r.data.closeTunnel.slice(-4),['-O','exit','--','holder']);
});
test('inspect-only remote view expires without creating a login tab or sign-in keeper',async t=>{
  const f=fakeCLI(t),d=read(f.file);d.accounts.work.machine.id='f'.repeat(64);d.accounts.work.machine.ssh='holder';d.accounts.work.machine.cli='/remote/sign-in.mjs';atomicJSON(f.file,d);
  const r=await invoke(f,['open','work','--inspect-only','--ttl','60']);assert.equal(r.ok,true);assert.equal(r.data.newSignInTab,false);assert.equal(r.data.inspect,'chrome://inspect/#devices');assert.ok(r.data.expiresAt);
  assert.ok(f.calls().some(c=>c.bin===f.bins.keeper&&c.args[0]==='tunnel'));assert.ok(!f.calls().some(c=>c.bin===f.bins.keeper&&c.args[0]==='sign-in'));assert.ok(!f.calls().some(c=>c.bin===f.bins.ssh&&c.args.at(-1).includes("'window'")));
});
test('wrong stable holder refuses before SSH and driveBlocked prevents open/check',async t=>{
  const f=fakeCLI(t),d=read(f.file);d.accounts.work.machine.id='f'.repeat(64);atomicJSON(f.file,d);assert.equal((await invoke(f,['--on-holder','f'.repeat(64),'check','work'])).error.code,'WRONG_MACHINE');assert.equal(f.calls().length,0);
  d.accounts.work.bridge.driveBlocked=true;atomicJSON(f.file,d);assert.equal((await invoke(f,['open','work'])).error.code,'DRIVE_BLOCKED');assert.equal((await invoke(f,['check','work'])).error.code,'DRIVE_BLOCKED');assert.equal(f.calls().length,0);
});
test('phone hand-off is existing approval guidance only, no message or browser call',async t=>{
  const f=fakeCLI(t);assert.equal((await invoke(f,['open','work','--device','phone','--approve'])).error.code,'LAPTOP_REQUIRED');const d=read(f.file);d.accounts.work.sites.handshake.phone={kind:'approve'};atomicJSON(f.file,d);assert.equal((await invoke(f,['open','work','--device','phone','--approve'])).data.method,'approve-on-phone');assert.equal(f.calls().length,0);
});
test('errors redact URL query secrets from child process failures',async t=>{const f=fakeCLI(t),r=await invoke(f,['check','work'],{FAIL:'1'});assert.equal(r.ok,false);assert.equal(JSON.stringify(r).includes('never-print'),false);assert.ok(r.error.error.includes('[url:example.invalid]'));});
