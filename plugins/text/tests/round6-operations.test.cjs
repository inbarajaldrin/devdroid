const {test}=require('node:test'),assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),os=require('node:os');
const {execFile}=require('node:child_process'),{promisify}=require('node:util');
const {install}=require('../scripts/setup.cjs');
function directory(t){const d=fs.mkdtempSync(path.join(os.tmpdir(),'text-operations-'));t.after(()=>fs.rmSync(d,{recursive:true,force:true}));return d;}
test('L6: pairing code notification enters the selected secondary account',async t=>{
  const root=directory(t),account=path.join(root,'accounts/us'),runtime=path.join(account,'runtime');fs.cpSync(path.resolve(__dirname,'..'),runtime,{recursive:true});
  fs.writeFileSync(path.join(account,'config.json'),JSON.stringify({account:'us',runtimeRoot:root,host:'peer.invalid',homeHost:'peer.invalid',ownerChat:'owner@lid',owners:['owner@lid'],aiPrefix:'AI: ',pairCodeVia:'sender.invalid'}));
  fs.writeFileSync(path.join(runtime,'engine/baileys/transport.mjs'),`import fs from 'node:fs';import path from 'node:path';export async function transport(o){await o.onCode('ABCD-EFGH');fs.mkdirSync(path.join(o.directory,'auth'),{recursive:true,mode:0o700});fs.writeFileSync(path.join(o.directory,'auth/creds.json'),JSON.stringify({me:{id:'fixture'},account:{details:'signed'}}));await o.onReady();return {close(){}};}`);
  const bin=path.join(root,'bin');fs.mkdirSync(bin);const capture=path.join(root,'notification.json');
  fs.writeFileSync(path.join(bin,'ssh'),'#!/usr/bin/env python3\nimport os,sys,json\nfrom pathlib import Path\np=json.load(sys.stdin)\nPath(os.environ["PAIR_CAPTURE"]).write_text(json.dumps(p))\nprint(json.dumps({"ok":True}))\n',{mode:0o700});
  const script=path.join(runtime,'scripts/setup.cjs'),current=fs.readFileSync(script,'utf8').includes('PHONE_STDIN_REQUIRED');
  // This compatibility branch lets the same account regression exercise the earlier CLI.
  const command=promisify(execFile)(process.execPath,[script,'link',...(current?['--phone-stdin']:['--phone','0'.repeat(11)]),'--account','us'],{env:{...process.env,HOME:root,TEXT_HOME:root,TEXT_ACCOUNT:'us',PATH:bin+path.delimiter+process.env.PATH,PAIR_CAPTURE:capture}});
  command.child.stdin.end('0'.repeat(11)+'\n');await command;
  assert.equal(JSON.parse(fs.readFileSync(capture)).account,'us');
});
test('L-service: an unavailable service adapter refuses before any runtime mutation',async t=>{
  const root=directory(t),destination=path.join(root,'install'),ask=path.join(root,'ask.py');fs.writeFileSync(ask,'# fixture');
  await assert.rejects(install({directory:destination,platform:'missing-adapter',env:{ASK_SCRIPT:ask},config:{host:'fixture',homeHost:'fixture',ownerChat:'owner@lid',owners:['owner@lid'],aiPrefix:'AI: '},bundle:{version:1,rules:[]},execute:async()=>({})}),{code:'SERVICE_PLATFORM'});
  assert.equal(fs.existsSync(destination),false);
});
test('M6: the saved logout choice changes the activation plan and the health expectation',async()=>{
  const {definition,inspect}=require('../engine/lib/service.cjs');
  for(const linger of [true,false]){
    const plan=definition({directory:'/fixture',platform:'linux',linger});assert.equal(plan.activation.some(s=>s.includes('enable-linger')),linger);
    const report=await inspect({directory:'/fixture',platform:'linux',linger,execute:async cmd=>({stdout:cmd==='loginctl'?'Linger=no':'ActiveState=active\nSubState=running'})});assert.equal(report.ok,!linger);
  }
});
test('M7: launchd diagnostic logging rotates instead of growing without a bound',t=>{
  const log=path.join(directory(t),'service.log'),{append}=require('../engine/lib/log.cjs');
  for(let i=0;i<20;i++)append(log,'12345678\n',20);
  assert.ok(fs.statSync(log).size<=20);assert.ok(fs.existsSync(log+'.3'));assert.ok(!fs.existsSync(log+'.4'));
});
