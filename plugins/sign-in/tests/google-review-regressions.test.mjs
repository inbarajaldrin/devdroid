import './environment.mjs';
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { config } from './support.mjs';
import { atomicJSON, read, machineId } from '../lib/core.mjs';
import { identityScopes } from '../lib/google-contract.mjs';
import { googleContract, googleHome, connect, checkGoogle, readGoogle, consoleStep } from '../lib/google.mjs';
import { main } from '../scripts/sign-in.mjs';
const metadataScope='https://www.googleapis.com/auth/gmail.metadata',readScope='https://www.googleapis.com/auth/gmail.readonly';
function fixture(t){
  const f=config(t);f.a.google={person:'sample',expectedEmail:'sample@example.invalid',optIn:{userWords:'I choose API control',at:'2026-11-01T00:00:00Z'},project:'sample-owned-project',productionConfirmedAt:'2026-11-01T00:01:00Z',services:{gmail:{api:'gmail.googleapis.com',scopes:[metadataScope],connectedAt:'2026-11-01T00:02:00Z'}}};f.a.sites.google={signInUrl:'https://accounts.google.com/',identity:{kind:'google',url:'https://myaccount.google.com/',expected:{name:f.a.google.expectedEmail}}};atomicJSON(f.file,{version:1,accounts:{work:f.a}});return f;
}
test('G1 Console refuses another project or authuser before mutations and never reports a fabricated intended URL',async t=>{
  const f=fixture(t);let current,calls=[];const deps={root:f.dir,guard:async()=>{},check:async()=>({contextId:'expected'}),navigate:async(a,args,url)=>{current=url;},oc:async(a,args)=>{calls.push(args);return args.includes('get')?current:'synthetic console';}};
  await consoleStep(f.file,'work',f.a,{step:'api',api:'gmail.googleapis.com'},deps);
  const good=current;
  for(const change of [{project:'another-persons-project'},{authuser:'1'}]){
    const wrong=new URL(good);for(const [k,v]of Object.entries(change))wrong.searchParams.set(k,v);current=wrong.href;calls=[];
    await assert.rejects(consoleStep(f.file,'work',f.a,{step:'api',api:'gmail.googleapis.com',click:'Enable'},deps),{code:'GOOGLE_CONSOLE_LOCATION'});assert.ok(!calls.some(a=>a.includes('click')));
  }
});
test('G2 Google remote check/read require account metadata proof and the receiver refuses stale expectations before driving',async t=>{
  const f=fixture(t);f.a.machine.id='f'.repeat(64);f.a.machine.ssh='fake-holder';
  let args;const missingProof={delegate:async(a,command)=>{args=command;return {account:'work',via:'google',service:'gmail',identity:{email:'different@example.invalid'},project:'other-project',status:'signed-in',scopes:[...identityScopes,metadataScope]};}};
  await assert.rejects(checkGoogle(f.file,'work',f.a,'gmail',missingProof),{code:'GOOGLE_REMOTE_CONTRACT'});assert.ok(args.includes('--expect'));
  await assert.rejects(readGoogle(f.file,'work',f.a,'gmail',['users','getProfile'],missingProof),{code:'GOOGLE_READ_ONLY'});
  await assert.rejects(readGoogle(f.file,'work',f.a,'gmail',['users','messages','list'],missingProof),{code:'GOOGLE_REMOTE_CONTRACT'});
  const expected=googleContract('work',f.a),wrongIdentity={delegate:async()=>({googleContract:expected,result:{account:'work',via:'google',service:'gmail',identity:{email:'different@example.invalid'},project:f.a.google.project,status:'signed-in',scopes:[...identityScopes,metadataScope]}})};
  await assert.rejects(checkGoogle(f.file,'work',f.a,'gmail',wrongIdentity),{code:'GOOGLE_IDENTITY_MISMATCH'});
  const payload=await readGoogle(f.file,'work',f.a,'gmail',['users','messages','list'],{delegate:async()=>({googleContract:expected,result:{messages:[]}})});assert.equal(payload.trust,'untrusted');assert.deepEqual(payload.data,{messages:[]});
  const changed=structuredClone(f.a);changed.google.expectedEmail='second@example.invalid';assert.notEqual(googleContract('work',changed),expected);changed.google.expectedEmail=f.a.google.expectedEmail;changed.google.services.gmail.scopes=[readScope];assert.notEqual(googleContract('work',changed),expected);
  await assert.rejects(main(['--accounts',f.file,'--on-holder',machineId(),'google','check','work','--service','gmail','--expect',expected]),{code:'GOOGLE_REMOTE_CONTRACT'});
});
test('G4 Gmail metadata upgrade explicitly replaces the old scope and completes one consent with the pinned gws transformation',async t=>{
  const f=fixture(t),calls=[],home=googleHome(f.file,'work',f.a,f.dir),cfg=path.join(home,'.config/gws');fs.writeFileSync(path.join(cfg,'client_secret.json'),JSON.stringify({installed:{client_id:'synthetic.apps.googleusercontent.com'}}),{mode:0o600});fs.writeFileSync(path.join(cfg,'token_cache.json'),'old fake token',{mode:0o600});
  const deps={root:f.dir,guard:async()=>{},ask:async question=>{assert.ok(question.includes('replacing'));return 'yes';},check:async()=>({contextId:'fake-context'}),install:async()=>'/fake/gws',launch:async()=>{},navigate:async()=>{},oc:async()=>{},login:async(bin,env,scopes,id,open)=>{calls.push(scopes);assert.ok(!scopes.includes(metadataScope));assert.deepEqual(scopes,[...identityScopes,readScope]);await open('https://accounts.google.com/fake-private');return {consented:true};},execute:async()=>{assert.equal(fs.existsSync(path.join(cfg,'token_cache.json')),false);return {stdout:JSON.stringify({auth_method:'oauth2',token_valid:true,user:f.a.google.expectedEmail,project_id:f.a.google.project,scopes:[...identityScopes,readScope]})};}};
  await assert.rejects(connect(f.file,'work',f.a,{service:'gmail',api:'gmail.googleapis.com',scope:readScope},deps),{code:'GOOGLE_SCOPE_REPLACEMENT'});assert.equal(calls.length,0);
  const result=await connect(f.file,'work',f.a,{service:'gmail',api:'gmail.googleapis.com',scope:readScope,'replace-scope':metadataScope},deps);assert.equal(calls.length,1);assert.equal(result.status,'signed-in');assert.deepEqual(read(f.file).accounts.work.google.services.gmail.scopes,[readScope]);
});
