import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {ROOT,read,atomicJSON,stateLocked,processStart,run,insist} from './core.mjs';
import {worker} from './bridge-lifecycle.mjs';

const validId=value=>typeof value==='string'&&value===value.trim()&&value.length>0&&value.length<=200&&!value.startsWith('-')&&!/[\x00-\x1f\x7f]/.test(value);
const uuid=value=>/^[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}$/i.test(value);
export function notificationRequest(env=process.env,explicit){
  const opener={};
  if(validId(env.CLAUDE_CODE_SESSION_ID))opener.claudeSessionId=env.CLAUDE_CODE_SESSION_ID;
  if(validId(env.CODEX_THREAD_ID))opener.codexThreadId=env.CODEX_THREAD_ID;
  let recipient=opener.codexThreadId?{kind:'codex',id:opener.codexThreadId}:opener.claudeSessionId?{kind:'claude',id:opener.claudeSessionId}:null;
  if(explicit!==undefined){
    const match=/^(claude|codex):(.*)$/.exec(explicit);
    const id=match?match[2]:explicit;insist(validId(id),'USAGE','--notify needs an agent name or ID');
    recipient={kind:match?match[1]:recipient?.kind||'claude',id};
  }
  return {opener:Object.keys(opener).length?opener:null,recipient};
}
export function validateNotificationRequest(value){
  const record=v=>v&&typeof v==='object'&&!Array.isArray(v);
  insist(record(value)&&Object.keys(value).every(k=>['opener','recipient'].includes(k)),'USAGE','Invalid notification request');
  const {opener,recipient}=value;
  insist(opener===null||record(opener)&&Object.keys(opener).every(k=>['claudeSessionId','codexThreadId'].includes(k)&&validId(opener[k])),'USAGE','Invalid opener');
  insist(recipient===null||record(recipient)&&Object.keys(recipient).length===2&&['claude','codex'].includes(recipient.kind)&&validId(recipient.id),'USAGE','Invalid notification recipient');
  return value;
}
export function locateAsk(env=process.env,root=ROOT){
  const exists=file=>{try{return path.isAbsolute(file)&&fs.statSync(file).isFile();}catch{return false;}};
  if(env.ASK_SCRIPT){insist(exists(env.ASK_SCRIPT),'ASK_MISSING','ASK_SCRIPT must name an existing absolute ask.py');return env.ASK_SCRIPT;}
  const home=env.HOME||os.homedir(),candidates=[path.resolve(root,'../session-tools/skills/ask/scripts/ask.py'),env.CLAUDE_PLUGIN_ROOT&&path.join(env.CLAUDE_PLUGIN_ROOT,'skills/ask/scripts/ask.py'),path.join(home,'.claude/skills/ask/scripts/ask.py')].filter(Boolean);
  const standard=candidates.find(exists);if(standard)return standard;
  for(const registry of [path.join(home,'.claude/plugins/installed_plugins.json'),path.join(env.CODEX_HOME||path.join(home,'.codex'),'plugins/installed_plugins.json')]){
    if(!fs.existsSync(registry))continue;
    for(const [name,entries]of Object.entries(read(registry).plugins||{}))if(name.split('@')[0]==='session-tools')for(const entry of Array.isArray(entries)?entries:[entries]){
      const file=entry?.installPath&&path.join(entry.installPath,'skills/ask/scripts/ask.py');if(file&&exists(file))return file;
    }
  }
  for(let dir=path.resolve(root);;dir=path.dirname(dir)){
    for(const name of ['.claude-plugin','.codex-plugin']){
      const catalog=path.join(dir,name,'marketplace.json');if(!fs.existsSync(catalog))continue;
      const declared=(read(catalog).plugins||[]).filter(p=>p.name==='session-tools');
      if(declared.length){const file=declared.filter(p=>typeof p.source==='string'&&p.source.startsWith('.')).map(p=>path.resolve(dir,p.source,'skills/ask/scripts/ask.py')).find(exists);insist(file,'ASK_MISSING','The declared session-tools provider is missing');return file;}
    }
    if(dir===path.dirname(dir))break;
  }
  insist(false,'ASK_MISSING','Install session-tools or set ASK_SCRIPT');
}
const reasons=new Set(['IDENTITY_MISMATCH','IDENTITY_UNKNOWN','IDENTITY_UNSETTLED','SIGNED_OUT','BLOCKED','SENSITIVE_SESSION','KEEPER_STOPPED','KEEPER_START_FAILED','KEEPER_FAILED','BRIDGE_NOT_RUNNING','BRIDGE_UNVERIFIED','ACCOUNT_CHANGED','HANDOFF_OWNER','HANDOFF_CHANGED']);
export function notificationMessage(state){
  const login=/^[a-z][a-z0-9-]{0,62}$/.test(state.account)?state.account:'configured login';
  let host='configured site';try{const u=new URL(state.origin);if(u.protocol==='https:')host=u.hostname;}catch{}
  if(state.status==='completed')return `Signed in: login ${login} (${host}). It is ready for drive.`;
  if(state.status==='cancelled')return `Sign-in cancelled: login ${login} (${host}).`;
  return `Sign-in needs attention: login ${login} (${host}). Reason: ${reasons.has(state.code)?state.code:'SIGN_IN_FAILED'}.`;
}
export async function sendNotification(recipient,message,stateFile,{env=process.env,root=ROOT,execute=run,locate=locateAsk}={}){
  const script=locate(env,root),dir=fs.mkdtempSync(path.join(path.dirname(stateFile),'.notice-'));fs.chmodSync(dir,0o700);
  const prompt=path.join(dir,'prompt.txt'),outcome=path.join(dir,'outcome.json');
  try{
    fs.writeFileSync(prompt,message,{mode:0o600,flag:'wx'});
    const args=[script,'--session',recipient.id,'--prompt-file',prompt,'--raw','--no-wait','--outcome',outcome];
    if(recipient.kind==='codex')args.push('--codex',...(uuid(recipient.id)?['--mid-turn','--exact']:['--auto']));
    else if(uuid(recipient.id))args.push('--exact');
    let failed=false;try{await execute('python3',args,{env,timeout:30000,maxBuffer:1024*1024});}catch{failed=true;}
    let result;try{result=read(outcome);}catch{}
    if(result?.version===1&&result.kind==='accepted')return {status:'delivered',code:'ASK_ACCEPTED'};
    return {status:'failed',code:result?.version===1&&result.kind==='not-delivered'?'ASK_DELIVERY_FAILED':failed?'ASK_DELIVERY_FAILED':'ASK_ACCEPTANCE_UNKNOWN'};
  }finally{fs.rmSync(dir,{recursive:true,force:true});}
}
export async function notifyHandoff(stateFile,generation,{send=sendNotification,...dependencies}={}){
  try{
    const state=await stateLocked(stateFile,()=>{
      const s=read(stateFile);
      if(s.generation!==generation||!['completed','cancelled','attention-required'].includes(s.status)||s.notification&&s.notification.status!=='pending')return null;
      s.notification=s.notify?{status:'sending',attemptedAt:new Date().toISOString(),pid:process.pid,start:processStart(process.pid)}:{status:'skipped',code:'NO_OPENER'};
      atomicJSON(stateFile,s);return s;
    });
    if(!state||!state.notify)return state?.notification;
    let result;try{result=await send(state.notify,notificationMessage(state),stateFile,dependencies);}catch(e){result={status:'failed',code:e.code==='ASK_MISSING'?'ASK_MISSING':'ASK_DELIVERY_FAILED'};}
    const delivered=result?.status==='delivered';
    const code=delivered?'ASK_ACCEPTED':['ASK_MISSING','ASK_DELIVERY_FAILED','ASK_ACCEPTANCE_UNKNOWN'].includes(result?.code)?result.code:'ASK_DELIVERY_FAILED';
    return await stateLocked(stateFile,()=>{
      const current=read(stateFile);if(current.generation!==generation)return null;
      current.notification={...current.notification,status:delivered?'delivered':'failed',code,finishedAt:new Date().toISOString()};atomicJSON(stateFile,current);return current.notification;
    });
  }catch{return {status:'failed',code:'NOTIFICATION_STATE_FAILED'};}
}
export async function scheduleNotification(stateFile,generation,{start=worker}={}){
  try{
    const state=read(stateFile);if(state.generation!==generation||state.notification&&state.notification.status!=='pending')return;
    if(!state.notify)return notifyHandoff(stateFile,generation);
    await start('sign-in-notice',stateFile,generation);
  }catch{
    try{await stateLocked(stateFile,()=>{const state=read(stateFile);if(state.generation===generation&&(!state.notification||state.notification.status==='pending')){state.notification={status:'failed',code:'NOTIFICATION_KEEPER_FAILED'};atomicJSON(stateFile,state);}});}catch{}
  }
}
