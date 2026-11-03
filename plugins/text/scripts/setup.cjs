#!/usr/bin/env node
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { promisify } = require('node:util');
const { execFile, spawn } = require('node:child_process');
const { read, write } = require('../engine/lib/store.cjs');
const { validate, hostOK } = require('../engine/lib/rules.cjs');
const { insist } = require('../engine/lib/errors.cjs');
const { request, remote } = require('../engine/lib/client.cjs');
const { convert, parseEnv } = require('../engine/lib/convert.cjs');
const { accountDirectory, runtimeDirectory } = require('../engine/lib/account.cjs');
const { ensureSocketFree } = require('../engine/lib/socket.cjs');
const { locateAsk } = require('../engine/lib/ask.cjs');
const services = require('../engine/lib/service.cjs');
const {createRequire}=require('node:module');
const {literals}=require('../engine/lib/dependencies.cjs');
const run = promisify(execFile);
const plugin = path.resolve(__dirname, '..');
function sourceHashes(folder){
  const result={},hash=require('node:crypto').createHash;
  function visit(dir){for(const entry of fs.readdirSync(dir,{withFileTypes:true})){if(['node_modules','bin','__pycache__'].includes(entry.name))continue;const file=path.join(dir,entry.name);if(entry.isDirectory())visit(file);else if(entry.isFile())result[path.relative(folder,file)]=hash('sha256').update(fs.readFileSync(file)).digest('hex');}}
  visit(folder);return result;
}
function entrances(directory, missingOnly = false) {
  const bin = path.join(directory,'runtime/bin'); fs.mkdirSync(bin,{recursive:true,mode:0o700});
  const quote = s => "'" + s.replaceAll("'", "'\\''") + "'";
  for (const [name,script,args] of [['text-relay','text.cjs','request --relay'],['text-setup','setup.cjs','"$@"']]) {
    const file = path.join(bin,name);
    if (missingOnly && fs.existsSync(file)) continue;
    fs.writeFileSync(file,`#!/bin/sh\nexec ${quote(process.execPath)} ${quote(path.join(directory,'runtime/scripts',script))} ${args}\n`,{mode:0o700});
  }
}
function configOK(c) {
  // Messages are labelled with the platform's name unless the owner chose another label.
  if (c && c.aiPrefix === undefined) c.aiPrefix = 'devdroid — ';
  insist(c && hostOK(c.host) && hostOK(c.homeHost) && /@(s\.whatsapp\.net|lid)$/.test(c.ownerChat || '') && (!c.noticeChat || /@g\.us$/.test(c.noticeChat)) && Array.isArray(c.owners) && c.owners.length && c.aiPrefix,
    'INVALID_CONFIG', 'Config needs host, homeHost, ownerChat JID and owners JIDs; a noticeChat must be a group');
  insist(c.owners.every(j => typeof j === 'string' && /@(s\.whatsapp\.net|lid)$/.test(j)) && typeof c.aiPrefix === 'string' && !!c.aiPrefix.trim(), 'INVALID_CONFIG', 'Owner identities need JIDs and AI prefix must be visible text');
  insist(!c.machines || Array.isArray(c.machines) && c.machines.length > 0 && c.machines.every(hostOK) && c.machines.includes(c.host), 'INVALID_CONFIG', 'machines must include this host');
  insist(!c.linkedVia || hostOK(c.linkedVia) && c.linkedVia !== c.host, 'INVALID_CONFIG', 'linkedVia must be a different SSH alias');
  insist(!c.userChat || /@(s\.whatsapp\.net|lid|g\.us)$/.test(c.userChat), 'INVALID_CONFIG', 'userChat must be an exact user-selected chat JID');
  insist((c.proxyReceivers || []).every(hostOK) && !(c.proxyReceivers || []).includes(c.host), 'INVALID_CONFIG', 'Invalid proxyReceivers');
  if(c.account!==undefined)accountDirectory('/unused',c.account);
  insist(c.linger===undefined || typeof c.linger==='boolean','INVALID_CONFIG','linger must save the user’s yes/no choice');
  insist(c.sendTimeoutMs===undefined || Number.isInteger(c.sendTimeoutMs)&&c.sendTimeoutMs>0&&c.sendTimeoutMs<=60000,'INVALID_CONFIG','sendTimeoutMs must be 1–60000 milliseconds');
  return c;
}
async function install({ directory, config, bundle, execute = run, env = process.env, upgrade = false, migration, platform = process.platform }) {
  services.definition({directory,platform});
  if (migration) {
    config = { ...migration.config, ...config, owners: [...new Set([...(migration.config?.owners || []), ...(config.owners || [])])], legacyTargets: { ...migration.legacyTargets, ...config.legacyTargets } };
    bundle ||= migration.bundle;
  }
  configOK(config); bundle=require('../engine/lib/rules.cjs').validateWiring(config,validate(bundle));
  const existing = fs.existsSync(path.join(directory, 'runtime')),saved=fs.existsSync(path.join(directory,'config.json')) || fs.existsSync(path.join(directory,'rules.json')) || fs.existsSync(path.join(directory,'state.json'));
  insist(!(existing||saved) || upgrade, 'ALREADY_INSTALLED', 'Saved installation exists; use --upgrade to preserve its rules and state');
  if(existing && upgrade){
    const baseline=read(path.join(directory,'source-manifest.json'),null);
    insist(baseline,'RUNTIME_BASELINE_MISSING','No source baseline exists; inspect and merge this runtime in place before upgrading');
    const current=sourceHashes(path.join(directory,'runtime')),changed=[...new Set([...Object.keys(baseline),...Object.keys(current)])].filter(n=>baseline[n]!==current[n]);
    insist(!changed.length,'CUSTOMIZED_RUNTIME','Preserve and merge user changes in place before upgrading: '+changed.join(', '));
  }
  const askScript = locateAsk(env);
  fs.mkdirSync(directory, { recursive: true, mode: 0o700 });
  fs.chmodSync(directory,0o700);
  const stage = fs.mkdtempSync(path.join(directory, 'install-'));
  try {
    for (const name of ['engine', 'skills', 'scripts', '.claude-plugin','LICENSE']) if(fs.existsSync(path.join(plugin,name)))fs.cpSync(path.join(plugin, name), path.join(stage, name), { recursive: true, filter: p => !p.split(path.sep).includes('node_modules') && !p.split(path.sep).includes('__pycache__') });
    await execute('npm', ['ci', '--no-audit', '--no-fund'], { cwd: path.join(stage, 'engine/baileys'), maxBuffer: 1024 * 1024 });
    const backup = existing ? path.join(directory, 'runtime-backup-' + Date.now()) : null;
    if (backup) fs.renameSync(path.join(directory, 'runtime'), backup);
    fs.renameSync(stage, path.join(directory, 'runtime'));
    entrances(directory);
    const account = config.account || 'main'; accountDirectory('/unused', account);
    const runtimeRoot = account === 'main' ? directory : path.resolve(directory, '../..');
    write(path.join(directory, 'config.json'), { ...read(path.join(directory,'config.json'),{}),...config, runtimeRoot, askScript });
    write(path.join(directory,'source-manifest.json'),sourceHashes(path.join(directory,'runtime')));
    if (!fs.existsSync(path.join(directory,'rules.json'))) write(path.join(directory, 'rules.json'), bundle);
    if (!fs.existsSync(path.join(directory,'state.json')) && migration) write(path.join(directory, 'state.json'), { inbound: {}, sent: migration.sent || {}, deliveries: {}, drafts: {}, homes: {}, hints: {} });
    const service = services.stage({ directory, account, platform,linger:config.linger, env: {...env,ASK_SCRIPT:askScript} });
    return { ok: true, directory, backup, service, serviceInstalled: false, linked: false };
  } finally { fs.rmSync(stage, { recursive: true, force: true }); }
}
async function doctor({ directory, roundTripId, call = request, execute = run, platform = process.platform, env = process.env }) {
  const issues=[];let config;
  for(const [name,check] of [['config.json',configOK],['rules.json',validate]])try{const value=check(read(path.join(directory,name),null));if(name==='config.json')config=value;}catch(e){issues.push({code:e.code || 'INVALID_INPUT',file:path.join(directory,name),error:e.message});}
  if(config && !issues.length)try{const bundle=read(path.join(directory,'rules.json'),null),canonical=require('../engine/lib/rules.cjs').validateWiring(config,bundle);if(JSON.stringify(bundle)!==JSON.stringify(canonical))issues.push({code:'OWNER_RULE_ALIAS',file:path.join(directory,'rules.json'),error:'Use the canonical ownerChat address for the owner rule; its other address belongs in aliases.'});}catch(e){issues.push({code:e.code || 'INVALID_WIRING',file:path.join(directory,'rules.json'),error:e.message});}
  if(issues.length)return {ok:false,checks:{config:!!config,rules:!issues.some(i=>i.file.endsWith('rules.json')),runtime:false},issues,failures:[],next:'Repair the reported files in place, preserving user changes, then run doctor again.'};
  const creds = read(path.join(directory, 'auth/creds.json'), {});
  for(const file of require('../engine/lib/auth.cjs').inspect(path.join(directory,'auth')))issues.push({code:'AUTH_PERMISSIONS',file,error:'Credentials must be owner-only (directory 0700, files 0600). Tighten their modes before linking.'});
  let service;
  try { service = await call(path.join(directory, 'engine.sock'), { cmd: 'ping' }); } catch (e) { service = { ok: false, error: e.message }; }
  const state = read(path.join(directory, 'state.json'), { inbound: {}, deliveries: {}, sent: {} });
  const manager = await services.inspect({ directory, account: config.account, execute, platform,env,linger:config.linger,askScript:config.askScript });
  if(platform==='linux' && !manager.lingering && config.linger!==false)issues.push({code:'LINGER_DISABLED',error:manager.next || 'Could not verify lingering. The service may stop after logout.'});
  if(manager.code==='NODE_MISSING')issues.push({code:manager.code,error:manager.error});
  const activated=services.registered({directory,account:config.account,platform,env});
  if(manager.staleAskOverride || fs.existsSync(activated) && fs.readFileSync(activated,'utf8').includes('ASK_SCRIPT') && !fs.readFileSync(activated,'utf8').includes(config.askScript || 'NO_CONFIGURED_ASK'))issues.push({code:'SERVICE_ENV_STALE',file:activated,error:'Activated service overrides /ask with an old path. Update that override or re-register the staged service with the user present; no reload was performed.'});
  let dependency;
  try { dependency = locateAsk({ ...env, ASK_SCRIPT: env.ASK_SCRIPT || config.askScript }); } catch (e) { issues.push({ code: e.code, error: e.message }); }
  if(dependency){const flags=new Set([...fs.readFileSync(dependency,'utf8').matchAll(/^\s*\w+\.add_argument\(["'](--[^"']+)["']/gm)].map(m=>m[1]));const missing=['--outcome','--exact','--mid-turn','--worker-receipt','--worker-stream','--worker-input'].filter(f=>!flags.has(f));if(missing.length)issues.push({code:'ASK_CAPABILITY',error:'Update the separate /ask dependency; missing '+missing.join(', ')});}
  const runtime = path.join(directory,'runtime');
  if (!fs.existsSync(runtime)) issues.push({code:'RUNTIME_MISSING', file:runtime});
  else {
    async function check(folder) {
      for (const entry of fs.readdirSync(folder,{withFileTypes:true})) {
        if (['node_modules','.git','__pycache__'].includes(entry.name)) continue;
        const file = path.join(folder,entry.name);
        if (entry.isDirectory()) await check(file);
        else if (/\.(cjs|mjs|js)$/.test(file)) try {
          await execute(process.execPath,['--check',file],{timeout:10000,maxBuffer:262144});
          const source=fs.readFileSync(file,'utf8'),deps=createRequire(file);
          for(const spec of literals(source))try{deps.resolve(spec);}catch(e){issues.push({code:spec.startsWith('.') || !['baileys','pino','sharp','qrcode-terminal','link-preview-js'].includes(spec)?'CUSTOMIZATION_BROKEN':'DEPENDENCIES_MISSING',file,error:'Cannot resolve '+spec+': '+e.message});}
        }
        catch (e) { issues.push({code:'CUSTOMIZATION_BROKEN',file,error:e.stderr || e.message}); }
      }
    }
    await check(runtime);
    for (const name of ['text-relay','text-setup']) {
      const file=path.join(runtime,'bin',name);
      if (!fs.existsSync(file)) issues.push({code:'ENTRANCE_MISSING',file});
      else try { await execute('/bin/sh',['-n',file],{timeout:5000,maxBuffer:262144}); }
      catch(e) { issues.push({code:'CUSTOMIZATION_BROKEN',file,error:e.stderr || e.message}); }
      if(fs.existsSync(file)){const executable=fs.readFileSync(file,'utf8').match(/^exec '([^']+)'/m)?.[1];if(executable)try{fs.accessSync(executable,fs.constants.X_OK);}catch{issues.push({code:'NODE_MISSING',file,error:'Entrance executable is missing: '+executable});}}
    }
    for(const name of ['package.json','package-lock.json'])try { read(path.join(runtime,'engine/baileys',name),null) || insist(false,'DEPENDENCY_MANIFEST','Missing dependency manifest'); }
    catch (e) { issues.push({code:'DEPENDENCY_MANIFEST',file:path.join(runtime,'engine/baileys',name),error:e.message}); }
    if (!fs.existsSync(path.join(runtime,'engine/baileys/node_modules/baileys'))) issues.push({code:'DEPENDENCIES_MISSING',error:'Run setup repair to install the runtime dependencies'});
    else try{await execute('npm',['ls','--omit=dev','--json'],{cwd:path.join(runtime,'engine/baileys'),timeout:10000,maxBuffer:262144});}catch(e){issues.push({code:e.code===1?'DEPENDENCIES_MISSING':'DEPENDENCY_CHECK_FAILED',error:e.stderr || e.message});}
  }
  for(const [name,source] of Object.entries(services.definition({directory,account:config.account,platform,env}).templates)){
    const file=path.join(directory,'service',name);if(!fs.existsSync(file))continue;
    const contents=fs.readFileSync(file,'utf8');if(name.endsWith('.service')?!/^\[Service\]$/m.test(contents)||!/^ExecStart=.+$/m.test(contents):!contents.includes('<plist')||!contents.includes('<key>ProgramArguments</key>'))issues.push({code:'SERVICE_TEMPLATE_BROKEN',file,error:'Repair the customized service definition in place; it was preserved.'});
    const executable=name.endsWith('.service')?contents.match(/^ExecStart="([^"]+)"/m)?.[1]:contents.match(/<key>ProgramArguments<\/key><array><string>([^<]+)<\/string>/)?.[1];
    if(executable)try{fs.accessSync(executable.replaceAll('%%','%').replaceAll('$$','$').replaceAll('&amp;','&').replaceAll('&quot;','"'),fs.constants.X_OK);}catch{issues.push({code:'NODE_MISSING',file,error:'Service executable is missing: '+executable});}
  }
  const failures = Object.values(state.failures || {}).filter(f => ['held','resending','retrying','released-uncertain','attempting'].includes(f.status));
  const receipt = roundTripId ? Object.values(state.deliveries || {}).find(d => d.replyTo === roundTripId && d.receipt && !d.receipt.verifiedBy && d.responseReceipt?.ok && !d.responseReceipt.verifiedBy && state.sent?.[d.responseReceipt.id]?.status === 'sent') : null;
  const checks = { config: true, rules: true, link: config.linkedVia ? !!service.linked : !!(creds.me?.id && creds.account), service: !!service.ok && !!service.ready,
    manager: !!manager.ok, runtime: !issues.length, roundTrip: roundTripId ? !!receipt : 'not-verified' };
  return { ok: checks.link && checks.service && checks.manager && checks.runtime && checks.roundTrip === true, checks, manager, engine:service, dependency, issues,
    failures: failures.map(({id,status,message,error,notice,noticeError,acceptance,evidence,file,deliveryId}) => ({id,status,message,error,notice,noticeError,acceptance,evidence,file,deliveryId})),
    next: receipt ? 'Matched inbound /ask receipt found; confirm the agent’s actual reply in the live chat.' : 'With the user present, send a unique stamped probe, swipe-reply from the phone, then pass its inbound message id as --round-trip. Doctor never sends a message.' };
}
async function resumePoint({directory,now=Date.now}){
  await ensureSocketFree(path.join(directory,'engine.sock'));
  const file=path.join(directory,'state.json'),state=read(file,{inbound:{},sent:{},deliveries:{},drafts:{},homes:{},hints:{}});
  state.lastHandledTs=Math.max(state.lastHandledTs || 0,now()/1000);write(file,state);
  return {ok:true,resumePoint:state.lastHandledTs,serviceChanged:false};
}
async function repair(options) {
  const { directory, execute = run, env = process.env, platform = process.platform } = options;
  const before = await doctor(options);
  const config = configOK(read(path.join(directory,'config.json'),null));
  const askScript = locateAsk({ ...env, ASK_SCRIPT: env.ASK_SCRIPT || (fs.existsSync(config.askScript || '') ? config.askScript : undefined) });
  // Repair plumbing around the installed source, preserving every customization.
  const runtime = path.join(directory,'runtime');
  insist(fs.existsSync(path.join(runtime,'engine/baileys/package-lock.json')), 'RUNTIME_MISSING', 'Install a runtime before repairing it');
  const blockers = before.issues.filter(i => !['DEPENDENCIES_MISSING','ASK_MISSING','ENTRANCE_MISSING','SERVICE_ENV_STALE'].includes(i.code));
  if (blockers.length) return {ok:false,before,issues:blockers,next:'Repair the reported files in place, preserving the user’s changes, then run repair and doctor again. No held message was resent.'};
  let dependencyBackup;
  if(before.issues.some(i=>i.code==='DEPENDENCIES_MISSING')){
    const engine=path.join(runtime,'engine/baileys'),stage=fs.mkdtempSync(path.join(directory,'repair-dependencies-'));
    try{
      for(const name of ['package.json','package-lock.json'])fs.copyFileSync(path.join(engine,name),path.join(stage,name));
      await execute('npm',['ci','--no-audit','--no-fund'],{cwd:stage,maxBuffer:1024*1024});
      insist(fs.existsSync(path.join(stage,'node_modules')),'DEPENDENCY_INSTALL_FAILED','npm did not create dependencies; original runtime remains intact');
      if(fs.existsSync(path.join(engine,'node_modules'))){dependencyBackup=path.join(directory,'dependencies-backup-'+Date.now());fs.renameSync(path.join(engine,'node_modules'),dependencyBackup);}
      fs.renameSync(path.join(stage,'node_modules'),path.join(engine,'node_modules'));
    }finally{fs.rmSync(stage,{recursive:true,force:true});}
  }
  write(path.join(directory,'config.json'),{...config,askScript});
  entrances(directory,true);
  const service = services.stage({directory,account:config.account,platform,linger:config.linger,env:{...env,ASK_SCRIPT:askScript}});
  const after = await doctor({...options,execute,env,platform});
  let notices;
  if (after.checks.runtime && after.checks.service && after.failures.some(f=>f.notice !== 'sent')) {
    try { notices=await (options.call || request)(path.join(directory,'engine.sock'),{cmd:'failure-notices'}); }
    catch(e) { notices={ok:false,error:e.message}; }
  }
  return {ok:after.checks.runtime && notices?.ok !== false,before,after,service,dependencyBackup,notices,next:'Source and service files are preserved/staged. Repair reports held failures privately but never releases their work. Service activation and linking need the user-present live round. Inspect unknown acceptance before the owner’s WhatsApp resend.'};
}
// An interrupted link leaves credentials WhatsApp never accepted, which would end every later attempt.
function setAsideUnfinishedLink(directory) {
  const auth = path.join(directory, 'auth'), creds = read(path.join(auth, 'creds.json'), null);
  if (creds && !creds.account) fs.renameSync(auth, `${auth}.interrupted-${Date.now()}`);
}
function openQrWindow(directory) {
  if (process.platform !== 'darwin') return null;
  const source = path.join(__dirname, 'qr-window.swift'), binary = path.join(directory, 'qr-window'), file = path.join(directory, 'link-qr');
  if (!fs.existsSync(binary) || fs.statSync(source).mtimeMs > fs.statSync(binary).mtimeMs)
    require('node:child_process').execFileSync('swiftc', ['-O', '-parse-as-library', source, '-o', binary], { stdio: 'ignore' });
  const show = text => { fs.writeFileSync(file + '.tmp', text + '\n', { mode: 0o600 }); fs.renameSync(file + '.tmp', file); };
  fs.rmSync(file, { force: true });
  spawn(binary, [file, '--title', 'Link WhatsApp', '--hint', 'WhatsApp > Linked devices > Link a device', '--code-only'], { detached: true, stdio: 'ignore' }).unref();
  return { show };
}
function pairRemote(host, phone, execute = spawn, account = 'main') {
  insist(hostOK(host) && /^\d{8,15}$/.test(phone), 'PAIR_ARGUMENT', 'Pairing needs an SSH alias and country-code phone digits');
  accountDirectory('/unused', account);
  // The pairing stream is shown here. The remote setup uses its own runtime and auth.
  const runtime = account === 'main' ? '$HOME/.devdroid/text/runtime' : '$HOME/.devdroid/text/accounts/' + account + '/runtime';
  const child=execute('ssh', ['-o', 'BatchMode=yes', '-o', 'ConnectTimeout=15', host,
    '"' + runtime + '/bin/text-setup" link --phone-stdin' + (account === 'main' ? '' : ' --account ' + account)], { stdio: ['pipe','inherit','inherit'] });
  child.stdin.end(phone+'\n');return child;
}
async function installRemote({host,config,bundle,migration,upgrade=false,execute=spawn,env=process.env}){
  insist(hostOK(host),'INVALID_HOST','Choose one SSH host before installing');configOK(config);if(bundle)validate(bundle);
  const stage=fs.mkdtempSync(path.join(env.TMPDIR || os.tmpdir(),'text-remote-install-'));
  try{
    for(const name of ['engine','skills','scripts','.claude-plugin','LICENSE'])if(fs.existsSync(path.join(plugin,name)))fs.cpSync(path.join(plugin,name),path.join(stage,'plugin',name),{recursive:true,filter:p=>!p.split(path.sep).some(n=>['node_modules','__pycache__'].includes(n))});
    write(path.join(stage,'config.json'),config);if(bundle)write(path.join(stage,'rules.json'),bundle);if(migration)write(path.join(stage,'migration.json'),migration);
    const bootstrap=`import json,os,sys,tarfile,tempfile,subprocess\nfrom pathlib import Path\nwith tempfile.TemporaryDirectory(prefix='text-install-') as d:\n with tarfile.open(fileobj=sys.stdin.buffer,mode='r|') as archive:\n  for member in archive:\n   p=Path(member.name)\n   if p.is_absolute() or '..' in p.parts or not(member.isfile() or member.isdir()):raise ValueError('Unsafe archive member')\n   archive.extract(member,d)\n args=['node',str(Path(d)/'plugin/scripts/setup.cjs'),'install','--config',str(Path(d)/'config.json'),'--account',${JSON.stringify(config.account || 'main')},'--directory',os.environ.get('TEXT_HOME') or str(Path.home()/'.devdroid/text')]\n args+=['--migration',str(Path(d)/'migration.json')] if (Path(d)/'migration.json').exists() else ['--rules',str(Path(d)/'rules.json')]\n ${upgrade?"args+=['--upgrade']":"pass"}\n sys.exit(subprocess.run(args).returncode)\n`;
    const quote=s=>"'"+s.replaceAll("'","'\\''")+"'";
    const child=execute('ssh',['-o','BatchMode=yes','-o','ConnectTimeout=15',host,'bash -lc '+quote('python3 -c '+quote(bootstrap))],{stdio:['pipe','pipe','pipe']});
    let stdout='',stderr='';child.stdout.on('data',b=>stdout+=b);child.stderr.on('data',b=>stderr+=b);
    const done=new Promise((resolve,reject)=>{child.once('error',reject);child.once('close',code=>{if(code===0)try{resolve(JSON.parse(stdout));}catch{reject(new Error('REMOTE_INSTALL_RECEIPT: '+stdout));}else reject(new Error('REMOTE_INSTALL_FAILED: '+stderr));});});
    const archive=spawn('tar',['-cf','-','-C',stage,'.'],{stdio:['ignore','pipe','pipe']});archive.stdout.pipe(child.stdin);archive.on('error',e=>child.stdin.destroy(e));
    return await done;
  }finally{fs.rmSync(stage,{recursive:true,force:true});}
}
async function discover(execute = run,{env=process.env,selected=[]}={}) {
  const hosts = new Set();
  const config = path.join(env.HOME || os.homedir(), '.ssh/config');
  if (fs.existsSync(config)) for (const line of fs.readFileSync(config, 'utf8').split('\n')) {
    const m = line.match(/^\s*Host\s+(.+)$/i); if (m) for (const h of m[1].split(/\s+/)) if (hostOK(h)) hosts.add(h);
  }
  try { const result = await execute('tailscale', ['status', '--json']); for (const peer of Object.values(JSON.parse(result.stdout).Peer || {})) if (peer.HostName && hostOK(peer.HostName)) hosts.add(peer.HostName); } catch (e) { if (e.code !== 'ENOENT') return { hosts: [...hosts], tailscaleError: e.stderr || e.message }; }
  const checked = [];
  for(const host of selected){insist(hostOK(host),'INVALID_HOST','Choose a valid SSH alias');hosts.add(host);}
  for (const host of hosts) {
    if(!selected.includes(host)){checked.push({host,checked:false});continue;}
    try {
      const probe=`import os,json\nfrom pathlib import Path\nh=Path.home();c=[Path(os.environ['ASK_SCRIPT'])] if os.environ.get('ASK_SCRIPT') else []\nc += [h/'.claude/skills/ask/scripts/ask.py']\nif os.environ.get('CLAUDE_PLUGIN_ROOT'):c.append(Path(os.environ['CLAUDE_PLUGIN_ROOT'])/'skills/ask/scripts/ask.py')\nr=h/'.claude/plugins/installed_plugins.json'\nif r.is_file():\n for n,entries in json.loads(r.read_text()).get('plugins',{}).items():\n  if n.split('@')[0]=='session-tools':\n   for e in entries if isinstance(entries,list) else [entries]:\n    if e.get('installPath'):c.append(Path(e['installPath'])/'skills/ask/scripts/ask.py')\nprint(json.dumps({'textInstalled':(h/'.devdroid/text/runtime/bin/text-setup').is_file(),'askInstalled':any(p.is_absolute() and p.is_file() for p in c)}))`;
      const quote=s=>"'"+s.replaceAll("'","'\\''")+"'";
      const result=await execute('ssh', ['-o', 'BatchMode=yes', '-o', 'ConnectTimeout=3', host, 'bash -lc '+quote('python3 -c '+quote(probe))]); checked.push({ host, reachable: true,...JSON.parse(result.stdout) }); }
    catch (e) { checked.push({ host, reachable: false, error: e.stderr || e.message }); }
  }
  return { hosts: checked };
}
async function main() {
  if(process.argv.length===3 && process.argv[2]==='--help'){console.log('text setup: validate --config FILE --rules FILE\ninstall --config FILE (--rules FILE | --migration FILE) [--upgrade] [--host HOST]\ndoctor [--round-trip ID] | resume-point | repair | discover [--host HOST] | convert --source FILE --host HOST --output FILE | sync-rules --host HOST | link --phone-stdin [--host HOST]\nUse --directory DIR for local runtime operations; remote setup uses its default destination. --account NAME selects an account.\nSee skills/text/references/operations.md for config format, optional conversion inputs and the user-present live round.');return;}
  const [cmd, ...args] = process.argv.slice(2), flags = {};
  for (let i = 0; i < args.length; i++) {
    if (['--upgrade','--phone-stdin','--window'].includes(args[i])) {insist(!flags[args[i]],'SETUP_OPTION','Repeated option'); flags[args[i]] = true; continue; }
    insist(args[i]!=='--phone','PHONE_STDIN_REQUIRED','Use --phone-stdin so phone digits never appear in process listings');
    insist(['--directory', '--account', '--config', '--rules', '--source', '--output', '--host', '--round-trip', '--env', '--plist', '--sent-index', '--migration'].includes(args[i]) && args[i + 1] && !flags[args[i]], 'SETUP_OPTION', 'Unknown, repeated or missing setup option'); flags[args[i]] = args[++i];
  }
  const options = { validate:['--config','--rules','--directory'], install: ['--directory', '--host','--config', '--rules', '--upgrade', '--migration'], doctor: ['--directory', '--round-trip'], 'resume-point':['--directory'], repair: ['--directory', '--round-trip'], discover: ['--host'], convert: ['--source', '--output', '--host', '--env', '--plist', '--sent-index'], 'sync-rules': ['--directory', '--rules', '--host'], link: ['--directory', '--host', '--phone-stdin', '--window'] };
  insist(options[cmd], 'SETUP_COMMAND', 'Use validate, install, doctor, repair, resume-point, discover, convert, sync-rules or link');
  for (const flag of Object.keys(flags)) insist(flag === '--account' || options[cmd].includes(flag), 'OPTION_NOT_APPLICABLE', `${flag} does not apply to ${cmd}`);
  insist(!(['link','install'].includes(cmd) && flags['--host'] && flags['--directory']), 'REMOTE_DIRECTORY_UNSUPPORTED', 'Remote setup uses the host’s installed default runtime');
  const installedDirectory = path.resolve(__dirname, '../..');
  const installed = read(path.join(installedDirectory, 'config.json'), {});
  const directory = runtimeDirectory(flags['--directory'] || process.env.TEXT_HOME, flags['--account'] || process.env.TEXT_ACCOUNT || installed.account || 'main', installedDirectory);
  let result;
  if (cmd === 'install') result = await (flags['--host']?installRemote:install)({ directory,host:flags['--host'], config: { ...read(flags['--config'], null), account: flags['--account'] || process.env.TEXT_ACCOUNT || 'main' }, bundle: flags['--rules'] ? read(flags['--rules'], null) : undefined, upgrade: flags['--upgrade'], migration: flags['--migration'] ? read(flags['--migration'], null) : undefined });
  else if(cmd==='validate'){insist(flags['--config']&&flags['--rules'],'VALIDATE_ARGUMENT','validate needs --config and --rules');const config=configOK(read(flags['--config'],null));result={ok:true,bundle:require('../engine/lib/rules.cjs').validateWiring(config,validate(read(flags['--rules'],null)))};}
  else if (cmd === 'resume-point') result=await resumePoint({directory});
  else if (cmd === 'doctor') result = await doctor({ directory, roundTripId: flags['--round-trip'] });
  else if (cmd === 'repair') result = await repair({ directory, roundTripId: flags['--round-trip'] });
  else if (cmd === 'discover') result = await discover(run,{selected:flags['--host']?[flags['--host']]:[]});
  else if (cmd === 'convert') {
    insist(flags['--source'] && flags['--host'] && flags['--output'], 'CONVERT_ARGUMENT', 'convert needs --source, --host and --output');
    let legacyEnv = flags['--env'] ? parseEnv(fs.readFileSync(flags['--env'], 'utf8')) : {};
    if (flags['--plist']) {
      const result = await run('python3', ['-c', 'import plistlib,json,sys;print(json.dumps(plistlib.load(open(sys.argv[1],"rb")).get("EnvironmentVariables",{})))', flags['--plist']]);
      legacyEnv = { ...legacyEnv, ...JSON.parse(result.stdout) };
    }
    result = convert(read(flags['--source'], null), flags['--host'], legacyEnv, flags['--sent-index'] ? read(flags['--sent-index'], {}) : {}); write(flags['--output'], result);
  } else if (cmd === 'sync-rules') {
    insist(hostOK(flags['--host']), 'INVALID_HOST', 'sync-rules needs --host');
    const bundle = validate(read(flags['--rules'] || path.join(directory, 'rules.json'), null));
    result = await remote(flags['--host'], { cmd: 'rules-set', bundle });
  } else if (cmd === 'link') {
    process.umask(0o077);
    insist(!flags['--phone-stdin']!==!flags['--window'],'PAIR_ARGUMENT','link needs --phone-stdin or --window');
    let input='';if(flags['--phone-stdin'])for await(const chunk of process.stdin){input+=chunk;insist(input.length<=32,'PAIR_ARGUMENT','Enter only country-code phone digits');}const phone=input.trim();
    insist(flags['--window'] || /^\d{8,15}$/.test(phone), 'PAIR_ARGUMENT', 'Provide country-code phone digits on stdin');
    if (flags['--host']) {
      insist(phone,'PAIR_ARGUMENT','Remote linking uses --phone-stdin');
      const child = pairRemote(flags['--host'], phone, spawn, flags['--account'] || 'main');
      await new Promise((resolve, reject) => { child.on('error', reject); child.on('exit', c => c === 0 ? resolve() : reject(new Error(`Remote linking exited ${c}`))); }); return;
    }
    await ensureSocketFree(path.join(directory, 'engine.sock'));
    const config = configOK(read(path.join(directory, 'config.json'), null));
    insist(!config.linkedVia, 'PROXY_CONFIG', 'This machine is configured to use a linked sender');
    const { transport } = await import('../engine/baileys/transport.mjs');
    setAsideUnfinishedLink(directory);
    const window=flags['--window']?openQrWindow(directory):null;
    let wa;
    try { await new Promise((resolve, reject) => {
      transport({ directory, config, login: true, phone: phone || undefined, onMessage: async () => {},
        onQr: window ? window.show : undefined,
        onExit: () => reject(Object.assign(new Error('WhatsApp ended the link before it finished; run link again'), { code: 'LINK_INTERRUPTED' })),
        onCode: async code => {
          console.log(JSON.stringify({ pairingCode: code, host: config.host, phoneAction: 'WhatsApp > Linked devices > Link with phone number' }));
          if (config.pairCodeVia) {
            const receipt = await require('../engine/lib/client.cjs').forAccount(config.account || 'main')(config.pairCodeVia, { cmd: 'send', chat: config.ownerChat, style: 'plain', confirmed: true, message: `Pair ${config.host}: ${code}` });
            insist(receipt.ok, 'PAIR_NOTICE_FAILED', receipt.error || 'Linked sender could not post the code');
          }
        },
        onReady: async () => {
          // Check the event's persisted identity before reporting link success.
          const creds = read(path.join(directory, 'auth/creds.json'), {});
          insist(creds.me?.id, 'LINK_PERSISTENCE', 'Connection opened before saved identity; check doctor after credentials persist');
          window?.show('done');
          console.log(JSON.stringify({ ok: true, linked: true, roundTrip: 'not-verified' })); wa?.close(); resolve();
        }, onError: reject }).then(api => { wa = api; }).catch(reject);
    }); } catch (e) { window?.show('failed ' + e.message); throw e; } finally { wa?.close(); } return;
  } else insist(false, 'SETUP_COMMAND', 'Use validate, install, doctor, repair, resume-point, discover, convert, sync-rules or link');
  console.log(JSON.stringify(result)); if (result.ok === false) process.exitCode = 1;
}
if (require.main === module) main().catch(e => { console.error(JSON.stringify({ ok: false, code: e.code || 'SETUP_FAILED', error: e.message })); process.exitCode = 1; });
module.exports = { install, installRemote,doctor, resumePoint,repair, configOK, pairRemote, discover };
