// Run the dependency's own CLI with its own process fakes. No live accounts or bridges.
import fs from 'node:fs';
import path from 'node:path';
import {pathToFileURL} from 'node:url';
import {execFileSync} from 'node:child_process';
const base=process.argv[2];
const {fakeCLI}=await import(pathToFileURL(base+'/tests/support.mjs'));
const cleanups=[];
try {
 const f=fakeCLI({after:fn=>cleanups.push(fn)});
 if (process.argv[3]) {
   const runner=process.argv[3], conflict=process.argv[4]==='conflict';
   const env={...f.env};
   if(conflict){const loader=path.join(f.dir,'conflict.mjs');fs.writeFileSync(loader,`globalThis.fetch=async()=>({ok:true,json:async()=>({daemonVersion:'2.0.3'})});`);env.NODE_OPTIONS+=' --import='+JSON.stringify(loader);}
   let result;
   try{result={status:0,stdout:execFileSync(process.execPath,[runner,base+'/scripts/sign-in.mjs'],{env,stdio:['ignore','pipe','pipe']}).toString()};}
   catch(error){result={status:error.status,stderr:error.stderr.toString()};}
   process.stdout.write(JSON.stringify({...result,calls:f.calls()}));
 } else process.stdout.write(execFileSync(process.execPath,[base+'/scripts/sign-in.mjs','--accounts',f.file,'check','work','--site','handshake'],{env:f.env}));
} finally {for(const fn of cleanups.reverse())fn();}
