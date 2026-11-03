import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import net from 'node:net';
import crypto from 'node:crypto';
import { ROOT, HOME, insist, Refusal, run, json, oc, local, remote, locked, privateDir, atomicJSON, read, processStart,assertReadable,untrusted,protectSession,stateLocked } from './core.mjs';
import { siteAt, drivable, canonical,load,validate,accountAt } from './accounts.mjs';
import { expression, identityFields, identityAssurance,validMarker } from '../browser/identity.mjs';
import { privateHolder } from './daemon.mjs';
import {worker} from './bridge-lifecycle.mjs';
import {notificationRequest,validateNotificationRequest,notifyHandoff,scheduleNotification} from './notifications.mjs';
export {stateLocked} from './core.mjs';

export const identityExpression=expression;
export const bridgeOperation=a=>path.join(os.tmpdir(),'devdroid-sign-in',`${a.machine.id}-${a.bridge.port}`);
export function cleanIdentity(raw={}) {
  const result={};
  for(const [k,v]of Object.entries(raw))if(identityFields.has(k)&&(typeof v==='string'||typeof v==='boolean')){
    if(typeof v==='string'&&!v.trim())continue;
    if(k==='profile'){try{const u=new URL(v);if(u.protocol==='https:')result[k]=u.origin+u.pathname;}catch{}}
    else result[k]=typeof v==='string'?v.replace(/[\r\n\t]/g,' ').slice(0,200):v;
  }
  return result;
}
export function judge(i,observed) {
  if(i.kind==='generic'){
    const result=genericObservation(observed);
    if(!i.expected?.marker&&i.recordedAt)return {status:'signed-in',identity:{},...identityAssurance(i)};
    insist(i.expected?.marker,'IDENTITY_ENROLLMENT_REQUIRED','Generic site (lower assurance): open the site and sign in by hand first',identityAssurance(i));
    insist(result.identity.marker||!result.page.candidates.length||result.page.candidates.includes(i.expected.marker),'IDENTITY_MISMATCH','Generic site (lower assurance): a different account label is visible; stop and ask the user',identityAssurance(i));
    insist(result.identity.marker,'IDENTITY_LOADING','Generic site (lower assurance): the confirmed marker is not visible',identityAssurance(i));
    insist(result.identity.marker===i.expected.marker,'IDENTITY_MISMATCH','Generic site (lower assurance): the confirmed marker differs; stop and ask the user',identityAssurance(i));
    return {status:'signed-in',identity:{marker:result.identity.marker},...identityAssurance(i)};
  }
  if(typeof observed==='string'){try{observed=JSON.parse(observed);}catch{throw new Refusal('IDENTITY_UNKNOWN','Unreadable identity result');}}
  insist(observed&&['ready','loading','signed-out','blocked','unknown'].includes(observed.state),'IDENTITY_UNKNOWN','Incomplete identity result');
  insist(observed.state!=='signed-out','SIGNED_OUT','Signed out; stop and ask the user');
  insist(observed.state!=='blocked','BLOCKED','Site challenge; stop and ask the user');
  insist(observed.state==='ready','IDENTITY_LOADING','Identity page has not settled');
  const identity=cleanIdentity(observed.identity),expected=i.expected||{};
  insist(i.recordedAt||Object.keys(expected).some(k=>!['authenticated','site'].includes(k)),'IDENTITY_ENROLLMENT_REQUIRED','Open the site and sign in by hand first',{identity});
  insist(Object.keys(expected).every(k=>identity[k]!==undefined&&identity[k]!==''),'IDENTITY_LOADING','Identity facts are not rendered yet');
  insist(Object.entries(expected).every(([k,v])=>identity[k]===v),'IDENTITY_MISMATCH','Expected identity not proven; stop and ask the user');
  return {status:'signed-in',identity,...identityAssurance(i)};
}
function capturedIdentity(i,raw){
  if(typeof raw==='string'){try{raw=JSON.parse(raw);}catch{throw new Refusal('IDENTITY_UNKNOWN','Unreadable identity observation');}}
  if(i.kind==='generic'){
    const observed=genericObservation(raw),marker=observed.page.candidates.find(v=>v!==observed.page.title)||observed.identity.marker;
    return marker?{marker}:{};
  }
  insist(raw?.state==='ready',raw?.state==='signed-out'?'SIGNED_OUT':raw?.state==='blocked'?'BLOCKED':'IDENTITY_LOADING','The sign-in page is not ready');
  const identity=cleanIdentity(raw.identity);insist(Object.keys(identity).length&&identity.authenticated!==false,'IDENTITY_LOADING','Identity facts are not visible yet');return identity;
}
export async function recordIdentity(file,name,site,i,identity,expectedOwner){
  return locked(file,()=>{
    const data=load(file),current=data.accounts[name]?.sites[site]?.identity;
    if(expectedOwner)insist(data.accounts[name]&&JSON.stringify(handoffOwner(file,name,data.accounts[name],site))===JSON.stringify(expectedOwner),'HANDOFF_OWNER','The login profile changed; no identity was recorded');
    if(current?.recordedAt&&current.kind===i.kind&&current.url===i.url&&JSON.stringify(current.expected)===JSON.stringify(identity))return current;
    insist(current&&JSON.stringify(current)===JSON.stringify(i),'ACCOUNT_CHANGED','Identity configuration changed; nothing was recorded');
    const next={...current,expected:identity,recordedAt:new Date().toISOString()};data.accounts[name].sites[site].identity=next;validate(data);atomicJSON(file,data);return next;
  });
}
async function recordHandoff(file,name,site,i,identity,handoff){
  if(!handoff)return recordIdentity(file,name,site,i,identity);
  return stateLocked(handoff.file,async()=>{
    const current=read(handoff.file);
    insist(current.generation===handoff.generation&&['opening','waiting-for-user','completed'].includes(current.status),'HANDOFF_CHANGED','The sign-in handoff changed; no identity was recorded');
    return recordIdentity(file,name,site,i,identity,current.owner);
  });
}
function genericObservation(raw,i={kind:'generic'}){
  if(typeof raw==='string'){try{raw=JSON.parse(raw);}catch{raw=null;}}
  const detail=identityAssurance(i),state=raw?.state;
  const codes={'signed-out':'SIGNED_OUT',blocked:'BLOCKED',private:'SENSITIVE_SESSION',loading:'IDENTITY_LOADING'};
  insist(state==='ready',codes[state]||'IDENTITY_UNKNOWN',(i.kind==='generic'?'Generic site (lower assurance): ':'Task ')+'page is '+(Object.hasOwn(codes,state)?state:'unrecognized')+'; stop and ask the user',detail);
  return {identity:raw.identity?.marker&&validMarker(raw.identity.marker)?{marker:raw.identity.marker}:{},page:{title:typeof raw.page?.title==='string'?raw.page.title.replace(/[\r\n\t]/g,' ').slice(0,200):'',candidates:Array.isArray(raw.page?.candidates)?raw.page.candidates.filter(validMarker).slice(0,12):[]},...detail};
}
function lowerAssuranceError(i,e){
  if(i?.kind==='generic'){
    e.detail={...e.detail,...identityAssurance(i)};
    if(!e.message.startsWith('Generic site (lower assurance):'))e.message='Generic site (lower assurance): '+e.message;
  }
  return e;
}
export async function launch(file,name,cmd='bridge-up') {
  const script=process.env.SIGN_IN_LAUNCHER||path.join(ROOT,'browser/ext-bridge.mjs');
  return json((await run(process.execPath,[script,'--accounts',file,cmd,name],{timeout:180000})).stdout);
}
export async function verify(file,name,a) {
  const p=await launch(file,name,'verify');
  insist(p.name===a.bridge.name&&p.port===a.bridge.port&&p.verified===true&&p.contextId,'BRIDGE_UNVERIFIED','Configured bridge is not proven; run bridge ACCOUNT');return p;
}
export async function bridge(file,name,a) {
  const p=local(a.machine)?await launch(file,name):await remote(a,['bridge',name]);
  insist(p.name===a.bridge.name&&p.port===a.bridge.port&&p.contextId,'BRIDGE_MISMATCH','Bridge identity differs from the accounts file');return p;
}
export async function navigate(a,args,url,context,execute=oc) {
  for(let attempt=0;attempt<2;attempt++){
    try{return await execute(a,[...args,'open',url],context);}
    catch(e){if(attempt||!/no (active )?page|not attached|target closed|frame.*detached|err_aborted|navigation.*race/i.test(e.message)||/auth|sign.?in|captcha|challenge/i.test(e.message))throw e;}
  }
}
export async function check(file,name,a,site,dependencies={}) {
  try{return await checkSite(file,name,a,site,dependencies);}catch(e){throw lowerAssuranceError(a.sites[site||a.primary]?.identity,e);}
}
async function checkSite(file,name,a,site,dependencies) {
  drivable(a);const [key,s]=siteAt(a,site);insist(s.identity,'IDENTITY_UNCONFIGURED','Configure an identity probe before acting');
  let i=dependencies.observe&&dependencies.marker?{...s.identity,expected:{marker:dependencies.marker}}:s.identity;
  if(!local(a.machine)){
    const proof=await remote(a,[dependencies.observe?'observe':'check',name,'--site',key]);
    if(dependencies.observe){insist(proof.account===name&&proof.site===key&&proof.status==='observed','REMOTE_IDENTITY_UNKNOWN','Remote observation is not proven');return {account:name,site:key,status:'observed',...(i.kind==='generic'?genericObservation({state:'ready',...proof}):{identity:cleanIdentity(proof.identity)})};}
    insist(proof.account===name&&proof.site===key&&proof.status==='signed-in','REMOTE_IDENTITY_UNKNOWN','Remote identity is not proven');
    return {account:name,site:key,holdingMachine:a.machine.id,...judge(s.identity,{state:'ready',identity:proof.identity})};
  }
  const d={verify,execute:oc,guard:privateHolder,delay:ms=>new Promise(r=>setTimeout(r,ms)),now:Date.now,...dependencies};
  await d.guard(a.machine);
  return locked(bridgeOperation(a),async()=>{
    const proof=await d.verify(file,name,a),session=`check-${name}-${crypto.randomUUID()}`;
    const args=['browser','--session',session,'--window','background'];let opened=false;
    try{
      opened=true;await navigate(a,args,i.url,proof.contextId,d.execute);
      const deadline=d.now()+8000;let last='',hits=0;
      while(d.now()<deadline){
        const observed=await d.execute(a,[...args,'eval',expression(i)],proof.contextId);
        if(dependencies.observe){
          if(i.kind==='generic'){
            try{return {account:name,site:key,status:'observed',...genericObservation(observed)};}catch(e){if(e.code!=='IDENTITY_LOADING')throw e;}
          }else{const o=typeof observed==='string'?JSON.parse(observed):observed;insist(o.state==='ready',o.state==='signed-out'?'SIGNED_OUT':'IDENTITY_UNKNOWN','Authentication is not ready; stop and ask the user');return {account:name,site:key,status:'observed',identity:cleanIdentity(o.identity)};}
        }else try{
          const recording=dependencies.record&&!i.recordedAt&&!Object.keys(i.expected||{}).length;
          const identity=recording?capturedIdentity(i,observed):null;
          const result=recording?{status:'signed-in',identity,...identityAssurance(i)}:judge(i,observed),fingerprint=JSON.stringify(result.identity);
          hits=fingerprint===last?hits+1:1;last=fingerprint;
          if(hits>=2){if(recording)i=await recordHandoff(file,name,key,i,identity,dependencies.handoff);return {account:name,site:key,contextId:proof.contextId,...result};}
        }
        catch(e){if(e.code!=='IDENTITY_LOADING')throw e;}
        await d.delay(250);
      }
      throw new Refusal('IDENTITY_UNSETTLED','Identity did not settle; no action was taken');
    }finally{if(opened)await d.execute(a,['browser','--session',session,'close'],proof.contextId);}
  });
}
export async function enroll(file,name,site,{check:observe=check}={}){
  const data=load(file),a=accountAt(data,name),[key,s]=siteAt(a,site),before=JSON.stringify(a);
  insist(local(a.machine),'HUMAN_STEP_ON_HOLDER','Re-record identity on its holder');drivable(a);
  try{
    const observed=await observe(file,name,a,key,{observe:true});let expected;
    if(s.identity?.kind==='generic'){
      const marker=observed.page.candidates.find(v=>v!==observed.page.title)||observed.identity.marker;expected=marker?{marker}:{};
    }else{
      insist(Object.keys(observed.identity).length&&observed.identity.authenticated!==false,'IDENTITY_LOADING','No signed-in facts are visible');expected=observed.identity;
    }
    return await locked(file,()=>{
      const fresh=load(file);insist(JSON.stringify(fresh.accounts[name])===before,'ACCOUNT_CHANGED','Account changed during enrollment; no enrollment was recorded');
      fresh.accounts[name].sites[key].identity={...s.identity,expected,recordedAt:new Date().toISOString()};validate(fresh);atomicJSON(file,fresh);
      return {account:name,site:key,enrolled:true,...identityAssurance(s.identity)};
    });
  }catch(e){throw lowerAssuranceError(s.identity,e);}
}
export const handoffOwner=(file,name,a,site)=>({file:canonical(file),account:name,site,machine:a.machine.id,profile:canonical(a.bridge.profileDir),port:a.bridge.port});
export const signInFile=(file,name,a,site,root=HOME)=>path.join(root,'sign-ins',crypto.createHash('sha256').update(JSON.stringify(handoffOwner(file,name,a,site))).digest('hex')+'.json');
export async function updateHandoff(file,generation,change){return stateLocked(file,()=>{const s=read(file);if(s.generation!==generation)return null;const next=change(s);if(next)atomicJSON(file,next);return next;});}
export async function parkHandoff(stateFile,start,a,park=launch){return stateLocked(stateFile,async()=>{const current=read(stateFile);if(current.generation===start.generation&&a.bridge.offscreen)await park(start.file,start.account,'park');});}
export async function window(file,name,a,site,dependencies={}) {
  drivable(a);insist(local(a.machine),'WRONG_MACHINE','Sign-in window must open on its holding machine');
  const d={root:HOME,guard:privateHolder,verify,execute:oc,worker,notify:scheduleNotification,...dependencies};await d.guard(a.machine);
  const request=validateNotificationRequest(d.request||notificationRequest(d.env||process.env,d.notifyTarget));
  const [key,s]=siteAt(a,site),stateFile=signInFile(file,name,a,key,d.root),proof=await d.verify(file,name,a),owner=handoffOwner(file,name,a,key);
  const state=await stateLocked(stateFile,()=>{
    if(fs.existsSync(stateFile)){
      const old=read(stateFile);insist(JSON.stringify(old.owner)===JSON.stringify(owner),'HANDOFF_OWNER','Sign-in state belongs to another account source');
      if(['opening','waiting-for-user'].includes(old.status)){
        insist(old.contextId===proof.contextId,'HANDOFF_CONTEXT','The human tab belongs to another bridge context');
        if(old.status==='opening')throw new Refusal('BUSY','A sign-in hand-off is opening');
        try{process.kill(old.pid,0);if(old.workerStart&&processStart(old.pid)===old.workerStart)return {...old,reused:true};}catch{}
        throw new Refusal('KEEPER_STOPPED','Inspect the existing human tab and finish, or explicitly cancel before a new sign-in');
      }
    }
    const next={owner,generation:crypto.randomUUID(),account:name,site:key,file,session:`login-${name}-${crypto.randomUUID()}`,contextId:proof.contextId,status:'opening',createdAt:new Date().toISOString(),origin:new URL(s.identity?.url||s.signInUrl).origin,opener:request.opener,notify:request.recipient,notification:{status:request.recipient?'pending':'skipped',...(!request.recipient?{code:'NO_OPENER'}:{})}};atomicJSON(stateFile,next);return next;
  });
  if(state.reused)return {account:name,site:key,status:state.status,session:state.session,generation:state.generation,reused:true};
  try{
    await stateLocked(bridgeOperation(a),()=>navigate(a,['browser','--session',state.session,'--window','foreground'],s.signInUrl,proof.contextId,d.execute),180000);
    const child=await d.worker('sign-in',stateFile);
    const current=await updateHandoff(stateFile,state.generation,s=>({...s,pid:child.pid,workerStart:s.workerStart||processStart(child.pid),status:s.status==='opening'?'waiting-for-user':s.status}));
    insist(current,'HANDOFF_CHANGED','A different hand-off replaced this request');
    return {account:name,site:key,status:current.status,session:current.session,generation:current.generation,keeperPid:child.pid};
  }catch(e){await updateHandoff(stateFile,state.generation,s=>['completed','cancelled'].includes(s.status)?s:{...s,status:'attention-required',code:s.code||e.code||'KEEPER_START_FAILED'});try{await parkHandoff(stateFile,state,a);}finally{await d.notify(stateFile,state.generation);}throw e;}
}
export async function keepSignIn(stateFile,{execute=oc,delay=ms=>new Promise(r=>setTimeout(r,ms)),park=launch,ready=()=>{},notify=notifyHandoff}={}) {
  const start=read(stateFile),data=(await import('./accounts.mjs')).load(start.file),a=data.accounts[start.account],s=a.sites[start.site];
  insist(start.generation&&JSON.stringify(start.owner)===JSON.stringify(handoffOwner(start.file,start.account,a,start.site)),'HANDOFF_OWNER','Keeper state does not match its account source');
  const keeperStart=processStart(process.pid);insist(keeperStart,'KEEPER_OWNER','Cannot identify the keeper process');
  let hits=0,last='',acknowledged=false;
  try{
    while(true){
      const state=read(stateFile);if(state.generation!==start.generation||!['opening','waiting-for-user'].includes(state.status))break;
      try{
        const i=load(start.file).accounts[start.account].sites[start.site].identity;
        const observed=await stateLocked(bridgeOperation(a),()=>execute(a,['browser','--session',start.session,'eval',expression(i)],start.contextId),180000);
        // Read-only heartbeats preserve the lease while the human types. Never navigate.
        try{
          const recording=!i.recordedAt&&!Object.keys(i.expected||{}).length,identity=recording?capturedIdentity(i,observed):judge(i,observed).identity,fingerprint=JSON.stringify(identity);
          hits=fingerprint===last?hits+1:1;last=fingerprint;
          if(recording&&hits>=2)s.identity=await recordHandoff(start.file,start.account,start.site,i,identity,{file:stateFile,generation:start.generation});
        }catch(e){hits=0;if(!['SIGNED_OUT','BLOCKED','SENSITIVE_SESSION','IDENTITY_LOADING','IDENTITY_ENROLLMENT_REQUIRED'].includes(e.code))throw e;}
        const changed=await updateHandoff(stateFile,start.generation,s=>['opening','waiting-for-user'].includes(s.status)?{...s,pid:process.pid,workerStart:keeperStart,heartbeatAt:new Date().toISOString(),status:hits>=2?'completed':'waiting-for-user'}:null);
        if(!changed)break;
        if(!acknowledged){acknowledged=true;ready();}
        if(hits>=2)break;
      }catch(e){await updateHandoff(stateFile,start.generation,s=>['opening','waiting-for-user'].includes(s.status)?{...s,status:'attention-required',code:e.code||'KEEPER_FAILED'}:null);break;}
      await delay(3000);
    }
  }finally{try{await parkHandoff(stateFile,start,a,park);}finally{await notify(stateFile,start.generation);}}
}
export async function cancelHandoff(stateFile,state,a,{park=launch,notify=scheduleNotification}={}){
  const changed=await updateHandoff(stateFile,state.generation,s=>['completed','cancelled'].includes(s.status)?s:{...s,status:'cancelled'});
  insist(changed,'HANDOFF_CHANGED','A different handoff replaced this cancellation');
  try{await parkHandoff(stateFile,state,a,park);}finally{await notify(stateFile,state.generation);}
  return changed;
}
export async function finish(file,name,a,site) {
  if(!local(a.machine))return remote(a,['finish',name,'--site',site||a.primary]);
  const [key]=siteAt(a,site),stateFile=signInFile(file,name,a,key),before=fs.existsSync(stateFile)?read(stateFile):null,proof=await check(file,name,a,key,{record:!!before&&['opening','waiting-for-user'].includes(before.status),handoff:before?{file:stateFile,generation:before.generation}:null});
  if(before){insist(before.contextId===proof.contextId,'HANDOFF_CONTEXT','The human tab belongs to another context');const result=await updateHandoff(stateFile,before.generation,s=>s.status==='cancelled'?null:{...s,status:'completed'});insist(result,'HANDOFF_CHANGED','The hand-off changed or was cancelled during finish');try{await parkHandoff(stateFile,before,a);}finally{await scheduleNotification(stateFile,before.generation);}}return proof;
}
export async function expireTunnel(stateFile,{start,delay=ms=>new Promise(r=>setTimeout(r,ms)),now=Date.now,ready=()=>{}}={}) {
  const state=read(stateFile);
  const forward=await (start||(await import('./tunnel.mjs')).ownForward)(state);
  try{
    atomicJSON(stateFile,{...state,status:'open'});ready();
    while(now()<state.expiresAt){try{if(read(stateFile).status==='closed')return;}catch{}await delay(Math.min(1000,state.expiresAt-now()));}
  }finally{await forward.stop();try{atomicJSON(stateFile,{...read(stateFile),status:'expired'});}catch{}}
}
async function freePort(){return new Promise((resolve,reject)=>{const s=net.createServer();s.once('error',reject);s.listen(0,'127.0.0.1',()=>{const p=s.address().port;s.close(()=>resolve(p));});});}
export async function open(file,name,a,site,{device='laptop',approve,ttl='900','inspect-only':inspectOnly=false,notify}={}) {
  drivable(a);const [key,s]=siteAt(a,site);
  const request=notificationRequest(process.env,notify);
  if(device==='phone'){
    insist(s.phone?.kind==='approve'&&approve===true,'LAPTOP_REQUIRED','Only an existing approve-on-phone prompt can be handed to a phone; passwords and QR require a laptop/second screen');
    return {account:name,site:key,status:'waiting-for-user',method:'approve-on-phone',note:'Approve the site prompt already on your phone; never give an agent its code. Then use finish.'};
  }
  insist(device==='laptop','DEVICE_UNKNOWN','Choose laptop or phone');
  await bridge(file,name,a);
  if(local(a.machine)&&inspectOnly){const inspect='chrome://inspect/#devices';await run(process.env.CHROME_OPEN_BIN||(process.platform==='darwin'?'open':'google-chrome'),process.platform==='darwin'?['-a','Google Chrome',inspect]:[inspect]);return {account:name,inspect,target:`localhost:${a.bridge.port}`,newSignInTab:false};}
  if(local(a.machine)){const result=await window(file,name,a,key,{request}),stateFile=signInFile(file,name,a,key);await stateLocked(stateFile,async()=>{const current=read(stateFile);if(current.generation===result.generation&&current.status==='waiting-for-user')await launch(file,name,'show');});return result;}
  const seconds=Number(ttl);insist(Number.isInteger(seconds)&&seconds>=60&&seconds<=3600,'TUNNEL_TTL','Tunnel lifetime must be 60–3600 seconds');
  const port=await freePort(),dir=path.join(HOME,'tunnels');privateDir(dir);
  const token=crypto.randomBytes(6).toString('hex'),control=path.join(dir,token+'.sock'),ssh=process.env.SSH_BIN||'ssh';
  const close=[ssh,'-S',control,'-O','exit','--',a.machine.ssh];
  const open=[ssh,'-M','-S',control,'-N','-T','-o','ControlPersist=no','-o','ForkAfterAuthentication=no','-o','BatchMode=yes','-o','ExitOnForwardFailure=yes','-o','ConnectTimeout=15','-o','ServerAliveInterval=15','-o','ServerAliveCountMax=2','-L',`127.0.0.1:${port}:127.0.0.1:${a.bridge.port}`,'--',a.machine.ssh];
  const stateFile=path.join(dir,token+'.json'),expiresAt=Date.now()+seconds*1000;
  atomicJSON(stateFile,{open,close,expiresAt,status:'opening'});
  try{
    await worker('tunnel',stateFile);
    const result=inspectOnly?{account:name,newSignInTab:false}:await remote(a,['window',name,'--site',key,'--notification-request',JSON.stringify(request)]);
    const inspect='chrome://inspect/#devices';await run(process.env.CHROME_OPEN_BIN||(process.platform==='darwin'?'open':'google-chrome'),process.platform==='darwin'?['-a','Google Chrome',inspect]:[inspect]);
    return {...result,inspect,target:`localhost:${port}`,expiresAt:new Date(expiresAt).toISOString(),closeTunnel:close,note:'Configure this localhost target, inspect the sign-in target; select popup targets too. Native passkey dialogs require another supported sign-in method. The tunnel expires automatically.'};
  }catch(e){atomicJSON(stateFile,{...read(stateFile),status:'closed'});throw e;}
}
export function readCommand(args,site){
  insist(args[0]==='browser','READ_ONLY_ENTRANCE','Use a supported read-only browser command');
  let i=1,session;
  while(args[i]?.startsWith('--')){
    if(args[i]==='--session'){insist(!session&&args[i+1]&&!args[i+1].startsWith('-'),'DRIVE_ARGS','Supply one task session');session=args[i+1];i+=2;}
    else if(args[i]==='--window'){insist(args[i+1]==='background','DRIVE_ARGS','Read tasks use background windows');i+=2;}
    else throw new Refusal('DRIVE_ARGS','Profile overrides and unknown options are not supported');
  }
  insist(session&&/^[a-zA-Z0-9][a-zA-Z0-9-]{0,62}$/.test(session)&&!session.startsWith('login-')&&!session.startsWith('check-'),'DRIVE_ARGS','Use a separate task session');
  const op=args[i++],rest=args.slice(i),arity={extract:0,state:0,find:1,get:1,open:1};
  insist(Object.hasOwn(arity,op)&&rest.length===arity[op]&&rest.every(v=>!v.startsWith('-')),'READ_ONLY_ENTRANCE','Supported reads: extract, state, find LOCATOR, get title, open HTTPS_URL');
  if(op==='get')insist(rest[0]==='title','DRIVE_ARGS','Only get title is supported here');
  if(op==='open'){let url;try{url=new URL(rest[0]);}catch{}insist(url?.protocol==='https:'&&!url.username&&!url.password&&url.origin===new URL(site.identity.url).origin&&!/logout|signout|delete|remove|submit|upload|follow|buy/i.test(url.pathname),'DRIVE_ARGS','Read navigation must stay on the verified site');}
  return ['browser','--session',session,'--window','background',op,...rest];
}
export async function drive(file,name,a,site,args,{root=HOME,check:prove=check,execute=oc}={}){
  try{return await driveSite(file,name,a,site,args,{root,prove,execute});}catch(e){throw lowerAssuranceError(a.sites[site||a.primary]?.identity,e);}
}
async function driveSite(file,name,a,site,args,{root,prove,execute}){
  drivable(a);const [key,s]=siteAt(a,site);insist(s.identity,'IDENTITY_UNCONFIGURED','Configure identity before driving');const command=readCommand(args,s);
  assertReadable(a,command[command.indexOf('--session')+1],root);
  if(!local(a.machine)){const proof=await remote(a,['drive',name,'--site',key,'--policy','private-pages-v1','--',...command]);insist(proof?.pagePolicy==='private-pages-v1'&&Object.hasOwn(proof,'result'),'PAGE_POLICY_MISMATCH','The holder does not prove private-page read protection; align plugin versions');return untrusted(proof.result,'remote-browser-page');}
  const proof=await prove(file,name,a,key);
  return stateLocked(bridgeOperation(a),async()=>{
    const session=command[command.indexOf('--session')+1],task=['browser','--session',session,'--window','background'],opening=command[5]==='open';
    const location=raw=>{let url;try{url=new URL(raw);}catch{}
    insist(url,'PAGE_LOCATION_UNKNOWN','Cannot establish the task page location; no content was read');
    insist(url.protocol==='https:'||url.href==='about:blank','PAGE_LOCATION_UNKNOWN','Unsupported page location; no content was read');
    if(url.hostname==='console.cloud.google.com'||url.hostname==='accounts.google.com'){protectSession(a,session,'private-google',root);throw new Refusal('SENSITIVE_SESSION','This Google control lease is private; no content was read');}
    insist(url.href==='about:blank'||url.origin===new URL(s.identity.url).origin,'PAGE_LOCATION_UNKNOWN','Task navigation left the verified site; no content was read');return url;};
    location(opening?command.at(-1):await execute(a,[...task,'get','url'],proof.contextId));
    if(opening){try{await execute(a,command,proof.contextId);}catch{throw new Refusal('PAGE_NAVIGATION_FAILED','Task navigation failed; page payload and upstream error withheld');}location(await execute(a,[...task,'get','url'],proof.contextId));}
    const page=await execute(a,[...task,'eval',expression({kind:'generic',url:s.identity.url,expected:s.identity.kind==='generic'?s.identity.expected:{}})],proof.contextId);
    if(s.identity.kind==='generic')judge(s.identity,page);else genericObservation(page,s.identity);
    if(opening)return untrusted({opened:true},'browser-navigation');
    return untrusted(await execute(a,command,proof.contextId),'browser-page');
  },180000);
}
export async function propose(file,name,a,site){
  const [key]=siteAt(a,site),proof=await check(file,name,a,key);
  return instructionProposal(name,key,proof,(await import('./accounts.mjs')).load(file).rulings);
}
export function instructionProposal(name,key,proof,rulings={}){
  const choice=rulings[`instructions.${name}.${key}`]?.value;
  if(choice==='no')return {account:name,site:key,suppressed:true,written:false};
  const prefix='Signed-in sites (use /sign-in):';
  const proposals=[path.join(os.homedir(),'.claude/CLAUDE.md'),path.join(os.homedir(),'.codex/AGENTS.md')].map(file=>{
    const lines=(fs.existsSync(file)?fs.readFileSync(file,'utf8'):'').split('\n').filter(l=>l.startsWith(prefix));insist(lines.length<=1,'INSTRUCTIONS_AMBIGUOUS','More than one sites line');
    const words=lines.length?lines[0].slice(prefix.length).split(',').map(v=>v.trim()).filter(Boolean):[];insist(words.every(v=>/^[a-z][a-z0-9-]*$/.test(v)),'INSTRUCTIONS_AMBIGUOUS','Sites line is not a list of words');
    return {file,before:lines[0]||null,after:`${prefix} ${[...new Set([...words,key])].join(', ')}`};
  });return {account:name,site:key,proof,needsUserOK:choice!=='yes',ruling:`instructions.${name}.${key}`,proposals,written:false};
}
