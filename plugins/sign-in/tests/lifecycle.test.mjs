import './environment.mjs';
import test from 'node:test';import assert from 'node:assert/strict';import fs from 'node:fs';import path from 'node:path';import os from 'node:os';import vm from 'node:vm';
import { compatible,privateHolder,sharedUsers } from '../lib/daemon.mjs';
import { recoverChromeLock,launchArgs,up,show,wake,verifyBridge } from '../browser/ext-bridge.mjs';
import { expression,kinds } from '../browser/identity.mjs';
import { check,judge,navigate,keepSignIn,expireTunnel,cleanIdentity,handoffOwner } from '../lib/sign-in.mjs';
import { read,atomicJSON } from '../lib/core.mjs';import {config,temp,observed} from './support.mjs';

test('bridge daemon starts when absent, shares matching version, never replaces a conflict',async()=>{
  let current=null,starts=0;const deps={execute:async()=>({stdout:'2.0.2'}),status:async()=>current,launch:async()=>{starts++;current={daemonVersion:'2.0.2'};}};
  await compatible({...deps,start:true});assert.equal(starts,1);await compatible({...deps,start:true});assert.equal(starts,1);
  current={daemonVersion:'1.8.7'};await assert.rejects(compatible({...deps,start:true}),{code:'DAEMON_CONFLICT'});assert.equal(starts,1);
  current=null;await assert.rejects(compatible(deps),{code:'DAEMON_STOPPED'});assert.equal(starts,1);
  await assert.rejects(compatible({...deps,execute:async()=>({stdout:'1.8.7'})}),{code:'CLI_CONFLICT'});
});
test('private-holder guard refuses a shared machine despite a private flag',async()=>{await assert.rejects(privateHolder({exclusive:true},async()=>['another-user']),{code:'SHARED_HOST'});await assert.rejects(privateHolder({exclusive:false},async()=>[]),{code:'PRIVATE_HOLDER_REQUIRED'});await privateHolder({exclusive:true},async()=>[]);});
test('host inspection distinguishes Mac service accounts from another person',async()=>{
  const execute=async(bin,args)=>({stdout:args.includes('-list')?'owner 501\nmacports 502\npostgres 503\nfriend 504':args.includes('/Users/macports')?'UserShell: /usr/bin/false\nNFSHomeDirectory: /opt/local/var/macports/home':args.includes('/Users/postgres')?'UserShell: /bin/bash\nNFSHomeDirectory: /Library/PostgreSQL/18':'UserShell: /bin/zsh\nNFSHomeDirectory: /Users/friend'});
  assert.deepEqual(await sharedUsers(execute,{platform:'darwin',uid:501}),['friend']);
});
test('crash singleton symlinks recover only same-host dead owners; live/foreign/unknown locks stay',async t=>{
  const dir=temp(t),lock=path.join(dir,'SingletonLock');fs.symlinkSync(os.hostname()+'-99999999',lock);fs.symlinkSync('/tmp/not-owned-target',path.join(dir,'SingletonSocket'));
  assert.equal(await recoverChromeLock(dir,{alive:()=>false}),true);assert.equal(fs.existsSync(lock),false);
  fs.symlinkSync(os.hostname()+'-1',lock);await assert.rejects(recoverChromeLock(dir,{alive:()=>true}),{code:'PROFILE_LOCKED'});assert.ok(fs.lstatSync(lock));fs.unlinkSync(lock);
  fs.symlinkSync('another-host-99999999',lock);await assert.rejects(recoverChromeLock(dir,{alive:()=>false}),{code:'PROFILE_LOCKED'});assert.ok(fs.lstatSync(lock));
});
test('park mirrors show and never closes a window; headless args are supported without a display',async()=>{
  const bounds=[];const cdp=async(port,work)=>work(async(method,p)=>method==='Target.getTargets'?{targetInfos:[{type:'page',targetId:'tab'}]}:method==='Browser.getWindowForTarget'?{windowId:1}:(bounds.push(p.bounds),{}));
  await show(1,cdp,'park');assert.equal(bounds[1].left,-32000);assert.equal(bounds[1].top,-32000);
  const args=launchArgs({profileDir:'/tmp/demo',port:19340,headless:true},'/extension');assert.ok(args.includes('--headless=new'));assert.ok(args.includes('--remote-debugging-address=127.0.0.1'));
});
test('wake opens only a blank tab when empty',async()=>{let puts=0;const fake=async(url,o)=>{if(o?.method==='PUT')puts++;return {ok:true,json:async()=>[]};};await wake(1,fake);assert.equal(puts,1);await wake(1,async()=>({ok:true,json:async()=>[{type:'page'}]}));assert.equal(puts,1);});
test('up starts daemon, preserves running PID and parks an offscreen bridge without alias overwrite',async t=>{
  const dir=temp(t),ext=fs.realpathSync(dir);fs.writeFileSync(path.join(ext,'manifest.json'),JSON.stringify({version:'1.0.26'}));
  const commands=[],methods=[];let started=false,parked=false;
  const cdp=async(port,work)=>work(async(method)=>{
    methods.push(method);if(method==='Extensions.getExtensions')return {extensions:[{id:'official',path:ext,enabled:true,version:'1.0.26'}]};
    if(method==='Target.getTargets')return {targetInfos:[{type:'service_worker',targetId:'worker',url:'chrome-extension://official/dist/background.js'}]};
    if(method==='Target.attachToTarget')return {sessionId:'sw'};if(method==='Runtime.evaluate')return {result:{value:{opencli_context_id_v1:'ctx'}}};return {};
  });
  const execute=async(bin,args)=>{commands.push(args);return {stdout:JSON.stringify({profiles:[{alias:'work',contextId:'ctx',connected:true,extensionVersion:'1.0.26'}]})};};
  const result=await up('work',{profileDir:'/profile',port:19340,offscreen:true,contextIds:['ctx']},{root:dir,pid:async()=>1234,compatible:async()=>{started=true;},spawn:()=>assert.fail('must not relaunch'),layer:{extension:ext},probe:async()=>true,ready:f=>f(),wake:async()=>({openedBlankTab:false}),park:async()=>{parked=true;},cdp,execute});
  assert.equal(result.pid,1234);assert.equal(started,true);assert.equal(parked,true);assert.ok(!commands.some(a=>a.includes('rename')));assert.ok(!methods.includes('Extensions.loadUnpacked'));
  await assert.rejects(verifyBridge({name:'different',profileDir:'/profile',port:19340},{pid:async()=>1234,layer:{extension:ext},cdp,execute}),{code:'BRIDGE_ALIAS_MISMATCH'});
});
test('alias collision refuses before a new browser launch',async()=>{
  await assert.rejects(up('reserved',{profileDir:'/unused',port:19340,contextIds:[]},{pid:async()=>null,compatible:async()=>{},spawn:()=>assert.fail('no spawn'),execute:async()=>({stdout:JSON.stringify({profiles:[{alias:'reserved',contextId:'foreign'}]})})}),{code:'ALIAS_COLLISION'});
});
test('all probes parse and reject login URLs without returning token/cookie fields',async()=>{
  for(const kind of kinds){const js=expression({kind,url:'https://example.invalid/'});new Function('return '+js);
    const result=await vm.runInNewContext(js,{location:{href:'https://example.invalid/login?token=secret',origin:'https://example.invalid',hostname:'example.invalid',pathname:'/login'},document:{title:'',readyState:'complete',body:{innerText:''},cookie:'secret cookie',querySelector:()=>null}});
    assert.equal(result.state,'signed-out');assert.equal(JSON.stringify(result).includes('secret'),false);
  }
});
test('workday, meta and amazon authentication checks retain harness distinctions',async()=>{
  const run=async(kind,url,elements)=>vm.runInNewContext(expression({kind,url}),{location:{href:url,origin:new URL(url).origin,pathname:new URL(url).pathname,hostname:new URL(url).hostname},document:{title:'Careers',readyState:'complete',body:{innerText:''},querySelector:s=>elements(s)}});
  assert.equal((await run('workday','https://tenant.example/jobs',s=>s.includes('utilityButtonSignIn')?{}:null)).state,'signed-out');
  assert.equal((await run('workday','https://tenant.example/jobs',()=>null)).state,'unknown');
  assert.equal((await run('workday','https://tenant.example/jobs',s=>s.includes('utilityMenuButton')?{}:null)).identity.authenticated,true);
  assert.equal((await run('meta','https://www.metacareers.com/profile/info',s=>s.includes('Open login page')?{}:null)).state,'signed-out');
});
test('identity output strips queries and arbitrary secret fields; mismatches never dump the observation',t=>{
  const {a}=config(t),i=a.sites.handshake.identity;
  const output=cleanIdentity({profile:'https://school.joinhandshake.com/profiles/test-user?token=secret',cookie:'secret',ID_TOKEN:'secret'});assert.deepEqual(output,{profile:observed.identity.profile});
  assert.equal(judge(i,{state:'ready',identity:{...observed.identity,token:'secret'}}).identity.token,undefined);
  assert.throws(()=>judge(i,{state:'signed-out',identity:{token:'secret'}}),e=>e.code==='SIGNED_OUT'&&!JSON.stringify(e).includes('secret'));
});
test('TikTok identity uses the current viewer and never a public profile recommendation',async()=>{
  const base={location:{href:'https://www.tiktok.com/foryou',origin:'https://www.tiktok.com'},document:{title:'TikTok',readyState:'complete',body:{innerText:''}}};
  const probe=data=>vm.runInNewContext(expression({kind:'tiktok',url:'https://www.tiktok.com/'}),{...base,document:{...base.document,querySelector:s=>s.startsWith('script#')?{textContent:JSON.stringify(data)}:s.startsWith('a[')?{href:'https://www.tiktok.com/@public-person'}:null}});
  assert.equal((await probe({userInfo:{user:{secUid:'public-id',uniqueId:'public-person'}}})).state,'signed-out');
  const result=await probe({__DEFAULT_SCOPE__:{'webapp.app-context':{user:{secUid:'owner-id',uniqueId:'owner',token:'never-return'}}}});assert.equal(result.identity.username,'owner');assert.equal(JSON.stringify(result).includes('never-return'),false);
});
test('first-navigation race retries once, auth errors never retry',async()=>{
  let calls=0;await navigate({},[],'https://example.invalid/','ctx',async()=>{if(++calls===1)throw Error('No active page; not attached');});assert.equal(calls,2);
  calls=0;await assert.rejects(navigate({},[],'https://example.invalid/','ctx',async()=>{calls++;throw Error('Sign-in required');}),/Sign-in required/);assert.equal(calls,1);
});
test('check settles twice in a unique disposable session, always closes only what it opens',async t=>{
  const {a,file}=config(t),calls=[];let clock=0,reads=0;
  const deps={guard:async()=>{},verify:async()=>({contextId:'ctx'}),now:()=>clock,delay:async ms=>{clock+=ms;},execute:async(a,args)=>{calls.push(args);if(args.includes('eval'))return ++reads===1?{state:'loading',identity:{}}:observed;return {};}};
  assert.equal((await check(file,'work',a,'handshake',deps)).status,'signed-in');assert.equal(reads,3);assert.ok(calls.every(c=>c[c.indexOf('--session')+1].startsWith('check-')));assert.equal(calls.at(-1).at(-1),'close');assert.ok(!calls.some(c=>c.join(' ').includes('login-')));
  calls.length=0;await assert.rejects(check(file,'work',a,'handshake',{...deps,execute:async(a,args)=>{calls.push(args);return args.includes('eval')?{state:'signed-out',identity:{}}:{};}}),{code:'SIGNED_OUT'});assert.equal(calls.at(-1).at(-1),'close');
});
test('driveBlocked prevents any identity navigation',async t=>{const {a,file}=config(t);a.bridge.driveBlocked=true;await assert.rejects(check(file,'work',a),{code:'DRIVE_BLOCKED'});});
test('keeper keeps its human lease alive past ten minutes, never navigates, then parks',async t=>{
  const {a,file,dir}=config(t),stateFile=path.join(dir,'sign-in.json');atomicJSON(stateFile,{owner:handoffOwner(file,'work',a,'handshake'),generation:'test-generation',account:'work',site:'handshake',file,session:'login-human',contextId:'ctx',status:'waiting-for-user'});
  let reads=0,time=0,parked=false;
  await keepSignIn(stateFile,{execute:async(a,args)=>{assert.ok(args.includes('eval'));assert.equal(args[args.indexOf('--session')+1],'login-human');reads++;return {state:'signed-out',identity:{}};},delay:async ms=>{time+=ms;if(time>600000)atomicJSON(stateFile,{...read(stateFile),status:'cancelled'});},park:async()=>{parked=true;}});
  assert.ok(reads>200);assert.ok(time>600000);assert.equal(parked,true);
});
test('keeper completes only after two identity observations and leaves the human tab open',async t=>{
  const {file,dir,a}=config(t),stateFile=path.join(dir,'sign-in.json');atomicJSON(stateFile,{owner:handoffOwner(file,'work',a,'handshake'),generation:'test-generation',account:'work',site:'handshake',file,session:'login-human',contextId:'ctx',status:'waiting-for-user'});let reads=0;
  await keepSignIn(stateFile,{execute:async(a,args)=>{assert.ok(!args.includes('close'));reads++;return observed;},delay:async()=>{},park:async()=>{}});assert.equal(reads,2);assert.equal(read(stateFile).status,'completed');
});
test('tunnel watchdog expires only its unique SSH master',async t=>{
  const dir=temp(t),file=path.join(dir,'tunnel.json'),close=['ssh','-S','/private/unique.sock','-O','exit','--','holder'];atomicJSON(file,{close,expiresAt:100,status:'opening'});let now=0,stopped=0;
  await expireTunnel(file,{now:()=>now,delay:async ms=>{now+=ms;},start:async state=>{assert.deepEqual(state.close,close);return {stop:async()=>{stopped++;}};}});assert.equal(stopped,1);assert.equal(read(file).status,'expired');
});
