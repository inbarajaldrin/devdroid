import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { ROOT, machineId, writeNew } from '../lib/core.mjs';
import { pins, platform, binary } from '../browser/settings.mjs';
export function temp(t){const dir=fs.mkdtempSync(path.join(os.tmpdir(),'sign-in-test-'));t.after(()=>fs.rmSync(dir,{recursive:true,force:true}));return dir;}
let syntheticPort=10000+(process.pid%1000)*50;
export function account(dir,name='work') {return {label:'Synthetic account',machine:{id:machineId(),exclusive:true,ssh:null,cli:path.join(dir,'runtime/scripts/sign-in.mjs')},bridge:{name,profileDir:path.join(dir,'profiles',name),port:syntheticPort++,offscreen:true,driveBlocked:false,contextIds:['ctx-work']},primary:'handshake',sites:{handshake:{signInUrl:'https://school.joinhandshake.com/login',identity:{kind:'handshake',url:'https://school.joinhandshake.com/settings',expected:{profile:'https://school.joinhandshake.com/profiles/test-user'}}}},permissions:[]};}
export function config(t){const dir=temp(t),a=account(dir),file=path.join(dir,'accounts.json');writeNew(file,{version:1,accounts:{work:a}});return {dir,a,file};}
export const observed={state:'ready',identity:{profile:'https://school.joinhandshake.com/profiles/test-user'}};
export function layer(t){
  const dir=temp(t),root=path.join(dir,'browser'),file=path.join(root,'accounts.json'),calls=[];
  const deps={root,target:platform(),shared:async()=>[],status:async()=>({daemonVersion:pins.opencli.version,profiles:[]}),execute:async(bin,args)=>{
    calls.push({bin,args});if(bin==='npm'){const prefix=args[args.indexOf('--prefix')+1];fs.mkdirSync(path.join(prefix,'node_modules/.bin'),{recursive:true});fs.writeFileSync(path.join(prefix,'node_modules/.bin/opencli'),'fake CLI');return {stdout:''};}
    if(args.includes('doctor'))throw Error('Doctor must not call stock doctor');
    return {stdout:bin.endsWith('opencli')?pins.opencli.version:`Google Chrome for Testing ${pins.chrome.version}`};
  },download:async(url,dest,hash)=>{calls.push({url,hash});fs.writeFileSync(dest,'fake verified archive');},extract:async(zip,dest)=>{
    fs.mkdirSync(dest,{recursive:true});if(zip.endsWith('extension.zip'))fs.writeFileSync(path.join(dest,'manifest.json'),JSON.stringify({version:pins.extension.version}));
    else{const bin=binary(path.dirname(zip),platform());fs.mkdirSync(path.dirname(bin),{recursive:true});fs.writeFileSync(bin,'fake Chrome');}
  }};
  const source=path.join(dir,'input.json');writeNew(source,{version:1,accounts:{work:account(dir)}});
  return {dir,root,file,source,calls,deps};
}
export function fakeCLI(t){
  const f=config(t),log=path.join(f.dir,'calls.jsonl'),script=path.join(f.dir,'fake.mjs');
  fs.writeFileSync(script,`#!/usr/bin/env node
import fs from 'node:fs';const args=process.argv.slice(2);fs.appendFileSync(process.env.CALLS,JSON.stringify({bin:process.argv[1],args,ownedCleanup:process.env.OPENCLI_CLOSE_OWNED_TAB})+'\\n');
if(process.argv[1].includes('keeper')&&process.send){if(args[0]==='tunnel'){const state=JSON.parse(fs.readFileSync(args[1]));fs.appendFileSync(process.env.CALLS,JSON.stringify({bin:state.open[0],args:state.open.slice(1)})+'\\n');}process.send({ready:true});}
if(process.env.FAIL){console.error('fake failed https://example.invalid/oauth?secret=never-print');process.exit(7);}
if(args.includes('--version')){console.log('2.0.2');process.exit(0);}
if(process.env.PRIVATE_FAILURE){console.error('client_secret=PRIVATE-FAKE-SECRET');process.exit(7);}
let data={};
if(process.argv[1].includes('launcher')){const file=args[args.indexOf('--accounts')+1],name=args.at(-1),a=JSON.parse(fs.readFileSync(file)).accounts[name];data={name,port:a.bridge.port,contextId:'ctx-work',verified:true};}
if(process.argv[1].includes('ssh')&&args.at(-1)?.includes("'check'"))data={account:'work',site:'handshake',status:'signed-in',identity:{profile:'https://school.joinhandshake.com/profiles/test-user'}};
if(process.argv[1].includes('ssh')&&args.at(-1)?.includes("'bridge'"))data={name:'work',port:Number(process.env.TEST_PORT),contextId:'ctx-remote'};
if(process.argv[1].includes('ssh')&&args.at(-1)?.includes("'window'"))data={account:'work',site:'handshake',status:'waiting-for-user',session:'login-remote'};
if(args.includes('eval'))data=JSON.parse(process.env.OBSERVED);
if(args.includes('eval')&&args.at(-1)==='true')data=true;
if(process.env.PRIVATE_PAYLOAD&&args.includes('browser')&&!args.includes('url'))data={secret:'PRIVATE-FAKE-SECRET'};
if(process.argv[1].includes('opencli')&&args.includes('extract'))data='synthetic extracted text';
if(process.argv[1].includes('ssh')&&args.at(-1)?.includes("'drive'"))data='synthetic remote text';
if(process.argv[1].includes('ssh')&&args.at(-1)?.includes("'drive'")&&args.at(-1)?.includes("'--policy'")&&!process.env.OLD_PAGE_POLICY)data={pagePolicy:'private-pages-v1',result:data};
if(args.includes('profile'))data={profiles:[{alias:'work',contextId:'ctx-work',connected:true,extensionVersion:'1.0.26'}]};
if(args.includes('browser')&&args.includes('--session')){const session=args[args.indexOf('--session')+1],store=process.env.CALLS+'.pages.json',pages=fs.existsSync(store)?JSON.parse(fs.readFileSync(store)):{};if(args.includes('open'))pages[session]=args[args.indexOf('open')+1];if(args.at(-1)==='close')delete pages[session];if(args.includes('get')&&args.includes('url'))data=pages[session]||'about:blank';fs.writeFileSync(store,JSON.stringify(pages),{mode:0o600});}
if(process.argv[1].includes('ssh'))data={holdingMachine:'f'.repeat(64),result:data};
console.log(JSON.stringify({ok:true,data}));
`,{mode:0o700});
  const bins={};for(const name of ['opencli','launcher','ssh','chrome','keeper']){bins[name]=path.join(f.dir,name+'.mjs');fs.copyFileSync(script,bins[name]);fs.chmodSync(bins[name],0o700);}
  const loader=path.join(f.dir,'fetch.mjs');fs.writeFileSync(loader,`import {createRequire,syncBuiltinESMExports} from 'node:module';const req=createRequire(import.meta.url),cp=req('node:child_process'),fs=req('node:fs'),util=req('node:util');const real=cp.execFile;cp.execFile=function(bin,args,opts,cb){if(bin==='dscl'){queueMicrotask(()=>cb(null,'test-user '+process.getuid()+'\\n',''));return {};}return real.apply(this,arguments);};cp.execFile[util.promisify.custom]=(bin,args,opts)=>new Promise((resolve,reject)=>cp.execFile(bin,args,opts,(e,stdout,stderr)=>{if(e){e.stdout=stdout;e.stderr=stderr;reject(e);}else resolve({stdout,stderr});}));const read=fs.readFileSync;fs.readFileSync=function(file,...args){if(file==='/etc/passwd')return 'test:x:'+process.getuid()+':1::/tmp:/bin/sh';return read.call(this,file,...args);};syncBuiltinESMExports();globalThis.fetch=async()=>({ok:true,json:async()=>({daemonVersion:'2.0.2',profiles:[]})});`);
  const env={...process.env,HOME:f.dir,SIGN_IN_HOME:path.join(f.dir,'browser'),OPENCLI_BIN:bins.opencli,SIGN_IN_LAUNCHER:bins.launcher,SIGN_IN_KEEPER:bins.keeper,SSH_BIN:bins.ssh,CHROME_OPEN_BIN:bins.chrome,NODE_OPTIONS:`--import=${JSON.stringify(loader)}`,CALLS:log,OBSERVED:JSON.stringify(observed),TEST_PORT:String(f.a.bridge.port)};
  return {...f,bins,env,calls:()=>fs.existsSync(log)?fs.readFileSync(log,'utf8').trim().split('\n').map(JSON.parse):[]};
}
