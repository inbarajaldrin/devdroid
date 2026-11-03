// Authentication checks only. No form interaction or site-task automation.
export const oauthOutcome = `(() => {const code=new URL(location.href).searchParams.get('error');if(['admin_policy_enforced','org_internal'].includes(code))return code;const text=(document.body?.innerText||'').slice(0,6000);return /admin_policy_enforced|org_internal|blocked by.*administrator/i.test(text)?'admin_policy_enforced':'pending';})()`;
export const identityFields = new Set(['profile', 'name', 'public-id', 'account-id', 'channel-id', 'channel', 'username', 'authenticated', 'site', 'marker']);
export const kinds = new Set(['handshake', 'youtube', 'linkedin', 'indeed', 'workday', 'meta', 'amazon-jobs', 'instagram', 'tiktok', 'google', 'generic']);
export const identityAssurance = i => i?.kind==='generic' ? {assurance:'generic-lower',personalIdentityVerified:false} : i?.recordedAt&&!Object.keys(i.expected||{}).some(k=>!['authenticated','site'].includes(k)) ? {assurance:'authentication-only',personalIdentityVerified:false} : {};
export const validMarker = value => typeof value==='string' && value===value.trim() && value.length>0 && value.length<=200 && !/[\x00-\x1f\x7f]/.test(value) && !/^(?:menu|settings|home|log ?out|sign out|log ?in|sign in|sign up)$/i.test(value);

// Fixed DOM observation only: no forms, storage, globals, requests or site selectors.
function genericPage(origin, marker) {
  const empty=state=>({state,identity:{}}), normal=value=>(value||'').replace(/\s+/g,' ').trim();
  if(location.href==='about:blank'||document.readyState==='loading')return empty('loading');
  if(['console.cloud.google.com','accounts.google.com'].includes(location.hostname))return empty('private');
  if(location.origin!==origin)return empty('signed-out');
  const title=normal(document.title), text=(document.body?.innerText||'').slice(0,16000);
  if(/captcha|verify (?:that )?you are human|unusual traffic|security check\b|access denied|checking your browser|challenge required|verify your identity|verification code|two.factor authentication/i.test(title+' '+text) || /\/(?:challenge|checkpoint|captcha|mfa|verify)(?:\/|$)/i.test(location.pathname))return empty('blocked');
  const visible=el=>{
    if(el.closest('[hidden], [aria-hidden="true"], [inert]')||!el.getClientRects().length)return false;
    for(let node=el;node;node=node.parentElement){const style=getComputedStyle(node);if(['hidden','collapse'].includes(style.visibility)||style.display==='none'||style.opacity==='0')return false;}
    return true;
  };
  if(/\/(?:login|signin|sign-in|sign_in|auth|oauth|authorize|saml|sso)(?:\/|$)/i.test(location.pathname) || /sign in|log in|login|sign up/i.test(title) || /you (?:are|have been) (?:signed|logged) out|(?:sign|log) in to (?:continue|access)|session (?:has )?expired/i.test(text))return empty('signed-out');
  if([...document.querySelectorAll('input[type="password"], input[autocomplete="one-time-code"]')].some(visible))return empty('signed-out');
  const controls=[...document.querySelectorAll('button, a, [role="button"], [aria-label], img[alt]')].filter(el=>!['INPUT','TEXTAREA','SELECT'].includes(el.tagName)&&visible(el));
  const labels=el=>[el.getAttribute('aria-label'),el.getAttribute('title'),el.tagName==='IMG'?el.getAttribute('alt'):el.innerText].map(normal).filter(v=>v&&v.length<=200);
  if(controls.some(el=>labels(el).some(v=>/^(?:log ?in|sign in|sign up|register|join now)(?:\s+(?:to|with)\b.*)?$/i.test(v))))return empty('signed-out');
  const accountLabel=/\b(?:account|profile|avatar|user)\b/i;
  const candidates=[...new Set(controls.filter(el=>accountLabel.test(labels(el).join(' '))||['BUTTON'].includes(el.tagName)&&el.querySelector('img')||el.getAttribute('role')==='button'&&el.querySelector('img')).flatMap(el=>[...labels(el),normal(el.querySelector('img')?.getAttribute?.('alt'))]))].filter(v=>v&&v.length<=200&&!/^(?:menu|settings|home|log ?out|sign out)$/i.test(v)).sort((a,b)=>Number(accountLabel.test(a))-Number(accountLabel.test(b))).slice(0,12);
  let found=false;
  if(marker){
    found=controls.some(el=>labels(el).includes(marker));
    // Generic account labels must belong to a control; names can also be rendered text.
    if(!found&&!/^(?:(?:open |your |my )?(?:account|profile|avatar|user)(?: menu)?)$/i.test(marker))found=[...document.querySelectorAll('body *:not(script):not(style):not(input):not(textarea):not(select)')].some(el=>visible(el)&&normal(el.innerText)===marker);
  }
  return {state:'ready',identity:found?{marker}:{},page:{title:title.slice(0,200),candidates}};
}
export function expression(i) {
  if(i.kind==='generic')return `(${genericPage.toString()})(${JSON.stringify(new URL(i.url).origin)},${JSON.stringify(i.expected?.marker||'')})`;
  return String.raw`(async () => {
    const kind = ${JSON.stringify(i.kind)}, expectedOrigin = ${JSON.stringify(new URL(i.url).origin)};
    const href = location.href, title = document.title.toLowerCase();
    if(href==='about:blank'||document.readyState==='loading')return {state:'loading',identity:{}};
    const text = (document.body?.innerText || '').slice(0,4000).toLowerCase();
    const blocked = /verify you are human|unusual traffic|security check required|access denied|captcha/.test(text+' '+title) || kind!=='google'&&/security check/.test(text+' '+title);
    const out = location.origin !== expectedOrigin || !!document.querySelector('input[type=password]') ||
      /\/login|\/signin|\/sign-in|\/sign_in|\/auth|shibboleth|\/cas\/login|duosecurity|\/saml/.test(href.toLowerCase()) ||
      /sign in|log in|login|sign up|join now/.test(title);
    if (blocked || out) return {state:blocked?'blocked':'signed-out',identity:{}};
    const cleanLink = el => {if(!el?.href)return null; const u=new URL(el.href);return u.origin+u.pathname;};
    const identity = {};
    if(kind==='handshake') identity.profile=cleanLink(document.querySelector('a[href*="/profiles/"]'));
    if(kind==='youtube') {
      identity['channel-id']=window.ytcfg?.data_?.CHANNEL_ID || null;
      identity.channel=document.querySelector('#entity-name')?.textContent?.trim() || null;
      identity.authenticated=window.ytcfg?.data_?.LOGGED_IN===true;
    }
    if(kind==='linkedin') {
      // Stock current-user GET; the CSRF value never leaves the page.
      const raw=(document.cookie.split('; ').find(v=>v.startsWith('JSESSIONID='))||'').slice(11).replace(/^"|"$/g,'');
      if(!raw)return {state:'signed-out',identity:{}};
      const r=await fetch('/voyager/api/me',{credentials:'include',headers:{'csrf-token':raw,Accept:'application/json'}});
      if([401,403].includes(r.status))return {state:'signed-out',identity:{}};
      if(!r.ok)return {state:'unknown',identity:{}};
      const data=await r.json(), m=data.miniProfile;
      identity['public-id']=m?.publicIdentifier || null;
      identity.name=[m?.firstName?.text||m?.firstName||'',m?.lastName?.text||m?.lastName||''].join(' ').trim();
    }
    if(kind==='instagram') {
      const r=await fetch('/api/v1/accounts/edit/web_form_data/',{credentials:'include',headers:{'X-IG-App-ID':'936619743392459'}});
      if(!r.ok||r.redirected)return {state:'signed-out',identity:{}};
      let data;try{data=await r.json();}catch{return {state:'signed-out',identity:{}};}
      const f=data.form_data;if(!f?.username)return {state:'signed-out',identity:{}};
      identity['account-id']=String(f.pk||'');identity.username=String(f.username);
    }
    if(kind==='tiktok') {
      const raw=document.querySelector('script#__UNIVERSAL_DATA_FOR_REHYDRATION__')?.textContent;
      if(!raw)return {state:'loading',identity:{}};
      let data;try{data=JSON.parse(raw);}catch{return {state:'unknown',identity:{}};}
      const valid=u=>u&&typeof u==='object'&&(u.secUid||u.sec_uid)?u:null;
      let viewer=valid(data.__DEFAULT_SCOPE__?.['webapp.app-context']?.user);
      // Legacy hydration must explicitly identify its owner; public profile data is insufficient.
      const stack=[data];let remaining=10000;
      while(!viewer&&stack.length&&remaining--){const node=stack.pop();if(!node||typeof node!=='object')continue;
        for(const u of [node.user,node.userInfo?.user])if(u&&(u.isOwner||u.is_owner||u.isCurrentUser))viewer=valid(u)||viewer;
        for(const value of Object.values(node))if(value&&typeof value==='object')stack.push(value);
      }
      if(!viewer)return {state:'signed-out',identity:{}};
      identity.username=String(viewer.uniqueId||viewer.unique_id||viewer.username||'');
      identity['account-id']=String(viewer.secUid||viewer.sec_uid);
      if(identity.username)identity.profile='https://www.tiktok.com/@'+encodeURIComponent(identity.username);
    }
    if(kind==='indeed') identity.authenticated=!!document.querySelector('[data-testid="account-menu"], [data-gnav-element-name="AccountMenu"], a[href*="/account/view"]');
    if(kind==='workday') {
      const signIn=!!document.querySelector('[data-automation-id="utilityButtonSignIn"]');
      const account=!!document.querySelector('[data-automation-id="utilityMenuButton"], [data-automation-id="accountSettingsButton"]');
      if(signIn&&!account)return {state:'signed-out',identity:{}};
      if(!account)return {state:'unknown',identity:{}};
      identity.authenticated=true;
    }
    if(kind==='meta') {
      if(document.querySelector('[aria-label="Open login page"]'))return {state:'signed-out',identity:{}};
      identity.authenticated=location.pathname.startsWith('/profile/');
    }
    if(kind==='amazon-jobs') identity.authenticated=location.hostname==='account.amazon.jobs' || location.hostname==='www.amazon.jobs';
    if(kind==='google') {
      // Only an active account control, never a chooser's list of identifiers.
      const controls=[...document.querySelectorAll('a[aria-label*="Google Account"], button[aria-label*="Google Account"]')];
      const names=[...new Set(controls.flatMap(el=>(el.getAttribute('aria-label')||'').match(/[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}/ig)||[]))];
      if(names.length!==1)return {state:'unknown',identity:{}};
      identity.name=names[0];
    }
    if(['indeed','workday','meta','amazon-jobs'].includes(kind)) {
      const name=document.querySelector('[data-testid="account-name"], [data-testid="profile-name"], [data-automation-id="userName"], [aria-label="Account name"]')?.textContent?.trim();
      if(name)identity.name=name;
    }
    if(identity.authenticated===false)return {state:'signed-out',identity:{}};
    return {state:document.readyState==='loading'?'loading':'ready',identity};
  })()`;
}
