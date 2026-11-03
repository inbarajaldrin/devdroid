import fs from 'node:fs';
import {load,validate} from './accounts.mjs';
import {locked,atomicJSON,writeNew,insist,grantKey} from './core.mjs';
import {setupInterview} from './google-contract.mjs';
export const ruling = value => ({value,at:new Date().toISOString()});
export async function remember(file,key,value,extra={},options={}) {
 return locked(file,()=>{const data=fs.existsSync(file)?load(file):{version:1,accounts:{},rulings:{}};data.rulings||={};if(Object.hasOwn(options,'expectedAll'))insist(JSON.stringify(data.rulings)===options.expectedAll,'RULING_CHANGED','Saved rulings changed while awaiting an answer; newer answers were preserved');data.rulings[key]={...data.rulings[key],...ruling(value),...extra};if(key.startsWith('grant.')&&value!=='yes')for(const [name,a]of Object.entries(data.accounts))a.permissions=a.permissions.filter(p=>grantKey(name,p)!==key);validate(data);if(fs.existsSync(file))atomicJSON(file,data);else writeNew(file,data);return data.rulings[key];});
}
export async function decide(file,key,ask,prompt,extra={}) {
 const comparable=v=>JSON.stringify(Array.isArray(v)?[...v].sort():v);
 const records=load(file).rulings,expectedAll=JSON.stringify(records),prior=records[key],same=prior&&Object.entries(extra).every(([k,v])=>comparable(prior[k])===comparable(v));
 if(prior?.value==='no')return 'no';
 if(same&&prior.value!=='ask')return prior.value;
 const answer=await ask(`${prompt}\n${prior?.value==='ask'?'Your saved rule is ask every time. Answer yes or no: ':'Answer yes, no, or ask (ask every time): '}`);
 insist(['yes','no','ask'].includes(answer),'RULING_REQUIRED','The person must choose yes, no or ask');
 if(!same&&prior?.value!=='ask')await remember(file,key,answer,extra,{expectedAll});
 else insist(JSON.stringify(load(file).rulings)===expectedAll,'RULING_CHANGED','Saved rulings changed while awaiting an answer; newer answers were preserved');
 return answer;
}
export async function setupChoices(file,{ask,sites,google}={}) {
 if(!fs.existsSync(file))writeNew(file,{version:1,accounts:{},rulings:{}});
 let data=load(file),r=data.rulings;
 let chosen=r['setup.sites']?.value;
 if(!r['setup.sites']||chosen==='ask'){const q=setupInterview().question;const answer=sites??await ask(`${q.recommendation}\n${q.text} (comma-separated words, none, or ask each setup): `);if(answer==='ask'){await remember(file,'setup.sites','ask');return {complete:false,saved:true,next:'Site choice deferred by your ask policy; run setup when ready.'};}chosen=setupInterview({sites:answer}).sites;if(r['setup.sites']?.value!=='ask')await remember(file,'setup.sites',chosen);}
 data=load(file);r=data.rulings;
 if(!r['setup.google']){const q=setupInterview({sites:chosen.join(',')||'none'}).question;const answer=google??await ask(`${q.recommendation}\n${q.text} (yes/no/ask): `);insist(['yes','no','ask'].includes(answer),'SETUP_CHOICE','Choose yes, no or ask');await remember(file,'setup.google',answer);}
 r=load(file).rulings;return {complete:true,sites:chosen,google:r['setup.google'].value,saved:true};
}
export function upgradeRulings(data) {
 // Additive v1 migration: no old account, grant or identity is discarded.
 const legacy=!Object.hasOwn(data,'rulings');data.rulings||={};if(!legacy)return data;
 const r=data.rulings,people=new Set();
 for(const [name,a]of Object.entries(data.accounts||{}))if(a.google){const g=a.google,legacyRuling=value=>({value,at:g.optIn.at});people.add(g.person);r['setup.google']??=legacyRuling('yes');r[`email.${name}`]??=legacyRuling(g.expectedEmail);if(g.project)r[`project.${g.person}`]??=legacyRuling(g.project);if(g.productionConfirmedAt)r[`production.${g.person}`]??={value:g.project,at:g.productionConfirmedAt};for(const [key,s]of Object.entries(g.services))if(s.connectedAt)r[`service.${name}.${key}`]??={...legacyRuling('yes'),api:s.api,scopes:s.scopes};}
 if(people.size===1)r.person??={value:[...people][0],at:Object.values(data.accounts).find(a=>a.google)?.google.optIn.at};return data;
}
