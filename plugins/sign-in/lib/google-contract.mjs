import { insist, slug } from './core.mjs';

// gws 0.22.5 always adds these three identity scopes to an explicit scope request.
export const identityScopes = ['openid', 'https://www.googleapis.com/auth/userinfo.email', 'https://www.googleapis.com/auth/userinfo.profile'];
export const exactScope = s => typeof s === 'string' && (s==='https://mail.google.com/' || /^https:\/\/www\.googleapis\.com\/auth\/[a-zA-Z0-9._/-]+$/.test(s));
export const scopeDenied=(r,scopes)=>r?.value==='no'&&(!r.scopes?.length||scopes.some(s=>r.scopes.includes(s)));
export const email = s => typeof s === 'string' && /^[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+$/.test(s) && s.length < 255;
export const projectID = s => typeof s === 'string' && /^[a-z][a-z0-9-]{4,28}[a-z0-9]$/.test(s);
const fields = (o, allowed) => insist(o && !Array.isArray(o) && Object.keys(o).every(k => allowed.includes(k)), 'GOOGLE_FORMAT', 'Unknown Google metadata field; secrets do not belong in accounts');
export function validateRulings(rulings={}) {
 insist(rulings&&typeof rulings==='object'&&!Array.isArray(rulings),'RULINGS_FORMAT','Expected saved rulings');
 for(const [key,r]of Object.entries(rulings)){
  insist(/^(setup\.(sites|google)|person|(email|project|production|blocked)\.[a-z][a-z0-9-]*|(service|scope|instructions)\.[a-z][a-z0-9-]*\.[a-z][a-z0-9-]*|grant\.[a-z][a-z0-9-]*\.[a-f0-9]{64})$/.test(key)&&r&&Object.keys(r).every(k=>['value','at','api','scopes'].includes(k))&&Number.isFinite(Date.parse(r.at)),'RULINGS_FORMAT','Invalid saved ruling');
  let valid;
  if(key==='setup.sites')valid=r.value==='ask'||Array.isArray(r.value)&&r.value.every(slug)&&new Set(r.value).size===r.value.length;
  else if(key==='person')valid=slug(r.value);
  else if(key.startsWith('email.'))valid=email(r.value);
  else if(key.startsWith('project.')||key.startsWith('production.'))valid=projectID(r.value);
  else if(key.startsWith('blocked.'))valid=['admin','unknown','clear'].includes(r.value);
  else valid=['yes','no','ask'].includes(r.value);
  insist(valid&&(!r.scopes||Array.isArray(r.scopes)&&r.scopes.every(exactScope))&&(!r.api||/^[a-z][a-z0-9-]*\.googleapis\.com$/.test(r.api)),'RULINGS_FORMAT','Invalid ruling value or scoped choice');
 }
}
export function validateGoogle(g) {
  fields(g, ['person','expectedEmail','optIn','project','productionConfirmedAt','services']);
  insist(slug(g.person) && email(g.expectedEmail), 'GOOGLE_FORMAT', 'Google needs a person key and expected account email');
  fields(g.optIn, ['userWords','at']);
  insist(typeof g.optIn.userWords === 'string' && g.optIn.userWords.trim() && g.optIn.userWords.length <= 500 && Number.isFinite(Date.parse(g.optIn.at)), 'GOOGLE_FORMAT', 'Record the person’s opt-in words and date');
  insist(!g.project || projectID(g.project), 'GOOGLE_FORMAT', 'Invalid project ID');
  insist(!g.productionConfirmedAt || g.project && Number.isFinite(Date.parse(g.productionConfirmedAt)), 'GOOGLE_FORMAT', 'Production confirmation needs a project and date');
  insist(g.services && typeof g.services === 'object' && !Array.isArray(g.services), 'GOOGLE_FORMAT', 'Google services must be keyed by one-word name');
  for (const [key,s] of Object.entries(g.services)) {
    fields(s, ['api','scopes','connectedAt']);
    insist(slug(key) && /^[a-z][a-z0-9-]*\.googleapis\.com$/.test(s.api || '') && Array.isArray(s.scopes) && s.scopes.length > 0 && s.scopes.every(exactScope) && new Set(s.scopes).size === s.scopes.length && (!s.connectedAt || Number.isFinite(Date.parse(s.connectedAt))), 'GOOGLE_FORMAT', 'Service needs an exact API and explicit scope list');
  }
}
export function googleService(a, service) {
  insist(a.google, 'GOOGLE_OPT_IN_REQUIRED', 'Offer and explain Google API control; only the person opts in');
  const s = a.google.services[service];
  insist(s, 'GOOGLE_SERVICE_UNKNOWN', 'Connect this service with its exact API and scopes first');
  return s;
}
export function setupInterview({sites, google} = {}) {
  if (sites === undefined) return {complete:false, question:{key:'sites', recommendation:'Choose only the sites you want to sign into now; you can add others later.', text:'Which sites would you like to sign into now?', answer:'--sites mail,music (any names you choose, or --sites none)'}};
  const chosen = sites === 'none' ? [] : sites.split(',');
  insist(chosen.every(slug) && new Set(chosen).size === chosen.length, 'SETUP_CHOICE', 'Choose comma-separated one-word sites or none');
  if (google === undefined) return {complete:false, sites:chosen, question:{key:'google', recommendation:'Choose browser-only unless you want direct Google API control now; API access can be added later.', text:'Browser sign-in lets agents use signed-in pages. Optional Google API control lets them call chosen Google services directly after you consent to exact scopes. It needs your own Cloud project and Desktop client. Would you like that control now?', answer:'--google yes|no'}};
  insist(['yes','no'].includes(google), 'SETUP_CHOICE', 'Answer Google API choice yes or no');
  return {complete:true, sites:chosen, google:google==='yes', permissionGranted:false, next:google==='yes'?'Choose a Google account bridge, then run google opt-in in a human terminal; connect only services wanted now.':'Sign into the chosen sites; Google API access can be added later.'};
}
