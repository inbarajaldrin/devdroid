import './environment.mjs';
import test from 'node:test';import assert from 'node:assert/strict';import fs from 'node:fs';import path from 'node:path';
import { validate,create,permission,grant } from '../lib/accounts.mjs';
import { convert } from '../lib/migrate.mjs';import { machineId,local,read,locked,processStart,writeNew,ROOT } from '../lib/core.mjs';
import { config,account,temp } from './support.mjs';
test('examples contain neutral placeholders and reject arbitrary readers',()=>{
  const data=read(path.join(ROOT,'accounts.example.json'));validate(data);assert.deepEqual(Object.keys(data.accounts),['example']);assert.equal(data.accounts.example.sites.example.signInUrl,'https://www.example.org/');
  const a=data.accounts.example;a.sites.example.identity.read={token:{global:'document.cookie'}};assert.throws(()=>validate(data),{code:'ACCOUNTS_FORMAT'});
});
test('stable machine routing ignores a changed hostname',t=>{const {a}=config(t);a.machine.host='changed-by-network';assert.equal(local(a.machine),true);a.machine.id='f'.repeat(64);assert.equal(local(a.machine),false);});
test('all five registry bridges convert with aliases, original profiles and driveBlocked',t=>{
  const dir=temp(t),registry={bridges:{'career-account':{purpose:'jobs',profileDir:'career',remoteDebuggingPort:19340},'video-account':{purpose:'google-video',profileDir:'youtube',remoteDebuggingPort:19341},'photo-account':{profileDir:'public',remoteDebuggingPort:19342,igAccount:'100'},'blocked-account':{profileDir:'personal',remoteDebuggingPort:19343,igAccount:'101',driveBlocked:true},'short-video':{profileDir:'video',remoteDebuggingPort:19344,tiktokAccount:'synthetic'}}};
  const data=convert(registry,dir,{aliases:{'career-account':'known'},id:machineId(),cli:'/runtime/sign-in.mjs'});assert.equal(Object.keys(data.accounts).length,5);assert.equal(data.accounts['blocked-account'].bridge.driveBlocked,true);assert.deepEqual(data.accounts['career-account'].bridge.contextIds,['known']);assert.equal(Object.keys(data.accounts['career-account'].sites).length,6);
  for(const site of Object.values(data.accounts['career-account'].sites))assert.ok(site.identity.kind);
  assert.equal(data.accounts['photo-account'].bridge.profileDir,path.join(dir,'public'));for(const a of Object.values(data.accounts))assert.deepEqual(a.permissions,[]);
});
test('permission is advisory, omitted resource never matches, unknown identity is explicit',t=>{
  const {a}=config(t);a.permissions=[{site:'handshake',action:'edit',resource:'profile',userWords:'Profile edits are fine',grantedAt:new Date().toISOString()}];
  assert.equal(permission(a,'handshake','edit','profile').needsUserOK,false);assert.equal(permission(a,'handshake','edit').needsUserOK,true);assert.equal(permission(a,'handshake','upload','profile').needsUserOK,true);assert.equal(permission(a,'handshake','read').advisory,true);
  a.sites.handshake.identity.expected={};assert.equal(permission(a,'handshake','read').identityReady,false);
});
test('create refuses self-issued grants, reused profiles, alias collisions and name mismatch',async t=>{
  const {dir,file,a}=config(t),b=account(dir,'fresh');b.bridge.port=19350;
  b.bridge.contextIds=[];
  b.permissions=[{site:'handshake',action:'edit'}];await assert.rejects(create(file,'fresh',b),{code:'USER_GRANT_REQUIRED'});b.permissions=[];
  fs.mkdirSync(b.bridge.profileDir,{recursive:true});fs.writeFileSync(path.join(b.bridge.profileDir,'existing'),'private');await assert.rejects(create(file,'fresh',b),{code:'MIGRATION_REQUIRED'});fs.rmSync(b.bridge.profileDir,{recursive:true});
  await assert.rejects(create(file,'fresh',b,{execute:async()=>({stdout:JSON.stringify({profiles:[{alias:'fresh'}]})})}),{code:'ALIAS_COLLISION'});
  await create(file,'fresh',b,{execute:async()=>({stdout:JSON.stringify({profiles:[]})})});assert.ok(read(file).accounts.fresh);assert.equal(fs.statSync(file).mode&0o777,0o600);
  a.bridge.name='another';assert.throws(()=>validate({version:1,accounts:{work:a}}),{code:'ACCOUNTS_FORMAT'});
});
test('canonical profiles and cross-account aliases cannot collide',t=>{const {dir,a}=config(t),b=account(dir,'other');b.bridge.port++;b.bridge.profileDir=a.bridge.profileDir+'/../work';assert.throws(()=>validate({version:1,accounts:{work:a,other:b}}),{code:'ACCOUNTS_CONFLICT'});});
test('grant records the explicit human words after identity proof for the exact scope',async t=>{
  const {file}=config(t);let proven=false;const answers=['I allow only these profile edits','yes'];
  await grant(file,'work',{site:'handshake',action:'edit',resource:'profile',prove:async()=>{proven=true;},ask:async()=>answers.shift()});
  assert.equal(proven,true);const [p]=read(file).accounts.work.permissions;assert.equal(p.userWords,'I allow only these profile edits');assert.equal(p.resource,'profile');assert.equal(p.grantedBy,undefined);
  await assert.rejects(grant(file,'work',{site:'handshake',action:'edit',prove:async()=>{},ask:async()=>''}),{code:'GRANT_SCOPE'});
});
test('plugin locks recover only proven dead owners, preserve live/unknown locks, and release on failure',async t=>{
  const dir=temp(t),file=path.join(dir,'operation'),lock=file+'.lock';
  writeNew(lock,{machine:machineId(),pid:99999999,start:'dead',token:'old'});await locked(file,async()=>{});assert.equal(fs.existsSync(lock),false);
  writeNew(lock,{machine:machineId(),pid:process.pid,start:processStart(process.pid),token:'live'});await assert.rejects(locked(file,async()=>{}),{code:'BUSY'});fs.unlinkSync(lock);
  fs.writeFileSync(lock,'unrecognized');await assert.rejects(locked(file,async()=>{}),{code:'BUSY'});fs.unlinkSync(lock);
  await assert.rejects(locked(file,async()=>{throw Error('failed work');}),/failed work/);assert.equal(fs.existsSync(lock),false);
});
