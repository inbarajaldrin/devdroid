import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import {spawn} from 'node:child_process';
import {HOME,ROOT,read,atomicJSON,locked,processStart,insist,Refusal,run,json,local} from './core.mjs';
import {canonical,load} from './accounts.mjs';

export const IDLE_GRACE=120000;
const owner=a=>({machine:a.machine.id,profile:canonical(a.bridge.profileDir),port:a.bridge.port});
export const lifecycleFile=(a,root=HOME)=>path.join(root,'bridge-work',crypto.createHash('sha256').update(JSON.stringify(owner(a))).digest('hex')+'.json');
const launch=async(file,name,cmd)=>json((await run(process.execPath,[process.env.SIGN_IN_LAUNCHER||path.join(ROOT,'browser/ext-bridge.mjs'),'--accounts',file,cmd,name],{timeout:180000})).stdout);
async function serial(file,work){const deadline=Date.now()+180000;while(true){try{return await locked(file,work);}catch(e){if(e.code!=='BUSY'||Date.now()>=deadline)throw e;await new Promise(r=>setTimeout(r,20));}}}
export async function worker(mode,file,...extra){
  const child=spawn(process.execPath,[process.env.SIGN_IN_KEEPER||path.join(ROOT,'scripts/keeper.mjs'),mode,file,...extra],{detached:true,stdio:['ignore','ignore','ignore','ipc']});
  try{await new Promise((resolve,reject)=>{
    const timer=setTimeout(()=>reject(new Refusal('KEEPER_START_FAILED','Background keeper did not acknowledge readiness')),30000);
    child.once('error',e=>{clearTimeout(timer);reject(e);});
    child.once('exit',()=>{clearTimeout(timer);reject(new Refusal('KEEPER_START_FAILED','Background keeper exited before readiness'));});
    child.once('message',m=>{clearTimeout(timer);m?.ready?resolve():reject(new Refusal('KEEPER_START_FAILED','Background keeper refused'));});
  });}finally{if(child.connected)child.disconnect();child.unref();}
  return child;
}
function alive(lease){
  try{process.kill(lease.pid,0);}catch(e){if(e.code==='ESRCH')return false;return true;}
  const start=processStart(lease.pid);return !start||!lease.start||start===lease.start;
}
function active(lease,isAlive=alive){
  if(lease.handoff){
    try{const state=read(lease.handoff);return state.generation===lease.generation&&!['completed','cancelled'].includes(state.status);}catch{return true;}
  }
  return isAlive(lease);
}
function prune(state,isAlive){state.leases=Object.fromEntries(Object.entries(state.leases||{}).filter(([,lease])=>active(lease,isAlive)));return state;}
export function bridgeWork(a,root=HOME,{isAlive=alive,now=Date.now}={}){
  const file=lifecycleFile(a,root);if(!fs.existsSync(file))return {managed:false,reasons:[],idleUntil:null};
  const state=prune(read(file),isAlive);
  return {managed:true,reasons:Object.values(state.leases).map(lease=>lease.purpose),idleUntil:state.idleUntil?new Date(state.idleUntil).toISOString():null,status:state.status,watcherAlive:!!state.watcher&&isAlive(state.watcher),idleRemainingMs:state.idleUntil?Math.max(0,state.idleUntil-now()):null};
}
export async function withBridge(file,name,a,purpose,work,dependencies={}){
  insist(local(a.machine),'WRONG_MACHINE','Bridge work runs on its holder');
  const d={root:HOME,launch,worker,now:Date.now,isAlive:alive,grace:IDLE_GRACE,...dependencies},stateFile=lifecycleFile(a,d.root),token=crypto.randomUUID();
  const start=processStart(process.pid);insist(start,'BRIDGE_LEASE_OWNER','Cannot identify the operation process');
  await serial(stateFile,async()=>{
    let state=fs.existsSync(stateFile)?prune(read(stateFile),d.isAlive):{owner:owner(a),file:canonical(file),name,leases:{},status:'stopped'};
    insist(JSON.stringify(state.owner)===JSON.stringify(owner(a)),'BRIDGE_LEASE_OWNER','Bridge work belongs to another profile');
    // Publish the operation before startup so a failed launcher remains recoverable.
    state.leases[token]={pid:process.pid,start,purpose};state.idleUntil=null;atomicJSON(stateFile,state);
    try{
      if(!state.watcher||!d.isAlive(state.watcher)){
        const child=await d.worker('bridge-idle',stateFile);
        state.watcher={pid:child.pid,start:processStart(child.pid)};atomicJSON(stateFile,state);
      }
      let running=false;
      if(state.status==='running'){
        try{await d.launch(file,name,'verify');running=true;}catch(e){if(!['BRIDGE_NOT_RUNNING','BRIDGE_SLEEPING','DAEMON_STOPPED'].includes(e.code))throw e;}
      }
      if(!running)await d.launch(file,name,'bridge-up');
      state.status='running';state.file=canonical(file);state.name=name;atomicJSON(stateFile,state);
    }catch(e){delete state.leases[token];state.idleUntil=d.now()+d.grace;atomicJSON(stateFile,state);throw e;}
  });
  try{
    const result=await work();
    // A human hand-off outlives the command process. Its generation is its lease.
    if(['open','window'].includes(purpose)&&result?.generation){
      const {signInFile}=await import('./sign-in.mjs');
      await pinHandoff(a,signInFile(file,name,a,result.site,d.root),result.generation,'human sign-in',d);
    }
    if(purpose==='google-console'){
      const {sessionState}=await import('./core.mjs'),page=sessionState(a,`google-console-${name}`,d.root);
      if(page.handoffFile&&fs.existsSync(page.handoffFile)){const handoff=read(page.handoffFile);await pinHandoff(a,page.handoffFile,handoff.generation,'human Google setup',d);}
    }
    return result;
  }finally{await serial(stateFile,()=>{
    const state=prune(read(stateFile),d.isAlive);delete state.leases[token];
    if(!Object.keys(state.leases).length)state.idleUntil=d.now()+d.grace;
    atomicJSON(stateFile,state);
  });}
}
export async function pinHandoff(a,handoff,generation,purpose,{root=HOME}={}){
  const file=lifecycleFile(a,root);if(!fs.existsSync(file))return;
  await serial(file,()=>{const state=read(file);state.leases['human-'+generation]={handoff,generation,purpose};state.idleUntil=null;atomicJSON(file,state);});
}
const closeIdle=async(file,name,cmd)=>(await import('../browser/ext-bridge.mjs')).command(file,cmd,name,{leaseLocked:true});
export async function reapBridge(stateFile,{launch:execute=closeIdle,now=Date.now,isAlive=alive,grace=IDLE_GRACE}={}){
  return locked(stateFile,async()=>{
    const state=prune(read(stateFile),isAlive);
    if(Object.keys(state.leases).length){state.idleUntil=null;atomicJSON(stateFile,state);return {held:true};}
    state.idleUntil??=now()+grace;
    if(now()<state.idleUntil){atomicJSON(stateFile,state);return {idle:true,remaining:state.idleUntil-now()};}
    const a=load(state.file).accounts[state.name];
    insist(a&&JSON.stringify(owner(a))===JSON.stringify(state.owner),'BRIDGE_LEASE_OWNER','Account changed; no browser was stopped');
    // Acquisition and close serialize on this file; a new lease cannot race shutdown.
    atomicJSON(stateFile,state);
    try{await execute(state.file,state.name,'bridge-down');state.status='stopped';}
    catch(e){if(e.code==='BRIDGE_NOT_RUNNING')state.status='stopped';else if(e.code==='EXTERNAL_BRIDGE')state.status='external-protected';else throw e;}
    state.watcher=null;atomicJSON(stateFile,state);return {stopped:state.status==='stopped',status:state.status};
  });
}
export async function keepBridges(stateFile,{ready=()=>{},delay=ms=>new Promise(r=>setTimeout(r,ms)),...dependencies}={}){
  // Acknowledge before taking the state lock held by the acquiring parent.
  ready();
  while(true){
    try{const result=await reapBridge(stateFile,dependencies);if(result.stopped||result.status==='external-protected')return;}
    catch(e){if(e.code!=='BUSY')throw e;}
    await delay(1000);
  }
}
