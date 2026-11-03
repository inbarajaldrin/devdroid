#!/usr/bin/env node
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { createInterface } from 'node:readline/promises';
import { insist, read, local, machineId, atomicJSON, locked, safeMessage,processStart,assertReadable } from '../lib/core.mjs';
import { load, validate, accountsPath, accountAt, siteAt, permission, create, grant,addSite,splitAccount,drivable } from '../lib/accounts.mjs';
import { check, open, bridge, window, propose, drive, finish, launch, signInFile,handoffOwner,enroll,readCommand,cancelHandoff } from '../lib/sign-in.mjs';
import {notificationRequest,validateNotificationRequest} from '../lib/notifications.mjs';
import {withBridge} from '../lib/bridge-lifecycle.mjs';
import {identityAssurance} from '../browser/identity.mjs';
import { migrate } from '../lib/migrate.mjs';
import { setupInterview,scopeDenied } from '../lib/google-contract.mjs';
import {setupChoices,remember} from '../lib/rulings.mjs';
export const HELP=`sign-in [--accounts FILE] [--on-holder EXPECTED_ID] COMMAND
  machine
  migrate --registry FILE [--identities FILE] [--sites-from FILE] [--private-machine]
  list | overview | bridge-up-all
  check|observe|finish|open|sign-in-status|cancel-sign-in ACCOUNT [--site SITE]
    open: [--device laptop|phone] [--approve] [--ttl SECONDS] [--inspect-only] [--notify NAME_OR_ID]
  bridge|show|park|wake|bridge-down ACCOUNT
  drive LOGIN [--site SITE] -- browser --session TASK COMMAND ...
  create ACCOUNT --from ACCOUNT_JSON
  add-site [LOGIN] --site SITE --origin HTTPS_ORIGIN [--private-machine]
  split ACCOUNT --keep SITE [--names JSON_FILE]
  grant ACCOUNT --site SITE --action ACTION --resource EXACT_RESOURCE (human terminal)
  enroll LOGIN [--site SITE] (optional: re-record observable identity, no questions)
  propose ACCOUNT [--site SITE] [--via google]
  permission ACCOUNT --site SITE --action ACTION [--resource EXACT_RESOURCE] [--via google]
  setup [--sites WORDS|none|ask] [--google yes|no|ask] [--accounts-from FILE]
    Saves each human answer once before installing. Answers are choices, not grants.
  google explain
  google opt-in ACCOUNT (human terminal; can be added after setup)
  google project ACCOUNT --project OWN_PROJECT [--client-file PRIVATE_DOWNLOAD] (human)
  google console ACCOUNT --step project|api|consent|desktop|production|iam [--api API]
    [--field ACCESSIBLE_NAME --value TEXT | --click ACCESSIBLE_NAME]
  google connect ACCOUNT --service WORD --api API --scope EXACT_SCOPE [--replace-scope OLD_SCOPE] (human)
  google check ACCOUNT --service WORD
  google read ACCOUNT --service WORD -- RESOURCE... get|list [--params JSON]
  grant ACCOUNT ... --via google grants API actions, separately from browser actions.
  doctor
  accounts-upgrade
  ruling KEY [--value ANSWER] [--remove] (human terminal; yes/no/ask or a fact)
  google change ACCOUNT [--email EMAIL | --client-file PRIVATE_DOWNLOAD | --block admin|unknown|clear]
  google revoke ACCOUNT [--service WORD [--scope SCOPE | --action ACTION --resource EXACT_RESOURCE]]
  google sync ACCOUNT (controller: copy nonsecret metadata from its proven holder)
  google console ACCOUNT --finish (human: complete handoff and park)
  revoke ACCOUNT --site SITE --action ACTION --resource EXACT_RESOURCE [--via google] (human)
Permissions are advisory rules for agents, not a shell security boundary.
No command enters credentials or writes instructions. QR-on-the-same-phone is unsupported.`;
export function parse(argv){
  const args=[...argv];let file,onHolder=false;
  while(['--accounts','--on-holder'].includes(args[0])){if(args.shift()==='--accounts'){insist(!file,'USAGE','Duplicate --accounts');file=args.shift();insist(file&&!file.startsWith('-'),'USAGE','--accounts needs a file');}else{insist(!onHolder,'USAGE','Duplicate --on-holder');onHolder=args.shift();insist(/^[a-f0-9]{64}$/.test(onHolder||''),'USAGE','Expected stable holder ID required');}}
  const cmd=args.shift(),definitions={
    machine:[0,[]],migrate:[0,['registry','identities','sites-from','private-machine']],list:[0,[]],overview:[0,[]],'bridge-up-all':[0,[]],
    check:[1,['site']],observe:[1,['site']],open:[1,['site','device','approve','ttl','inspect-only','notify']],bridge:[1,[]],window:[1,['site','notify','notification-request']],finish:[1,['site']],
    'sign-in-status':[1,['site']],'cancel-sign-in':[1,['site']],drive:[1,['site','policy']],show:[1,[]],park:[1,[]],wake:[1,[]],'bridge-down':[1,[]],
    create:[1,['from']],'add-site':[-1,['site','origin','private-machine']],split:[1,['keep','names']],grant:[1,['site','action','resource','via']],revoke:[1,['site','action','resource','via']],enroll:[1,['site']],propose:[1,['site','via']],permission:[1,['site','action','resource','via']],setup:[0,['accounts-from','sites','google']],doctor:[0,[]],
    'accounts-upgrade':[0,[]],ruling:[1,['value','remove']],
    google:[args[0]==='explain'?1:2,['service','api','scope','replace-scope','project','client-file','step','field','value','click','expect','email','block','action','resource','finish']],
  };
  insist(definitions[cmd],'USAGE',HELP);const [count,allowed]=definitions[cmd],values=[],options={};let rest=[];
  while(args.length){const item=args.shift();if(item==='--'){insist(cmd==='drive'||cmd==='google','USAGE','Only drive and google read take trailing arguments');rest=args.splice(0);break;}
    if(item.startsWith('--')){const key=item.slice(2);insist(allowed.includes(key)&&!Object.hasOwn(options,key),'USAGE','Unknown or duplicate option');if(['approve','private-machine','inspect-only','remove','finish'].includes(key))options[key]=true;else{const v=args.shift();insist(v&&!v.startsWith('--'),'USAGE','Option needs a value');options[key]=v;}}
    else values.push(item);
  }
  insist(count===-1?values.length<=1:values.length===count,'USAGE',HELP);
  if(cmd==='google'){
    const allowedRoutes={explain:[], 'opt-in':[],project:['project','client-file'],console:['step','api','field','value','click','expect','finish'],connect:['service','api','scope','replace-scope'],check:['service','expect'],read:['service','expect'],change:['email','client-file','block'],revoke:['service','scope','action','resource'],metadata:[],sync:[]};
    insist(Object.hasOwn(allowedRoutes,values[0])&&Object.keys(options).every(k=>allowedRoutes[values[0]].includes(k))&&(!rest.length||values[0]==='read'),'USAGE','Unknown Google route or option');
  }
  return {cmd,file:accountsPath(file),name:cmd==='google'?values[1]:values[0],route:cmd==='google'?values[0]:undefined,options,onHolder,rest};
}
async function human(work){insist(process.stdin.isTTY&&process.stdout.isTTY,'HUMAN_TERMINAL_REQUIRED','This permission or Google consent step belongs to the person in their terminal');const terminal=createInterface({input:process.stdin,output:process.stdout});try{return await work(prompt=>terminal.question(prompt));}finally{terminal.close();}}
async function dispatch(argv){
  if(!argv.length||argv.length===1&&argv[0]==='--help')return {usage:HELP};
  const {cmd,file,name,route,options:o,onHolder,rest}=parse(argv);
  insist(!onHolder||machineId()===onHolder,'WRONG_MACHINE','Delegation reached a different platform ID');
  if(cmd==='machine')return {id:machineId(),note:'Stable platform identifier; hostnames are labels only'};
  if(cmd==='migrate'){insist(o.registry,'USAGE','Supply --registry');return migrate(file,o.registry,{identitiesFile:o.identities,sitesFile:o['sites-from'],exclusive:!!o['private-machine']});}
  if(cmd==='setup'||cmd==='doctor'){
    if(cmd==='setup'){
      const r=fs.existsSync(file)?load(file).rulings:{};
      if(!r['setup.sites']||r['setup.sites'].value==='ask'||!r['setup.google']){
        // The person's answers given in chat, passed as flags, are saved like terminal answers.
        if(!process.stdin.isTTY&&o.sites&&o.google){const interview=await setupChoices(file,{sites:o.sites,google:o.google});if(!interview.complete)return interview;const layer=await import('../lib/setup.mjs');return {...await layer.setup(file,o['accounts-from']),interview};}
        if(!process.stdin.isTTY){const value=r['setup.sites']?.value,interview=setupInterview({...o,sites:Array.isArray(value)?value.join(',')||'none':value==='ask'?undefined:o.sites});return {...interview,complete:false,saved:false,requiresHuman:true,humanEntrance:'Ask the person each question in chat, then pass their answers as --sites and --google.'};}
      }
      const interview=r['setup.sites']&&r['setup.sites'].value!=='ask'&&r['setup.google']?{complete:true,sites:r['setup.sites'].value,google:r['setup.google'].value,saved:true}:await human(ask=>setupChoices(file,{ask,...o}));if(!interview.complete)return interview;const layer=await import('../lib/setup.mjs');return {...await layer.setup(file,o['accounts-from']),interview};
    }
    return (await import('../lib/setup.mjs')).doctor(file);
  }
  if(cmd==='google'&&route==='explain')return (await import('../lib/google.mjs')).explanation;
  if(cmd==='accounts-upgrade')return locked(file,()=>{const data=load(file);atomicJSON(file,data);return {upgraded:true,accounts:Object.keys(data.accounts),rulingsSaved:true,profilesMoved:false,needsSplit:Object.entries(data.accounts).filter(([,a])=>Object.keys(a.sites).length>1).map(([account])=>({account,command:`split ${account} --keep SITE`}))};});
  if(cmd==='ruling'&&!o.value&&!o.remove&&fs.existsSync(file)){const records=load(file).rulings;if(Object.hasOwn(records,name))return {ruling:name,record:records[name]};}
  if(cmd==='ruling')return human(async ask=>{
    const parts=name.split('.'),d=fs.existsSync(file)?load(file):null;
    if(['email','blocked','service','scope','instructions','grant'].includes(parts[0])&&d?.accounts[parts[1]])insist(local(d.accounts[parts[1]].machine),'HUMAN_STEP_ON_HOLDER','Change this account ruling on its holder, then sync the controller');
    if(o.remove)return locked(file,()=>{const d=load(file);delete d.rulings[name];validate(d);atomicJSON(file,d);return {removed:name};});
    let value=o.value??await ask(`Saved ruling ${name}. Recommendation: allow only what you want; no and ask are saved too. Answer: `);
    if(name==='setup.sites')value=value==='ask'?'ask':value==='none'?[]:value.split(',');
    return {ruling:name,record:await remember(file,name,value)};
  });
  if(cmd==='create'){insist(o.from,'USAGE','Supply --from');return create(file,name,read(path.resolve(o.from)));}
  if(cmd==='add-site')return addSite(file,name,o.site,o.origin,{exclusive:!!o['private-machine']});
  if(cmd==='split')return splitAccount(file,name,{keep:o.keep,names:o.names?read(path.resolve(o.names)):{}});
  const data=load(file);
  if(cmd==='list')return Object.entries(data.accounts).map(([account,a])=>({account,machine:a.machine.id,driveBlocked:!!a.bridge.driveBlocked,primary:a.primary,legacyMultiSite:Object.keys(a.sites).length>1,...(Object.keys(a.sites).length>1?{splitCommand:`split ${account} --keep SITE`}:{}),sites:Object.entries(a.sites).map(([site,s])=>({site,probe:!!s.identity,probeKind:s.identity?.kind||null,...identityAssurance(s.identity),identityRecorded:!!s.identity?.recordedAt,identityEnrolled:s.identity?.kind!=='generic'&&!!s.identity&&Object.keys(s.identity.expected||{}).some(k=>!['authenticated','site'].includes(k)),...(s.identity?.kind==='generic'?{markerEnrolled:!!s.identity.expected?.marker}:{})})),...(a.google?{google:{person:a.google.person,identity:a.google.expectedEmail,project:a.google.project,services:a.google.services}}:{}),permissions:a.permissions}));
  if(cmd==='overview')return (await import('../browser/ext-bridge.mjs')).command(file,'state');
  if(cmd==='bridge-up-all'){const result=[];for(const [login,a]of Object.entries(data.accounts))if(local(a.machine))result.push(await main(['--accounts',file,'bridge',login]));return result;}
  const a=accountAt(data,name);insist(!onHolder||(local(a.machine)&&machineId()===onHolder),'WRONG_MACHINE','Delegated command reached the wrong stable machine; correct SSH routing');
  if(cmd==='google'){
    const g=await import('../lib/google.mjs');
    const expected=g.googleContract(name,a);
    insist(!o.expect||/^[a-f0-9]{64}$/.test(o.expect)&&o.expect===expected,'GOOGLE_REMOTE_CONTRACT','Controller and holder disagree about the Google account, project or scopes; no operation was performed');
    const reply=result=>o.expect?{googleContract:expected,result}:result;
    if(route==='opt-in')return human(ask=>g.optIn(file,name,{ask}));
    if(route==='project')return human(ask=>g.configureProject(file,name,a,{project:o.project,clientFile:o['client-file'],ask}));
    if(route==='metadata')return g.googleMetadata(file,name,a);
    if(route==='sync')return g.syncGoogle(file,name,a);
    if(route==='console'&&o.finish)return human(async()=>{insist(local(a.machine),'HUMAN_STEP_ON_HOLDER','Finish on the holder');return (await import('../lib/human-lease.mjs')).finishHuman(file,name,a);});
    if(route==='console')return reply(await g.consoleStep(file,name,a,o));
    if(route==='connect')return human(ask=>g.connect(file,name,a,o,{ask}));
    if(['check','read'].includes(route)){const operation=ask=>route==='check'?g.checkGoogle(file,name,a,o.service,{ask}):g.readGoogle(file,name,a,o.service,rest,{ask});const asks=['setup.google',`service.${name}.${o.service}`,`scope.${name}.${o.service}`].some(k=>data.rulings[k]?.value==='ask'||!data.rulings[k]&&!k.startsWith('scope.'));return reply(await (asks?human(operation):operation()));}
    if(route==='change')return human(ask=>g.changeGoogle(file,name,o,{ask}));
    if(route==='revoke')return human(()=>g.revokeGoogle(file,name,o));
  }
  if(cmd==='check'||cmd==='observe')return check(file,name,a,o.site,{observe:cmd==='observe'});
  if(cmd==='open')return open(file,name,a,o.site,o);
  if(cmd==='bridge')return bridge(file,name,a);
  if(cmd==='window')return window(file,name,a,o.site,{request:o['notification-request']?validateNotificationRequest(JSON.parse(o['notification-request'])):notificationRequest(process.env,o.notify)});
  if(cmd==='finish')return finish(file,name,a,o.site);
  if(cmd==='drive'){insist(!o.policy||o.policy==='private-pages-v1','PAGE_POLICY_MISMATCH','Unsupported private-page policy');const result=await drive(file,name,a,o.site,rest);return o.policy?{pagePolicy:'private-pages-v1',result}:result;}
  if(['show','park','wake','bridge-down'].includes(cmd)){if(!local(a.machine))return (await import('../lib/core.mjs')).remote(a,[cmd,name]);return launch(file,name,cmd);}
  if(cmd==='sign-in-status'||cmd==='cancel-sign-in'){
    if(!local(a.machine))return (await import('../lib/core.mjs')).remote(a,[cmd,name,'--site',o.site||a.primary]);
    const [key]=siteAt(a,o.site),stateFile=signInFile(file,name,a,key),state=read(stateFile);
    insist(JSON.stringify(state.owner)===JSON.stringify(handoffOwner(file,name,a,key)),'HANDOFF_OWNER','State belongs to a different account source');
    if(cmd==='cancel-sign-in')await cancelHandoff(stateFile,state,a);
    const current=read(stateFile),dead=current.status==='waiting-for-user'&&(!current.workerStart||processStart(current.pid)!==current.workerStart);
    return {account:name,site:key,status:dead?'attention-required':current.status,code:dead?'KEEPER_STOPPED':current.code,session:current.session,opener:current.opener||null,notify:current.notify||null,notification:current.notification||null};
  }
  if(cmd==='grant'){insist(local(a.machine),'HUMAN_STEP_ON_HOLDER','Run grant in a human terminal on the holder');return human(ask=>grant(file,name,{...o,ask,prove:async()=>o.via==='google'?(await import('../lib/google.mjs')).checkGoogle(file,name,a,o.site):check(file,name,a,o.site)}));}
  if(cmd==='revoke')return human(async()=>{insist(local(a.machine),'HUMAN_STEP_ON_HOLDER','Revoke on the holder');return (await import('../lib/accounts.mjs')).revokeGrant(file,name,o);});
  if(cmd==='enroll')return enroll(file,name,o.site);
  if(cmd==='propose'){insist(!o.via||o.via==='google','USAGE','Unknown access route');const proposals=await import('../lib/sign-in.mjs');if(data.rulings[`instructions.${name}.${o.site||a.primary}`]?.value==='no')return proposals.instructionProposal(name,o.site||a.primary,{},data.rulings);if(o.via==='google'){const proof=await (await import('../lib/google.mjs')).checkGoogle(file,name,a,o.site);return proposals.instructionProposal(name,o.site,proof,data.rulings);}return propose(file,name,a,o.site);}
  if(cmd==='permission'){
    insist(o.site&&o.action,'USAGE','Supply --site and --action');
    if(o.via==='google'&&data.rulings['setup.google']?.value==='no')return {account:name,allowed:false,needsUserOK:true,ask:false,reason:'google-control-declined',advisory:true};
    if(!local(a.machine)){const proof=await (await import('../lib/core.mjs')).remote(a,['permission',name,...Object.entries(o).flatMap(([k,v])=>['--'+k,v])]);insist(proof?.account===name&&typeof proof.needsUserOK==='boolean'&&proof.advisory===true,'PERMISSION_UNVERIFIED','The holder did not return an account permission decision');return proof;}
    if(o.via==='google'){const rule=data.rulings[`service.${name}.${o.site}`],scopes=data.rulings[`scope.${name}.${o.site}`],blocked=data.rulings[`blocked.${name}`]?.value;const denied=rule?.value==='no'||['admin','unknown'].includes(blocked)||scopeDenied(scopes,a.google?.services[o.site]?.scopes||[]);if(denied)return {account:name,allowed:false,needsUserOK:true,ask:false,reason:'google-service-disabled',advisory:true};if(rule?.value==='ask'||data.rulings['setup.google']?.value==='ask'||scopes?.value==='ask'||!rule)return {account:name,allowed:false,needsUserOK:true,ask:true,reason:'google-human-choice-required',advisory:true};}
    return {account:name,...permission(a,o.site,o.action,o.resource,o.via)};
  }
}
export async function main(argv,dependencies={}){
  if(!argv.length||argv.length===1&&argv[0]==='--help')return dispatch(argv);
  const parsed=parse(argv),{cmd,file,name,route,options:o,onHolder,rest}=parsed;
  const managed=['check','observe','open','window','finish','enroll','drive','bridge','show','park','wake','cancel-sign-in','propose','grant'].includes(cmd)||cmd==='google'&&['console','connect'].includes(route);
  if(!managed)return dispatch(argv);
  if(cmd==='open'||cmd==='window')notificationRequest(process.env,o.notify);
  insist(!onHolder||machineId()===onHolder,'WRONG_MACHINE','Delegation reached a different platform ID');
  const a=accountAt(load(file),name);
  if(['grant','propose'].includes(cmd)&&o.via==='google')return dispatch(argv);
  if(cmd==='propose'&&load(file).rulings[`instructions.${name}.${o.site||a.primary}`]?.value==='no')return dispatch(argv);
  if(!local(a.machine))return dispatch(argv);
  if(!['bridge','show','park','wake'].includes(cmd))drivable(a);
  if(['check','observe','open','window','finish','enroll','drive','cancel-sign-in'].includes(cmd))siteAt(a,o.site);
  if(cmd==='drive'){
    const s=siteAt(a,o.site)[1];insist(s.identity,'IDENTITY_UNCONFIGURED','Configure identity before driving');
    const command=readCommand(rest,s);assertReadable(a,command[2]);
    insist(!o.policy||o.policy==='private-pages-v1','PAGE_POLICY_MISMATCH','Unsupported private-page policy');
  }
  // Human-only Google gates refuse before any startup.
  if(cmd==='google'&&(route==='connect'||o.finish))insist(process.stdin.isTTY&&process.stdout.isTTY,'HUMAN_TERMINAL_REQUIRED','Google consent and private handoff completion belong to the person');
  if(cmd==='grant')insist(process.stdin.isTTY&&process.stdout.isTTY,'HUMAN_TERMINAL_REQUIRED','Only the person grants standing permission');
  if(cmd==='open'&&o.device==='phone')return dispatch(argv);
  return (dependencies.withBridge||withBridge)(file,name,a,cmd==='google'?`google-${route}`:cmd,()=>dispatch(argv));
}
if(process.argv[1]&&path.resolve(process.argv[1])===fileURLToPath(import.meta.url))main(process.argv.slice(2)).then(data=>console.log(JSON.stringify({ok:true,data:process.argv.includes('--on-holder')?{holdingMachine:machineId(),result:data}:data},null,2))).catch(e=>{console.error(JSON.stringify({ok:false,code:e.code||'SIGN_IN_FAILED',error:safeMessage(e.message),...(e.detail?{detail:e.detail}:{})},null,2));process.exitCode=1;});
