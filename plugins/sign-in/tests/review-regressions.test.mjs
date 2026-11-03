import './environment.mjs';
import test from 'node:test';import assert from 'node:assert/strict';import fs from 'node:fs';import path from 'node:path';import cp from 'node:child_process';import {syncBuiltinESMExports} from 'node:module';import {promisify} from 'node:util';import {EventEmitter,once} from 'node:events';
import {readCommand,keepSignIn,handoffOwner,signInFile,window as signInWindow} from '../lib/sign-in.mjs';
import {locked,atomicJSON,read,processStart,ROOT,writeNew} from '../lib/core.mjs';import {create} from '../lib/accounts.mjs';import {up} from '../browser/ext-bridge.mjs';import {config,account,temp,observed,fakeCLI} from './support.mjs';
const exec=promisify(cp.execFile);
test('F1 read parser rejects profile overrides in every position and mutation commands',t=>{
  const {a}=config(t),s=a.sites.handshake;
  for(const args of [
    ['browser','--session','task','extract','--profile=foreign'],['browser','--profile','foreign','--session','task','extract'],['browser','--session','task','extract','--pro=foreign'],
    ...['check','uncheck','select','dialog','click','drag','keys','upload','eval','chrome'].map(op=>['browser','--session','task',op,'[1]']),['browser','--session','login-human','extract'],
  ])assert.throws(()=>readCommand(args,s));
  assert.deepEqual(readCommand(['browser','--session','task','extract'],s),['browser','--session','task','--window','background','extract']);
});
test('F2 obsolete keeper cannot complete, heartbeat or park a replacement generation',async t=>{
  const {file,dir,a}=config(t),stateFile=path.join(dir,'handoff.json');
  const state={owner:handoffOwner(file,'work',a,'handshake'),generation:'old',account:'work',site:'handshake',file,session:'login-old',contextId:'ctx',status:'waiting-for-user'};atomicJSON(stateFile,state);let calls=0,parks=0;
  await keepSignIn(stateFile,{execute:async()=>{if(++calls===2)atomicJSON(stateFile,{...state,generation:'new',session:'login-new',status:'waiting-for-user'});return observed;},delay:async()=>{},park:async()=>{parks++;}});
  assert.equal(read(stateFile).generation,'new');assert.equal(read(stateFile).status,'waiting-for-user');assert.equal(read(stateFile).heartbeatAt,undefined);assert.equal(parks,0);
});
test('F3 state namespace includes account source and physical bridge, and reuse verifies the bridge',async t=>{
  const {file,dir,a}=config(t),second=path.join(dir,'second.json'),b=structuredClone(a);b.bridge.profileDir=path.join(dir,'different-profile');atomicJSON(second,{version:1,accounts:{work:b}});
  const firstState=signInFile(file,'work',a,'handshake',dir),secondState=signInFile(second,'work',b,'handshake',dir);assert.notEqual(firstState,secondState);
  atomicJSON(firstState,{owner:handoffOwner(file,'work',a,'handshake'),generation:'first',file,account:'work',site:'handshake',session:'login-first',contextId:'ctx',status:'waiting-for-user',pid:process.pid,workerStart:processStart(process.pid)});
  let verified=0;const result=await signInWindow(second,'work',b,'handshake',{root:dir,guard:async()=>{},verify:async()=>{verified++;return {contextId:'ctx'};},execute:async()=>({}),worker:async()=>({pid:process.pid})});
  assert.equal(verified,1);assert.notEqual(result.session,'login-first');assert.equal(result.reused,undefined);assert.equal(read(firstState).session,'login-first');
});
test('F5 unknown process inspection never steals a live operation lock',async t=>{
  const dir=temp(t),file=path.join(dir,'op');let entered=false;
  await locked(file,async()=>{const original=cp.execFileSync;cp.execFileSync=function(bin,...args){if(bin==='ps')throw Error('inspection unavailable');return original.call(this,bin,...args);};syncBuiltinESMExports();try{await assert.rejects(locked(file,async()=>{entered=true;}),{code:'BUSY'});}finally{cp.execFileSync=original;syncBuiltinESMExports();}});assert.equal(entered,false);
});
test('F6 verified ownership survives initial readiness failure and a retry',async t=>{
  const dir=temp(t),extension=path.join(dir,'ext'),binary=path.join(dir,'chrome');fs.mkdirSync(extension);fs.writeFileSync(path.join(extension,'manifest.json'),JSON.stringify({version:'1.0.26'}));fs.writeFileSync(binary,'fake');
  const b={profileDir:path.join(dir,'profile'),port:19340,contextIds:[]};
  const deps={root:dir,pid:async()=>null,compatible:async()=>{},execute:async()=>({stdout:JSON.stringify({profiles:[]})}),portFree:async()=>{},recover:async()=>{},layer:{binary,extension},ownership:async()=> 'owned-start',spawn:()=>{const c=new EventEmitter();c.pid=12345;c.unref=()=>{};queueMicrotask(()=>c.emit('spawn'));return c;},ready:f=>f(),probe:async()=>{throw Error('not ready');}};
  await assert.rejects(up('work',b,deps),/not ready/);const receipt=path.join(dir,'owned/work.json');assert.equal(read(receipt).start,'owned-start');
  await assert.rejects(up('work',b,{...deps,pid:async()=>12345}),/not ready/);assert.equal(read(receipt).pid,12345);
});
test('F7 a wrong physical receiver cannot accept its own different local account record',async t=>{
  const sender=fakeCLI(t),receiver=config(t),data=read(sender.file);data.accounts.work.machine.id='f'.repeat(64);data.accounts.work.machine.ssh='wrong-destination';data.accounts.work.machine.cli=path.join(ROOT,'scripts/sign-in.mjs');atomicJSON(sender.file,data);
  const script=path.join(sender.dir,'wrong-ssh.mjs');fs.writeFileSync(script,`#!/usr/bin/env node\nimport {spawnSync} from 'node:child_process';const r=spawnSync(process.execPath,${JSON.stringify([path.join(ROOT,'scripts/sign-in.mjs'),'--accounts',receiver.file,'--on-holder','f'.repeat(64),'check','work'])},{env:process.env,encoding:'utf8'});process.stdout.write(r.stdout);process.stderr.write(r.stderr);process.exit(r.status);`,{mode:0o700});
  await assert.rejects(exec(process.execPath,[path.join(ROOT,'scripts/sign-in.mjs'),'--accounts',sender.file,'check','work'],{env:{...sender.env,SSH_BIN:script}}),e=>JSON.parse(e.stderr).code==='WRONG_MACHINE');
});
test('F8 symlinked parents reserve a missing profile leaf before any directory exists',async t=>{
  const {file,dir,a}=config(t);a.bridge.driveBlocked=true;atomicJSON(file,{version:1,accounts:{work:a}});const parent=path.dirname(a.bridge.profileDir);fs.mkdirSync(parent,{recursive:true});const alias=path.join(dir,'same-parent');fs.symlinkSync(parent,alias);
  const b=account(dir,'other');b.bridge.contextIds=[];b.bridge.port++;b.bridge.profileDir=path.join(alias,'work');
  await assert.rejects(create(file,'other',b,{execute:async()=>({stdout:JSON.stringify({profiles:[]})})}),{code:'ACCOUNTS_CONFLICT'});assert.equal(read(file).accounts.other,undefined);assert.equal(read(file).accounts.work.bridge.driveBlocked,true);
});
test('F4 detached watchdog owns and expires its foreground tunnel after caller death',async t=>{
  const hooks=[],lifecycle={after:fn=>hooks.push(fn)};t.after(async()=>{const errors=[];for(const hook of hooks.reverse())try{await hook();}catch(e){errors.push(e);}if(errors.length)throw new AggregateError(errors,'Test cleanup failed');});
  const dir=temp(lifecycle),stateFile=path.join(dir,'tunnel.json'),expireFile=path.join(dir,'expire'),doneFile=path.join(dir,'done.json'),ownedFile=path.join(dir,'owned.json');
  const fake=path.join(dir,'ssh.mjs'),keeper=path.join(dir,'keeper.mjs'),wrapper=path.join(dir,'caller.mjs');
  fs.writeFileSync(fake,`#!/usr/bin/env node
process.send({bound:true});setInterval(()=>{},1000);`,{mode:0o700});
  writeNew(stateFile,{open:[fake,'-M','-S',path.join(dir,'control'),'-N'],close:[fake,'-S',path.join(dir,'control'),'-O','exit','--','holder'],expiresAt:100,status:'opening'});
  fs.writeFileSync(keeper,`
import fs from 'node:fs';import {spawn} from 'node:child_process';import {once} from 'node:events';
import {expireTunnel} from ${JSON.stringify(new URL('../lib/sign-in.mjs',import.meta.url).href)};
import {ownForward} from ${JSON.stringify(new URL('../lib/tunnel.mjs',import.meta.url).href)};
import {atomicJSON,processStart} from ${JSON.stringify(new URL('../lib/core.mjs',import.meta.url).href)};
const paths=${JSON.stringify({stateFile,expireFile,doneFile,ownedFile})};let time=0,wake,child,exited,bound;
const owned={keeper:{pid:process.pid,start:processStart(process.pid)}};atomicJSON(paths.ownedFile,owned);
const watcher=fs.watch(${JSON.stringify(dir)},()=>{if(fs.existsSync(paths.expireFile)){time=100;wake?.();}});
try{
 await expireTunnel(paths.stateFile,{
  now:()=>time,delay:()=>new Promise(resolve=>{wake=resolve;if(time===100)resolve();}),
  start:state=>ownForward(state,{
   now:()=>time,delay:()=>exited,
   launch:(bin,args)=>{child=spawn(bin,args,{stdio:['ignore','ignore','ignore','ipc']});exited=new Promise(resolve=>{child.once('exit',resolve);child.once('error',resolve);});bound=once(child,'message').catch(()=>[{failed:true}]);child.once('spawn',()=>{owned.forward={pid:child.pid,start:processStart(child.pid)};atomicJSON(paths.ownedFile,owned);});return child;},
   execute:async()=>{const [message]=await Promise.race([bound,exited.then(()=>{throw Error('Fake forwarding stopped before readiness');})]);if(!message.bound)throw Error('Unexpected forwarding message');return {stdout:'',stderr:'Master running'};},
  }),
  ready:()=>{process.send({ready:true});},
 });
 atomicJSON(paths.doneFile,{ok:true});
}catch(e){atomicJSON(paths.doneFile,{ok:false,code:e.code||'WATCHDOG_FAILED'});process.exitCode=1;}
finally{watcher.close();}
`);
  fs.writeFileSync(wrapper,`import {spawn} from 'node:child_process';const c=spawn(process.execPath,${JSON.stringify([keeper])},{detached:true,stdio:['ignore','ignore','ignore','ipc']});let ready=false;c.on('message',m=>{ready=!!m.ready;process.send(m);});c.on('error',()=>process.exit(1));c.on('exit',()=>{if(!ready)process.exit(1);});c.unref();process.once('message',()=>process.kill(process.pid,'SIGKILL'));`);
  const done=new Promise((resolve,reject)=>{const watcher=fs.watch(dir,()=>{if(fs.existsSync(doneFile)){watcher.close();try{resolve(read(doneFile));}catch(e){reject(e);}}});lifecycle.after(()=>watcher.close());});
  const caller=cp.spawn(process.execPath,[wrapper],{stdio:['ignore','ignore','ignore','ipc']}),exited=once(caller,'exit');
  lifecycle.after(async()=>{if(caller.exitCode===null&&!caller.signalCode){caller.kill('SIGTERM');await exited;}if(fs.existsSync(ownedFile))for(const owner of Object.values(read(ownedFile)))if(owner.start&&processStart(owner.pid)===owner.start)try{process.kill(owner.pid,'SIGTERM');}catch{}});
  const [message]=await Promise.race([once(caller,'message'),exited.then(()=>{throw Error('Caller/keeper stopped before readiness');})]);assert.equal(message.ready,true);
  caller.send({die:true});const [,signal]=await exited;assert.equal(signal,'SIGKILL');
  assert.equal(read(stateFile).status,'open');const forward=read(ownedFile).forward;assert.doesNotThrow(()=>process.kill(forward.pid,0));
  fs.writeFileSync(expireFile,'expire');assert.deepEqual(await done,{ok:true});assert.equal(read(stateFile).status,'expired');
  assert.throws(()=>process.kill(forward.pid,0),e=>e.code==='ESRCH');
});
