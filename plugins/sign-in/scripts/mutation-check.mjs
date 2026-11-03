#!/usr/bin/env node
import fs from 'node:fs';
import path from 'node:path';
import {spawnSync} from 'node:child_process';
import {ROOT} from '../lib/core.mjs';
const baseline=process.argv[2]==='--baseline',source=baseline?path.resolve(ROOT,'..','.scratchpad/round4/baseline/sign-in'):ROOT;
const scratch=path.join(ROOT,'..','.scratchpad','round4',baseline?'baseline-mutations':'mutations');fs.mkdirSync(scratch,{recursive:true});
const guards=[
 ['endpoint','google.mjs',line=>line.includes("insist(url.origin==='https://accounts.google.com'"),'','endpoint, client, redirect'],
 ['scope','google.mjs',line=>line.includes('insist(JSON.stringify(actual)===JSON.stringify([...scopes].sort())'),'','endpoint, client, redirect'],
 ['admin-output','google.mjs',line=>line.includes('if(/admin_policy_enforced|org_internal/.test(buffer))'),'','admin output'],
 ['browser-identity','google.mjs',line=>line.includes('const proof=await d.check(file,name,a,gsite[0]),bin='),line=>line.replace('await d.check(file,name,a,gsite[0])',"{contextId:'mutant'}"),'signed-out browser identity'],
 ['environment','google.mjs',line=>line.includes('return {PATH:process.env.PATH'),line=>line.replace('return {PATH:','return {...process.env,PATH:'),'environment whitelist'],
 ['post-location','google.mjs',line=>line.includes('if(label||field){try{await checkLocation()')||line.includes('if(label||field)await checkLocation()'),'','post-action project changes'],
 ['concurrent-edit','google.mjs',line=>line.includes('insist(JSON.stringify(fresh.google)===baseline'),line=>line.replace("insist(JSON.stringify(fresh.google)===baseline,'GOOGLE_ACCOUNT_CHANGED','Google account changed during consent; check before recording');",''),'concurrent Google edits'],
];
let failed=false;
for(const [name,relative,match,replace,pattern]of guards){
 const dir=fs.mkdtempSync(path.join(scratch,name+'-')),copy=path.join(dir,'sign-in');fs.cpSync(source,copy,{recursive:true,filter:p=>!p.includes('node_modules')});
 const target=path.join(copy,'lib',relative),lines=fs.readFileSync(target,'utf8').split('\n');let changed=0;
 const next=lines.map(line=>{if(!match(line))return line;changed++;return typeof replace==='function'?replace(line):replace;}).join('\n');
 if(changed!==1)throw Error(`Expected one guard for ${name}, found ${changed}`);
 const home=path.join(dir,'home'),tmp=path.join(dir,'tmp');for(const p of [home,tmp])fs.mkdirSync(p,{mode:0o700});
 const tests=baseline?[path.join(copy,'tests/google.test.mjs'),path.join(copy,'tests/google-review-regressions.test.mjs')]:['--test-name-pattern',pattern,path.join(copy,'tests/round4.test.mjs')];
 const run=()=>spawnSync(process.execPath,['--test',...tests],{encoding:'utf8',env:{PATH:process.env.PATH,LANG:'en_US.UTF-8',HOME:home,TMPDIR:tmp,SIGN_IN_HOME:path.join(home,'browser')}});
 const control=run();if(control.status!==0){console.log(`${name}: INCONCLUSIVE (unmodified control failed)`);console.log(control.stdout,control.stderr);failed=true;continue;}
 fs.writeFileSync(target,next);const result=run();
 const killed=result.status!==0&&result.stdout.includes('AssertionError');
 console.log(`${name}: ${killed?'KILLED':result.status===0?'SURVIVED':'INCONCLUSIVE'} (unmodified control passed; guard matches ${changed})`);
 if(baseline){if(result.status!==0){console.log(result.stdout,result.stderr);failed=true;}}
 else if(!killed){console.log(result.stdout,result.stderr);failed=true;}
}
process.exitCode=failed?1:0;
