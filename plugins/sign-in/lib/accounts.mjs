import fs from 'node:fs';
import path from 'node:path';
import { HOME, machineId, insist, slug, webURL, read, locked, atomicJSON, writeNew, run, json, opencli,grantKey,local } from './core.mjs';
import { identityFields, kinds, validMarker, identityAssurance } from '../browser/identity.mjs';
import { validateGoogle, googleService, validateRulings } from './google-contract.mjs';
import {upgradeRulings} from './rulings.mjs';
export const ACTIONS = ['read', 'export', 'edit', 'fill', 'post', 'submit', 'send', 'delete', 'follow', 'buy', 'upload'];
export function canonical(file,seen=new Set()) {
  const full=path.resolve(file);insist(!seen.has(full),'PROFILE_PATH','Profile symlink cycle');seen.add(full);
  try{return fs.realpathSync(full);}catch(e){if(!['ENOENT','ENOTDIR'].includes(e.code))throw e;}
  try{if(fs.lstatSync(full).isSymbolicLink())return canonical(path.resolve(path.dirname(full),fs.readlinkSync(full)),seen);}catch(e){if(e.code!=='ENOENT')throw e;}
  const parent=path.dirname(full);insist(parent!==full,'PROFILE_PATH','Cannot resolve profile root');return path.join(canonical(parent,seen),path.basename(full));
}
export function validate(file) {
  insist(file?.version === 1 && Object.keys(file).every(k=>['version','accounts','rulings'].includes(k)) && file.accounts && typeof file.accounts === 'object' && !Array.isArray(file.accounts), 'ACCOUNTS_FORMAT', 'Expected version:1, accounts and optional rulings; unknown fields are refused');
  validateRulings(file.rulings);
  const names=new Set(), ports=new Set(), profiles=new Set(), projects=new Map(), people=new Map(), googleEmails=new Set();
  for(const [name,a] of Object.entries(file.accounts)) {
    const keys=(o,allowed)=>insist(o&&Object.keys(o).every(k=>allowed.includes(k)),'ACCOUNTS_FORMAT','Unknown account field; credentials and arbitrary readers are not supported');
    keys(a,['label','machine','bridge','primary','sites','permissions','google']);
    insist(slug(name)&&a&&a.machine&&/^[a-f0-9]{64}$/.test(a.machine.id||'')&&path.isAbsolute(a.machine.cli||''), 'ACCOUNTS_FORMAT','Account needs a stable machine id and absolute CLI path');
    const b=a.bridge;
    keys(a.machine,['id','exclusive','ssh','cli','accounts','label']);keys(b,['name','profileDir','port','offscreen','headless','driveBlocked','contextIds','migrated']);
    insist(b&&b.name===name&&path.isAbsolute(b.profileDir||'')&&Number.isInteger(b.port)&&b.port>=1024&&b.port<=65535,'ACCOUNTS_FORMAT','Bridge name must match account; absolute profile and valid port required');
    for(const [set,value]of [[names,name],[ports,b.port],[profiles,canonical(b.profileDir)]]){const key=a.machine.id+':'+value;insist(!set.has(key),'ACCOUNTS_CONFLICT','Account name, port or profile collides');set.add(key);}
    insist(a.sites&&Object.hasOwn(a.sites,a.primary),'ACCOUNTS_FORMAT','Choose a primary site');
    for(const [site,s]of Object.entries(a.sites)){
      keys(s,['signInUrl','identity','phone']);
      insist(slug(site)&&webURL(s.signInUrl),'ACCOUNTS_FORMAT','HTTPS sign-in URL required');
      if(s.identity!==null){const i=s.identity;keys(i,['kind','url','expected','recordedAt']);insist(i&&kinds.has(i.kind)&&webURL(i.url)&&!i.read,'ACCOUNTS_FORMAT','Choose a named probe or the restricted generic marker check');
        insist(!i.recordedAt||Number.isFinite(Date.parse(i.recordedAt)),'ACCOUNTS_FORMAT','Identity recording needs a valid date');
        for(const [key,v]of Object.entries(i.expected||{}))insist(identityFields.has(key)&&['string','boolean'].includes(typeof v)&&v!=='','ACCOUNTS_FORMAT','Only identity facts may be expected');
        if(i.kind==='generic'){
          insist(new URL(i.url).origin===new URL(s.signInUrl).origin,'ACCOUNTS_FORMAT','Generic sign-in and observation must share an origin');
          insist(i.expected===undefined||i.expected&&typeof i.expected==='object'&&!Array.isArray(i.expected),'ACCOUNTS_FORMAT','Generic expected facts must be a marker object');
          insist(Object.keys(i.expected||{}).every(k=>k==='marker')&&(!Object.hasOwn(i.expected||{},'marker')||validMarker(i.expected.marker)),'ACCOUNTS_FORMAT','Generic lower-assurance sites expect only a person-confirmed visible marker');
        }else insist(!Object.hasOwn(i.expected||{},'marker'),'ACCOUNTS_FORMAT','Named probes cannot use a generic marker');}
      for(const url of [s.signInUrl,s.identity?.url,s.identity?.expected?.profile].filter(Boolean)){
        insist(webURL(url),'ACCOUNTS_FORMAT','Identity links must be HTTPS without credentials');const u=new URL(url);
        insist(![...u.searchParams.keys()].some(k=>/token|password|passcode|secret|session|authorization|^code$/i.test(k)),'ACCOUNTS_FORMAT','Ephemeral authentication values cannot be stored in account URLs');
        if(url===s.identity?.expected?.profile)insist(!u.search&&!u.hash,'ACCOUNTS_FORMAT','Expected profile links must omit query and fragment');
      }
      insist(!s.phone||s.phone.kind==='approve','ACCOUNTS_FORMAT','Only approve-on-phone is supported');
    }
    if(a.google){
      const g=a.google;validateGoogle(g);
      const emailKey=g.expectedEmail.toLowerCase();insist(!googleEmails.has(emailKey),'GOOGLE_CONFLICT','This email has multiple registered holders; the person must choose one before use');googleEmails.add(emailKey);
      if(g.project){insist(!people.has(g.person)||people.get(g.person)===g.project,'GOOGLE_CONFLICT','One project per person');insist(!projects.has(g.project)||projects.get(g.project)===g.person,'GOOGLE_CONFLICT','Project belongs to another person');people.set(g.person,g.project);projects.set(g.project,g.person);}
    }
    insist(Array.isArray(a.permissions),'ACCOUNTS_FORMAT','Explicit permissions array required');
    for(const p of a.permissions)insist(p&&Object.keys(p).every(k=>['via','site','action','resource','userWords','grantedAt'].includes(k))&&(p.via==='google'?Object.hasOwn(a.google?.services||{},p.site):!p.via&&Object.hasOwn(a.sites,p.site))&&ACTIONS.includes(p.action)&&!['read','export'].includes(p.action)&&typeof p.resource==='string'&&p.resource.trim()&&typeof p.userWords==='string'&&p.userWords.trim()&&p.userWords.length<=500&&!p.grantedBy&&Number.isFinite(Date.parse(p.grantedAt)),'ACCOUNTS_FORMAT','Standing grants require user-entered words and exact resource');
  }
  return file;
}
export const accountsPath = supplied=>path.resolve(supplied||path.join(HOME,'accounts.json'));
export const load=file=>upgradeRulings(validate(read(file)));
export function accountAt(data,name){insist(Object.hasOwn(data.accounts,name),'ACCOUNT_UNKNOWN','Unknown account');return data.accounts[name];}
export function siteAt(a,site=a.primary){insist(Object.hasOwn(a.sites,site),'SITE_UNKNOWN','Unknown site');return [site,a.sites[site]];}
export function drivable(a){insist(!a.bridge.driveBlocked,'DRIVE_BLOCKED','This account is drive-blocked; only the user can change that ruling');}
export async function addSite(file,name,site,origin,{exclusive=false}={}){
  insist(slug(site)&&webURL(origin),'USAGE','Supply --site NAME and --origin HTTPS_ORIGIN');
  const url=new URL(origin);
  insist(url.pathname==='/'&&!url.search&&!url.hash,'USAGE','Supply only an HTTPS origin, without a path, query or fragment');
  name=name||site;insist(slug(name),'USAGE','Choose a login name');
  return locked(file,()=>{
    const data=fs.existsSync(file)?load(file):{version:1,accounts:{}};
    insist(!Object.hasOwn(data.accounts,name),'ACCOUNT_EXISTS','Login exists; choose another name for a second identity');
    const locals=Object.values(data.accounts).filter(a=>local(a.machine));
    insist(exclusive||locals.some(a=>a.machine.exclusive),'PRIVATE_HOLDER_REQUIRED','Confirm this private machine with --private-machine');
    const profileDir=path.join(path.dirname(file),'profiles',name);
    insist(!fs.existsSync(profileDir),'MIGRATION_REQUIRED','A profile already exists here; migrate it rather than reuse it');
    let port=19340;while(locals.some(a=>a.bridge.port===port))port++;
    insist(port<=65535,'ACCOUNTS_CONFLICT','No configured bridge port remains');
    const identity={kind:'generic',url:url.origin+'/',expected:{}};
    data.accounts[name]={label:name,machine:{id:machineId(),exclusive:true,ssh:null,cli:path.join(HOME,'runtime/scripts/sign-in.mjs'),accounts:path.resolve(file)},bridge:{name,profileDir,port,offscreen:true,driveBlocked:false,contextIds:[]},primary:site,sites:{[site]:{signInUrl:identity.url,identity}},permissions:[]};
    validate(data);if(fs.existsSync(file))atomicJSON(file,data);else writeNew(file,data);
    return {login:name,account:name,site,origin:url.origin,...identityAssurance(identity),signInRequired:true};
  });
}
export async function splitAccount(file,name,{keep,names={}}={}){
  return locked(file,async()=>{
    const data=load(file),a=accountAt(data,name),sites=Object.keys(a.sites);
    insist(local(a.machine),'HUMAN_STEP_ON_HOLDER','Split on the holding machine');
    if(sites.length===1)return {account:name,unchanged:true,profilePreserved:true};
    insist(keep&&Object.hasOwn(a.sites,keep),'SPLIT_CHOICE_REQUIRED','Choose --keep SITE for the existing profile; other sites get empty profiles and need sign-in');
    insist(!a.google||a.sites[keep].identity?.kind==='google','GOOGLE_SPLIT_KEEP','Keep the Google site on this profile to preserve its optional API configuration');
    insist(names&&typeof names==='object'&&!Array.isArray(names)&&Object.entries(names).every(([site,name])=>site!==keep&&sites.includes(site)&&slug(name)),'USAGE','Split names map other sites to login names');
    insist(!(await import('./bridge-lifecycle.mjs')).bridgeWork(a).reasons.length,'BRIDGE_LEASED','Finish active work before splitting this profile');
    const others=sites.filter(site=>site!==keep),created=[];
    for(const site of others){
      const login=names[site]||`${name}-${site}`;insist(slug(login)&&!Object.hasOwn(data.accounts,login),'ACCOUNT_EXISTS','A split login name is invalid or already exists');
      const profileDir=path.join(path.dirname(file),'profiles',login);insist(!fs.existsSync(profileDir),'MIGRATION_REQUIRED','A split profile already exists; nothing was changed');
      let port=19340;while(Object.values(data.accounts).some(b=>b.machine.id===a.machine.id&&b.bridge.port===port))port++;
      const s=structuredClone(a.sites[site]);if(s.identity){s.identity.expected={};delete s.identity.recordedAt;}
      data.accounts[login]={label:login,machine:structuredClone(a.machine),bridge:{name:login,profileDir,port,offscreen:a.bridge.offscreen,driveBlocked:a.bridge.driveBlocked,contextIds:[]},primary:site,sites:{[site]:s},permissions:[]};
      created.push({login,site,signInRequired:true});
    }
    a.sites={[keep]:a.sites[keep]};a.primary=keep;a.permissions=a.permissions.filter(p=>p.via==='google'||p.site===keep);
    validate(data);atomicJSON(file,data);return {account:name,kept:keep,profilePreserved:true,created,credentialsCopied:false};
  });
}
export function permission(a,site,action,resource,via){
  const s=via==='google'?googleService(a,site):siteAt(a,site)[1];insist(!via||via==='google','USAGE','Unknown access route');insist(ACTIONS.includes(action),'ACTION_UNKNOWN','Unknown action');
  const standing=resource&&a.permissions.find(p=>p.site===site&&p.action===action&&p.resource===resource&&p.via===via);
  const needsUserOK=!['read','export'].includes(action)&&!standing;
  return {site,action,resource:resource||null,...(via?{via}:{}),...identityAssurance(s.identity),needsUserOK,reason:standing?'standing-permission':needsUserOK?'ask-at-this-moment':'read-only',advisory:true,identityReady:via==='google'?!!s.connectedAt:!!s.identity&&(!!s.identity.recordedAt||Object.keys(s.identity.expected||{}).some(k=>!['authenticated','site'].includes(k))),driveBlocked:!!a.bridge.driveBlocked};
}
export async function create(file,name,a,{execute=run}={}){
  insist(slug(name)&&!a.permissions?.length&&!a.google,'USER_GRANT_REQUIRED','Create accepts no standing grants or Google opt-in; use the human steps');
  validate({version:1,accounts:{[name]:a}});
  insist(Object.keys(a.sites).length===1,'ONE_SITE_PER_LOGIN','Create one login per website; migrate legacy profiles explicitly');
  insist(!a.bridge.migrated&&!a.bridge.contextIds?.length,'MIGRATION_REQUIRED','New accounts cannot import alias lineage; use migrate');
  insist(!fs.existsSync(a.bridge?.profileDir||'')||fs.readdirSync(a.bridge.profileDir).length===0,'MIGRATION_REQUIRED','An existing profile must be migrated with its breaker, not reused as a new account');
  const aliases=json((await execute(opencli(),['profile','list'])).stdout).profiles||[];
  insist(!aliases.some(p=>p.alias===name||p.contextId===name||p.alias===a.bridge?.name),'ALIAS_COLLISION','Name is already an OpenCLI profile selector; migrate it instead');
  return locked(file,async()=>{const data=fs.existsSync(file)?load(file):{version:1,accounts:{}};insist(!Object.hasOwn(data.accounts,name),'ACCOUNT_EXISTS','Account exists');data.accounts[name]=a;validate(data);if(fs.existsSync(file))atomicJSON(file,data);else writeNew(file,data);return {account:name,file,bridgeCreatedOnOpen:true};});
}
export async function recordContext(file,name,contextId){return locked(file,async()=>{const data=load(file),a=accountAt(data,name);a.bridge.contextIds=[...new Set([...(a.bridge.contextIds||[]),contextId])];atomicJSON(file,data);});}
export async function grant(file,name,{site,action,resource,via,ask,prove}){
  const data=load(file),a=accountAt(data,name);permission(a,site,action,resource,via);drivable(a);
  insist(ACTIONS.includes(action)&&!['read','export'].includes(action)&&resource?.trim(),'GRANT_SCOPE','An exact write action and resource are required');
  const key=grantKey(name,{site,action,resource,via}),prior=data.rulings[key]?.value;
  if(['no','ask'].includes(prior))return {account:name,recorded:false,policy:prior,ruling:key,needsUserOK:true};
  await prove();if(a.permissions.some(p=>p.site===site&&p.action===action&&p.resource===resource&&p.via===via))return {account:name,site,action,resource,recorded:true,reused:true};
  const baseline=load(file),before=JSON.stringify([baseline.accounts[name],baseline.rulings[key]]);
  const userWords=await ask(`Standing permission for ${name}/${site} via ${via||'browser'}, action ${action}, resource ${JSON.stringify(resource)}. Start your own affirmative words with "I allow ", or answer no / ask (ask per action). No credentials: `);
  if(['no','ask'].includes(userWords)){await (await import('./rulings.mjs')).remember(file,key,userWords);return {account:name,recorded:false,policy:userWords,ruling:key,needsUserOK:true};}
  insist(typeof userWords==='string'&&/^I allow \S/.test(userWords)&&userWords.length<=500,'USER_GRANT_REQUIRED','Use explicit affirmative words beginning I allow, or no / ask');
  return locked(file,async()=>{const fresh=load(file);insist(JSON.stringify([fresh.accounts[name],fresh.rulings[key]])===before,'ACCOUNT_CHANGED','Account or permission ruling changed while awaiting your answer; no grant was recorded');fresh.accounts[name].permissions.push({site,action,resource,...(via?{via}:{}),userWords,grantedAt:new Date().toISOString()});fresh.rulings[key]={value:'yes',at:new Date().toISOString()};validate(fresh);atomicJSON(file,fresh);return {account:name,site,action,resource,ruling:key,recorded:true};});
}
export async function revokeGrant(file,name,{site,action,resource,via}){insist(site&&action&&resource,'GRANT_SCOPE','Choose an exact site, action and resource');return locked(file,()=>{const data=load(file),a=accountAt(data,name),key=grantKey(name,{site,action,resource,via});a.permissions=a.permissions.filter(p=>!(p.site===site&&p.action===action&&p.resource===resource&&p.via===via));data.rulings[key]={value:'ask',at:new Date().toISOString()};validate(data);atomicJSON(file,data);return {revoked:true,ruling:key};});}
