import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import net from 'node:net';
import { spawn } from 'node:child_process';
import { HOME, ROOT, insist, Refusal, read, locked, atomicJSON, writeNew, run, local, remote, oc, ocPrivate, protectSession, untrusted,sessionState,setSessionState,grantKey } from './core.mjs';
import {remember,decide} from './rulings.mjs';
import { load, accountAt, validate, canonical, drivable } from './accounts.mjs';
import { identityScopes, exactScope, email, projectID, googleService,scopeDenied } from './google-contract.mjs';
import { download, verifyFiles, fileHashes } from './setup.mjs';
import { check, verify, navigate, launch, bridgeOperation, stateLocked } from './sign-in.mjs';
import { privateHolder } from './daemon.mjs';
import {oauthOutcome} from '../browser/identity.mjs';
import {systemClock} from './time.mjs';

export const gwsPin = read(path.join(ROOT, 'browser/gws-versions.json'));
export const explanation = {
  optIn:true,
  browser:'A bridge reuses the pages you signed into; you sign in by hand.',
  api:'Google API control lets agents call explicitly chosen services directly, using your own project and Desktop OAuth client. You choose exact scopes and give consent.',
  recommendation:'Offer this during setup. Choose browser-only unless you want API control now; you can add it later.',
  permissions:'OAuth scopes give technical access, not permission to change things. Writes still need your OK or your exact standing grant.',
  gates:'Only you sign in, approve consent, attest project ownership and Production publishing. Workspace admin blocks stop the setup.',
};
const now = () => new Date().toISOString();
function enabled(file){insist(load(file).rulings['setup.google']?.value!=='no','GOOGLE_DECLINED','Google control is declined; only the person can change that saved ruling');}
function currentGoogle(file,name,a){const data=load(file);enabled(file);insist(googleContract(name,a)===googleContract(name,accountAt(data,name)),'GOOGLE_ACCOUNT_CHANGED','Account metadata changed; reload it before using this login');if(a.google){insist(!data.rulings[`email.${name}`]||data.rulings[`email.${name}`].value.toLowerCase()===a.google.expectedEmail.toLowerCase(),'GOOGLE_ACCOUNT_CHANGED','The saved email changed; update the login through google change');if(a.google.project)insist(data.rulings[`project.${a.google.person}`]?.value===a.google.project,'GOOGLE_PROJECT_MISMATCH','The saved ownership ruling no longer matches this client project');}}
function serviceRules(file,name,a,service){
 currentGoogle(file,name,a);const r=load(file).rulings,g=a.google,s=g.services[service];enabled(file);
 insist(!['admin','unknown'].includes(r[`blocked.${name}`]?.value),'GOOGLE_BLOCK_REVIEW_REQUIRED','The person must review the saved blocked outcome');
 insist(!r[`email.${name}`]||r[`email.${name}`].value.toLowerCase()===g.expectedEmail.toLowerCase(),'GOOGLE_ACCOUNT_CHANGED','The saved email changed; use google change before accessing this login');
 insist(r[`production.${g.person}`]?.value===g.project,'GOOGLE_PROJECT_CONFIRMATION','The current Production attestation is missing or changed; the person must confirm it through google project');
 insist(r[`service.${name}.${service}`]?.value!=='no','GOOGLE_SERVICE_CONSENT','The person declined this service');
 const sr=r[`scope.${name}.${service}`];insist(!scopeDenied(sr,s.scopes),'GOOGLE_SCOPE_REVOKED','A recorded scope was revoked; change or revoke the connection before use');
 return r;
}
async function servicePolicy(file,name,a,service,ask){
 const keys=['setup.google',`service.${name}.${service}`,`scope.${name}.${service}`],approved=new Map(),s=a.google.services[service];
 for(const key of keys){const r=serviceRules(file,name,a,service);if(r[key]?.value==='ask'||!r[key]&&!key.startsWith('scope.')){insist(ask,'RULING_REQUIRED','An unknown choice or saved ask policy requires this operation in a human terminal on the holder');insist(await decide(file,key,ask,`Allow ${name}/${service} for this operation using ${s.api} and scopes ${s.scopes.join(', ')}?`,key.startsWith('service.')?{api:s.api,scopes:s.scopes}:{})==='yes','GOOGLE_SERVICE_CONSENT','This operation was declined or deferred');approved.set(key,JSON.stringify(load(file).rulings[key]));}}
 const latest=serviceRules(file,name,a,service);for(const key of keys)if(latest[key]?.value==='ask'||!latest[key]&&!key.startsWith('scope.'))insist(approved.has(key)&&approved.get(key)===JSON.stringify(latest[key]),'RULING_CHANGED','A changed ask policy needs a new human operation');
 return JSON.stringify(latest);
}
export function googleContract(name,a) {
  const g=a.google;
  return crypto.createHash('sha256').update(JSON.stringify({protocol:'google-rulings-v1',account:name,holder:a.machine.id,bridge:{name:a.bridge.name,port:a.bridge.port,profileDir:path.resolve(a.bridge.profileDir),driveBlocked:!!a.bridge.driveBlocked},person:g?.person,email:g?.expectedEmail?.toLowerCase(),project:g?.project,production:g?.productionConfirmedAt,optIn:{userWords:g?.optIn?.userWords,at:g?.optIn?.at},services:Object.entries(g?.services||{}).sort(([a],[b])=>a<b?-1:a>b?1:0).map(([name,s])=>({name,api:s.api,scopes:[...s.scopes].sort(),connectedAt:s.connectedAt}))})).digest('hex');
}
export async function delegateGoogle(a,name,route,args,delegate=remote) {
  const expected=googleContract(name,a),proof=await delegate(a,['google',route,name,'--expect',expected,...args]);
  insist(proof?.googleContract===expected&&Object.hasOwn(proof,'result'),'GOOGLE_REMOTE_CONTRACT','The holder did not prove the expected Google account, project and scopes');
  return proof.result;
}
const accountRuling=(key,name)=>key===`email.${name}`||key===`blocked.${name}`||['service','scope','instructions','grant'].some(prefix=>key.startsWith(`${prefix}.${name}.`));
export function googleMetadata(file,name,a){
 insist(local(a.machine),'WRONG_MACHINE','Metadata must come from the holder');const d=load(file);
 return {protocol:'google-rulings-v1',account:name,machine:a.machine.id,bridge:{name:a.bridge.name,port:a.bridge.port,profileDir:a.bridge.profileDir},google:a.google||null,permissions:a.permissions.filter(p=>p.via==='google'),rulings:Object.fromEntries(Object.entries(d.rulings).filter(([k])=>accountRuling(k,name)||k===`project.${a.google?.person}`||k===`production.${a.google?.person}`))};
}
export async function syncGoogle(file,name,a,{delegate=remote}={}){
 insist(!local(a.machine),'GOOGLE_SYNC_LOCAL','Run sync on the controller');
 const report=await delegate(a,['google','metadata',name]);
 insist(report?.protocol==='google-rulings-v1'&&report.account===name&&report.machine===a.machine.id&&JSON.stringify(report.bridge)===JSON.stringify({name:a.bridge.name,port:a.bridge.port,profileDir:a.bridge.profileDir}),'GOOGLE_REMOTE_CONTRACT','Metadata came from another bridge, holder or protocol');
 insist(report.rulings&&typeof report.rulings==='object'&&!Array.isArray(report.rulings)&&Array.isArray(report.permissions),'GOOGLE_REMOTE_CONTRACT','Holder metadata must include a complete ruling and API-grant snapshot');
 await mutate(file,name,(fresh,data)=>{if(report.google)fresh.google=report.google;else delete fresh.google;fresh.permissions=[...fresh.permissions.filter(p=>p.via!=='google'),...(report.google?report.permissions||[]:[])];for(const key of Object.keys(data.rulings))if(accountRuling(key,name))delete data.rulings[key];for(const [key,r]of Object.entries(report.rulings||{})){insist(accountRuling(key,name)||key===`project.${fresh.google?.person}`||key===`production.${fresh.google?.person}`,'GOOGLE_REMOTE_CONTRACT','Unrelated ruling in remote metadata');data.rulings[key]=r;}});
 return {account:name,synced:true,secretsTransferred:false};
}
function safeFailure(raw) {
  const text=String(raw);
  if(/admin_policy_enforced|access blocked.*administrator|org_internal/i.test(text)) return new Refusal('GOOGLE_ADMIN_BLOCKED','Workspace policy blocks this client. Ask your administrator; do not retry or bypass it.');
  if(/serviceusage.services.use|serviceusageconsumer/i.test(text)) return new Refusal('GOOGLE_QUOTA_PERMISSION','This Google account needs Service Usage Consumer on the person-owned project; review that exact IAM change.');
  if(/SERVICE_DISABLED|API.*not.*enabled/i.test(text))return new Refusal('GOOGLE_API_NOT_ENABLED','Enable the requested API on the recorded project; no consent retry is needed');
  if(/\b404\b|NOT_FOUND/.test(text))return new Refusal('GOOGLE_API_NOT_FOUND','The requested API resource was not found; check its identifier');
  return new Refusal('GOOGLE_AUTH_FAILED','Google operation failed; stop for the person. Raw OAuth output is withheld because it can contain secrets.');
}
// Never allow an imported path or symlink to seed credentials from a live gws setup.
function outsideLive(file) {
  insist(!/(?:^|\/)\.config\/gws(?:\/|$)|\/Library\/Application Support\/gws(?:\/|$)/.test(path.resolve(file)), 'GOOGLE_LIVE_CONFIG','Use a newly downloaded private Desktop client file, never an existing gws configuration');
}
function privateStat(file, directory) {
  const stat=fs.lstatSync(file);
  insist(!stat.isSymbolicLink() && (directory?stat.isDirectory():stat.isFile()) && stat.uid===process.getuid?.() && (stat.mode&0o077)===0,'GOOGLE_PRIVATE_PATH','Google state must be owned by you, private and free of symlinks');
}
function directory(dir) {
  if(!fs.existsSync(dir)) fs.mkdirSync(dir,{recursive:true,mode:0o700});
  privateStat(dir,true);return dir;
}
function inspectTree(dir) {
  privateStat(dir,true);
  for(const item of fs.readdirSync(dir)){const file=path.join(dir,item),s=fs.lstatSync(file);privateStat(file,s.isDirectory());if(s.isDirectory())inspectTree(file);}
}
function privately(work) {
  // Children inherit umask at spawn. Restore the parent immediately after spawning.
  const previous=process.umask(0o077);try{return work();}finally{process.umask(previous);}
}
export function googleHome(file,name,a,root=HOME) {
  outsideLive(canonical(root));
  const base=directory(path.join(canonical(root),'google'));
  const key=crypto.createHash('sha256').update(JSON.stringify([canonical(file),name,a.machine.id])).digest('hex');
  const home=directory(path.join(directory(path.join(base,'accounts')),key));
  directory(path.join(home,'.config'));directory(path.join(home,'.config','gws'));inspectTree(home);
  return home;
}
export function isolatedEnv(home) {
  // Whitelist: inherited token/client/ADC/proxy/keyring/Cloud SDK overrides never survive.
  return {PATH:process.env.PATH||'/usr/bin:/bin',LANG:'en_US.UTF-8',HOME:home,USER:process.env.USER||'',TMPDIR:directory(path.join(home,'tmp')),XDG_CONFIG_HOME:path.join(home,'.config'),XDG_CACHE_HOME:directory(path.join(home,'.cache')),GOOGLE_WORKSPACE_CLI_CONFIG_DIR:path.join(home,'.config/gws'),GOOGLE_WORKSPACE_CLI_KEYRING_BACKEND:'file',CLOUDSDK_CONFIG:directory(path.join(home,'.config/gcloud'))};
}
export async function installGws({root=HOME,target=`${process.platform}-${process.arch}`,fetcher=fetch,execute=run,downloadArchive=download}={}) {
  outsideLive(canonical(root));
  const pin=gwsPin.targets[target];insist(pin,'PLATFORM_UNSUPPORTED','No pinned gws archive for this platform');
  const base=directory(path.join(canonical(root),'google')), releases=directory(path.join(base,'gws')),dest=path.join(releases,gwsPin.version),receipt=path.join(dest,'receipt.json');
  return locked(path.join(base,'gws-install'),async()=>{
    if(fs.existsSync(dest)){
      privateStat(dest,true);privateStat(path.join(dest,'payload'),true);privateStat(receipt,false);
      const r=read(receipt);insist(r.version===gwsPin.version&&r.target===target&&r.sha256===pin.sha256,'GWS_INSTALL_CHANGED','gws receipt differs; no replacement attempted');
      verifyFiles(path.join(dest,'payload'),r.files);return path.join(dest,'payload','gws');
    }
    const stage=fs.mkdtempSync(path.join(base,'.gws-'));fs.chmodSync(stage,0o700);
    try {
      const archive=path.join(stage,'gws.tgz'),payload=directory(path.join(stage,'payload'));
      await downloadArchive(`${gwsPin.repository}/releases/download/v${gwsPin.version}/google-workspace-cli-${pin.target}.tar.gz`,archive,pin.sha256,'sha256','hex',fetcher);
      const members=(await execute('tar',['-tzf',archive])).stdout.trim().split('\n');
      insist(members.length&&members.every(m=>!path.isAbsolute(m)&&!m.split('/').includes('..')),'GWS_ARCHIVE','Unsafe release archive paths');
      await execute('tar',['-xzf',archive,'-C',payload]);
      // tar restores the archive's root mode; the private installation root must stay private.
      fs.chmodSync(payload,0o700);
      // Accept root or wrapped release layouts, with exactly one regular gws binary.
      const files=fileHashes(payload),matches=Object.keys(files).filter(f=>path.basename(f)==='gws'&&!files[f].startsWith('link:'));
      insist(matches.length===1&&!Object.values(files).some(v=>v.startsWith('link:')),'GWS_ARCHIVE','Release must contain one regular gws binary and no links');
      if(matches[0]!=='gws')fs.copyFileSync(path.join(payload,matches[0]),path.join(payload,'gws'),fs.constants.COPYFILE_EXCL);
      fs.chmodSync(path.join(payload,'gws'),0o700);
      const version=(await execute(path.join(payload,'gws'),['--version'],{env:isolatedEnv(directory(path.join(stage,'version-home')))})).stdout.trim();
      insist(new RegExp(`(?:^|\\s)${gwsPin.version.replaceAll('.','\\.')}(?:$|\\s)`).test(version),'GWS_VERSION','Installed gws differs from the pinned release');
      fs.unlinkSync(archive);fs.rmSync(path.join(stage,'version-home'),{recursive:true});
      writeNew(path.join(stage,'receipt.json'),{version:gwsPin.version,target,sha256:pin.sha256,files:fileHashes(payload)});
      fs.renameSync(stage,dest);return path.join(dest,'payload','gws');
    } finally {fs.rmSync(stage,{recursive:true,force:true});}
  });
}
export function doctorGoogle(root=HOME) {
  outsideLive(canonical(root));const base=path.join(canonical(root),'google');
  if(!fs.existsSync(base))return {installed:false,optional:true,version:gwsPin.version};
  privateStat(base,true);const releases=path.join(base,'gws');
  if(!fs.existsSync(releases))return {installed:false,optional:true,version:gwsPin.version};
  privateStat(releases,true);const dest=path.join(releases,gwsPin.version);
  if(!fs.existsSync(dest))return {installed:false,optional:true,version:gwsPin.version};
  privateStat(dest,true);const receipt=path.join(dest,'receipt.json');privateStat(receipt,false);const r=read(receipt),pin=gwsPin.targets[r.target];
  insist(r.version===gwsPin.version&&pin?.sha256===r.sha256,'GWS_INSTALL_CHANGED','gws receipt does not match pinned release');
  privateStat(path.join(dest,'payload'),true);verifyFiles(path.join(dest,'payload'),r.files);
  return {installed:true,version:gwsPin.version,bytesVerified:true,executed:false};
}
async function gwsJSON(bin,args,env,execute=run) {
  let stdout;
  try {({stdout}=await privately(()=>execute(bin,args,{env})));}
  catch(e){throw safeFailure(`${e.stderr||''} ${e.stdout||''} ${e.message||''}`);}
  let data;try{data=JSON.parse(stdout);}catch{throw new Refusal('GOOGLE_AUTH_FAILED','Unreadable Google response; raw output withheld');}
  if(data?.error)throw safeFailure(JSON.stringify(data.error));return data;
}
const scopeName = s => s==='email'?'https://www.googleapis.com/auth/userinfo.email':s==='profile'?'https://www.googleapis.com/auth/userinfo.profile':s;
export function judgeGoogle(a, service, report, extraScopes=[]) {
  if(report?.token_error)throw safeFailure(report.token_error);
  insist(report?.token_valid===true&&report.auth_method==='oauth2','GOOGLE_SIGNED_OUT','No valid API login; stop for the person');
  insist(a.google.project&&a.google.productionConfirmedAt&&report.project_id===a.google.project,'GOOGLE_PROJECT_MISMATCH','The configured API project differs from the person-owned project');
  insist(email(report.user)&&report.user.toLowerCase()===a.google.expectedEmail.toLowerCase(),'GOOGLE_IDENTITY_MISMATCH','Google API identity differs from the intended account');
  insist(Array.isArray(report.scopes)&&report.scopes.every(s=>s==='openid'||s==='email'||s==='profile'||exactScope(s)),'GOOGLE_SCOPE_UNKNOWN','Google did not report a usable granted scope set');
  const actual=[...new Set(report.scopes.map(scopeName))].sort(),required=[...new Set([...identityScopes,...Object.values(a.google.services).flatMap(s=>s.scopes),...extraScopes])].sort();
  insist(required.every(s=>actual.includes(s)),'GOOGLE_SCOPE_MISSING','The current token lacks requested scopes; the person must re-consent');
  insist(actual.every(s=>required.includes(s)),'GOOGLE_SCOPE_EXCESS','The token has scopes outside the recorded choices; review them with the person');
  return {via:'google',service,identity:{email:report.user},scopes:actual,project:a.google.project,projectVerification:'local-client-config-only',status:'signed-in'};
}
async function mutate(file,name,change){return locked(file,async()=>{const data=load(file);await change(accountAt(data,name),data);validate(data);atomicJSON(file,data);});}
function retireLogin(home){const dir=path.join(home,'.config/gws');for(const name of ['credentials.enc','credentials.json','.encryption_key','token_cache.json']){const file=path.join(dir,name);if(fs.existsSync(file)){privateStat(file,false);fs.renameSync(file,`${file}.retired-${crypto.randomUUID()}`);}}}
export async function changeGoogle(file,name,options,{root=HOME,guard=privateHolder,ask=async()=>{throw new Refusal('RULING_REQUIRED','Human answer required');}}={}) {
 const a=accountAt(load(file),name);insist(local(a.machine)&&a.google,'GOOGLE_OPT_IN_REQUIRED','Change this account on its holder');await guard(a.machine);drivable(a);
 if(options.email)insist(!Object.entries(load(file).accounts).some(([key,other])=>key!==name&&other.google?.expectedEmail.toLowerCase()===options.email.toLowerCase()),'GOOGLE_CONFLICT','This email already has a registered login');
 const home=googleHome(file,name,a,root);
 if(options['client-file'])return configureProject(file,name,a,{project:a.google.project,clientFile:options['client-file'],ask},{root,guard,rotate:true});
 insist(email(options.email)||['admin','unknown','clear'].includes(options.block),'GOOGLE_CHANGE','Supply --email or --block admin|unknown|clear');
 return locked(path.join(home,'operation'),async()=>{if(options.email)retireLogin(home);await mutate(file,name,(fresh,data)=>{
   if(options.email){fresh.google.expectedEmail=options.email;data.rulings[`email.${name}`]={value:options.email,at:now()};for(const s of Object.values(fresh.google.services))delete s.connectedAt;fresh.permissions=fresh.permissions.filter(p=>p.via!=='google');for(const key of Object.keys(data.rulings))if(key.startsWith(`service.${name}.`))delete data.rulings[key];for(const s of Object.values(fresh.sites))if(s.identity?.kind==='google')s.identity.expected={name:options.email};}
   if(options.block)data.rulings[`blocked.${name}`]={value:options.block,at:now()};
 });return {account:name,changed:true,serverRevocation:false};});
}
export async function revokeGoogle(file,name,{service,scope,action,resource}={}, {root=HOME,guard=privateHolder}={}) {
 const a=accountAt(load(file),name);insist(local(a.machine)&&a.google,'GOOGLE_OPT_IN_REQUIRED','Revoke on the holder');await guard(a.machine);const home=googleHome(file,name,a,root);
 return locked(path.join(home,'operation'),async()=>{const fresh=accountAt(load(file),name);if(service){const s=googleService(fresh,service);if(scope)insist(s.scopes.includes(scope),'GOOGLE_SCOPE_UNKNOWN','Scope is not recorded');}insist(!scope||service,'GOOGLE_SCOPE_UNKNOWN','Choose the service for this scope');if(action)insist(service&&resource,'GRANT_SCOPE','Choose exact service, action and resource');if(!action)retireLogin(home);await mutate(file,name,(fresh,data)=>{
   if(action){insist(service&&resource,'GRANT_SCOPE','Choose exact service, action and resource');fresh.permissions=fresh.permissions.filter(p=>!(p.via==='google'&&p.site===service&&p.action===action&&p.resource===resource));data.rulings[grantKey(name,{site:service,via:'google',action,resource})]={value:'ask',at:now()};return;}
   if(service){const s=googleService(fresh,service);if(scope){insist(s.scopes.includes(scope),'GOOGLE_SCOPE_UNKNOWN','Scope is not recorded');s.scopes=s.scopes.filter(x=>x!==scope);data.rulings[`scope.${name}.${service}`]={value:'no',at:now(),scopes:[...new Set([...(data.rulings[`scope.${name}.${service}`]?.scopes||[]),scope])]};delete s.connectedAt;if(s.scopes.length){data.rulings[`service.${name}.${service}`]={value:'yes',at:now(),api:s.api,scopes:s.scopes};}else delete fresh.google.services[service];}else delete fresh.google.services[service];fresh.permissions=fresh.permissions.filter(p=>!(p.via==='google'&&p.site===service));if(!fresh.google.services[service])data.rulings[`service.${name}.${service}`]={value:'no',at:now()};}
   else {delete fresh.google;fresh.permissions=fresh.permissions.filter(p=>p.via!=='google');data.rulings['setup.google']={value:'no',at:now()};}
 });return {account:name,revoked:true,serverRevocation:false,note:'Local access disabled. The person may separately revoke Google consent; no server action was performed.'};});
}
export async function optIn(file,name,{ask,guard=privateHolder}) {
  const a=accountAt(load(file),name);insist(local(a.machine),'HUMAN_STEP_ON_HOLDER','Opt in on the account holder');drivable(a);
  await guard(a.machine);
  insist(await decide(file,'setup.google',ask,`${explanation.api}\n${explanation.permissions}`)==='yes','GOOGLE_DECLINED','Google API control is declined or deferred; change the saved ruling to opt in');
  if(a.google)return {account:name,optedIn:true,reused:true};
  let r=load(file).rulings,person=r.person?.value,expectedEmail=r[`email.${name}`]?.value;
  if(!person){person=await ask('Choose your one-word person key (saved for all your accounts): ');await remember(file,'person',person);}
  if(!expectedEmail){expectedEmail=await ask('Which Google email should this account prove? ');await remember(file,`email.${name}`,expectedEmail);}
  insist(!Object.entries(load(file).accounts).some(([key,other])=>key!==name&&other.google?.expectedEmail.toLowerCase()===expectedEmail.toLowerCase()),'GOOGLE_CONFLICT','This Google account already has a holder; transfer it rather than create another login');
  await mutate(file,name,(fresh,data)=>{fresh.google={person,expectedEmail,optIn:{userWords:data.rulings['setup.google'].value,at:data.rulings['setup.google'].at},services:{}};});
  return {account:name,optedIn:true,next:'google console: use your own project, Desktop client and Production audience; then google project'};
}
export async function configureProject(file,name,a,{project,clientFile,ask},{root=HOME,guard=privateHolder,rotate=false}={}) {
  enabled(file);
  insist(a.google,'GOOGLE_OPT_IN_REQUIRED','Opt in first');insist(local(a.machine),'HUMAN_STEP_ON_HOLDER','Configure the Desktop client on the holder');drivable(a);
  await guard(a.machine);
  insist(projectID(project),'GOOGLE_PROJECT','Supply the owned project ID');
  const known=load(file).rulings[`project.${a.google.person}`]?.value;
  insist(!known||known===project,'GOOGLE_CONFLICT','Reuse the project already owned by this person');
  if(!known){insist(await ask(`Is ${project} your own project for person ${a.google.person}? Type yes: `)==='yes','GOOGLE_PROJECT_CONFIRMATION','Project ownership not confirmed');await remember(file,`project.${a.google.person}`,project);}
  if(!clientFile){
    await mutate(file,name,fresh=>{insist(!fresh.google.project||fresh.google.project===project,'GOOGLE_PROJECT_EXISTS','A configured project is not replaced');fresh.google.project=project;});
    return {account:name,project,next:'google console: enable the needed API, configure consent, create Desktop client, publish Production; then google project --client-file'};
  }
  outsideLive(clientFile);outsideLive(canonical(clientFile));privateStat(clientFile,false);
  const source=read(clientFile),client=source?.installed;
  insist(client&& !source.web && client.project_id===project && typeof client.client_id==='string' && client.client_id.endsWith('.apps.googleusercontent.com') && typeof client.client_secret==='string' && client.client_secret.length>0 && client.auth_uri==='https://accounts.google.com/o/oauth2/auth' && client.token_uri==='https://oauth2.googleapis.com/token','GOOGLE_DESKTOP_CLIENT','Download an installed (Desktop) OAuth client from the intended project');
  const production=load(file).rulings[`production.${a.google.person}`];
  if(production?.value!==project)insist(await ask('Is this Desktop client ready with its audience published to Production? Type yes: ')==='yes','GOOGLE_PROJECT_CONFIRMATION','Production must be confirmed by the person');
  if(production?.value!==project)await remember(file,`production.${a.google.person}`,project);
  const data=load(file);for(const other of Object.values(data.accounts))if(other.google?.person===a.google.person&&other.google.project)insist(other.google.project===project,'GOOGLE_CONFLICT','Reuse the one project for this person');
  const home=googleHome(file,name,a,root),config=path.join(home,'.config/gws/client_secret.json');
  await locked(path.join(home,'operation'),async()=>{
    await locked(file,async()=>{
      const fresh=load(file),g=accountAt(fresh,name).google;insist(g&&(!g.project||g.project===project),'GOOGLE_PROJECT_EXISTS','A configured project is not replaced');
      insist(fresh.rulings[`project.${g.person}`]?.value===project,'GOOGLE_ACCOUNT_CHANGED','Project ownership changed before client import');
      g.project=project;g.productionConfirmedAt||=now();validate(fresh);
      if(rotate){retireLogin(home);for(const s of Object.values(g.services))delete s.connectedAt;}
      if(fs.existsSync(config)){privateStat(config,false);insist(rotate||JSON.stringify(read(config))===JSON.stringify(source),'GOOGLE_CLIENT_CONFLICT','A different client exists; use the human change route to rotate it');if(rotate)atomicJSON(config,source);}else writeNew(config,source);
      atomicJSON(file,fresh);
    });
  });
  return {account:name,project,desktopClientInstalled:true,production:'person-confirmed',secretsPrinted:false};
}
export async function checkGoogle(file,name,a,service,deps={}) {
  enabled(file);
  googleService(a,service);drivable(a);
  if(!local(a.machine)){
    const proof=await delegateGoogle(a,name,'check',['--service',service],deps.delegate);
    insist(proof?.account===name&&proof.service===service&&proof.via==='google','GOOGLE_REMOTE_CONTRACT','Holder returned proof for another account or service');
    return {account:name,...judgeGoogle(a,service,{auth_method:'oauth2',token_valid:proof.status==='signed-in',user:proof.identity?.email,project_id:proof.project,scopes:proof.scopes})};
  }
  const d={root:HOME,guard:privateHolder,install:installGws,execute:run,...deps};await d.guard(a.machine);
  currentGoogle(file,name,a);
  const rules=await servicePolicy(file,name,a,service,deps.ask);
  const home=googleHome(file,name,a,d.root);
  return locked(path.join(home,'operation'),async()=>{
    const bin=await d.install({root:d.root}),report=await gwsJSON(bin,['auth','status'],isolatedEnv(home),d.execute);
    currentGoogle(file,name,a);insist(JSON.stringify(load(file).rulings)===rules,'GOOGLE_ACCOUNT_CHANGED','Saved rulings changed during the operation');return {account:name,...judgeGoogle(a,service,report)};
  });
}
export function readArgs(service,args) {
  const split=args.indexOf('--params'),words=split<0?args:args.slice(0,split);
  insist(words.length>=2&&['get','list'].includes(words.at(-1))&&words.slice(0,-1).every(w=>/^[a-zA-Z][a-zA-Z0-9]*$/.test(w)),'GOOGLE_READ_ONLY','Use discovery resource names followed by get or list');
  let params;
  if(split>=0){insist(args.length===split+2,'GOOGLE_READ_ONLY','Only one --params JSON object is allowed');try{params=JSON.parse(args.at(-1));}catch{}insist(params&&typeof params==='object'&&!Array.isArray(params),'GOOGLE_READ_ONLY','Expected a params object');}
  return [service,...words,...(params?['--params',JSON.stringify(params)]:[])];
}
export async function readGoogle(file,name,a,service,args,deps={}) {
  enabled(file);
  googleService(a,service);drivable(a);const command=readArgs(service,args);
  if(!local(a.machine))return untrusted(await delegateGoogle(a,name,'read',['--service',service,'--',...args],deps.delegate),'remote-google-api');
  const d={root:HOME,guard:privateHolder,install:installGws,execute:run,...deps};await d.guard(a.machine);
  currentGoogle(file,name,a);
  const rules=await servicePolicy(file,name,a,service,deps.ask);
  const home=googleHome(file,name,a,d.root);
  return locked(path.join(home,'operation'),async()=>{
    const bin=await d.install({root:d.root}),env=isolatedEnv(home);
    judgeGoogle(a,service,await gwsJSON(bin,['auth','status'],env,d.execute));
    currentGoogle(file,name,a);insist(JSON.stringify(load(file).rulings)===rules,'GOOGLE_ACCOUNT_CHANGED','Saved rulings changed during identity proof');const result=await gwsJSON(bin,command,env,d.execute);currentGoogle(file,name,a);insist(JSON.stringify(load(file).rulings)===rules,'GOOGLE_ACCOUNT_CHANGED','Saved rulings changed during the operation');return untrusted(result,'google-api');
  });
}
export function oauthURL(value,clientID,scopes) {
  const url=new URL(value),redirect=new URL(url.searchParams.get('redirect_uri'));
  insist(url.origin==='https://accounts.google.com' && ['/o/oauth2/auth','/o/oauth2/v2/auth'].includes(url.pathname)&&url.searchParams.get('client_id')===clientID&&redirect.protocol==='http:'&&['localhost','127.0.0.1','[::1]'].includes(redirect.hostname)&&!redirect.username&&!redirect.password&&redirect.port,'GOOGLE_OAUTH_URL','Unexpected OAuth endpoint, client or loopback redirect');
  const actual=url.searchParams.get('scope')?.split(' ').filter(Boolean).sort();
  insist(JSON.stringify(actual)===JSON.stringify([...scopes].sort()),'GOOGLE_OAUTH_SCOPES','Login URL differs from the exact chosen scopes');
  return {url:url.href,redirect};
}
async function listenerReady(redirect,child,deadline,clock) {
  while(clock.now()<deadline){
    insist(child.exitCode===null,'GOOGLE_LISTENER_STOPPED','OAuth listener exited before navigation');
    if(await new Promise(resolve=>{const s=net.connect({host:redirect.hostname==='localhost'?'127.0.0.1':redirect.hostname.replace(/[\[\]]/g,''),port:Number(redirect.port)});s.setTimeout(200);s.once('connect',()=>{s.destroy();resolve(true);});s.once('error',()=>resolve(false));s.once('timeout',()=>{s.destroy();resolve(false);});}))return;
    await clock.sleep(50);
  }
  throw new Refusal('GOOGLE_LISTENER_TIMEOUT','The OAuth loopback listener did not become ready; no browser was navigated');
}
// Keep the real process alive until its callback completes. Neither URL nor raw streams escape.
export async function login(bin,env,scopes,clientID,openURL,{spawnProcess=spawn,ready,clock=systemClock,timeout=300000,observe=async()=> 'pending',poll=250}={}) {
  const child=privately(()=>spawnProcess(bin,['auth','login','--scopes',scopes.join(',')],{env,stdio:['ignore','pipe','pipe']}));
  let buffer='',opened=false,failure,opening=Promise.resolve(),force,stopping=false;
  const deadline=clock.now()+timeout;
  const waitReady=ready||((...args)=>listenerReady(...args,clock));
  const completed=new Promise((resolve,reject)=>{child.once('error',()=>reject(new Refusal('GOOGLE_LOGIN_START','Could not start the isolated login listener')));child.once('close',code=>{clock.clearTimeout(force);code===0?resolve():reject(safeFailure(buffer));});});
  const stop=()=>{if(stopping||child.exitCode!==null)return;stopping=true;child.kill('SIGTERM');force=clock.setTimeout(()=>{if(child.exitCode===null)child.kill('SIGKILL');},2000);force.unref?.();};
  const interrupted=()=>{failure=new Refusal('GOOGLE_CONSENT_CANCELLED','Consent interrupted; no service was recorded');stop();};
  process.once('SIGINT',interrupted);process.once('SIGTERM',interrupted);
  const timer=clock.setTimeout(()=>{failure=new Refusal('GOOGLE_CONSENT_TIMEOUT','Consent outcome is unresolved; review the page with the person before another attempt');stop();},timeout);
  let observing=false;const monitor=clock.setInterval(async()=>{if(!opened||observing||failure)return;observing=true;try{const code=await observe();if(['admin_policy_enforced','org_internal'].includes(code)){failure=safeFailure(code);stop();}}catch{}finally{observing=false;}},poll);
  const inspect=chunk=>{
    buffer+=chunk.toString();if(buffer.length>262144){failure=new Refusal('GOOGLE_LOGIN_OUTPUT','Unexpected login output volume');stop();return;}
    if(/admin_policy_enforced|org_internal/.test(buffer)){failure=safeFailure(buffer);stop();return;}
    const match=buffer.match(/https:\/\/accounts\.google\.com\/[^\s]+/);
    if(!opened&&match&&/[\r\n]/.test(buffer.slice(match.index+match[0].length))){opened=true;opening=(async()=>{const target=oauthURL(match[0],clientID,scopes);await waitReady(target.redirect,child,deadline);await openURL(target.url);})().catch(e=>{failure=e instanceof Refusal?e:new Refusal('GOOGLE_HANDOFF_FAILED','Could not open the consent tab; OAuth details withheld');stop();});}
  };
  child.stdout.on('data',inspect);child.stderr.on('data',inspect);
  try{await completed;await opening;insist(!failure,failure?.code,failure?.message);insist(opened,'GOOGLE_NO_CONSENT_URL','Login completed without the expected consent handoff');return {consented:true};}
  catch(e){if(failure)throw failure;throw e;}finally{clock.clearTimeout(timer);clock.clearInterval(monitor);process.removeListener('SIGINT',interrupted);process.removeListener('SIGTERM',interrupted);buffer='';if(child.exitCode===null)stop();}
}
export async function connect(file,name,a,{service,api,scope,'replace-scope':replaceScope},{ask,...deps}) {
  enabled(file);
  currentGoogle(file,name,a);
  insist(a.google?.project&&a.google.productionConfirmedAt,'GOOGLE_PROJECT_REQUIRED','Configure your own Desktop client and confirm Production first');drivable(a);
  insist(local(a.machine),'HUMAN_STEP_ON_HOLDER','Run connect in a human terminal on the holder (SSH terminal is fine); consent appears in that account bridge');
  insist(/^[a-z][a-z0-9-]{0,62}$/.test(service||'')&&/^[a-z][a-z0-9-]*\.googleapis\.com$/.test(api||'')&&exactScope(scope),'GOOGLE_EXACT_SCOPE','Choose one service, one API and one exact scope for this need');
  const d={root:HOME,guard:privateHolder,install:installGws,execute:run,login,verify,launch,oc:ocPrivate,navigate,check,ask,...deps};await d.guard(a.machine);
  insist(load(file).rulings[`production.${a.google.person}`]?.value===a.google.project,'GOOGLE_PROJECT_CONFIRMATION','The person must confirm the current Production attestation before consent');
  insist(!['admin','unknown'].includes(load(file).rulings[`blocked.${name}`]?.value),'GOOGLE_BLOCK_REVIEW_REQUIRED','Review the saved blocked/unresolved outcome with the person; never retry or bypass it');
  insist(scope!=='https://www.googleapis.com/auth/cloud-platform','GOOGLE_SCOPE_TOO_BROAD','Cloud platform control is not a service-scoped Google connection');
  const scopeService=scope==='https://mail.google.com/'?'gmail':scope.split('/auth/')[1]?.split(/[./]/)[0],apiService=api==='calendar-json.googleapis.com'?'calendar':api.split('.')[0];
  const warnings=[];if(scopeService!==apiService)warnings.push('a different API from the requested service');if(!scope.endsWith('.readonly'))warnings.push(scope==='https://mail.google.com/'?'all mail and write/delete access':'a scope not named readonly: review its permissions; it may allow writes or broad data access');
  const warning=warnings.length?`WARNING: This scope permits ${warnings.join('; ')}. `:'';
  const scopeRuling=load(file).rulings[`scope.${name}.${service}`];
  insist(!scopeDenied(scopeRuling,[scope]),'GOOGLE_SCOPE_REVOKED','This scope was removed by the person; change that saved ruling before requesting it again');
  const old=a.google.services[service];insist(!old||old.api===api,'GOOGLE_SERVICE_CONFLICT','Service API cannot change');
  insist(load(file).rulings[`service.${name}.${service}`]?.value!=='no','GOOGLE_SERVICE_CONSENT','This service was declined; only the person changes that saved answer');
  if(old?.connectedAt&&old.scopes.includes(scope)&&!replaceScope)return checkGoogle(file,name,a,service,d);
  const rootChoice=load(file).rulings['setup.google'];if(!rootChoice||rootChoice.value==='ask')insist(await decide(file,'setup.google',ask,`Allow Google API control for this service connection on ${name}?`)==='yes','GOOGLE_DECLINED','Google control declined or deferred for this attempt');
  insist(!replaceScope||exactScope(replaceScope)&&replaceScope!==scope&&old?.scopes.includes(replaceScope),'GOOGLE_SCOPE_REPLACEMENT','Only an existing scope on this same service may be explicitly replaced');
  const candidate=structuredClone(a);
  candidate.google.services[service]={api,scopes:[...new Set([...(old?.scopes||[]).filter(s=>s!==replaceScope),scope])]};
  const scopes=[...new Set([...identityScopes,...Object.values(candidate.google.services).flatMap(s=>s.scopes)])];
  for(const [key,s]of Object.entries(candidate.google.services)){const r=load(file).rulings,sr=r[`scope.${name}.${key}`],serviceRule=r[`service.${name}.${key}`];insist(serviceRule?.value!=='no','GOOGLE_SERVICE_CONSENT','A service in this consent union was declined');insist(!scopeDenied(sr,s.scopes),'GOOGLE_SCOPE_REVOKED','A scope in this consent union was revoked');if(key!==service&&(!serviceRule||serviceRule.value==='ask'))insist(await decide(file,`service.${name}.${key}`,ask,`Allow this consent for existing service ${name}/${key}, ${s.api}, scopes ${s.scopes.join(', ')}?`,{api:s.api,scopes:s.scopes})==='yes','GOOGLE_SERVICE_CONSENT','Existing service request declined or deferred');if(sr?.value==='ask')insist(await decide(file,`scope.${name}.${key}`,ask,`Allow this consent for ${name}/${key}, scopes ${s.scopes.join(', ')}?`,{scopes:s.scopes})==='yes','GOOGLE_SERVICE_CONSENT','Scope request declined or deferred');}
  if(scopeRuling?.value==='yes'&&scopeRuling.scopes?.includes(scope)&&load(file).rulings[`service.${name}.${service}`]?.value!=='ask')await remember(file,`service.${name}.${service}`,'yes',{api,scopes:candidate.google.services[service].scopes});
  insist(!(scopes.includes('https://www.googleapis.com/auth/gmail.metadata')&&['https://www.googleapis.com/auth/gmail.readonly','https://www.googleapis.com/auth/gmail.modify','https://mail.google.com/'].some(s=>scopes.includes(s))),'GOOGLE_SCOPE_REPLACEMENT','gws removes redundant Gmail metadata access. Review --replace-scope https://www.googleapis.com/auth/gmail.metadata instead of silently changing the chosen scopes.');
  insist(await decide(file,`service.${name}.${service}`,ask,`${warning}Connect ${name}/${service} for ${a.google.expectedEmail} using ${api}, adding ${scope}${replaceScope?`, replacing ${replaceScope}`:''}? Requested scopes, including identity and existing services: ${scopes.join(', ')}. This is technical access, not permission to write.`,{api,scopes:candidate.google.services[service].scopes})==='yes','GOOGLE_SERVICE_CONSENT','Service request declined or deferred');
  const approvedRules=JSON.stringify(load(file).rulings);
  const home=googleHome(file,name,a,d.root);
  return locked(path.join(home,'operation'),async()=>{
    // Freeze the account definition while consenting; a concurrent edit cannot change the intended identity/scopes.
    const baseline=JSON.stringify(a.google);
    const ruleBaseline=JSON.stringify(load(file).rulings);enabled(file);insist(load(file).rulings[`service.${name}.${service}`]?.value!=='no','GOOGLE_SERVICE_CONSENT','Service was declined before consent');
    insist(JSON.stringify(load(file).rulings)===approvedRules,'GOOGLE_ACCOUNT_CHANGED','Saved rulings changed before consent');currentGoogle(file,name,a);const latest=load(file).rulings;for(const [key,s]of Object.entries(candidate.google.services)){const revoked=latest[`scope.${name}.${key}`];insist(!scopeDenied(revoked,s.scopes),'GOOGLE_SCOPE_REVOKED','A scope in this consent union was revoked; remove or change that connection first');insist(latest[`service.${name}.${key}`]?.value!=='no','GOOGLE_SERVICE_CONSENT','A service in this consent union was declined');}insist(latest[`production.${a.google.person}`]?.value===a.google.project,'GOOGLE_PROJECT_CONFIRMATION','Production attestation changed before consent');insist(!['admin','unknown'].includes(latest[`blocked.${name}`]?.value),'GOOGLE_BLOCK_REVIEW_REQUIRED','Consent was blocked before this attempt');
    insist(JSON.stringify(accountAt(load(file),name).google)===baseline,'GOOGLE_ACCOUNT_CHANGED','Account changed before consent; no OAuth started');
    const gsite=Object.entries(a.sites).find(([,s])=>s.identity?.kind==='google'&&s.identity.expected?.name?.toLowerCase()===a.google.expectedEmail.toLowerCase());
    insist(gsite,'GOOGLE_BROWSER_IDENTITY','Enroll a Google browser identity matching this API account before consent');
    const proof=await d.check(file,name,a,gsite[0]),bin=await d.install({root:d.root}),env=isolatedEnv(home),client=read(path.join(env.GOOGLE_WORKSPACE_CLI_CONFIG_DIR,'client_secret.json')).installed;
    const session=`oauth-${name}-${crypto.randomUUID()}`,args=['browser','--session',session,'--window','background'];let opened=false,success=false;
    protectSession(a,session,'oauth',d.root);
    const saved=new Map();for(const leaf of ['credentials.enc','credentials.json','.encryption_key','token_cache.json']){const p=path.join(env.GOOGLE_WORKSPACE_CLI_CONFIG_DIR,leaf);if(fs.existsSync(p)){privateStat(p,false);saved.set(leaf,fs.readFileSync(p));}}
    try {
      currentGoogle(file,name,a);insist(JSON.stringify(load(file).rulings)===ruleBaseline,'GOOGLE_ACCOUNT_CHANGED','Saved rulings changed during browser proof; no OAuth listener was started');
      await d.login(bin,env,scopes,client.client_id,async url=>{
        await stateLocked(bridgeOperation(a),async()=>{await d.navigate(a,args,url,proof.contextId,d.oc);opened=true;await d.launch(file,name,'show');});
      },{observe:()=>d.oc(a,[...args,'eval',oauthOutcome],proof.contextId)});
      inspectTree(home);
      const cache=path.join(env.GOOGLE_WORKSPACE_CLI_CONFIG_DIR,'token_cache.json');
      if(fs.existsSync(cache)){privateStat(cache,false);fs.renameSync(cache,`${cache}.before-${crypto.randomUUID()}`);}
      const report=await gwsJSON(bin,['auth','status'],env,d.execute);
      candidate.google.services[service].connectedAt=now();
      const result=judgeGoogle(candidate,service,report);
      await mutate(file,name,(fresh,data)=>{
        insist(JSON.stringify(fresh.google)===baseline,'GOOGLE_ACCOUNT_CHANGED','Google account changed during consent; check before recording');
        insist(JSON.stringify(data.rulings)===ruleBaseline,'GOOGLE_ACCOUNT_CHANGED','Saved rulings changed during consent; no connection was recorded');fresh.google=candidate.google;});success=true;
      return {account:name,...result,cacheReset:true};
    } catch(e){
      for(const leaf of ['credentials.enc','credentials.json','.encryption_key','token_cache.json']){const p=path.join(env.GOOGLE_WORKSPACE_CLI_CONFIG_DIR,leaf);if(saved.has(leaf))fs.writeFileSync(p,saved.get(leaf),{mode:0o600});else if(fs.existsSync(p)){privateStat(p,false);fs.unlinkSync(p);}}
      if(['GOOGLE_ADMIN_BLOCKED','GOOGLE_CONSENT_TIMEOUT'].includes(e.code))await remember(file,`blocked.${name}`,e.code==='GOOGLE_ADMIN_BLOCKED'?'admin':'unknown');
      throw e;
    } finally {
      // On failure the person's tab remains available for inspection. Never click a consent button.
      if(opened)await stateLocked(bridgeOperation(a),async()=>{try{if(success)await d.oc(a,[...args,'close'],proof.contextId);}finally{if(a.bridge.offscreen)await d.launch(file,name,'park');}});
    }
  });
}

const consoleSteps={
  project:{path:'/projectcreate',fields:['Project name','Project ID'],clicks:['Create']},
  api:{path:'/apis/library/',fields:[],clicks:['Enable']},
  consent:{path:'/auth/overview',fields:['App name','User support email','Developer contact information'],clicks:['Get started','External','Internal','Next','Create','Save']},
  desktop:{path:'/auth/clients',fields:['Name'],clicks:['Create client','Application type','Desktop app','Create']},
  production:{path:'/auth/audience',fields:[],clicks:[]},
  iam:{path:'/iam-admin/iam',fields:[],clicks:[]},
};
export async function consoleStep(file,name,a,{step,api,field,value,click:label},deps={}) {
  enabled(file);
  insist(a.google,'GOOGLE_OPT_IN_REQUIRED','Explain Google API control and record the person’s opt-in first');drivable(a);
  if(!local(a.machine))return delegateGoogle(a,name,'console',['--step',step,...Object.entries({api,field,value,click:label}).filter(([,v])=>v!==undefined).flatMap(([k,v])=>['--'+k,v])],deps.delegate);
  const rule=consoleSteps[step];insist(rule,'GOOGLE_CONSOLE_STEP','Choose project, api, consent, desktop, production or iam');
  if(step==='project'){const data=load(file);insist(!a.google.project&&!data.rulings[`project.${a.google.person}`]&&!Object.values(data.accounts).some(other=>other.google?.person===a.google.person&&other.google.project),'GOOGLE_PROJECT_EXISTS','This person already has a project; reuse it');}
  insist(!field||rule.fields.includes(field)&&typeof value==='string'&&value.length>0&&value.length<=200&&!/[\r\n]/.test(value),'GOOGLE_CONSOLE_FIELD','Only named non-credential setup fields can be filled');
  insist(!label||rule.clicks.includes(label),'GOOGLE_HUMAN_GATE','This control is the person’s step; do not drive consent, publishing, IAM grants or secret downloads');
  insist(!(field&&label)&&(!value||field),'GOOGLE_CONSOLE_INPUT','Choose at most one named field or click per invocation');
  insist(step==='project'||a.google.project,'GOOGLE_PROJECT_REQUIRED','Record a project before project-specific console steps');
  insist(step!=='project'||!label&&!field,'GOOGLE_HUMAN_GATE','The person creates the project; no Console mutation is allowed before its project is recorded');
  const project=a.google.project;
  if(project)insist(load(file).rulings[`project.${a.google.person}`]?.value===project,'GOOGLE_PROJECT_MISMATCH','The saved ownership ruling does not match the Console project');
  const url=new URL('https://console.cloud.google.com'+rule.path);
  if(step==='api'){insist(/^[a-z][a-z0-9-]*\.googleapis\.com$/.test(api||''),'GOOGLE_API_REQUIRED','Supply the one API to enable');url.pathname+=api;}
  if(project)url.searchParams.set('project',project);
  url.searchParams.set('authuser',a.google.expectedEmail);
  const d={root:HOME,guard:privateHolder,check,verify,oc:ocPrivate,navigate,...deps};await d.guard(a.machine);
  const gsite=Object.entries(a.sites).find(([,s])=>s.identity?.kind==='google'&&s.identity.expected?.name?.toLowerCase()===a.google.expectedEmail.toLowerCase());
  insist(gsite,'GOOGLE_BROWSER_IDENTITY','Enroll the intended Google email in this bridge first');
  const proof=await d.check(file,name,a,gsite[0]);
  let humanLease;
  const result=await stateLocked(bridgeOperation(a),async()=>{
    currentGoogle(file,name,a);
    const session=`google-console-${name}`,args=['browser','--session',session,'--window','background'];
    insist(!sessionState(a,session,d.root).pendingAction&&!sessionState(a,session,d.root).humanActive,'GOOGLE_CONSOLE_PENDING','Finish the existing human handoff before another Console action');
    setSessionState(a,session,{sourceFile:canonical(file),sourceAccount:name,handoffFile:null},d.root);
    if(step==='desktop')protectSession(a,`google-console-${name}`,'desktop',d.root);
    if(!label&&!field)await d.navigate(a,args,url.href,proof.contextId,d.oc);
    const checkLocation=async()=>{
      const raw=await d.oc(a,[...args,'get','url'],proof.contextId);let current;try{current=new URL(raw);}catch{}
      const knownPath=step==='desktop'?current?.pathname===url.pathname||current?.pathname===url.pathname+'/create':current?.pathname===url.pathname;
      insist(!current||![...current.searchParams.keys()].some(k=>/secret|token|password|authorization|^code$|^state$/i.test(k)),'GOOGLE_CONSOLE_LOCATION','Console location contains private authorization data; no page content was read');
      insist(current?.origin===url.origin&&knownPath&&(!project||current.searchParams.get('project')===project)&&current.searchParams.get('authuser')===a.google.expectedEmail,'GOOGLE_CONSOLE_LOCATION','Console tab is not on this account, project and setup step; open and inspect it first');
    };
    await checkLocation();
    currentGoogle(file,name,a);
    if(label||field){
      setSessionState(a,session,{pendingAction:{step,kind:label?'click':'fill'}},d.root);
      // The caller reads state and uses accessible names; no coordinates or arbitrary selectors.
      const role=label?({'External':'radio','Internal':'radio','Application type':'combobox','Desktop app':'option'}[label]||'button'):'textbox';
      await d.oc(a,[...args,label?'click':'fill','--role',role,'--name',label||field,...(field?[value]:[])],proof.contextId);
    }
    // A snapshot of Desktop clients can contain a secret. Only return the state on other steps.
    if(step==='desktop'){
      if(label!=='Create')setSessionState(a,session,{pendingAction:null},d.root);
      await (d.launch||launch)(file,name,'show');
      setSessionState(a,session,{humanActive:true},d.root);humanLease=[file,name,a,session,proof.contextId,d.root];
      return {step,gate:'Private human lease: choose/download the client yourself. Finish the handoff explicitly; no page-reading verb can expose this tab.'};
    }
    if(label||field){try{await checkLocation();}catch(e){e.detail={actionAttempted:true,retry:false,unverified:true,needsHuman:true};throw e;}}
    setSessionState(a,session,{sensitive:false,pendingAction:null},d.root);
    if(['production','iam','consent','project'].includes(step)){
      await (d.launch||launch)(file,name,'show');setSessionState(a,session,{humanActive:true},d.root);humanLease=[file,name,a,session,proof.contextId,d.root];
    }
    return {step,url:url.href,content:untrusted(await d.oc(a,[...args,'state'],proof.contextId),'google-console'),...(step==='production'||step==='iam'?{gate:'Person approval required here; no automated publish or IAM grant.'}:{})};
  });
  if(humanLease)await (d.handoff||(await import('./human-lease.mjs')).handoff)(...humanLease);
  return result;
}
