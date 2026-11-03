import { spawn } from 'node:child_process';
import { run, insist, processStart } from './core.mjs';

// The detached watchdog owns foreground SSH before forwarding can exist.
export async function ownForward(state,{launch=spawn,execute=run,delay=ms=>new Promise(r=>setTimeout(r,ms)),now=Date.now,identity=processStart}={}) {
  const child=launch(state.open[0],state.open.slice(1),{stdio:'ignore'});
  const running=()=>child.exitCode===null&&!child.signalCode;
  let error;child.on('error',e=>{error=e;});
  const stamp=()=>identity(child.pid);let start;
  const stop=async()=>{
    if(!running())return;
    insist(start&&stamp()===start,'TUNNEL_OWNER','Cannot positively identify the owned tunnel');
    child.kill('SIGTERM');
    const end=now()+3000;while(running()&&now()<end)await delay(20);
    if(running()){insist(stamp()===start,'TUNNEL_OWNER','Tunnel process changed');child.kill('SIGKILL');}
  };
  try{
    await new Promise((resolve,reject)=>{child.once('spawn',resolve);child.once('error',reject);});
    start=stamp();insist(start,'TUNNEL_OWNER','Cannot identify the new tunnel process');
    const deadline=Math.min(state.expiresAt,now()+20000);
    while(now()<deadline){
      if(error)throw error;
      insist(running(),'TUNNEL_START_FAILED','SSH forwarding exited before readiness');
      try{const r=await execute(state.close[0],[...state.close.slice(1,3),'-O','check',...state.close.slice(-2)],{timeout:1000});if(/Master running/.test(r.stdout+' '+r.stderr))return {stop};}catch{}
      await delay(100);
    }
    throw Error('SSH forwarding did not become ready');
  }catch(e){if(start)await stop();else if(running())child.kill('SIGTERM');throw e;}
}
