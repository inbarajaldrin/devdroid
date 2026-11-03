import './environment.mjs';
import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import path from 'node:path';
import {execFile} from 'node:child_process';
import {promisify} from 'node:util';
import {addSite,load,validate} from '../lib/accounts.mjs';
import {expression} from '../browser/identity.mjs';
import {check,drive,enroll,judge,window as signInWindow,keepSignIn,handoffOwner} from '../lib/sign-in.mjs';
import {ROOT,atomicJSON,read,protectSession} from '../lib/core.mjs';
import {config,fakeCLI} from './support.mjs';

const origin='https://music.example.invalid',marker='Avery Example';
const generic=expected=>({kind:'generic',url:origin+'/',expected:expected||{}});
function element(text,attributes={},options={}){
  return {tagName:options.tag||'BUTTON',innerText:text,getAttribute:key=>attributes[key]||null,
    closest:()=>options.hidden?{}:null,getClientRects:()=>options.noRect?[]:[{}],
    querySelector:()=>options.avatar?{}:null,style:options.style||{visibility:'visible',display:'block'}};
}
function page(i=generic({marker}),options={}){
  const url=new URL(options.url||origin+'/'),controls=options.controls||[element('',{'aria-label':marker},{avatar:true})];
  return vm.runInNewContext(expression(i),{
    location:{href:url.href,origin:url.origin,hostname:url.hostname,pathname:url.pathname},
    document:{title:options.title||'Music library',readyState:options.readyState||'complete',body:{innerText:options.text||'Your library'},
      querySelector:()=>options.input?{}:null,
      querySelectorAll:selector=>selector.startsWith('input[')?options.input?[element('',{}, {tag:'INPUT',hidden:options.hiddenInput})]:[]:selector.startsWith('body ')?options.textNodes||[]:controls},
    getComputedStyle:el=>el.style,
  });
}
async function fixture(t){
  const f=config(t);atomicJSON(f.file,{version:1,accounts:{}});await addSite(f.file,'work','music',origin,{exclusive:true});f.a=load(f.file).accounts.work;
  let clock=0;const calls=[],pages=new Map();
  const deps={guard:async()=>{},verify:async()=>({contextId:'verified-context'}),now:()=>clock,delay:async ms=>{clock+=ms;},
    execute:async(a,args,context)=>{
      assert.equal(context,'verified-context');calls.push(args);
      const session=args[args.indexOf('--session')+1],op=args[args.indexOf('--window')+2]||args.at(-1);
      if(op==='open')pages.set(session,args.at(-1));
      if(op==='get'&&args.at(-1)==='url')return pages.get(session)||origin+'/';
      if(op==='eval'){
        // Execute the exact production expression, including the enrollment marker.
        const url=new URL(pages.get(session)||origin+'/');
        return vm.runInNewContext(args.at(-1),{location:{href:url.href,origin:url.origin,hostname:url.hostname,pathname:url.pathname},
          document:{title:'Music library',readyState:'complete',body:{innerText:'Your library'},querySelector:()=>null,
            querySelectorAll:selector=>selector.startsWith('input[')?[]:[element('',{'aria-label':marker},{avatar:true})]},getComputedStyle:el=>el.style});
      }
      if(op==='extract')return 'Top artists: The Example Trio. Recently played: Invented Song.';
      if(op==='state')return {title:'Music library'};
      if(op==='find')return ['Top artists'];
      if(op==='get')return 'Music library';
      return {};
    }};
  return {...f,calls,pages,deps,observe:(file,name,a,site,extra)=>check(file,name,a,site,{...deps,...extra})};
}

test('an arbitrary HTTPS origin creates its own login without replacing named probes',async t=>{
  const f=config(t),before=structuredClone(f.a.sites.handshake),result=await addSite(f.file,undefined,'music',origin);
  assert.equal(result.assurance,'generic-lower');assert.equal(result.personalIdentityVerified,false);
  const data=load(f.file);assert.deepEqual(data.accounts.work.sites.handshake,before);
  assert.deepEqual(data.accounts.music.sites.music,{signInUrl:origin+'/',identity:generic()});
  await assert.rejects(addSite(f.file,'music','music',origin),{code:'ACCOUNT_EXISTS'});
  for(const url of ['http://music.example.invalid','https://music.example.invalid/path','https://music.example.invalid/?a=b','https://music.example.invalid/#a'])await assert.rejects(addSite(f.file,'work','other',url),{code:'USAGE'});
});
test('generic configuration cannot import arbitrary readers or pretend a personal identity is verified',async t=>{
  const f=await fixture(t);
  for(const expected of [{name:marker},{authenticated:true},{marker:'Menu'},{marker:'A\nB'},[],null]){
    const d=load(f.file);d.accounts.work.sites.music.identity.expected=expected;assert.throws(()=>validate(d),{code:'ACCOUNTS_FORMAT'});
  }
  const d=load(f.file);d.accounts.work.sites.music.identity.url='https://other.example.invalid/';assert.throws(()=>validate(d),{code:'ACCOUNTS_FORMAT'});
  d.accounts.work.sites.music.identity=generic();d.accounts.work.sites.music.identity.read='document.body';assert.throws(()=>validate(d),{code:'ACCOUNTS_FORMAT'});
});
test('generic observation returns a bounded title and visible avatar/name candidates, never a title as identity',()=>{
  const result=page(generic());assert.equal(result.state,'ready');assert.equal(result.page.title,'Music library');assert.deepEqual([...result.page.candidates],[marker]);assert.deepEqual(Object.keys(result.identity),[]);
  assert.equal(page().identity.marker,marker);
  assert.equal(page(generic({marker}),{controls:[],textNodes:[element(marker,{}, {tag:'SPAN'})]}).identity.marker,marker);
  assert.equal(page(generic({marker:'Music library'}),{controls:[]}).identity.marker,undefined);
  assert.equal(page(generic({marker}),{controls:[element('',{'aria-label':marker},{hidden:true})]}).identity.marker,undefined);
  assert.equal(page(generic({marker}),{controls:[element('',{'aria-label':marker},{style:{visibility:'hidden',display:'block'}})]}).identity.marker,undefined);
  assert.equal(page(generic({marker}),{controls:[element('',{'aria-label':marker},{style:{visibility:'visible',display:'block',opacity:'0'}})]}).identity.marker,undefined);
});
test('generic classifier refuses signed-out pages, redirects, challenges and private Google pages',()=>{
  const cases=[
    [{url:'https://other.example.invalid/'},'signed-out'],
    [{url:origin+'/login'},'signed-out'],[{url:origin+'/oauth/authorize'},'signed-out'],
    [{title:'Log in to Music'},'signed-out'],[{input:true},'signed-out'],
    [{text:'You have been signed out'},'signed-out'],[{text:'Your session has expired'},'signed-out'],
    [{controls:[element('Log in')]},'signed-out'],[{controls:[element('Sign up')]},'signed-out'],
    [{title:'Verify you are human'},'blocked'],[{text:'Please enter your verification code'},'blocked'],
    [{url:origin+'/challenge'},'blocked'],[{url:'https://console.cloud.google.com/auth/clients'},'private'],
    [{url:'https://accounts.google.com/'},'private'],
  ];
  for(const [options,state]of cases){const result=page(generic({marker}),options);assert.equal(result.state,state,JSON.stringify(options));assert.deepEqual(Object.keys(result.identity),[]);assert.equal(result.page,undefined);}
  assert.equal(page(generic({marker}),{controls:[element('Log in',{}, {hidden:true}),element(marker)]}).state,'ready');
  assert.equal(page(generic({marker}),{input:true,hiddenInput:true}).state,'ready');
});
test('generic judgment requires a completed sign-in and the exact recorded marker, with lower-assurance errors',()=>{
  assert.throws(()=>judge(generic(),page(generic())),e=>e.code==='IDENTITY_ENROLLMENT_REQUIRED'&&e.detail.assurance==='generic-lower');
  assert.throws(()=>judge(generic({marker}),page(generic({marker}),{controls:[]})),{code:'IDENTITY_LOADING'});
  assert.throws(()=>judge(generic({marker}),{state:'ready',identity:{marker:'Morgan Example'}}),{code:'IDENTITY_MISMATCH'});
  const result=judge(generic({marker}),page());assert.equal(result.personalIdentityVerified,false);assert.equal(result.assurance,'generic-lower');
});
test('an avatar/account-control label is session evidence without claiming a personal name',()=>{
  const i=generic({marker:'Open user menu'}),observation=page(i,{controls:[element('',{'aria-label':'Open user menu'},{avatar:true})]});
  assert.deepEqual([...observation.page.candidates],['Open user menu']);
  const proof=judge(i,observation);assert.equal(proof.identity.marker,'Open user menu');assert.equal(proof.personalIdentityVerified,false);assert.equal(proof.identity.name,undefined);
  assert.equal(page(i,{controls:[],textNodes:[element('Open user menu',{}, {tag:'SPAN'})]}).identity.marker,undefined);
});
test('generic site records observable identity without questions and supports every read verb',async t=>{
  const f=await fixture(t);
  const opened=await signInWindow(f.file,'work',f.a,'music',{...f.deps,root:f.dir,worker:async()=>({pid:process.pid})});
  assert.ok(opened.session.startsWith('login-'));assert.ok(f.calls[0].includes('foreground'));assert.equal(f.calls[0].at(-1),origin+'/');
  const observation=await f.observe(f.file,'work',f.a,'music',{observe:true});assert.equal(observation.assurance,'generic-lower');assert.equal(observation.page.title,'Music library');
  await assert.rejects(f.observe(f.file,'work',f.a,'music'),{code:'IDENTITY_ENROLLMENT_REQUIRED'});
  const enrolled=await enroll(f.file,'work','music',{check:f.observe,ask:async()=>assert.fail('no identity confirmation')});
  assert.equal(enrolled.personalIdentityVerified,false);
  f.a=load(f.file).accounts.work;assert.deepEqual(f.a.sites.music.identity.expected,{marker});
  const proof=await f.observe(f.file,'work',f.a,'music');assert.equal(proof.status,'signed-in');assert.equal(proof.assurance,'generic-lower');
  for(const tail of [['open',origin+'/library'],['extract'],['state'],['find','Top artists'],['get','title']]){
    const result=await drive(f.file,'work',f.a,'music',['browser','--session','task-read',...tail],{root:f.dir,check:f.observe,execute:f.deps.execute});assert.equal(result.trust,'untrusted');
    if(tail[0]==='extract')assert.match(result.data,/Top artists.*Recently played/);
  }
  const reads=f.calls.filter(args=>args[args.indexOf('--session')+1]==='task-read');assert.ok(reads.every(args=>args.includes('background')));
  assert.ok(!f.calls.some(args=>['fill','click','type'].some(op=>args.includes(op))));
});
test('automatic identity recording preserves an account changed during observation',async t=>{
  const f=await fixture(t),observe=async(...args)=>{const result=await f.observe(...args),d=load(f.file);d.accounts.work.label='Changed';atomicJSON(f.file,d);return result;};
  await assert.rejects(enroll(f.file,'work','music',{check:observe}),{code:'ACCOUNT_CHANGED'});
  assert.deepEqual(load(f.file).accounts.work.sites.music.identity.expected,{});
});
test('generic checks close their background tab on refusal and label unsettled or blocked evidence',async t=>{
  const f=await fixture(t);f.a.sites.music.identity.expected={marker};
  for(const state of ['signed-out','blocked','private','unknown','loading']){
    const calls=[];
    await assert.rejects(check(f.file,'work',f.a,'music',{...f.deps,execute:async(a,args)=>{calls.push(args);return {state,identity:{}};}}),e=>e.detail.assurance==='generic-lower'&&e.detail.personalIdentityVerified===false);
    assert.equal(calls.at(-1).at(-1),'close');assert.ok(calls.slice(0,-1).every(args=>args.includes('background')));
  }
});
test('generic drive refuses login, challenge, marker loss, foreign and Console tasks before any content read',async t=>{
  const f=await fixture(t);f.a.sites.music.identity.expected={marker};let lease=0;
  for(const [url,state,identity,code]of [
    [origin+'/login','signed-out',{},'SIGNED_OUT'],[origin+'/','blocked',{},'BLOCKED'],
    [origin+'/','ready',{},'IDENTITY_LOADING'],['https://other.example.invalid/','ready',{marker},'PAGE_LOCATION_UNKNOWN'],
    ['https://console.cloud.google.com/auth/clients','ready',{marker},'SENSITIVE_SESSION'],
    ['https://accounts.google.com/','ready',{marker},'SENSITIVE_SESSION'],
  ])for(const tail of [['extract'],['state'],['find','name'],['get','title'],['open',origin+'/library']]){
    const calls=[],execute=async(a,args)=>{calls.push(args);if(args.includes('open'))return {};if(args.at(-1)==='url')return url;if(args.includes('eval'))return {state,identity};assert.fail('content must not be read');};
    await assert.rejects(drive(f.file,'work',f.a,'music',['browser','--session','refusal-'+lease++,...tail],{root:f.dir,check:async()=>({contextId:'verified-context'}),execute}),e=>e.code===code&&e.detail.assurance==='generic-lower');
  }
});
test('generic drive preserves same-origin, read-only and private-lease refusals before verification',async t=>{
  const f=await fixture(t),prove=async()=>assert.fail('must refuse before browser verification');
  for(const tail of [['open','https://other.example.invalid/'],['click','Profile'],['eval','document.body'],['get','url']])await assert.rejects(drive(f.file,'work',f.a,'music',['browser','--session','task',...tail],{root:f.dir,check:prove}));
  protectSession(f.a,'human-private','human',f.dir);await assert.rejects(drive(f.file,'work',f.a,'music',['browser','--session','human-private','extract'],{root:f.dir,check:prove}),{code:'SENSITIVE_SESSION'});
  f.a.bridge.driveBlocked=true;await assert.rejects(check(f.file,'work',f.a,'music',f.deps),{code:'DRIVE_BLOCKED'});
});
test('generic keeper reads the human tab without navigating and completes only on an enrolled marker',async t=>{
  const f=await fixture(t);f.a.sites.music.identity.expected={marker};atomicJSON(f.file,{version:1,accounts:{work:f.a}});
  const stateFile=path.join(f.dir,'keeper.json');atomicJSON(stateFile,{owner:handoffOwner(f.file,'work',f.a,'music'),generation:'generic-test',file:f.file,account:'work',site:'music',session:'login-human',contextId:'verified-context',status:'waiting-for-user'});
  let reads=0,parks=0;await keepSignIn(stateFile,{execute:async(a,args)=>{assert.ok(args.includes('eval'));reads++;return reads===1?{state:'signed-out',identity:{}}:page();},delay:async()=>{},park:async()=>{parks++;}});
  assert.equal(reads,3);assert.equal(parks,1);assert.equal(read(stateFile).status,'completed');
});
test('CLI creates a separate login, labels list and re-records identity without a terminal',async t=>{
  const f=fakeCLI(t),exec=promisify(execFile),invoke=async args=>{try{return JSON.parse((await exec(process.execPath,[path.join(ROOT,'scripts/sign-in.mjs'),'--accounts',f.file,...args],{env:f.env})).stdout);}catch(e){return JSON.parse(e.stderr);}};
  assert.equal((await invoke(['add-site','music','--site','music','--origin',origin])).data.assurance,'generic-lower');assert.equal(f.calls().length,0);
  const site=(await invoke(['list'])).data.find(a=>a.account==='music').sites[0];assert.equal(site.personalIdentityVerified,false);assert.equal(site.identityEnrolled,false);assert.equal(site.markerEnrolled,false);
  assert.equal((await invoke(['enroll','music'])).data.enrolled,true);
  const opened=await invoke(['open','music']);assert.equal(opened.ok,true);assert.ok(f.calls().some(c=>c.args.includes('open')&&c.args.at(-1)===origin+'/'));
});
