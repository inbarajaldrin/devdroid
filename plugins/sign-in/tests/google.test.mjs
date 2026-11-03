import './environment.mjs';
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import http from 'node:http';
import { execFile,spawn } from 'node:child_process';
import {once} from 'node:events';
import {manualClock} from './clock.mjs';
import { promisify } from 'node:util';
import { config, account, temp } from './support.mjs';
import { read, atomicJSON, run, machineId } from '../lib/core.mjs';
import { validate, permission, grant } from '../lib/accounts.mjs';
import { main, parse } from '../scripts/sign-in.mjs';
import { instructionProposal } from '../lib/sign-in.mjs';
import { setupInterview, identityScopes } from '../lib/google-contract.mjs';
import { optIn, configureProject, googleHome, isolatedEnv, installGws, doctorGoogle, gwsPin, judgeGoogle, oauthURL, login, connect, checkGoogle, consoleStep, readGoogle, readArgs } from '../lib/google.mjs';
const exec=promisify(execFile), scope='https://www.googleapis.com/auth/calendar.events.readonly', extra='https://www.googleapis.com/auth/tasks.readonly';
const metadata=()=>({person:'sample-person',expectedEmail:'sample@example.invalid',optIn:{userWords:'I want direct API control with chosen scopes',at:'2026-11-01T00:00:00Z'},project:'sample-owned-project',productionConfirmedAt:'2026-11-01T00:01:00Z',services:{}});
function fixture(t,{connected=false}={}){
  const f=config(t);f.a.google=metadata();f.a.sites.google={signInUrl:'https://accounts.google.com/',identity:{kind:'google',url:'https://myaccount.google.com/',expected:{name:f.a.google.expectedEmail}}};
  if(connected)f.a.google.services.calendar={api:'calendar-json.googleapis.com',scopes:[scope],connectedAt:'2026-11-01T00:02:00Z'};
  atomicJSON(f.file,{version:1,accounts:{work:f.a}});return f;
}
const report=(scopes=[...identityScopes,scope])=>({auth_method:'oauth2',token_valid:true,project_id:'sample-owned-project',user:'sample@example.invalid',scopes,refresh_token:'never-return-this',client_secret:'never-return-this'});
const yes=async()=> 'yes';
async function server(t,handler){const s=http.createServer(handler);await new Promise(r=>s.listen(0,'127.0.0.1',r));t.after(()=>new Promise(r=>{s.closeAllConnections();s.close(r);}));return `http://127.0.0.1:${s.address().port}`;}
test('setup interview offers sites and API up front, one question at a time, without installing or granting',async()=>{
  assert.equal((await main(['setup'])).question.key,'sites');
  const second=await main(['setup','--sites','youtube,handshake']);assert.equal(second.question.key,'google');assert.ok(second.question.recommendation);assert.equal(second.google,undefined);
  assert.equal(setupInterview({sites:'none',google:'no'}).google,false);assert.equal(setupInterview({sites:'youtube',google:'yes'}).permissionGranted,false);
  assert.throws(()=>setupInterview({sites:'bad/value'}),{code:'SETUP_CHOICE'});
  assert.equal((await main(['google','explain'])).optIn,true);
  assert.equal(parse(['google','connect','work','--service','calendar','--scope',scope,'--api','calendar-json.googleapis.com']).route,'connect');
  assert.throws(()=>parse(['google','connect','work','--full','yes']),{code:'USAGE'});
  assert.throws(()=>parse(['google','explain','--project','other']),{code:'USAGE'});
});
test('only the human opt-in records their words; the account format rejects secrets, invalid scope syntax and project conflicts',async t=>{
  const {file,a,dir}=config(t),answers=['yes','sample-person','sample@example.invalid'];
  await optIn(file,'work',{ask:async()=>answers.shift(),guard:async()=>{}});assert.equal(read(file).accounts.work.google.optIn.userWords,'yes');
  const data=read(file);data.accounts.work.google.token='secret';assert.throws(()=>validate(data),{code:'GOOGLE_FORMAT'});delete data.accounts.work.google.token;
  const g=metadata();g.services.calendar={api:'calendar-json.googleapis.com',scopes:['--full']};data.accounts.work.google=g;assert.throws(()=>validate(data),{code:'GOOGLE_FORMAT'});
  data.accounts.work.google=metadata();const b=account(dir,'other');b.google={...metadata(),expectedEmail:'second@example.invalid',project:'a-different-project'};data.accounts.other=b;assert.throws(()=>validate(data),{code:'GOOGLE_CONFLICT'});
  const no=config(t);await assert.rejects(optIn(no.file,'work',{ask:async()=>'',guard:async()=>{}}),{code:'RULING_REQUIRED'});assert.equal(read(no.file).accounts.work.google,undefined);
});
test('Google and browser permissions stay separate; omitted resource never widens API grants; proposed word is not written',async t=>{
  const {file,a}=fixture(t,{connected:true});
  assert.equal(permission(a,'calendar','edit','primary','google').needsUserOK,true);
  const answers=['I allow edits to my primary calendar','yes'];await grant(file,'work',{site:'calendar',via:'google',action:'edit',resource:'primary',ask:async()=>answers.shift(),prove:async()=>report()});
  const saved=read(file).accounts.work;assert.equal(permission(saved,'calendar','edit','primary','google').needsUserOK,false);assert.equal(permission(saved,'calendar','edit',undefined,'google').needsUserOK,true);assert.equal(permission(saved,'calendar','read',undefined,'google').needsUserOK,false);
  assert.throws(()=>permission(saved,'calendar','edit','primary'),{code:'SITE_UNKNOWN'});
  const cli=await main(['--accounts',file,'permission','work','--via','google','--site','calendar','--action','edit']);assert.equal(cli.needsUserOK,true);
  const proposal=instructionProposal('work','calendar',{identity:{email:a.google.expectedEmail}});assert.equal(proposal.written,false);assert.equal(proposal.needsUserOK,true);assert.ok(proposal.proposals.every(p=>p.after.endsWith('calendar')));
});
test('Desktop client is explicit, private, person owned and never imported from live gws',async t=>{
  const f=fixture(t);delete f.a.google.project;delete f.a.google.productionConfirmedAt;atomicJSON(f.file,{version:1,accounts:{work:f.a}});
  await configureProject(f.file,'work',f.a,{project:'sample-owned-project',ask:yes},{root:f.dir,guard:async()=>{}});
  const a=read(f.file).accounts.work;
  const client=path.join(f.dir,'download.json'),payload={installed:{client_id:'synthetic.apps.googleusercontent.com',client_secret:'synthetic-secret',project_id:'sample-owned-project',auth_uri:'https://accounts.google.com/o/oauth2/auth',token_uri:'https://oauth2.googleapis.com/token',redirect_uris:['http://localhost']}};
  fs.writeFileSync(client,JSON.stringify(payload),{mode:0o600});
  await assert.rejects(configureProject(f.file,'work',a,{project:a.google.project,clientFile:'/do-not-read/.config/gws/client_secret.json',ask:yes},{root:f.dir,guard:async()=>{}}),{code:'GOOGLE_LIVE_CONFIG'});
  fs.chmodSync(client,0o644);await assert.rejects(configureProject(f.file,'work',a,{project:a.google.project,clientFile:client,ask:yes},{root:f.dir,guard:async()=>{}}),{code:'GOOGLE_PRIVATE_PATH'});fs.chmodSync(client,0o600);
  payload.web=payload.installed;fs.writeFileSync(client,JSON.stringify(payload));await assert.rejects(configureProject(f.file,'work',a,{project:a.google.project,clientFile:client,ask:yes},{root:f.dir,guard:async()=>{}}),{code:'GOOGLE_DESKTOP_CLIENT'});delete payload.web;fs.writeFileSync(client,JSON.stringify(payload));
  const result=await configureProject(f.file,'work',a,{project:a.google.project,clientFile:client,ask:yes},{root:f.dir,guard:async()=>{}});assert.equal(JSON.stringify(result).includes('synthetic-secret'),false);
  const dest=path.join(googleHome(f.file,'work',a,f.dir),'.config/gws/client_secret.json');assert.equal(fs.statSync(dest).mode&0o777,0o600);assert.equal(read(dest).installed.project_id,a.google.project);
});
test('isolated homes and environment never seed user config, tokens, keys, proxies or ADC',t=>{
  const f=fixture(t),home=googleHome(f.file,'work',f.a,f.dir),other=googleHome(f.file,'other',f.a,f.dir);assert.notEqual(home,other);
  const env=isolatedEnv(home);for(const forbidden of ['GOOGLE_WORKSPACE_CLI_TOKEN','GOOGLE_WORKSPACE_CLI_CLIENT_SECRET','GOOGLE_WORKSPACE_CLI_CREDENTIALS_FILE','GOOGLE_APPLICATION_CREDENTIALS','HTTPS_PROXY','NODE_OPTIONS'])assert.equal(env[forbidden],undefined);
  assert.equal(env.HOME,home);assert.equal(env.GOOGLE_WORKSPACE_CLI_KEYRING_BACKEND,'file');
  fs.symlinkSync('/do-not-read',path.join(home,'.config/gws/credentials.enc'));assert.throws(()=>googleHome(f.file,'work',f.a,f.dir),{code:'GOOGLE_PRIVATE_PATH'});
});
test('lazy pinned gws installer verifies archive before execution, reuses matching bytes and refuses tampering',async t=>{
  const root=temp(t),calls=[],target='darwin-arm64';
  await assert.rejects(installGws({root,target,fetcher:async()=>({ok:true,arrayBuffer:async()=>Buffer.from('modified archive')}),execute:async()=>{throw Error('must not execute');}}),{code:'CHECKSUM_MISMATCH'});
  const dependencies={root,target,downloadArchive:async(url,file,hash)=>{calls.push({url,hash});fs.writeFileSync(file,'fake release');},execute:async(bin,args)=>{
    if(args[0]==='-tzf')return {stdout:'release/gws\nrelease/LICENSE\n'};
    if(args[0]==='-xzf'){const dir=args.at(-1);fs.chmodSync(dir,0o755);fs.mkdirSync(path.join(dir,'release'));fs.writeFileSync(path.join(dir,'release/gws'),'fake native binary');fs.writeFileSync(path.join(dir,'release/LICENSE'),'license');return {stdout:''};}
    return {stdout:'gws 0.22.5'};
  }};
  const bin=await installGws(dependencies);assert.equal(calls.length,1);assert.equal(calls[0].hash,gwsPin.targets[target].sha256);assert.ok(calls[0].url.includes('/v0.22.5/'));
  assert.equal(doctorGoogle(root).executed,false);assert.equal(await installGws(dependencies),bin);assert.equal(calls.length,1);fs.appendFileSync(bin,'changed');await assert.rejects(installGws(dependencies),{code:'INSTALL_CHANGED'});assert.throws(()=>doctorGoogle(root),{code:'INSTALL_CHANGED'});
});
test('identity and granted scopes are projected strictly, with explicit admin and quota gates',t=>{
  const {a}=fixture(t,{connected:true});assert.deepEqual(judgeGoogle(a,'calendar',report()).identity,{email:a.google.expectedEmail});assert.equal(JSON.stringify(judgeGoogle(a,'calendar',report())).includes('never-return-this'),false);
  assert.throws(()=>judgeGoogle(a,'calendar',{...report(),user:'different@example.invalid'}),{code:'GOOGLE_IDENTITY_MISMATCH'});
  assert.throws(()=>judgeGoogle(a,'calendar',{...report(),project_id:'someone-elses-project'}),{code:'GOOGLE_PROJECT_MISMATCH'});
  assert.throws(()=>judgeGoogle(a,'calendar',report(identityScopes)),{code:'GOOGLE_SCOPE_MISSING'});
  assert.throws(()=>judgeGoogle(a,'calendar',report([...identityScopes,scope,extra])),{code:'GOOGLE_SCOPE_EXCESS'});
  assert.throws(()=>judgeGoogle(a,'calendar',{token_error:'admin_policy_enforced never-print-secret'}),{code:'GOOGLE_ADMIN_BLOCKED'});
  assert.throws(()=>judgeGoogle(a,'calendar',{token_error:'serviceusage.services.use'}),{code:'GOOGLE_QUOTA_PERMISSION'});
  assert.throws(()=>judgeGoogle(a,'calendar',{...report(),token_valid:false}),{code:'GOOGLE_SIGNED_OUT'});
});
test('fake gws talks to fake Google; identity check leaks neither raw output nor error secrets',async t=>{
  const f=fixture(t,{connected:true}),calls=[];let state=report();
  const base=await server(t,(req,res)=>{res.setHeader('Content-Type','application/json');res.end(JSON.stringify(state));});
  const bin=path.join(f.dir,'gws.mjs');fs.writeFileSync(bin,`#!/usr/bin/env node\nimport fs from 'node:fs';import path from 'node:path';const cache=path.join(process.env.GOOGLE_WORKSPACE_CLI_CONFIG_DIR,'cache');fs.mkdirSync(cache,{recursive:true});fs.writeFileSync(path.join(cache,'synthetic'),'private fake data');const r=await fetch(process.env.FAKE_GOOGLE);console.log(await r.text());`,{mode:0o700});
  const deps={root:f.dir,guard:async()=>{},install:async()=>bin,execute:async(bin,args,opts)=>{calls.push(args);return exec(bin,args,{...opts,env:{...opts.env,FAKE_GOOGLE:base}});}};
  const result=await checkGoogle(f.file,'work',f.a,'calendar',deps);assert.equal(result.identity.email,f.a.google.expectedEmail);assert.equal(JSON.stringify(result).includes('never-return-this'),false);assert.deepEqual(calls,[['auth','status']]);
  const cache=path.join(googleHome(f.file,'work',f.a,f.dir),'.config/gws/cache');assert.equal(fs.statSync(cache).mode&0o777,0o700);assert.equal(fs.statSync(path.join(cache,'synthetic')).mode&0o777,0o600);
  state={token_error:'admin_policy_enforced https://example.invalid/?code=secret'};await assert.rejects(checkGoogle(f.file,'work',f.a,'calendar',deps),e=>e.code==='GOOGLE_ADMIN_BLOCKED'&&!e.message.includes('secret'));assert.equal(calls.length,2);
  deps.execute=async()=>{throw Error('refresh_token=never-print-this');};await assert.rejects(checkGoogle(f.file,'work',f.a,'calendar',deps),e=>!e.message.includes('never-print-this'));
});
test('OAuth listener survives URL handoff and first-bind race until fake callback; no raw URLs leave login',async t=>{
  const dir=temp(t),bin=path.join(dir,'gws.mjs'),home=path.join(dir,'isolated');fs.mkdirSync(home,{mode:0o700});
  fs.writeFileSync(bin,`#!/usr/bin/env node
import http from 'node:http';
const scopes=process.argv.at(-1).split(',');const s=http.createServer((req,res)=>{res.setHeader('Connection','close');res.end('fake consent complete',()=>s.close(()=>process.exit(0)));});
const probe=http.createServer();probe.listen(0,'127.0.0.1',()=>{const port=probe.address().port;probe.close(()=>{
const u=new URL('https://accounts.google.com/o/oauth2/v2/auth');u.searchParams.set('client_id','synthetic.apps.googleusercontent.com');u.searchParams.set('redirect_uri','http://localhost:'+port);u.searchParams.set('scope',scopes.join(' '));u.searchParams.set('state','fake-state-secret');
process.once('message',()=>s.listen(port,'127.0.0.1',()=>process.send({bound:true})));console.error(u.href);
});});`,{mode:0o700});
  let child,opened=0,retries=0;const clock=manualClock();
  clock.sleep=async ms=>{assert.equal(ms,50);retries++;const bound=once(child,'message');child.send({bind:true});assert.equal((await bound)[0].bound,true);};
  const spawnProcess=(bin,args,opts)=>{child=spawn(bin,args,{...opts,stdio:['ignore','pipe','pipe','ipc']});t.after(()=>{if(child.exitCode===null)child.kill('SIGTERM');});return child;};
  const result=await login(bin,isolatedEnv(home),[...identityScopes,scope],'synthetic.apps.googleusercontent.com',async url=>{opened++;const redirect=new URL(new URL(url).searchParams.get('redirect_uri'));redirect.hostname='127.0.0.1';const response=await fetch(redirect,{headers:{Connection:'close'}});assert.equal(response.status,200);assert.equal(await response.text(),'fake consent complete');},{timeout:5000,clock,spawnProcess});
  assert.equal(retries,1);assert.equal(opened,1);assert.deepEqual(result,{consented:true});assert.equal(JSON.stringify(result).includes('fake-state'),false);assert.equal(clock.now(),0);assert.equal(clock.pending,0);
  assert.throws(()=>oauthURL('https://evil.invalid/','synthetic',identityScopes),/./);
});

function connectionDeps(f,overrides={}){
  const calls=[],home=googleHome(f.file,'work',f.a,f.dir),configDir=path.join(home,'.config/gws');
  fs.writeFileSync(path.join(configDir,'client_secret.json'),JSON.stringify({installed:{client_id:'synthetic.apps.googleusercontent.com'}}),{mode:0o600});
  const deps={root:f.dir,guard:async()=>{},ask:yes,install:async()=>'/fake/gws',check:async()=>({contextId:'context-synthetic'}),execute:async()=>({stdout:JSON.stringify(report())}),login:async(bin,env,scopes,id,open)=>{calls.push({loginScopes:scopes});await open('https://accounts.google.com/o/oauth2/auth?state=private');calls.push({callback:true});return {consented:true};},oc:async(a,args)=>{calls.push(args);return {};},navigate:async(a,args,url)=>{calls.push([...args,'open','[private OAuth URL]']);},launch:async(file,name,op)=>{calls.push({window:op});},...overrides};return {deps,calls,home,configDir};
}
test('connect preserves the human tab until callback, resets only owned stale cache, proves identity/scopes then records',async t=>{
  const f=fixture(t),{deps,calls,configDir}=connectionDeps(f);fs.writeFileSync(path.join(configDir,'token_cache.json'),'old scopes and fake token',{mode:0o600});
  deps.execute=async()=>{assert.equal(fs.existsSync(path.join(configDir,'token_cache.json')),false);assert.equal(calls.some(c=>c.callback),true);return {stdout:JSON.stringify(report())};};
  const result=await connect(f.file,'work',f.a,{service:'calendar',api:'calendar-json.googleapis.com',scope},deps);assert.equal(result.status,'signed-in');assert.equal(read(f.file).accounts.work.google.services.calendar.scopes[0],scope);
  assert.equal(calls.filter(c=>Array.isArray(c)&&c.at(-1)==='close').length,1);assert.deepEqual(calls.filter(c=>c.window).map(c=>c.window),['show','park']);assert.equal(fs.readdirSync(configDir).filter(v=>v.startsWith('token_cache.json.before-')).length,1);
  assert.equal(JSON.stringify(result).includes('private OAuth'),false);
});
test('adding one service requests the union once, never defaults; failed identity leaves service unrecorded and human tab alive',async t=>{
  const f=fixture(t,{connected:true}),{deps,calls}=connectionDeps(f,{execute:async()=>({stdout:JSON.stringify(report([...identityScopes,scope,extra]))})});
  await connect(f.file,'work',f.a,{service:'tasks',api:'tasks.googleapis.com',scope:extra},deps);assert.deepEqual(calls[0].loginScopes,[...identityScopes,scope,extra]);assert.ok(read(f.file).accounts.work.google.services.tasks.connectedAt);
  const bad=fixture(t),b=connectionDeps(bad,{execute:async()=>({stdout:JSON.stringify({...report(),user:'wrong@example.invalid'})})});await assert.rejects(connect(bad.file,'work',bad.a,{service:'calendar',api:'calendar-json.googleapis.com',scope},b.deps),{code:'GOOGLE_IDENTITY_MISMATCH'});assert.equal(read(bad.file).accounts.work.google.services.calendar,undefined);assert.equal(b.calls.some(c=>Array.isArray(c)&&c.at(-1)==='close'),false);
});
test('console adapter uses the verified account, named elements, and stops at human publishing/secret/IAM gates',async t=>{
  const f=fixture(t),calls=[];let location;const deps={root:f.dir,launch:async()=>{},handoff:async()=>{},guard:async()=>{},check:async()=>({contextId:'proved-context'}),navigate:async(a,args,url,ctx)=>{location=url;calls.push({url,ctx});},oc:async(a,args,ctx)=>{calls.push({args,ctx});return args.includes('get')?location:{url:location,snapshot:'fake console state'};}};
  const state=await consoleStep(f.file,'work',f.a,{step:'api',api:'calendar-json.googleapis.com'},deps);assert.equal(state.content.trust,'untrusted');assert.equal(state.content.data.snapshot,'fake console state');assert.equal(calls[0].ctx,'proved-context');
  await consoleStep(f.file,'work',f.a,{step:'consent'},deps);
  await (await import('../lib/human-lease.mjs')).finishHuman(f.file,'work',f.a,f.dir,{park:async()=>{}});
  await consoleStep(f.file,'work',f.a,{step:'consent',field:'App name',value:'my-access'},deps);assert.ok(calls.some(c=>c.args?.includes('--name')&&c.args?.includes('App name')));
  for(const step of ['production','iam','desktop'])await assert.rejects(consoleStep(f.file,'work',f.a,{step,click:step==='desktop'?'Download JSON':'Confirm'},deps),{code:'GOOGLE_HUMAN_GATE'});
  await assert.rejects(consoleStep(f.file,'work',f.a,{step:'consent',field:'Password',value:'never'},deps),{code:'GOOGLE_CONSOLE_FIELD'});
  await (await import('../lib/human-lease.mjs')).finishHuman(f.file,'work',f.a,f.dir,{park:async()=>{}});
  const desktop=await consoleStep(f.file,'work',f.a,{step:'desktop'},deps);assert.equal(desktop.state,undefined);
  await (await import('../lib/human-lease.mjs')).finishHuman(f.file,'work',f.a,f.dir,{park:async()=>{}});
  await consoleStep(f.file,'work',f.a,{step:'consent'},deps);await (await import('../lib/human-lease.mjs')).finishHuman(f.file,'work',f.a,f.dir,{park:async()=>{}});location=location.replace('sample-owned-project','another-project');await assert.rejects(consoleStep(f.file,'work',f.a,{step:'consent',field:'App name',value:'my-access'},deps),{code:'GOOGLE_CONSOLE_LOCATION'});
});
test('API reads go through isolated gws after identity/scope proof and cannot enter auth or helper/write commands',async t=>{
  const f=fixture(t,{connected:true}),calls=[];const deps={root:f.dir,guard:async()=>{},install:async()=>'/fake/gws',execute:async(bin,args)=>{calls.push(args);return {stdout:JSON.stringify(args[0]==='auth'?report():{items:[]})};}};
  const payload=await readGoogle(f.file,'work',f.a,'calendar',['events','list','--params','{"calendarId":"primary"}'],deps);assert.equal(payload.trust,'untrusted');assert.deepEqual(payload.data,{items:[]});assert.deepEqual(calls[0],['auth','status']);assert.equal(calls[1][0],'calendar');
  for(const args of [['auth','export'],['+send'],['events','insert'],['events','list','--upload','/secret'],['events','get','--params','{}','--debug']])assert.throws(()=>readArgs('calendar',args),{code:'GOOGLE_READ_ONLY'});
});
test('Google human routes refuse noninteractive CLI before touching a client or auth store',async t=>{
  const {file}=fixture(t);for(const args of [['google','opt-in','work'],['google','project','work','--project','sample-owned-project','--client-file','/no-read'],['google','connect','work','--service','calendar','--api','calendar-json.googleapis.com','--scope',scope]])await assert.rejects(main(['--accounts',file,...args]),{code:'HUMAN_TERMINAL_REQUIRED'});
});
