import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { HOME, ROOT, read, writeNew, machineId, insist } from './core.mjs';
import { validate } from './accounts.mjs';

export function jobsSites() {
  const site=(kind,signInUrl,url)=>({signInUrl,identity:{kind,url,expected:{}}});
  return {
    handshake:site('handshake','https://YOUR-SCHOOL.joinhandshake.com/login','https://YOUR-SCHOOL.joinhandshake.com/settings'),
    linkedin:site('linkedin','https://www.linkedin.com/login','https://www.linkedin.com/feed/'),
    indeed:site('indeed','https://secure.indeed.com/auth','https://www.indeed.com/myjobs'),
    workday:site('workday','https://YOUR-TENANT.wd5.myworkdayjobs.com/YOUR_SITE','https://YOUR-TENANT.wd5.myworkdayjobs.com/YOUR_SITE'),
    meta:site('meta','https://www.metacareers.com/login/','https://www.metacareers.com/profile/info'),
    'amazon-jobs':site('amazon-jobs','https://account.amazon.jobs/','https://account.amazon.jobs/'),
  };
}
export function convert(registry,base,{id=machineId(),cli=path.join(HOME,'runtime/scripts/sign-in.mjs'),aliases={},identities={},exclusive=false,sites={}}={}) {
  const data={version:1,accounts:{}};
  for(const [name,b]of Object.entries(registry.bridges||{})) {
    if(name.startsWith('_'))continue;
    const details=sites[name]||{},purpose=details.purpose||b.purpose;
    let accountSites,primary;
    if(purpose==='jobs'){accountSites=jobsSites();primary='handshake';}
    else if(purpose==='google-video'){primary='youtube';accountSites={youtube:{signInUrl:'https://studio.youtube.com/',identity:{kind:'youtube',url:'https://studio.youtube.com/',expected:{}}},google:{signInUrl:'https://accounts.google.com/',identity:{kind:'google',url:'https://myaccount.google.com/',expected:{}}}};}
    else if(b.igAccount){primary='instagram';accountSites={instagram:{signInUrl:'https://www.instagram.com/',identity:{kind:'instagram',url:'https://www.instagram.com/',expected:{'account-id':String(b.igAccount)}}}};}
    else if(b.tiktokAccount){primary='tiktok';accountSites={tiktok:{signInUrl:'https://www.tiktok.com/',identity:{kind:'tiktok',url:'https://www.tiktok.com/',expected:{profile:`https://www.tiktok.com/@${b.tiktokAccount}`}}}};}
    else {primary='unconfigured';accountSites={unconfigured:{signInUrl:'https://example.invalid/',identity:null}};}
    for(const [key,s]of Object.entries(accountSites))if(s.identity){if(identities[name]?.[key])s.identity.expected=identities[name][key];const override=details.sites?.[key];if(override){s.signInUrl=override.signInUrl;s.identity.url=override.identityUrl;}}
    data.accounts[name]={label:name,machine:{id,exclusive,cli,ssh:null},bridge:{name,profileDir:path.resolve(base,b.profileDir),port:b.remoteDebuggingPort,offscreen:!!b.offscreen,driveBlocked:!!b.driveBlocked,contextIds:aliases[name]?[aliases[name]]:[],migrated:true},primary,sites:accountSites,permissions:[]};
  }
  return validate(data);
}
export function migrate(output,registryFile,{identitiesFile,sitesFile,exclusive=false}={}) {
  insist(!fs.existsSync(output),'ACCOUNTS_EXISTS','Migration never overwrites an accounts file');
  const config=path.join(os.homedir(),'.opencli/browser-profiles.json');
  const aliases=fs.existsSync(config)?read(config).aliases||{}:{};
  const identities=identitiesFile?read(path.resolve(identitiesFile)):{};
  const sites=sitesFile?read(path.resolve(sitesFile)):{};
  const data=convert(read(path.resolve(registryFile)),path.dirname(path.resolve(registryFile)),{aliases,identities,sites,exclusive});
  writeNew(output,data);return {file:output,accounts:Object.keys(data.accounts),profilesMoved:false,liveConfigurationChanged:false};
}
