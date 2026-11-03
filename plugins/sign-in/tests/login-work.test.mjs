import './environment.mjs';
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import {addSite,load,splitAccount} from '../lib/accounts.mjs';
import {withBridge,reapBridge,bridgeWork,lifecycleFile,pinHandoff,IDLE_GRACE,keepBridges} from '../lib/bridge-lifecycle.mjs';
import {atomicJSON,read,processStart} from '../lib/core.mjs';
import {keepSignIn,handoffOwner,judge,check} from '../lib/sign-in.mjs';
import {main} from '../scripts/sign-in.mjs';
import {config,observed} from './support.mjs';
import {manualClock} from './clock.mjs';

const origin='https://music.example.invalid';
const site=()=>({signInUrl:origin+'/',identity:{kind:'generic',url:origin+'/',expected:{}}});
function workFixture(t){
  const f=config(t),clock=manualClock(),calls=[];
  const deps={root:f.dir,now:clock.now,isAlive:()=>true,worker:async(mode)=>{calls.push(mode);return {pid:process.pid};},launch:async(file,name,cmd)=>{calls.push(cmd);return {contextId:'fixture-context'};}};
  return {...f,clock,calls,deps,stateFile:lifecycleFile(f.a,f.dir)};
}
test('one website creates one login; a second identity gets a distinct name, port and profile',async t=>{
  const f=config(t),old=read(f.file).accounts.work;
  const first=await addSite(f.file,undefined,'music',origin),second=await addSite(f.file,'music-work','music',origin),data=load(f.file);
  assert.equal(first.login,'music');assert.equal(second.login,'music-work');
  for(const name of ['music','music-work']){assert.deepEqual(Object.keys(data.accounts[name].sites),['music']);assert.equal(data.accounts[name].primary,'music');assert.equal(fs.existsSync(data.accounts[name].bridge.profileDir),false);}
  assert.notEqual(data.accounts.music.bridge.port,data.accounts['music-work'].bridge.port);assert.notEqual(data.accounts.music.bridge.profileDir,data.accounts['music-work'].bridge.profileDir);
  assert.deepEqual(data.accounts.work,old);
  await assert.rejects(addSite(f.file,'music','music',origin),{code:'ACCOUNT_EXISTS'});
});
test('a first login needs private-machine ownership, and single-site upgrades preserve the profile and facts',async t=>{
  const f=config(t),fresh=path.join(f.dir,'fresh.json');
  await assert.rejects(addSite(fresh,undefined,'music',origin),{code:'PRIVATE_HOLDER_REQUIRED'});
  await addSite(fresh,undefined,'music',origin,{exclusive:true});assert.ok(load(fresh).accounts.music);
  const before=read(f.file).accounts.work;
  const upgrade=await main(['--accounts',f.file,'accounts-upgrade']);assert.deepEqual(upgrade.needsSplit,[]);assert.equal(upgrade.profilesMoved,false);assert.deepEqual(read(f.file).accounts.work,before);
  assert.equal((await splitAccount(f.file,'work')).unchanged,true);
});
test('legacy multi-site profiles split only by explicit choice, preserving one profile and creating empty logins',async t=>{
  const f=config(t),d=read(f.file);d.accounts.work.sites.music=site();atomicJSON(f.file,d);
  fs.mkdirSync(f.a.bridge.profileDir,{recursive:true});const sentinel=path.join(f.a.bridge.profileDir,'synthetic.txt');fs.writeFileSync(sentinel,'invented profile contents');
  const before=fs.readFileSync(f.file,'utf8');await assert.rejects(splitAccount(f.file,'work'),{code:'SPLIT_CHOICE_REQUIRED'});assert.equal(fs.readFileSync(f.file,'utf8'),before);
  const upgrade=await main(['--accounts',f.file,'accounts-upgrade']);assert.deepEqual(upgrade.needsSplit,[{account:'work',command:'split work --keep SITE'}]);assert.equal(Object.keys(load(f.file).accounts.work.sites).length,2);
  const result=await main(['--accounts',f.file,'split','work','--keep','handshake']),data=load(f.file);
  assert.equal(result.profilePreserved,true);assert.deepEqual(Object.keys(data.accounts.work.sites),['handshake']);assert.equal(data.accounts.work.bridge.profileDir,f.a.bridge.profileDir);assert.equal(fs.readFileSync(sentinel,'utf8'),'invented profile contents');
  assert.deepEqual(data.accounts['work-music'].sites.music.identity.expected,{});assert.equal(fs.existsSync(data.accounts['work-music'].bridge.profileDir),false);assert.deepEqual(data.accounts['work-music'].permissions,[]);
});
test('on-demand work starts once, sequences reuse the bridge, and shutdown waits two idle minutes',async t=>{
  const f=workFixture(t);
  await withBridge(f.file,'work',f.a,'check',async()=>{assert.deepEqual(bridgeWork(f.a,f.dir).reasons,['check']);},f.deps);
  assert.equal(f.calls.filter(v=>v==='bridge-up').length,1);assert.equal(bridgeWork(f.a,f.dir,f.deps).idleRemainingMs,IDLE_GRACE);
  await f.clock.advance(IDLE_GRACE-1);assert.equal((await reapBridge(f.stateFile,f.deps)).idle,true);assert.ok(!f.calls.includes('bridge-down'));
  await withBridge(f.file,'work',f.a,'drive',async()=>{},f.deps);assert.equal(f.calls.filter(v=>v==='bridge-up').length,1);assert.equal(f.calls.filter(v=>v==='bridge-idle').length,1);
  await f.clock.advance(IDLE_GRACE);assert.equal((await reapBridge(f.stateFile,f.deps)).stopped,true);assert.equal(f.calls.at(-1),'bridge-down');
  await withBridge(f.file,'work',f.a,'observe',async()=>{},f.deps);assert.equal(f.calls.filter(v=>v==='bridge-up').length,2);assert.equal(f.calls.filter(v=>v==='bridge-idle').length,2);
});
test('a bridge held by any overlapping operation is never stopped, even past the idle deadline',async t=>{
  const f=workFixture(t);let entered,release;const begun=new Promise(r=>{entered=r;}),hold=new Promise(r=>{release=r;});
  const pending=withBridge(f.file,'work',f.a,'drive',async()=>{entered();await hold;},f.deps);await begun;
  await withBridge(f.file,'work',f.a,'check',async()=>{},f.deps);await f.clock.advance(IDLE_GRACE*3);
  assert.equal((await reapBridge(f.stateFile,f.deps)).held,true);assert.deepEqual(bridgeWork(f.a,f.dir).reasons,['drive']);assert.ok(!f.calls.includes('bridge-down'));
  release();await pending;await f.clock.advance(IDLE_GRACE);assert.equal((await reapBridge(f.stateFile,f.deps)).stopped,true);
});
test('completed and cancelled handoffs release durable leases; waiting handoffs survive their opener exiting',async t=>{
  for(const status of ['completed','cancelled']){
    const f=workFixture(t),handoff=path.join(f.dir,'human.json');atomicJSON(handoff,{generation:'human-generation',status:'waiting-for-user'});
    await withBridge(f.file,'work',f.a,'open',async()=>{await pinHandoff(f.a,handoff,'human-generation','human sign-in',f.deps);},f.deps);
    await f.clock.advance(IDLE_GRACE*4);assert.equal((await reapBridge(f.stateFile,f.deps)).held,true);
    assert.deepEqual(bridgeWork(f.a,f.dir).reasons,['human sign-in']);atomicJSON(handoff,{generation:'human-generation',status});
    assert.equal((await reapBridge(f.stateFile,f.deps)).idle,true);await f.clock.advance(IDLE_GRACE);assert.equal((await reapBridge(f.stateFile,f.deps)).stopped,true);
  }
});
test('failed work releases its lease, dead owners recover, uncertain owners and external browsers stay protected',async t=>{
  const f=workFixture(t);await assert.rejects(withBridge(f.file,'work',f.a,'check',async()=>{throw Error('fixture failure');},f.deps),/fixture failure/);assert.deepEqual(bridgeWork(f.a,f.dir).reasons,[]);
  const state=read(f.stateFile);state.leases.dead={pid:99999999,start:'invented',purpose:'dead caller'};state.idleUntil=null;atomicJSON(f.stateFile,state);
  assert.equal((await reapBridge(f.stateFile,{...f.deps,isAlive:()=>false})).idle,true);await f.clock.advance(IDLE_GRACE);
  const refused={...f.deps,isAlive:()=>false,launch:async()=>{throw Object.assign(Error('protected'),{code:'EXTERNAL_BRIDGE'});}};
  assert.equal((await reapBridge(f.stateFile,refused)).status,'external-protected');
  state.leases.uncertain={pid:process.pid,start:processStart(process.pid),purpose:'uncertain caller'};atomicJSON(f.stateFile,state);
  assert.equal((await reapBridge(f.stateFile,{...f.deps,isAlive:()=>true})).held,true);
});
test('the watcher acknowledges without a lock race, then exits after idle shutdown',async t=>{
  const f=workFixture(t);await withBridge(f.file,'work',f.a,'check',async()=>{},f.deps);let acknowledged=false;
  await keepBridges(f.stateFile,{...f.deps,ready:()=>{acknowledged=true;},delay:async()=>f.clock.advance(IDLE_GRACE)});assert.equal(acknowledged,true);assert.equal(f.calls.at(-1),'bridge-down');
});
test('opening a generic login automatically records its visible name and later refuses a different identity',async t=>{
  const f=config(t),d=read(f.file);d.accounts.work.primary='music';d.accounts.work.sites={music:site()};atomicJSON(f.file,d);f.a=d.accounts.work;
  const stateFile=path.join(f.dir,'signin.json');atomicJSON(stateFile,{owner:handoffOwner(f.file,'work',f.a,'music'),generation:'auto-record',account:'work',site:'music',file:f.file,session:'login-human',contextId:'ctx',status:'waiting-for-user'});
  let reads=0;await keepSignIn(stateFile,{execute:async()=>{reads++;return reads<=2?{state:reads===1?'signed-out':'private',identity:{}}:{state:'ready',identity:{},page:{title:'Music library',candidates:['Avery Example']}};},delay:async()=>{},park:async()=>{}});
  const identity=load(f.file).accounts.work.sites.music.identity;assert.equal(reads,4);assert.equal(read(stateFile).status,'completed');assert.deepEqual(identity.expected,{marker:'Avery Example'});assert.ok(identity.recordedAt);
  assert.equal(judge(identity,{state:'ready',identity:{marker:'Avery Example'}}).status,'signed-in');assert.throws(()=>judge(identity,{state:'ready',identity:{marker:'Morgan Example'}}),{code:'IDENTITY_MISMATCH'});
  assert.throws(()=>judge(identity,{state:'ready',identity:{},page:{title:'Music library',candidates:['Morgan Example']}}),{code:'IDENTITY_MISMATCH'});
});
test('opening a named login records only its observed facts, without any identity confirmation',async t=>{
  const f=config(t),d=read(f.file);d.accounts.work.sites.handshake.identity.expected={};atomicJSON(f.file,d);f.a=d.accounts.work;
  const stateFile=path.join(f.dir,'signin.json');atomicJSON(stateFile,{owner:handoffOwner(f.file,'work',f.a,'handshake'),generation:'named-record',account:'work',site:'handshake',file:f.file,session:'login-human',contextId:'ctx',status:'waiting-for-user'});
  await keepSignIn(stateFile,{execute:async()=>observed,delay:async()=>{},park:async()=>{}});
  assert.deepEqual(load(f.file).accounts.work.sites.handshake.identity.expected,observed.identity);assert.equal(read(stateFile).status,'completed');
});
test('an obsolete handoff cannot automatically record identity or park its replacement',async t=>{
  const f=config(t),d=read(f.file);d.accounts.work.sites.handshake.identity.expected={};atomicJSON(f.file,d);f.a=d.accounts.work;
  const stateFile=path.join(f.dir,'signin.json'),state={owner:handoffOwner(f.file,'work',f.a,'handshake'),generation:'old',account:'work',site:'handshake',file:f.file,session:'login-old',contextId:'ctx',status:'waiting-for-user'};atomicJSON(stateFile,state);
  let reads=0,parks=0;await keepSignIn(stateFile,{execute:async()=>{if(++reads===2)atomicJSON(stateFile,{...state,generation:'replacement',session:'login-new'});return observed;},delay:async()=>{},park:async()=>{parks++;}});
  assert.equal(read(stateFile).generation,'replacement');assert.deepEqual(load(f.file).accounts.work.sites.handshake.identity.expected,{});assert.equal(parks,0);
});
test('generic recording with no exposed identity stays lower assurance and never fabricates personal facts',async t=>{
  const f=config(t),d=read(f.file);d.accounts.work.primary='music';d.accounts.work.sites={music:site()};atomicJSON(f.file,d);f.a=d.accounts.work;let time=0;
  const proof=await check(f.file,'work',f.a,'music',{record:true,guard:async()=>{},verify:async()=>({contextId:'ctx'}),now:()=>time,delay:async ms=>{time+=ms;},execute:async(a,args)=>args.includes('eval')?{state:'ready',identity:{},page:{title:'Music library',candidates:[]}}:{}});
  assert.equal(proof.personalIdentityVerified,false);assert.deepEqual(proof.identity,{});assert.ok(load(f.file).accounts.work.sites.music.identity.recordedAt);
});
