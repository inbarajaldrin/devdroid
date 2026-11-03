const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs'), os = require('node:os'), path = require('node:path');
const { Engine } = require('../engine/lib/engine.cjs');
const { stamp } = require('../engine/lib/rules.cjs');
const { normalize } = require('../engine/lib/normalize.cjs');
const { write } = require('../engine/lib/store.cjs');
const target = { kind: 'live', agent: 'claude', id: '11111111-1111-4111-8111-111111111111', host: 'mac', name: 'plan/47' };
function fixture(t, overrides = {}) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'text-fix-')); t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
  const rule = { chat: 'group@g.us', label: 'Jobs', target, answerMode: 'draft', allow: ['friend@lid'], triggers: { keywords: ['@ai'] }, ...overrides };
  const rulesFile = path.join(dir,'rules.json'), stateFile = path.join(dir,'state.json');
  write(rulesFile,{version:1,permissions:{[rule.chat]:{send:'allow'}},rules:[rule]});
  const sends=[], asks=[], scripts=[];
  const config={host:'mac',homeHost:'mac',ownerChat:'owner@lid',owners:['owner@lid'],aiPrefix:'AI: ',legacyHost:'mac'};
  const wa={messageId:require('./native-id.cjs').nativeId,status:async()=>({ready:true,linked:true}),resolve:async c=>c==='me'?config.ownerChat:c,find:async()=>[],members:async()=>({}),
    download:async()=>'/fake/photo.jpg',send:async(chat,text,options)=>{sends.push({chat,text,options});return {key:{id:options.id}};}};
  const ask={deliver:async(...args)=>{asks.push(args);return {status:'delivered',sessionId:target.id};}};
  const options={config,rulesFile,stateFile,wa,ask,runScript:async(...a)=>{scripts.push(a);return 'script output';}};
  const engine=new Engine(options); let serial=0;
  const message=(m={})=>({id:'m'+(++serial),chat:rule.chat,senders:['friend@lid'],text:'@ai hello',...m});
  const receive=m=>engine.receive(message(m));
  const owner=(text,m={})=>receive({chat:config.ownerChat,senders:['owner@lid'],text,...m});
  return {engine,options,config,rule,dir,rulesFile,stateFile,wa,ask,sends,asks,scripts,message,receive,owner};
}
test('no local JSON command or flag can forge WhatsApp input, owner approval or a trusted relay',async t=>{
  const f=fixture(t);
  for (const cmd of ['inbound','approve','publish','request-reply']) await assert.rejects(f.engine.request({cmd,message:{fromMe:true,text:'send'}}),{code:'TRANSPORT_ONLY'});
  await assert.rejects(f.engine.request({cmd:'send',account:'us',chat:f.rule.chat,message:'wrong account',confirmed:true,target}),{code:'ACCOUNT_CONFLICT'});
  await assert.rejects(f.engine.request({cmd:'send',chat:'group@g.us',message:'bypass',confirmed:true,preformatted:true,relayed:true}),{code:'UNTRUSTED_RELAY'});
  await assert.rejects(f.engine.request({cmd:'relay-event',envelope:{payload:{from:'mac',to:'mac',at:Date.now(),kind:'inbound',data:{fromMe:true}}}}),{code:'UNTRUSTED_RELAY'});
  assert.equal(f.sends.length,0);
});
test('chosen drafts cannot be bypassed by send/confirmed/replyTo and survive restart',async t=>{
  const f=fixture(t);const received=await f.receive();const d=await f.engine.request({cmd:'respond',deliveryId:received.deliveryId,message:'first draft'});
  assert.equal(f.sends.length,1);assert.equal(f.sends[0].chat,'owner@lid');
  const bypass=await f.engine.request({cmd:'send',chat:f.rule.chat,message:'bypass',confirmed:true,replyTo:'m1',target});
  assert.equal(bypass.held,true);assert.ok(f.sends.every(s=>s.chat==='owner@lid'));
  const restarted=new Engine(f.options);
  await restarted.receive(f.message({chat:'owner@lid',senders:['friend@lid'],text:'send '+d.draftId}));
  assert.ok(f.sends.every(s=>s.chat==='owner@lid'));
  await restarted.receive(f.message({chat:'owner@lid',senders:['owner@lid'],text:'send '+d.draftId}));
  assert.equal(f.sends.at(-1).chat,f.rule.chat);assert.equal(f.sends.at(-1).options.quoted.key.id,'m1');
  await assert.rejects(restarted.receive(f.message({chat:'owner@lid',senders:['owner@lid'],text:'send '+d.draftId})),{code:'DRAFT_REQUIRED'});
});
test('drafts can be revised and discarded; stale releases and ambiguous bare send are inert',async t=>{
  const f=fixture(t);const incoming=await f.receive();const first=await f.engine.request({cmd:'respond',deliveryId:incoming.deliveryId,message:'old'});
  const second=await f.engine.request({cmd:'draft-revise',draftId:first.draftId,message:'revised'});
  await assert.rejects(f.owner('send '+first.draftId),{code:'DRAFT_REQUIRED'});
  assert.equal((await f.owner('send')).ignored,'ambiguous-approval');
  await f.owner('send '+second.draftId);assert.match(f.sends.at(-1).text,/revised/);
  const third=await f.engine.request({cmd:'send',chat:f.rule.chat,message:'discard this',target,confirmed:true});
  await f.engine.request({cmd:'discard',id:third.draftId});
  await assert.rejects(f.owner('send '+third.draftId),{code:'DRAFT_REQUIRED'});
});
test('new rules require owner confirmation and never announce or introduce people automatically',async t=>{
  const f=fixture(t);const bundle={version:1,rules:[{...f.rule,answerMode:'answer',allow:['friend@lid','new@lid']}]};
  const proposal=await f.engine.request({cmd:'rules-set',bundle,confirmed:true});
  assert.equal(f.engine.rules().rules[0].answerMode,'draft');assert.ok(f.sends.every(s=>s.chat==='owner@lid'));
  await f.owner('confirm '+proposal.proposalId);assert.equal(f.engine.rules().rules[0].answerMode,'answer');
  assert.ok(f.sends.every(s=>s.chat==='owner@lid'));
  const note=await f.engine.request({cmd:'announce',chat:f.rule.chat});
  assert.ok(f.sends.every(s=>s.chat==='owner@lid'));
  await f.owner('send '+note.proposalId);assert.equal(f.sends.at(-1).chat,f.rule.chat);
  assert.doesNotMatch(f.sends.at(-1).text,/friend@lid|new@lid|11111111|mac/);
});
test('help is proposed privately, contains no people/session/machine identifiers, and sends only after confirmation',async t=>{
  const f=fixture(t);const result=await f.receive({text:'/help'});
  assert.ok(result.held);assert.equal(f.asks.length,0);assert.ok(f.sends.every(s=>s.chat==='owner@lid'));
  await f.owner('allow '+result.proposalId);
  const help=f.sends.at(-1);assert.equal(help.chat,f.rule.chat);assert.match(help.text,/Jobs/);assert.doesNotMatch(help.text,/friend@lid|11111111|mac/);
});
test('tell-me notifies only; listen delivers context and answers only after the owner asks in their own chat',async t=>{
  const notify=fixture(t,{answerMode:'tell-me'});await notify.receive();
  assert.equal(notify.asks.length,0);assert.equal(notify.scripts.length,0);assert.equal(notify.sends[0].chat,'owner@lid');
  await assert.rejects(notify.engine.request({cmd:'send',chat:notify.rule.chat,message:'escape',confirmed:true,target}),{code:'CONTEXT_ONLY'});
  const f=fixture(t,{answerMode:'listen'});const incoming=await f.receive();
  await assert.rejects(f.engine.request({cmd:'respond',deliveryId:incoming.deliveryId,message:'too early'}),{code:'CONTEXT_ONLY'});
  await f.owner('reply '+incoming.deliveryId);assert.equal(f.asks.length,2);
  await f.engine.request({cmd:'respond',deliveryId:incoming.deliveryId,message:'requested answer'});
  assert.equal(f.sends.at(-1).chat,'owner@lid');
});
test('owner messages keep their chat mode despite expiry; expired external wiring stays closed',async t=>{
  const f=fixture(t,{answerMode:'tell-me',expiresAt:'2000-01-01T00:00:00Z'});
  assert.equal((await f.receive()).ignored,'expired');
  await f.receive({senders:['owner@lid']});assert.equal(f.asks.length,0);assert.equal(f.sends.at(-1).chat,'owner@lid');
});
test('delivery/script/media failures are visible privately and media failure retains the original text',async t=>{
  const f=fixture(t,{answerMode:'answer'});f.ask.deliver=async()=>{throw new Error('quota');};
  await assert.rejects(f.receive(),/quota/);assert.match(f.sends.at(-1).text,/Delivery failed.*quota/);assert.equal(f.sends.at(-1).chat,'owner@lid');
  f.ask.deliver=async(...args)=>{f.asks.push(args);return {status:'delivered'};};f.wa.download=async()=>{throw new Error('download refused');};
  await assert.rejects(f.receive({text:'@ai voice',media:{kind:'voice note',extension:'ogg'}}),{code:'MEDIA_INTAKE_FAILED'});
  assert.match(f.sends.at(-1).text,/voice.*download refused/);assert.match(f.sends.at(-1).text,/@ai voice/);assert.equal(f.asks.length,0);
  write(f.rulesFile,{version:1,rules:[{...f.rule,commands:{'/bad':{type:'script',host:'mac',argv:['/bad']}}}]});
  f.engine.runScript=async()=>{throw new Error('script failed');};
  await assert.rejects(f.receive({text:'/bad',senders:['owner@lid']}),/script failed/);assert.match(f.sends.at(-1).text,/script failed/);
});
test('not-routed hint matches live trigger/rate and never spams ordinary chatter or third parties',async t=>{
  const f=fixture(t);await f.receive({chat:'mom@lid',senders:['owner@lid'],text:'dinner?'});assert.equal(f.sends.length,0);
  const quote=String.fromCharCode(96)+'unresolved'+String.fromCharCode(96);
  await f.receive({senders:['owner@lid'],text:'answer',quotedText:quote,quotedOwn:true});
  await f.receive({senders:['owner@lid'],text:'answer again',quotedText:quote,quotedOwn:true});
  await f.receive({senders:['owner@lid'],text:'other account',quotedText:quote,quotedOwn:false});
  assert.equal(f.sends.length,1);assert.equal(f.sends[0].chat,'owner@lid');assert.doesNotMatch(f.sends[0].text,/@g.us/);
});
test('reactions and protocol events never route under every-message rules; captionless typed media does',async t=>{
  const f=fixture(t,{triggers:{every:true},answerMode:'answer'});
  for (const content of [{reactionMessage:{text:'👍'}},{protocolMessage:{type:0}},{senderKeyDistributionMessage:{}}]) {
    const m=normalize({key:{id:Math.random().toString(),remoteJid:f.rule.chat,participant:'friend@lid'},message:content},[]);
    assert.equal((await f.engine.receive(m)).ignored,'non-content');
  }
  await f.receive({text:'',media:{kind:'voice note',extension:'ogg'}});assert.match(f.asks[0][1],/Transcribe locally/);
});
test('swipe-replies win over commands and help, including live stamps predating the switch',async t=>{
  const f=fixture(t,{answerMode:'answer',commands:{'/usage':{type:'script',host:'mac',argv:['/usr/bin/uptime']}}});
  for (const text of ['/usage','/help']) await f.receive({senders:['owner@lid'],text,quotedOwn:true,quotedText:'\x60plan/47\x60\n\nold message\n\n_'+target.id+'_'});
  assert.equal(f.asks.length,2);assert.equal(f.scripts.length,0);assert.equal(f.sends.length,0);
  assert.equal(f.asks[0][0].id,target.id);assert.match(f.asks[0][1],/old message/);
});
test('commands preserve literal arguments and script commands use no agent',async t=>{
  const f=fixture(t,{answerMode:'answer',commands:{'/usage':{type:'script',host:'mac',argv:['/usr/bin/printf','%s']},'/review':{type:'instruction',host:'mac',text:'Review project'}}});
  await f.receive({text:'/usage $(literal)',senders:['owner@lid']});assert.deepEqual(f.scripts[0],[['/usr/bin/printf','%s'],'$(literal)']);assert.equal(f.asks.length,0);
  await f.receive({text:'/review module',senders:['owner@lid']});assert.match(f.asks[0][1],/Review project/);assert.match(f.asks[0][1],/module/);
});
test('bounded agent work runs concurrently and an unresolved delivery never stalls a send',async t=>{
  const f=fixture(t,{answerMode:'answer',triggers:{every:true},named:{second:{...target,id:'22222222-2222-4222-8222-222222222222'}}});
  f.engine.pool.limit=2;const releases=[];let active=0,max=0;
  f.ask.deliver=async()=>{active++;max=Math.max(max,active);await new Promise(resolve=>releases.push(resolve));active--;return {status:'delivered'};};
  const first=f.receive({text:'first'}),second=f.receive({text:'@second second'}),third=f.receive({text:'third'});
  for(let i=0;i<20&&active<2;i++)await new Promise(resolve=>setImmediate(resolve));assert.equal(active,2);
  const sent=await f.engine.request({cmd:'send',chat:'me',message:'build done',style:'plain'});assert.ok(sent.id);assert.equal(active,2);
  releases.splice(0).forEach(resolve=>resolve());await new Promise(resolve=>setImmediate(resolve));releases.splice(0).forEach(resolve=>resolve());
  await Promise.all([first,second,third]);assert.equal(max,2);
});
test('agent refusals stay held even with the obsolete retry flag; only owner resend releases them',async t=>{
  const f=fixture(t);let fail=true,calls=0;
  f.ask.deliver=async()=>{calls++;if(fail){const e=new Error('offline before delivery');e.retryable=true;e.notDelivered=true;throw e;}return {status:'delivered'};};
  await assert.rejects(f.receive(),/offline/);assert.equal(f.engine.retryQueue.count(),0);
  fail=false;await f.engine.retry();assert.equal(calls,1);
  const held=Object.values(f.engine.state.failures)[0];await f.owner('resend '+held.id);assert.equal(calls,2);
  f.ask.deliver=async()=>{throw new Error('quota');};await assert.rejects(f.receive(),/quota/);assert.equal(f.engine.retryQueue.count(),0);
});
test('state retains capped stubs instead of full Baileys thumbnails, and dedupes after restart',async t=>{
  const f=fixture(t);for(let i=0;i<650;i++) await f.receive({id:'noise'+i,text:'ordinary',raw:{thumbnail:'x'.repeat(20000)}});
  const saved=JSON.parse(fs.readFileSync(f.stateFile));assert.ok(Object.keys(saved.inbound).length<=600);assert.doesNotMatch(JSON.stringify(saved),/thumbnail/);
  const restarted=new Engine(f.options);assert.equal((await restarted.receive(f.message({id:'noise649',text:'ordinary'}))).duplicate,true);
});
test('quoted context uses the live compaction tiers and file pointer',async t=>{
  const f=fixture(t,{answerMode:'answer'});f.ask.inspect=async()=>({compactedAt:0});
  const first=await f.engine.request({cmd:'send',chat:f.rule.chat,confirmed:true,target,message:'first body',media:{kind:'file',name:'report.pdf',data:'YWJj'}});
  const latest=await f.engine.request({cmd:'send',chat:f.rule.chat,confirmed:true,target,message:'latest body'});
  await f.receive({quotedId:latest.id,quotedOwn:true,quotedText:f.sends.at(-1).text,text:'@ai newest'});assert.doesNotMatch(f.asks.at(-1)[1],/Replying to/);
  f.engine.state.sent[first.id].ts-=1000;
  await f.receive({quotedId:first.id,quotedOwn:true,quotedText:'first body',text:'@ai older'});assert.match(f.asks.at(-1)[1],/report.pdf/);
  f.ask.inspect=async()=>({compactedAt:Date.now()+1000});
  await f.receive({quotedId:first.id,quotedOwn:true,quotedText:'first body',text:'@ai compacted'});assert.match(f.asks.at(-1)[1],/first body/);assert.doesNotMatch(f.asks.at(-1)[1],/_11111111/);
});
test('owner aliases and plain-message return targets stay exact',async t=>{
  const f=fixture(t,{chat:'friend@s.whatsapp.net',answerMode:'answer'});
  const incoming=await f.receive({chat:'friend@lid',chatAliases:['friend@lid','friend@s.whatsapp.net'],text:'@ai hi'});assert.ok(incoming.deliveryId);
  const sent=await f.engine.request({cmd:'send',chat:f.rule.chat,style:'plain',confirmed:true,target,message:'plain answer'});
  await f.receive({quotedId:sent.id,quotedOwn:true,text:'follow up'});assert.equal(f.asks.length,2);assert.equal(f.asks[1][0].id,target.id);
});
test('project-group notifications follow the chosen stamped style; userChat cannot bypass draft mode',async t=>{
  const f=fixture(t,{private:true,answerMode:'answer',style:'stamped'});
  await f.engine.request({cmd:'send',chat:f.rule.chat,confirmed:true,target,message:'project update'});
  assert.match(f.sends[0].text,/^`Claude( · [^`]+)?`\n/);
  write(f.rulesFile,{version:1,rules:[{...f.rule,answerMode:'draft'}]});f.config.userChat=f.rule.chat;
  const held=await f.engine.request({cmd:'send',chat:f.rule.chat,confirmed:true,target,message:'must wait'});
  assert.ok(held.held);assert.equal(f.sends.at(-1).chat,'owner@lid');
});
test('restart catch-up uses persisted watermark without dropping concurrent out-of-order fresh input',async t=>{
  const f=fixture(t,{answerMode:'answer',triggers:{every:true}});const initial=f.engine.state.firstSeenAt;
  await f.receive({id:'newer',timestamp:initial+10});await f.receive({id:'concurrent-older',timestamp:initial+5});assert.equal(f.asks.length,2);
  const restarted=new Engine(f.options);
  assert.equal((await restarted.receive(f.message({id:'forgotten-duplicate',timestamp:initial+5}))).ignored,'history-before-install');
  await restarted.receive(f.message({id:'after-downtime',timestamp:initial+20}));assert.equal(f.asks.length,3);
});
module.exports={fixture,target};
test('with a notice chat, the engine posts its notices there and accepts approvals there, never in the owner chat',async t=>{
  const f=fixture(t,{answerMode:'answer'});f.config.noticeChat='notices@g.us';f.ask.deliver=async()=>{throw new Error('quota');};
  await assert.rejects(f.receive(),/quota/);
  assert.match(f.sends.at(-1).text,/Delivery failed.*quota/);assert.equal(f.sends.at(-1).chat,'notices@g.us');
  assert.ok(f.sends.every(s=>s.chat!=='owner@lid'));
  assert.equal(f.engine.ownerSpace('notices@g.us'),true);assert.equal(f.engine.ownerSpace('group@g.us'),false);
});
test('group-add only adds the owner\'s own accounts',async t=>{
  const f=fixture(t);const added=[];f.wa.addToGroup=async(chat,p)=>{added.push([chat,p]);return {chat,added:p};};
  await assert.rejects(f.engine.request({cmd:'group-add',chat:'notices@g.us',participant:'stranger@s.whatsapp.net'}),{code:'NOT_OWNER'});
  assert.equal((await f.engine.request({cmd:'group-add',chat:'notices@g.us',participant:'owner@lid'})).added,'owner@lid');
  assert.deepEqual(added,[['notices@g.us','owner@lid']]);
});
test('an agent can close a delivery that needs no answer, with its reason and nothing posted',async t=>{
  const f=fixture(t,{answerMode:'answer'});await f.receive();
  const id=Object.keys(f.engine.state.deliveries).at(-1), sent=f.sends.length;
  await assert.rejects(f.engine.request({cmd:'respond-none',deliveryId:id,reason:'no'}),{code:'REASON_REQUIRED'});
  assert.equal((await f.engine.request({cmd:'respond-none',deliveryId:id,reason:'an echo of our own post'})).closed,id);
  assert.equal(f.sends.length,sent);assert.equal(f.engine.state.deliveries[id].noResponse.reason,'an echo of our own post');
  await assert.rejects(f.engine.request({cmd:'respond',deliveryId:id,message:'late'}),{code:'ALREADY_RESPONDED'});
});
test('a script command can run longer and reply with the image it names on its first line',async t=>{
  const img=path.join(fs.mkdtempSync(path.join(os.tmpdir(),'text-img-')),'usage.png');fs.writeFileSync(img,'png bytes');
  const f=fixture(t,{answerMode:'answer',commands:{'/usage':{type:'script',host:'mac',argv:['/usr/bin/true'],timeoutSeconds:300,image:'first-line'}}});
  f.options.runScript=async(argv,args,opts)=>{f.scripts.push([argv,args,opts]);return img+'\nSeats under 90%: one, two.';};f.engine.runScript=f.options.runScript;
  await f.receive({senders:['owner@lid'],text:'/usage'});
  assert.equal(f.scripts.at(-1)[2].timeoutSeconds,300);
  const sent=f.sends.at(-1);assert.match(sent.text,/Seats under 90%/);assert.equal(sent.options.media.kind,'image');assert.equal(sent.options.media.name,'usage.png');
});
test('script timeouts and image settings are validated',async t=>{
  const {validate:validateRules}=require('../engine/lib/rules.cjs');
  const bundle=c=>({version:1,rules:[{chat:'g@g.us',label:'G',target,answerMode:'answer',commands:{'/x':{type:'script',host:'mac',argv:['/bin/true'],...c}}}]});
  for (const bad of [{timeoutSeconds:0},{timeoutSeconds:601},{image:'last-line'}]) assert.throws(()=>validateRules(bundle(bad)),{code:'INVALID_COMMAND'});
});
test('the owner\'s own sessions are stamped with their name; a home agent shows only its kind',async t=>{
  const id='11111111-1111-4111-8111-111111111111';
  assert.ok(stamp({kind:'live',agent:'claude',host:'mac',id},'devdroid-07','hi').startsWith('`Claude · devdroid-07`\n'));
  assert.ok(stamp({kind:'home',agent:'codex',host:'mac',id,homeKey:'helper'},'helper','hi',{},'Family (US)').startsWith('`Codex · Family (US)`\n'));
  assert.ok(stamp({kind:'home',agent:'codex',host:'mac',id,homeKey:'helper'},'helper','hi').startsWith('`Codex`\n'));
});
test('group-create starts the group with the owner\'s reading account',async t=>{
  const f=fixture(t);const made=[];f.wa.createGroup=async(subject,people)=>{made.push(people);return {id:'g@g.us',name:subject};};
  await f.engine.request({cmd:'group-create',subject:'Paper'});
  assert.deepEqual(made,[[f.config.ownerChat]]);
});
