const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs'), os = require('node:os'), path = require('node:path'), net = require('node:net');
const { promisify } = require('node:util');
const { execFile } = require('node:child_process');
const { Engine, digest } = require('../engine/lib/engine.cjs');
const { write } = require('../engine/lib/store.cjs');
const { createExternalQueue } = require('../engine/lib/external-queue.cjs');
const { locateAsk } = require('../engine/lib/ask.cjs');
function fixture(t, rule) {
  const dir=fs.mkdtempSync(path.join(os.tmpdir(),'text-review-'));t.after(()=>fs.rmSync(dir,{recursive:true,force:true}));
  const target={kind:'live',agent:'claude',host:'mac',id:'11111111-1111-4111-8111-111111111111',name:'plan47'};
  const rulesFile=path.join(dir,'rules.json'),stateFile=path.join(dir,'state.json');
  const r={chat:'team@g.us',label:'Team',target,answerMode:'draft',allow:['friend@lid'],triggers:{every:true},...rule};
  write(rulesFile,{version:1,rules:[r]});const sends=[],asks=[];
  const options={config:{host:'mac',homeHost:'mac',ownerChat:'owner@lid',owners:['owner@lid'],aiPrefix:'AI: '},rulesFile,stateFile,
    wa:{messageId:require('./native-id.cjs').nativeId,resolve:async c=>c,send:async(chat,text,opts)=>{sends.push({chat,text,opts});return {key:{id:opts.id}};}},ask:{deliver:async(t,p)=>{asks.push({t,p});return {status:'delivered'};}}};
  const e=new Engine(options);let n=0;
  const receive=m=>e.receive({id:'input'+(++n),chat:r.chat,senders:['friend@lid'],text:'hello',...m});
  const owner=text=>receive({chat:'owner@lid',senders:['owner@lid'],text});
  return {e,options,dir,r,target,sends,asks,receive,owner};
}
test('unruled outsiders cannot gain an implicit answer mode by replying to a confirmed outbound message',async t=>{
  const f=fixture(t);const sent=await f.e.request({cmd:'send',chat:'mom@lid',message:'late',style:'plain',target:f.target,confirmed:true});
  const reply=await f.receive({chat:'mom@lid',senders:['mom@lid'],text:'call me',quotedOwn:true,quotedId:sent.id});
  assert.equal(reply.ignored,'not-allowed');assert.equal(f.asks.length,0);assert.equal(f.sends.length,1);
});
test('outgoing phone/LID aliases enforce the same draft, including before an inbound alias was learned',async t=>{
  const f=fixture(t,{chat:'friend@lid'});f.options.wa.aliases=async jid=>[jid,'friend@lid'];
  assert.equal((await f.e.request({cmd:'send',chat:'fixture-contact@s.whatsapp.net',message:'protected',confirmed:true,target:f.target})).held,true);
  assert.ok(f.sends.every(s=>s.chat==='owner@lid'));
  f.options.wa.aliases=async()=>[];
  assert.equal((await f.e.request({cmd:'send',chat:'fixture-contact@s.whatsapp.net',message:'still protected',confirmed:true,target:f.target})).held,true);
  await assert.rejects(f.e.request({cmd:'send',chat:'unknown@s.whatsapp.net',message:'unresolved',confirmed:true,target:f.target}),{code:'ALIASES_UNRESOLVED'});
});
test('rule confirmation previews show access, exact script, host and every other changed authority field',async t=>{
  const f=fixture(t);const next={...f.r,allow:['stranger@lid'],expiresAt:'2099-01-01T00:00:00Z',style:'plain',commands:{'/usage':{type:'script',host:'box',argv:['/bin/sh','-c','sensitive-command'],allow:['stranger@lid']}}};
  await f.e.request({cmd:'rules-set',bundle:{version:1,rules:[next]}});
  const preview=f.sends[0].text;for(const value of ['changes from','stranger@lid','/bin/sh','sensitive-command','box','expiresAt','style'])assert.ok(preview.includes(value),value);
  assert.deepEqual(f.e.rules().rules[0],f.r);
});
test('ordinary owner swipe-replies send/confirm/reply/discard reach their agent instead of being swallowed',async t=>{
  const f=fixture(t);const sent=await f.e.request({cmd:'send',chat:'owner@lid',message:'Should I send?',target:f.target});
  for(const text of ['send','confirm','reply','discard'])await f.receive({chat:'owner@lid',senders:['owner@lid'],text,quotedId:sent.id,quotedOwn:true});
  assert.equal(f.asks.length,4);
});
test('owner WhatsApp edits reach the drafting agent and its answer creates a revised held draft',async t=>{
  const f=fixture(t);const incoming=await f.receive();const draft=await f.e.request({cmd:'respond',deliveryId:incoming.deliveryId,message:'first'});
  const preview=f.sends.at(-1);
  const edit=await f.receive({chat:'owner@lid',senders:['owner@lid'],text:'make it friendlier',quotedId:preview.opts.id,quotedOwn:true});
  assert.match(f.asks.at(-1).p,new RegExp(draft.draftId));
  const revised=await f.e.request({cmd:'respond',deliveryId:edit.deliveryId,message:'friendlier'});
  assert.ok(revised.held);assert.equal(f.e.state.drafts[draft.draftId].status,'discarded');
  assert.ok(f.sends.every(s=>s.chat==='owner@lid'));
});
test('failed or invalid draft revision retains the original; text-only revision preserves its attachment',async t=>{
  const f=fixture(t);const d=await f.e.request({cmd:'send',chat:f.r.chat,message:'photo',target:f.target,confirmed:true,media:{kind:'image',name:'pic.png',data:'YWJj'}});
  await assert.rejects(f.e.request({cmd:'draft-revise',draftId:d.draftId,message:' '}),{code:'MESSAGE_REQUIRED'});assert.equal(f.e.state.drafts[d.draftId].status,'held');
  const oldFile=f.e.state.drafts[d.draftId].mediaRef;
  const revised=await f.e.request({cmd:'draft-revise',draftId:d.draftId,message:'new caption'});
  assert.equal(fs.existsSync(oldFile),false);await f.owner('send '+revised.draftId);assert.equal(f.sends.at(-1).opts.media.data,'YWJj');
});
test('a dead retry target cannot starve another target and exhausted items get one final notice',async t=>{
  const f=fixture(t),attempts=[],expired=[];let round=0;
  const q=createExternalQueue({file:path.join(f.dir,'queue.json'),maxAttempts:2,deliver:async r=>{attempts.push(r.id);return {ok:r.id==='B'&&round>0,err:'offline'};},log:()=>{},exhausted:async i=>expired.push(i.id)});
  q.enqueue({id:'A',route:{id:'A'},message:'a'});q.enqueue({id:'B',route:{id:'B'},message:'b'});
  await q.drain();round++;await q.drain();assert.deepEqual(attempts,['A','B','A','B']);assert.deepEqual(expired,['A']);assert.equal(q.count(),0);
});
test('prepared inputs persist across restart and replay after a discarded transaction',async t=>{
  const f=fixture(t);const id='transaction';
  f.e.state.proposals[id]={id,type:'rules',status:'held',data:{base:digest(f.e.rules()),bundle:f.e.rules()}};
  f.e.state.prepared={id,at:Date.now(),coordinator:'mac'};f.e.save();
  assert.equal((await f.receive({text:'queued input'})).queued,'rules-updating');assert.equal(f.asks.length,0);
  const restarted=new Engine(f.options);await restarted.request({cmd:'discard',id});await restarted.drainingDeferred;
  assert.equal(f.asks.length,1);assert.match(f.asks[0].p,/queued input/);assert.equal(Object.keys(restarted.state.deferred).length,0);
});
test('a stale peer prepare recovers from its coordinator decision without losing queued input',async t=>{
  const f=fixture(t);f.e.state.prepared={id:'old',at:Date.now()-180000,coordinator:'other',bundle:f.e.rules()};f.e.forward=async()=>({ok:true,status:'discarded'});f.e.save();
  await f.receive({text:'held while rules change'});await f.e.retry();await f.e.drainingDeferred;
  assert.equal(f.e.state.prepared,undefined);assert.equal(f.asks.length,1);
});
test('catalog and installed-plugin dependency resolution work without ASK_SCRIPT',t=>{
  const f=fixture(t),skill=path.join(f.dir,'checkout/plugins/text/skills/text'),provider=path.join(f.dir,'checkout/plugins/session-tools/skills/ask/scripts/ask.py');
  fs.mkdirSync(path.dirname(provider),{recursive:true});fs.writeFileSync(provider,'# provider');
  write(path.join(f.dir,'checkout/.claude-plugin/marketplace.json'),{plugins:[{name:'session-tools',source:'./plugins/session-tools'}]});
  assert.equal(locateAsk({HOME:path.join(f.dir,'empty-home')},skill),provider);
  const cached=path.join(f.dir,'cached-provider/skills/ask/scripts/ask.py');fs.mkdirSync(path.dirname(cached),{recursive:true});fs.writeFileSync(cached,'# cached');
  write(path.join(f.dir,'home/.claude/plugins/installed_plugins.json'),{plugins:{'session-tools@market':[{installPath:path.join(f.dir,'cached-provider')}]}});
  assert.equal(locateAsk({HOME:path.join(f.dir,'home')},path.join(f.dir,'standalone/skills/text')),cached);
});
test('the installed secondary-account CLI uses its own socket even with inherited TEXT_HOME',async t=>{
  const f=fixture(t),account=path.join(f.dir,'accounts/us'),runtime=path.join(account,'runtime');
  fs.cpSync(path.resolve(__dirname,'..'),runtime,{recursive:true});write(path.join(account,'config.json'),{account:'us',host:'mac',runtimeRoot:f.dir});
  const socket=path.join(account,'engine.sock');let got;
  const server=net.createServer(c=>c.once('data',d=>{got=JSON.parse(d);c.end('{"ok":true}\n');}));await new Promise(resolve=>server.listen(require('../engine/lib/socket.cjs').address(socket),resolve));t.after(()=>server.close());
  const env={...process.env,TEXT_HOME:account};delete env.TEXT_ACCOUNT;
  const result=await promisify(execFile)(process.execPath,[path.join(runtime,'scripts/text.cjs'),'respond','answer','--delivery-id','delivery','--account','us'],{env});
  assert.equal(JSON.parse(result.stdout).ok,true);assert.equal(got.deliveryId,'delivery');assert.equal(got.account,'us');
});
test('secondary-account prompts, pairing and service definitions consistently select that account',async t=>{
  const f=fixture(t);f.e.config.account='us';await f.receive();assert.match(f.asks[0].p,/--account us/);
  const {install,pairRemote}=require('../scripts/setup.cjs');let invocation;
  pairRemote('box','0'.repeat(11),(...args)=>{invocation=args;return {stdin:{end(){}}};},'us');assert.match(invocation[1].at(-1),/accounts\/us\/runtime\/bin\/text-setup.*--account us/);
  const dependency=path.join(f.dir,'ask.py');fs.writeFileSync(dependency,'# dependency');const directory=path.join(f.dir,'accounts/us');
  await install({directory,config:{...f.options.config,account:'us'},bundle:{version:1,rules:[]},env:{ASK_SCRIPT:dependency},execute:async()=>({})});
  assert.match(fs.readFileSync(path.join(directory,'service/com.devdroid.text.us.plist'),'utf8'),/<string>com.devdroid.text.us<\/string>/);
  assert.ok(fs.existsSync(path.join(directory,'service/devdroid-text-us.service')));
});
