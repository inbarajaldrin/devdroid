import './environment.mjs';
import test from 'node:test';import assert from 'node:assert/strict';import fs from 'node:fs';import path from 'node:path';
import { setup,doctor,fileHashes,download,digest,verifyRuntime } from '../lib/setup.mjs';
import { pins } from '../browser/settings.mjs';import { read,ROOT } from '../lib/core.mjs';import {layer} from './support.mjs';
test('runtime is pinned and every npm dependency has integrity',()=>{const files=verifyRuntime();assert.ok(files['scripts/keeper.mjs']);const lock=read(path.join(ROOT,'browser/package-lock.json'));for(const [key,p]of Object.entries(lock.packages))if(key)assert.ok(p.integrity&&p.resolved.startsWith('https://registry.npmjs.org/'));});
test('setup installs browser layer only in a temporary root, no daemon or website command',async t=>{
  const f=layer(t),r=await setup(f.file,f.source,f.deps);assert.equal(r.installed,true);assert.equal(r.bridgesStarted,false);assert.equal(fs.existsSync(path.join(f.root,'profiles')),false);assert.ok(fs.existsSync(path.join(f.root,'runtime/scripts/keeper.mjs')));assert.deepEqual(read(f.file),read(f.source));
  assert.ok(!f.calls.some(c=>c.args?.some(a=>['doctor','bridge-up','browser'].includes(a))));assert.equal((await doctor(f.file,f.deps)).healthy,true);
});
test('setup reuses a byte-identical pre-existing official extension without replacing its directory',async t=>{
  const f=layer(t),ext=path.join(f.root,'opencli-extension',pins.extension.version);fs.mkdirSync(ext,{recursive:true});fs.writeFileSync(path.join(ext,'manifest.json'),JSON.stringify({version:pins.extension.version}));const inode=fs.statSync(ext).ino;
  await setup(f.file,f.source,f.deps);assert.equal(fs.statSync(ext).ino,inode);assert.equal((await doctor(f.file,f.deps)).healthy,true);
});
test('setup refuses a differing extension and preserves its existing bytes',async t=>{
  const f=layer(t),ext=path.join(f.root,'opencli-extension',pins.extension.version);fs.mkdirSync(ext,{recursive:true});fs.writeFileSync(path.join(ext,'manifest.json'),'do not replace');const before=fileHashes(ext);
  await assert.rejects(setup(f.file,f.source,f.deps),{code:'INSTALL_CHANGED'});assert.deepEqual(fileHashes(ext),before);assert.equal(fs.existsSync(path.join(f.root,'opencli')),false);
});
test('doctor never calls stock doctor and refuses shared hosts',async t=>{
  const f=layer(t);await setup(f.file,f.source,f.deps);f.calls.length=0;
  await doctor(f.file,f.deps);assert.ok(!f.calls.some(c=>c.args?.includes('doctor')));
  await assert.rejects(doctor(f.file,{...f.deps,shared:async()=>['another-human']}),e=>e.code==='DOCTOR_FAILED'&&e.detail.checks.some(c=>c.code==='SHARED_HOST'));
});
test('doctor skips all execution after failed installed integrity',async t=>{
  const f=layer(t);await setup(f.file,f.source,f.deps);fs.appendFileSync(path.join(f.root,'opencli/node_modules/.bin/opencli'),'tampered');let executions=0;
  await assert.rejects(doctor(f.file,{...f.deps,execute:async()=>{executions++;throw Error('must not execute');}}),e=>e.code==='DOCTOR_FAILED'&&e.detail.checks.some(c=>c.skipped));assert.equal(executions,0);
});
test('doctor reports daemon conflicts from status without starting anything',async t=>{
  const f=layer(t);await setup(f.file,f.source,f.deps);await assert.rejects(doctor(f.file,{...f.deps,status:async()=>({daemonVersion:'1.8.7'})}),e=>e.code==='DOCTOR_FAILED'&&e.detail.checks.some(c=>c.code==='DAEMON_CONFLICT'));
});
test('real downloader checks fake response bytes before writing archive',async t=>{
  const f=layer(t),dest=path.join(f.dir,'archive.zip'),bytes=Buffer.from('trusted');
  await assert.rejects(download('https://example.invalid/',dest,pins.extension.sha256,'sha256','hex',async()=>({ok:true,arrayBuffer:async()=>bytes})),{code:'CHECKSUM_MISMATCH'});assert.equal(fs.existsSync(dest),false);
  await download('https://example.invalid/',dest,digest(bytes),'sha256','hex',async()=>({ok:true,arrayBuffer:async()=>bytes}));assert.deepEqual(fs.readFileSync(dest),bytes);
});
test('concurrent account creation during setup is preserved before publication',async t=>{
  const f=layer(t);let raced=false;await assert.rejects(setup(f.file,f.source,{...f.deps,download:async(...args)=>{await f.deps.download(...args);if(!raced){raced=true;fs.writeFileSync(f.file,JSON.stringify({version:1,accounts:{}}));}}}),{code:'ACCOUNTS_EXISTS'});assert.deepEqual(read(f.file),{version:1,accounts:{}});assert.equal(fs.existsSync(path.join(f.root,'opencli')),false);
});
test('a newer plugin upgrades only the installed runtime folder and keeps the old one',async t=>{
  const f=layer(t);await setup(f.file,f.source,f.deps);
  const manifest=path.join(f.root,'runtime/browser/runtime.sha256.json');fs.writeFileSync(manifest,'{"older":"runtime"}');
  const opencli=fileHashes(path.join(f.root,'opencli'));
  const r=await setup(f.file,null,f.deps);
  assert.ok(r.runtimeUpgraded&&r.runtimeUpgraded.backup&&fs.existsSync(r.runtimeUpgraded.backup));
  assert.deepEqual(read(manifest),read(path.join(ROOT,'browser/runtime.sha256.json')));
  assert.deepEqual(fileHashes(path.join(f.root,'opencli')),opencli);
  assert.equal((await setup(f.file,null,f.deps)).runtimeUpgraded,undefined);
});
