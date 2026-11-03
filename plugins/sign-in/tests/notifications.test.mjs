import './environment.mjs';
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import {execFile} from 'node:child_process';
import {promisify} from 'node:util';
import {window as signInWindow,keepSignIn,cancelHandoff,handoffOwner,signInFile} from '../lib/sign-in.mjs';
import {notificationRequest,locateAsk,notifyHandoff,scheduleNotification,sendNotification} from '../lib/notifications.mjs';
import {atomicJSON,read,ROOT} from '../lib/core.mjs';
import {config,fakeCLI,observed} from './support.mjs';

const claude='11111111-1111-4111-8111-111111111111',codex='22222222-2222-4222-8222-222222222222';
function fixture(t,status='waiting-for-user'){
  const f=config(t),stateFile=signInFile(f.file,'work',f.a,'handshake',f.dir),request=notificationRequest({CODEX_THREAD_ID:codex});
  const state={owner:handoffOwner(f.file,'work',f.a,'handshake'),generation:'notice-generation',account:'work',site:'handshake',file:f.file,session:'login-human',contextId:'fixture-context',status,origin:'https://www.example.org',opener:request.opener,notify:request.recipient,notification:{status:'pending'}};
  atomicJSON(stateFile,state);return {...f,stateFile,state};
}
test('open records Claude and Codex openers before starting the keeper, and explicit recipients override delivery only',async t=>{
  for(const env of [{CLAUDE_CODE_SESSION_ID:claude},{CODEX_THREAD_ID:codex},{CLAUDE_CODE_SESSION_ID:claude,CODEX_THREAD_ID:codex}]){
    const f=config(t),stateFile=signInFile(f.file,'work',f.a,'handshake',f.dir);
    await signInWindow(f.file,'work',f.a,'handshake',{root:f.dir,env,notifyTarget:'claude:example-agent',guard:async()=>{},verify:async()=>({contextId:'fixture-context'}),execute:async()=>{},worker:async()=>{
      const state=read(stateFile);assert.deepEqual(state.opener,notificationRequest(env).opener);assert.deepEqual(state.notify,{kind:'claude',id:'example-agent'});return {pid:process.pid};
    }});
    assert.equal(read(stateFile).notification.status,'pending');
  }
  assert.deepEqual(notificationRequest({CODEX_THREAD_ID:codex}).recipient,{kind:'codex',id:codex});
  assert.deepEqual(notificationRequest({},'example-agent').recipient,{kind:'claude',id:'example-agent'});
  assert.throws(()=>notificationRequest({},'--invalid'),{code:'USAGE'});
});
test('completion sends exactly one short notice after the handoff is already complete',async t=>{
  const f=fixture(t),messages=[],send=async(recipient,message)=>{assert.equal(read(f.stateFile).status,'completed');assert.deepEqual(recipient,{kind:'codex',id:codex});messages.push(message);return {status:'delivered'};};
  await keepSignIn(f.stateFile,{execute:async()=>observed,delay:async()=>{},park:async()=>{},notify:(file,generation)=>notifyHandoff(file,generation,{send})});
  await notifyHandoff(f.stateFile,f.state.generation,{send});
  assert.deepEqual(messages,['Signed in: login work (www.example.org). It is ready for drive.']);
  assert.equal(read(f.stateFile).notification.status,'delivered');assert.equal(messages.join('').includes(observed.identity.profile),false);
});
test('cancellation notifies even without a live keeper, and the keeper cannot send a duplicate',async t=>{
  const f=fixture(t),messages=[],send=async(_recipient,message)=>{messages.push(message);return {status:'delivered'};},notify=(file,generation)=>notifyHandoff(file,generation,{send});
  await cancelHandoff(f.stateFile,f.state,f.a,{park:async()=>{},notify});
  await keepSignIn(f.stateFile,{execute:async()=>assert.fail('cancelled handoff must not read'),park:async()=>{},notify});
  assert.deepEqual(messages,['Sign-in cancelled: login work (www.example.org).']);assert.equal(read(f.stateFile).status,'cancelled');
});
test('a keeper failure reports only an allowed reason code, with no page or identity values',async t=>{
  const f=fixture(t),messages=[];
  await keepSignIn(f.stateFile,{execute:async()=>{throw Object.assign(Error('invented private page text'),{code:'IDENTITY_MISMATCH'});},park:async()=>{},notify:(file,generation)=>notifyHandoff(file,generation,{send:async(_recipient,message)=>{messages.push(message);return {status:'delivered'};}})});
  assert.equal(read(f.stateFile).status,'attention-required');assert.deepEqual(messages,['Sign-in needs attention: login work (www.example.org). Reason: IDENTITY_MISMATCH.']);
  assert.equal(JSON.stringify(read(f.stateFile).notification).includes('invented private'),false);
});
test('without an opener, completion skips delivery without looking for an ask script',async t=>{
  const f=fixture(t);atomicJSON(f.stateFile,{...f.state,opener:null,notify:null,notification:{status:'skipped',code:'NO_OPENER'}});
  await keepSignIn(f.stateFile,{execute:async()=>observed,delay:async()=>{},park:async()=>{},notify:(file,generation)=>notifyHandoff(file,generation,{send:async()=>assert.fail('no recipient')})});
  assert.equal(read(f.stateFile).status,'completed');assert.deepEqual(read(f.stateFile).notification,{status:'skipped',code:'NO_OPENER'});
});
test('delivery failure is recorded, never retried, and never changes the completed handoff',async t=>{
  const f=fixture(t,'completed');let attempts=0;
  const send=async()=>{attempts++;throw Error('invented private executor output');};
  await notifyHandoff(f.stateFile,f.state.generation,{send});await notifyHandoff(f.stateFile,f.state.generation,{send});
  const state=read(f.stateFile);assert.equal(state.status,'completed');assert.equal(state.notification.status,'failed');assert.equal(state.notification.code,'ASK_DELIVERY_FAILED');assert.equal(attempts,1);assert.equal(JSON.stringify(state.notification).includes('invented private'),false);
});
test('concurrent completion and cancellation paths claim one delivery; a replacement generation is untouched',async t=>{
  const f=fixture(t,'completed');let began,release,attempts=0;const entered=new Promise(r=>{began=r;}),hold=new Promise(r=>{release=r;});
  const send=async()=>{attempts++;began();await hold;return {status:'delivered'};};
  const first=notifyHandoff(f.stateFile,f.state.generation,{send});await entered;await notifyHandoff(f.stateFile,f.state.generation,{send});assert.equal(attempts,1);
  atomicJSON(f.stateFile,{...f.state,generation:'replacement',notification:{status:'pending'}});release();await first;
  assert.deepEqual(read(f.stateFile).notification,{status:'pending'});assert.equal(read(f.stateFile).generation,'replacement');
});
test('notification-worker failure does not block cancellation and is visible as a failed notice',async t=>{
  const f=fixture(t);let attempts=0;
  const notify=(file,generation)=>scheduleNotification(file,generation,{start:async()=>{attempts++;throw Error('fixture worker failed');}});
  await cancelHandoff(f.stateFile,f.state,f.a,{park:async()=>{},notify});await notify(f.stateFile,f.state.generation);
  assert.equal(read(f.stateFile).status,'cancelled');assert.equal(read(f.stateFile).notification.code,'NOTIFICATION_KEEPER_FAILED');assert.equal(attempts,1);
});
test('ask lookup honours the override, installed session-tools and the enclosing marketplace',t=>{
  const f=config(t),root=path.join(f.dir,'checkout/plugins/sign-in'),provider=path.join(f.dir,'provider/skills/ask/scripts/ask.py'),write=(file,value)=>{fs.mkdirSync(path.dirname(file),{recursive:true});fs.writeFileSync(file,typeof value==='string'?value:JSON.stringify(value));};
  write(provider,'# fixture provider');
  assert.equal(locateAsk({HOME:f.dir,ASK_SCRIPT:provider},root),provider);
  assert.throws(()=>locateAsk({HOME:f.dir,ASK_SCRIPT:path.join(f.dir,'missing.py')},root),{code:'ASK_MISSING'});
  write(path.join(f.dir,'.claude/plugins/installed_plugins.json'),{plugins:{'session-tools@fixture':[{installPath:path.join(f.dir,'provider')}]}});
  assert.equal(locateAsk({HOME:f.dir},root),provider);fs.rmSync(path.join(f.dir,'.claude'),{recursive:true});
  write(path.join(f.dir,'checkout/.claude-plugin/marketplace.json'),{plugins:[{name:'session-tools',source:'../provider'}]});
  assert.equal(locateAsk({HOME:f.dir},root),provider);
});
test('ask transport uses a private prompt file, exact session routing and typed acceptance',async t=>{
  const f=fixture(t,'completed'),script=path.join(f.dir,'ask.py');fs.writeFileSync(script,'# fixture provider');
  for(const recipient of [{kind:'codex',id:codex},{kind:'claude',id:claude},{kind:'claude',id:'example-agent'}]){
    const result=await sendNotification(recipient,'Short fixture notice.',f.stateFile,{env:{ASK_SCRIPT:script},execute:async(bin,args)=>{
      assert.equal(bin,'python3');assert.equal(args[0],script);assert.ok(args.includes('--no-wait'));assert.ok(!args.includes('--message'));assert.equal(fs.readFileSync(args[args.indexOf('--prompt-file')+1],'utf8'),'Short fixture notice.');
      assert.equal(args.includes('--codex'),recipient.kind==='codex');assert.equal(args.includes('--exact'),recipient.id!== 'example-agent');if(recipient.kind==='codex')assert.ok(args.includes('--mid-turn'));
      atomicJSON(args[args.indexOf('--outcome')+1],{version:1,kind:'accepted'});return {stdout:'ignored fixture output'};
    }});assert.equal(result.status,'delivered');
  }
  assert.deepEqual(fs.readdirSync(path.dirname(f.stateFile)).filter(name=>name.startsWith('.notice-')),[]);
  const result=await sendNotification({kind:'codex',id:codex},'Short fixture notice.',f.stateFile,{env:{ASK_SCRIPT:script},execute:async(_bin,args)=>{atomicJSON(args[args.indexOf('--outcome')+1],{version:1,kind:'not-delivered'});throw Error('fixture refused');}});assert.equal(result.code,'ASK_DELIVERY_FAILED');
});
test('CLI records the opener and explicit target, exposes notification state and forwards the request to a holder',async t=>{
  const f=fakeCLI(t),exec=promisify(execFile),invoke=async args=>JSON.parse((await exec(process.execPath,[path.join(ROOT,'scripts/sign-in.mjs'),'--accounts',f.file,...args],{env:{...f.env,CODEX_THREAD_ID:codex}})).stdout).data;
  await invoke(['open','work','--notify','claude:example-agent']);
  const state=await invoke(['sign-in-status','work']);assert.deepEqual(state.opener,{codexThreadId:codex});assert.deepEqual(state.notify,{kind:'claude',id:'example-agent'});assert.equal(state.notification.status,'pending');
  const stateFile=signInFile(f.file,'work',f.a,'handshake',f.env.SIGN_IN_HOME),stored=read(stateFile);atomicJSON(stateFile,{...stored,status:'completed'});
  await notifyHandoff(stateFile,stored.generation,{send:async()=>{throw Error('fixture delivery failed');}});
  const failed=await invoke(['sign-in-status','work']);assert.equal(failed.status,'completed');assert.equal(failed.notification.status,'failed');assert.equal(failed.notification.code,'ASK_DELIVERY_FAILED');
  const remote=fakeCLI(t),data=read(remote.file);data.accounts.work.machine.id='f'.repeat(64);data.accounts.work.machine.ssh='holder';atomicJSON(remote.file,data);
  await exec(process.execPath,[path.join(ROOT,'scripts/sign-in.mjs'),'--accounts',remote.file,'open','work','--ttl','60'],{env:{...remote.env,CODEX_THREAD_ID:codex}});
  const delegated=remote.calls().find(c=>c.args.at(-1)?.includes("'window'"));assert.ok(delegated.args.at(-1).includes('--notification-request'));assert.ok(delegated.args.at(-1).includes(codex));
});
