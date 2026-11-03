import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import {fork} from 'node:child_process';
import {HOME,ROOT,read,atomicJSON,locked,processStart,insist,ocPrivate,setSessionState,sessionState} from './core.mjs';
import {load,canonical} from './accounts.mjs';
import {launch,stateLocked,bridgeOperation} from './sign-in.mjs';
import {systemClock} from './time.mjs';
export async function handoff(file,name,a,session,context,root=HOME,{start=fork,timeout=10000,clock=systemClock}={}){
 file=canonical(file);root=canonical(root);
 const stateFile=path.join(root,'human-leases',crypto.createHash('sha256').update(JSON.stringify([a.machine.id,a.bridge.port,a.bridge.profileDir,session])).digest('hex')+'.json');
 const state=await locked(stateFile,()=>{
  const page=sessionState(a,session,root);insist(!page.humanActive&&!page.pendingAction||page.sourceFile===file&&page.sourceAccount===name,'HANDOFF_OWNER','Another account source owns this human lease');
  if(fs.existsSync(stateFile)){const old=read(stateFile);if(old.status==='waiting'){
    insist(old.file===file&&old.context===context,'HANDOFF_OWNER','Another account source owns this human lease');
    insist(old.pid,'BUSY','Human lease is opening; finish it explicitly if its worker failed');
    let alive=true;try{process.kill(old.pid,0);}catch(e){if(e.code==='ESRCH')alive=false;}
    if(alive){insist(old.start&&processStart(old.pid)===old.start,'BUSY','Cannot prove the existing human lease owner');return {...old,reused:true};}
  }}
  const state={file,name,session,context,owner:{machine:a.machine.id,port:a.bridge.port,profile:a.bridge.profileDir},generation:crypto.randomUUID(),status:'waiting',expiresAt:Date.now()+3600000};atomicJSON(stateFile,state);return state;
 });
 setSessionState(a,session,{sourceFile:file,sourceAccount:name,handoffFile:stateFile},root);
 if(state.reused)return {reused:true};
 try{
  const child=start(path.join(ROOT,'scripts/keeper.mjs'),['human',stateFile],{detached:true,env:{...process.env,SIGN_IN_HOME:root},stdio:['ignore','ignore','ignore','ipc']});
  await new Promise((resolve,reject)=>{const timer=clock.setTimeout(()=>{child.kill('SIGTERM');reject(Error('Human lease did not acknowledge'));},timeout);child.once('message',m=>{clock.clearTimeout(timer);m?.ready?resolve():reject(Error('Human lease refused'));});child.once('error',e=>{clock.clearTimeout(timer);reject(e);});child.once('exit',()=>{clock.clearTimeout(timer);reject(Error('Human lease stopped'));});});
  child.disconnect();child.unref();return {keptAlive:true};
 }catch(e){await stateLocked(stateFile,()=>{const s=read(stateFile);if(s.generation===state.generation)atomicJSON(stateFile,{...s,status:'attention-required'});});throw e;}
}
export async function keepHuman(stateFile,{execute=ocPrivate,park=launch,delay=ms=>new Promise(r=>setTimeout(r,ms)),ready=()=>{}}={}){
 const initial=read(stateFile),a=load(initial.file).accounts[initial.name];let acknowledged=false;
 const owner={machine:a.machine.id,port:a.bridge.port,profile:a.bridge.profileDir};insist(JSON.stringify(initial.owner)===JSON.stringify(owner),'HANDOFF_OWNER','Human lease belongs to another physical bridge');
 try{while(true){const s=read(stateFile);if(s.generation!==initial.generation||s.status!=='waiting'||Date.now()>=s.expiresAt)break;
  await stateLocked(bridgeOperation(a),()=>execute(a,['browser','--session',s.session,'eval','true'],s.context));
  const updated=await stateLocked(stateFile,()=>{const fresh=read(stateFile);if(fresh.generation!==initial.generation)return false;atomicJSON(stateFile,{...fresh,pid:process.pid,start:processStart(process.pid)});return true;});if(!updated)break;
  if(!acknowledged){acknowledged=true;ready();}await delay(3000);
 }}finally{await stateLocked(stateFile,async()=>{const s=read(stateFile),current=load(initial.file).accounts[initial.name];if(s.generation===initial.generation){atomicJSON(stateFile,{...s,status:'completed'});if(a.bridge.offscreen&&current?.machine.id===owner.machine&&current?.bridge.port===owner.port&&current?.bridge.profileDir===owner.profile)await park(initial.file,initial.name,'park');}});}
}
export async function finishHuman(file,name,a,root=HOME,{park=launch}={}){
 file=canonical(file);root=canonical(root);const session=`google-console-${name}`,owner={machine:a.machine.id,port:a.bridge.port,profile:a.bridge.profileDir};
 return stateLocked(bridgeOperation(a),()=>locked(file,async()=>{
  const current=load(file).accounts[name];insist(current&&JSON.stringify({machine:current.machine.id,port:current.bridge.port,profile:current.bridge.profileDir})===JSON.stringify(owner),'HANDOFF_OWNER','The account holder changed; no window was parked');
  const s=sessionState(a,session,root);insist(s.sourceFile===file&&s.sourceAccount===name&&(s.humanActive||s.pendingAction||s.handoffFile),'HANDOFF_OWNER','Only the original account source can finish this Console handoff');
  if(s.handoffFile){const handoffFile=canonical(s.handoffFile);insist(handoffFile.startsWith(path.join(root,'human-leases')+path.sep),'HANDOFF_OWNER','Invalid lease source');await stateLocked(handoffFile,()=>{const state=read(handoffFile);insist(canonical(state.file)===file&&state.name===name&&state.session===session&&JSON.stringify(state.owner)===JSON.stringify(owner),'HANDOFF_OWNER','This human lease belongs to another account source');atomicJSON(handoffFile,{...state,status:'completed'});});}
  if(a.bridge.offscreen)await park(file,name,'park');
  setSessionState(a,session,{pendingAction:null,humanActive:false},root);return {finished:true,parked:!!a.bridge.offscreen};
 }));
}
