import './environment.mjs';
import test from 'node:test';import assert from 'node:assert/strict';import fs from 'node:fs';import path from 'node:path';import {EventEmitter} from 'node:events';import {PassThrough} from 'node:stream';
import {config} from './support.mjs';import {manualClock} from './clock.mjs';import {login} from '../lib/google.mjs';import {identityScopes} from '../lib/google-contract.mjs';import {handoff,finishHuman} from '../lib/human-lease.mjs';import {protectSession,setSessionState,sessionState} from '../lib/core.mjs';import {canonical} from '../lib/accounts.mjs';
const spawnFake=()=>{const child=new EventEmitter();child.stdout=new PassThrough();child.stderr=new PassThrough();child.exitCode=null;child.kill=()=>{child.killed=true;child.exitCode=1;queueMicrotask(()=>child.emit('close',1));};return child;};
test('login deadline is controlled by its injected clock and still stops the owned listener',async()=>{
 const clock=manualClock(),child=spawnFake();const outcome=login('/fake',{},identityScopes,'fake',async()=>assert.fail('no handoff'),{spawnProcess:()=>child,timeout:5000,clock}).catch(e=>e);
 try{await clock.advance(4999);assert.equal(child.killed,undefined);await clock.advance(1);assert.equal(child.killed,true);assert.equal((await outcome).code,'GOOGLE_CONSENT_TIMEOUT');assert.equal(clock.pending,0);}finally{if(child.exitCode===null)child.kill();await outcome;}
});
test('login escalates a timed-out unresponsive listener at the unchanged kill deadline',async()=>{
 const clock=manualClock(),child=spawnFake(),signals=[];child.kill=signal=>{signals.push(signal);if(signal==='SIGKILL'){child.exitCode=1;queueMicrotask(()=>child.emit('close',1));}};
 const outcome=login('/fake',{},identityScopes,'fake',async()=>assert.fail('no URL'),{spawnProcess:()=>child,timeout:5000,clock}).catch(e=>e);
 await clock.advance(5000);assert.deepEqual(signals,['SIGTERM']);await clock.advance(1999);assert.deepEqual(signals,['SIGTERM']);await clock.advance(1);assert.deepEqual(signals,['SIGTERM','SIGKILL']);assert.equal((await outcome).code,'GOOGLE_CONSENT_TIMEOUT');assert.equal(clock.pending,0);
});
test('human keeper readiness deadline is testable without waiting for wall time',async t=>{
 const f=config(t),clock=manualClock();let began,killed=false;const started=new Promise(resolve=>{began=resolve;});
 const outcome=handoff(f.file,'work',f.a,'google-console-work','verified',f.dir,{clock,timeout:100,start:()=>{const child=new EventEmitter();child.kill=()=>{killed=true;};began();return child;}}).catch(e=>e);
 await started;await clock.advance(100);assert.match((await outcome).message,/did not acknowledge/);assert.equal(killed,true);assert.equal(clock.pending,0);
});
test('human lease recovery accepts the same private root through a symlink alias',async t=>{
 const f=config(t),actual=canonical(f.dir),alias=path.join(f.dir,'root-alias');fs.symlinkSync(actual,alias);const session='google-console-work',clock=manualClock();protectSession(f.a,session,'desktop',alias);setSessionState(f.a,session,{humanActive:true,sourceFile:canonical(f.file),sourceAccount:'work'},alias);
 const start=()=>{const c=new EventEmitter();c.disconnect=()=>{};c.unref=()=>{};c.kill=()=>{};queueMicrotask(()=>c.emit('message',{ready:true}));return c;};
 await handoff(f.file,'work',f.a,session,'verified',alias,{start,clock});let parks=0;await finishHuman(f.file,'work',f.a,actual,{park:async file=>{assert.equal(file,canonical(f.file));parks++;}});assert.equal(parks,1);assert.equal(sessionState(f.a,session,actual).humanActive,false);
});
test('human finish canonicalizes legacy lease pointer and source spellings before ownership checks',async t=>{
 const f=config(t),actual=canonical(f.dir),alias=path.join(f.dir,'legacy-alias');fs.symlinkSync(actual,alias);const session='google-console-work',file=path.join(actual,'human-leases','legacy.json');
 fs.mkdirSync(path.dirname(file),{recursive:true});fs.writeFileSync(file,JSON.stringify({file:path.join(alias,'accounts.json'),name:'work',session,owner:{machine:f.a.machine.id,port:f.a.bridge.port,profile:f.a.bridge.profileDir},status:'waiting'}));
 setSessionState(f.a,session,{sourceFile:canonical(f.file),sourceAccount:'work',humanActive:true,handoffFile:path.join(alias,'human-leases','legacy.json')},actual);
 let parks=0;await finishHuman(f.file,'work',f.a,actual,{park:async()=>{parks++;}});assert.equal(parks,1);assert.equal(JSON.parse(fs.readFileSync(file)).status,'completed');
});
