const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs'),os=require('node:os'),path=require('node:path');
const {execFile}=require('node:child_process');const {promisify}=require('node:util');
const {definition,inspect}=require('../engine/lib/service.cjs');
const {install,doctor,repair}=require('../scripts/setup.cjs');
const {write}=require('../engine/lib/store.cjs');
function fixture(t){
  const directory=fs.mkdtempSync(path.join(os.tmpdir(),'text-service-'));t.after(()=>fs.rmSync(directory,{recursive:true,force:true}));
  const ask=path.join(directory,'ask.py');fs.writeFileSync(ask,['--outcome','--exact','--mid-turn','--worker-receipt','--worker-stream','--worker-input'].map(flag=>'p.add_argument('+JSON.stringify(flag)+')').join('\n'));const calls=[],requests=[];
  const config={host:'test',homeHost:'test',owners:['owner@lid'],ownerChat:'owner@lid',aiPrefix:'AI: '};
  const execute=async(cmd,args,opts)=>{
    calls.push([cmd,args,opts]);
    if(cmd==='npm'&&args[0]==='ci')for(const name of ['baileys','pino','qrcode-terminal','sharp','link-preview-js']){const folder=path.join(opts.cwd,'node_modules',name);fs.mkdirSync(folder,{recursive:true});fs.writeFileSync(path.join(folder,'index.js'),'module.exports={};');}
    if(cmd===process.execPath||cmd==='/bin/sh')return promisify(execFile)(cmd,args,opts);
    return {stdout:cmd==='loginctl'?'Linger=yes\n':cmd==='systemctl'?'ActiveState=active\nSubState=running\nExecMainStatus=0\nRestart=on-failure\nRestartUSec=30s\n':'state = running'};
  };
  const call=async(_socket,req)=>{assert.ok(['ping','failure-notices'].includes(req.cmd));requests.push(req);return {ok:true,ready:true,linked:true};};
  return {directory,config,calls,requests,execute,call,env:{HOME:directory,ASK_SCRIPT:ask,PATH:'/usr/local/bin:/usr/bin:/bin'}};
}
test('Linux and macOS templates share restart/exit policy with safe paths and separate accounts',()=>{
  const directory='/tmp/text $user %host/a "quote"';
  for(const account of ['main','us']){
    const l=definition({directory,account,platform:'linux'}),m=definition({directory,account,platform:'darwin'});
    assert.equal(l.filename,account==='main'?'devdroid-text.service':'devdroid-text-us.service');
    assert.match(l.templates[l.filename],/Restart=on-failure\nRestartSec=30/);assert.match(l.templates[l.filename],/StartLimitIntervalSec=0/);
    assert.match(l.templates[l.filename],/UMask=0077/);assert.match(l.templates[l.filename],/\$\$user %%host/);
    assert.match(m.templates[m.filename],/<key>SuccessfulExit<\/key><false\/>/);assert.match(m.templates[m.filename],/<key>ThrottleInterval<\/key><integer>30/);
    assert.match(m.templates[m.filename],/a &quot;quote&quot;/);
  }
});
test('doctor inspects a user systemd service or a GUI launchd service through the same adapter',async()=>{
  const calls=[];
  for(const platform of ['linux','darwin']){
    const result=await inspect({directory:'/tmp/runtime',platform,account:'us',uid:123,execute:async(cmd,args)=>{calls.push([cmd,args]);return {stdout:cmd==='loginctl'?'Linger=yes\n':platform==='linux'?'ActiveState=active\nSubState=running':'state = running'};}});
    assert.equal(result.ok,true);
  }
  assert.deepEqual(calls[0].slice(0,1),['systemctl']);assert.ok(calls[0][1].includes('--user'));assert.ok(calls[0][1].includes('devdroid-text-us.service'));
  assert.deepEqual(calls.find(c=>c[0]==='launchctl'),['launchctl',['print','gui/123/com.devdroid.text.us']]);
  assert.equal((await inspect({directory:'/tmp/runtime',platform:'linux',execute:async()=>({stdout:'ActiveState=inactive\nSubState=dead'})})).ok,false);
  assert.equal((await inspect({directory:'/tmp/runtime',platform:'linux',execute:async()=>{throw new Error('No user bus');}})).ok,false);
});
test('M6: a running user service without lingering is not healthy after logout',async()=>{
  const result=await inspect({directory:'/fixture',platform:'linux',execute:async(cmd)=>({stdout:cmd==='loginctl'?'Linger=no\n':'ActiveState=active\nSubState=running'})});
  assert.equal(result.ok,false);assert.equal(result.lingering,false);assert.match(result.next,/enable-linger/);
});
test('M7: both managers provide activation and log instructions, and doctor checks the executable',async t=>{
  for(const platform of ['linux','darwin']){const d=definition({directory:'/fixture',platform});assert.ok(d.activation.length);assert.match(d.logs,/journalctl|service\.log/);}
  const result=await inspect({directory:'/fixture',platform:'darwin',node:'/missing/node',execute:async()=>({stdout:'state = running'})});assert.equal(result.ok,false);assert.equal(result.code,'NODE_MISSING');
});
for(const platform of ['linux','darwin'])test('isolated '+platform+' install/doctor/repair preserves customized source, rules and held messages',async t=>{
  const f=fixture(t),options={...f,platform,bundle:{version:1,rules:[]}};
  const result=await install(options);assert.equal(result.service.manager,platform==='linux'?'systemd-user':'launchd');assert.equal(result.serviceInstalled,false);
  const custom=path.join(f.directory,'runtime/engine/custom.cjs');fs.writeFileSync(custom,'module.exports = "user customization";\n');
  const snapshot=fs.readFileSync(custom,'utf8');const state={deliveries:{},sent:{},failures:{held:{id:'held',status:'held',message:'original',error:{message:'quota'}}}};
  write(path.join(f.directory,'state.json'),state);write(path.join(f.directory,'auth/creds.json'),{me:{id:'bot'},account:{details:'signed'}});
  const before=await doctor(options);assert.equal(before.checks.runtime,true,JSON.stringify(before.issues));assert.equal(before.checks.manager,true);assert.equal(before.failures[0].message,'original');assert.equal(before.ok,false);
  // Repair a missing dependency and a damaged staged service without replacing source.
  fs.rmSync(path.join(f.directory,'runtime/engine/baileys/node_modules'),{recursive:true});fs.rmSync(result.service.template);
  fs.rmSync(path.join(f.directory,'runtime/bin/text-relay'));
  const repaired=await repair(options);assert.equal(repaired.ok,true);assert.equal(fs.readFileSync(custom,'utf8'),snapshot);assert.deepEqual(JSON.parse(fs.readFileSync(path.join(f.directory,'state.json'))),state);
  assert.match(fs.readFileSync(result.service.template,'utf8'),platform==='linux'?/RestartSec=30/:/ThrottleInterval/);
  assert.match(fs.readFileSync(path.join(f.directory,'runtime/bin/text-relay'),'utf8'),/request --relay/);
  assert.deepEqual(f.requests.filter(r=>r.cmd!=='ping'),[{cmd:'failure-notices'}]);assert.equal(repaired.notices.ok,true);
  assert.ok(f.calls.filter(([cmd])=>['systemctl','launchctl'].includes(cmd)).every(([,args])=>args.includes('show')||args.includes('print')));
});
test('doctor identifies broken user code; repair leaves it intact for an agent to fix in place',async t=>{
  const f=fixture(t),options={...f,platform:'linux',bundle:{version:1,rules:[]}};await install(options);
  const broken=path.join(f.directory,'runtime/engine/custom.cjs');fs.writeFileSync(broken,'function broken( {');
  const report=await doctor(options);assert.equal(report.checks.runtime,false);assert.ok(report.issues.some(i=>i.code==='CUSTOMIZATION_BROKEN'&&i.file===broken));
  const count=f.calls.filter(([cmd,args])=>cmd==='npm'&&args[0]==='ci').length;
  assert.equal((await repair(options)).ok,false);assert.equal(fs.readFileSync(broken,'utf8'),'function broken( {');assert.equal(f.calls.filter(([cmd,args])=>cmd==='npm'&&args[0]==='ci').length,count);
  fs.writeFileSync(broken,'module.exports="fixed customization";');assert.equal((await repair(options)).ok,true);
});
test('healthy repair leaves patched dependencies and customized service definitions untouched',async t=>{
  const f=fixture(t),options={...f,platform:'linux',bundle:{version:1,rules:[]}},installed=await install(options);
  const patch=path.join(f.directory,'runtime/engine/baileys/node_modules/baileys/user-patch.txt');fs.writeFileSync(patch,'keep this patch');
  const customized=fs.readFileSync(installed.service.template,'utf8')+'\n# user customization\n';fs.writeFileSync(installed.service.template,customized);
  const before=f.calls.filter(([cmd,args])=>cmd==='npm'&&args[0]==='ci').length;
  const result=await repair(options);assert.equal(result.ok,true,JSON.stringify(result.after?.issues));
  assert.equal(f.calls.filter(([cmd,args])=>cmd==='npm'&&args[0]==='ci').length,before);assert.equal(fs.readFileSync(patch,'utf8'),'keep this patch');assert.equal(fs.readFileSync(installed.service.template,'utf8'),customized);
});
test('failed dependency repair stages npm separately and preserves every installed patch',async t=>{
  const f=fixture(t),options={...f,platform:'linux',bundle:{version:1,rules:[]}};await install(options);
  const modules=path.join(f.directory,'runtime/engine/baileys/node_modules'),patch=path.join(modules,'baileys/user-patch.txt');fs.writeFileSync(patch,'keep patch');fs.rmSync(path.join(modules,'pino/index.js'));
  const execute=async(cmd,args,opts)=>{if(cmd==='npm'&&args[0]==='ci'){assert.ok(opts.cwd.startsWith(path.join(f.directory,'repair-dependencies-')));fs.rmSync(path.join(opts.cwd,'node_modules'),{recursive:true,force:true});throw new Error('offline install refused');}return f.execute(cmd,args,opts);};
  await assert.rejects(repair({...options,execute}),/offline install refused/);assert.equal(fs.readFileSync(patch,'utf8'),'keep patch');assert.equal(fs.existsSync(modules),true);
  assert.equal(fs.readdirSync(f.directory).some(n=>n.startsWith('repair-dependencies-')),false);
});
test('doctor resolves literal imports without executing customized code or mistaking prose for code',async t=>{
  const f=fixture(t),options={...f,platform:'linux',bundle:{version:1,rules:[]}};await install(options);
  const marker=path.join(f.directory,'must-not-execute'),file=path.join(f.directory,'runtime/engine/custom.cjs');
  fs.writeFileSync(file,`require('node:fs').writeFileSync(${JSON.stringify(marker)},'bad');\n// require('not-a-module')\nconst prose="from 'human'";\nconst example=/require('also-not-a-module')/;\nrequire('./missing-user-module.cjs');\n`);
  const report=await doctor(options),issues=report.issues.filter(i=>i.file===file);assert.equal(report.checks.runtime,false);assert.equal(issues.length,1);assert.match(issues[0].error,/missing-user-module/);assert.equal(fs.existsSync(marker),false);
});
test('broken rules and customized service files name the cause and stay intact for repair in place',async t=>{
  const f=fixture(t),options={...f,platform:'linux',bundle:{version:1,rules:[]}},installed=await install(options),rules=path.join(f.directory,'rules.json');
  fs.writeFileSync(rules,'{broken');const report=await doctor(options);assert.equal(report.issues[0].code,'INVALID_JSON');assert.equal(report.issues[0].file,rules);
  assert.equal((await repair(options)).ok,false);assert.equal(fs.readFileSync(rules,'utf8'),'{broken');write(rules,{version:1,rules:[]});
  fs.writeFileSync(installed.service.template,'broken user service');const damaged=await repair(options);assert.equal(damaged.ok,false);assert.ok(damaged.issues.some(i=>i.code==='SERVICE_TEMPLATE_BROKEN'));assert.equal(fs.readFileSync(installed.service.template,'utf8'),'broken user service');
});
test('doctor detects a stale activated ASK_SCRIPT and repair requests user-present re-registration',async t=>{
  const f=fixture(t),options={...f,platform:'linux',bundle:{version:1,rules:[]}};await install(options);
  const registered=require('../engine/lib/service.cjs').registered({...options,account:'main'});fs.mkdirSync(path.dirname(registered),{recursive:true});fs.writeFileSync(registered,'[Service]\nEnvironment="ASK_SCRIPT=/missing/old-ask.py"\n');
  const report=await doctor(options);assert.ok(report.issues.some(i=>i.code==='SERVICE_ENV_STALE'&&i.file===registered));
  const repaired=await repair(options);assert.equal(repaired.ok,false);assert.ok(repaired.after.issues.some(i=>i.code==='SERVICE_ENV_STALE'));assert.match(fs.readFileSync(registered,'utf8'),/old-ask/);assert.match(repaired.after.issues.find(i=>i.code==='SERVICE_ENV_STALE').error,/user present/);
  assert.ok(f.calls.filter(([cmd])=>cmd==='systemctl').every(([,args])=>args.includes('show')));
});

test('a service saves no per-session temporary folder in PATH',()=>{
  const {lastingPath}=require('../engine/lib/service.cjs');
  assert.equal(lastingPath('/var/folders/x/T/shims:/opt/homebrew/bin:/tmp/a:/usr/bin'),'/opt/homebrew/bin:/usr/bin');
});
