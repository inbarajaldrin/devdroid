const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs'), os = require('node:os'), path = require('node:path');
const {Engine} = require('../engine/lib/engine.cjs');
const {Ask} = require('../engine/lib/ask.cjs');
const {write} = require('../engine/lib/store.cjs');
const notDelivered=message=>Object.assign(new Error(message),{notDelivered:true});
function fixture(t, mode='answer') {
  const dir=fs.mkdtempSync(path.join(os.tmpdir(),'text-failure-'));t.after(()=>fs.rmSync(dir,{recursive:true,force:true}));
  const target={kind:'live',agent:'claude',host:'mac',id:'11111111-1111-4111-8111-111111111111',name:'worker'};
  const rulesFile=path.join(dir,'rules.json'),stateFile=path.join(dir,'state.json'),sends=[],asks=[];
  write(rulesFile,{version:1,permissions:{'jobs@g.us':{send:'allow'}},rules:[{chat:'jobs@g.us',label:'Jobs',target,answerMode:mode,allow:['friend@lid'],triggers:{every:true}}]});
  const wa={messageId:require('./native-id.cjs').nativeId,resolve:async c=>c==='me'?'owner@lid':c,send:async(chat,text,options)=>sends.push({chat,text,options})};
  const ask={deliver:async(...a)=>{asks.push(a);return {status:'delivered'};}};
  const options={config:{host:'mac',homeHost:'mac',ownerChat:'owner@lid',owners:['owner@lid'],aiPrefix:'AI: '},rulesFile,stateFile,wa,ask};
  let engine=new Engine(options), serial=0;
  const receive=(m={})=>engine.receive({id:'phone'+(++serial),chat:'jobs@g.us',senders:['friend@lid'],text:'original question',...m});
  const owner=(text,m={})=>receive({chat:'owner@lid',senders:['owner@lid'],text,...m});
  return {options,dir,wa,ask,sends,asks,target,receive,owner,get e(){return engine;},restart(){engine=new Engine(options);return engine;}};
}
test('agent unreachable, login, quota, broken customization and uncertain acceptance are held with the message and cause',async t=>{
  for(const why of ['agent unreachable','login expired','quota 429','customization TypeError','uncertain acceptance']) {
    const f=fixture(t);f.ask.deliver=async()=>{throw Object.assign(new Error(why),{retryable:true});};
    await assert.rejects(f.receive(),new RegExp(why));
    assert.equal(f.e.retryQueue.count(),0);const held=Object.values(f.e.state.failures)[0];
    assert.equal(held.status,'held');assert.match(f.sends.at(-1).text,/original question/);assert.ok(f.sends.at(-1).text.includes(why));
    assert.ok(f.sends.every(s=>s.chat==='owner@lid'));await f.e.retry();assert.equal(Object.keys(f.e.state.failures).length,1);
  }
});
test('/ask never marks an unreachable session retryable',async t=>{
  const f=fixture(t),a=new Ask({directory:f.dir,script:'/fake/ask.py',execute:async(_bin,args)=>{fs.writeFileSync(args[args.indexOf('--outcome')+1],JSON.stringify({version:1,kind:'not-delivered',code:'SESSION_MISSING'}));throw new Error('exact session UUID is not running');}});
  await assert.rejects(a.deliver(f.target,'message',{}),e=>e.code==='ASK_FAILED'&&e.retryable===false);
});
test('resend requires genuine owner WhatsApp; duplicate and simultaneous approvals release once across restart',async t=>{
  const f=fixture(t);f.ask.deliver=async()=>{throw notDelivered('quota');};await assert.rejects(f.receive());
  const held=Object.values(f.e.state.failures)[0];f.restart();f.ask.deliver=async()=>{f.asks.push('released');return {status:'delivered'};};
  await assert.rejects(f.e.request({cmd:'resend',id:held.id,confirmed:true}),{code:'TRANSPORT_ONLY'});
  await assert.rejects(f.e.request({cmd:'inbound',message:{chat:'owner@lid',fromMe:true,text:'resend '+held.id}}),{code:'TRANSPORT_ONLY'});
  await f.receive({chat:'owner@lid',text:'resend '+held.id});assert.equal(f.asks.length,0);
  const results=await Promise.allSettled([f.owner('resend '+held.id,{id:'owner-resend-1'}),f.owner('resend '+held.id,{id:'owner-resend-2'})]);
  assert.equal(results.filter(r=>r.status==='fulfilled').length,1);assert.equal(f.asks.length,1);
  assert.equal((await f.owner('resend '+held.id,{id:'owner-resend-1'})).duplicate,true);
  f.restart();await assert.rejects(f.owner('resend '+held.id),{code:'RESEND_ALREADY_RELEASED'});assert.equal(f.asks.length,1);
});
test('quoting a private failure notice selects resend; a discarded failure never releases',async t=>{
  const f=fixture(t);f.ask.deliver=async()=>{throw notDelivered('quota');};await assert.rejects(f.receive());
  const first=Object.values(f.e.state.failures)[0],preview=f.sends.at(-1);
  await f.owner('discard '+first.id);await assert.rejects(f.owner('resend '+first.id),{code:'RESEND_ALREADY_RELEASED'});
  await assert.rejects(f.receive());const second=Object.values(f.e.state.failures).find(x=>x.id!==first.id);
  f.ask.deliver=async()=>{f.asks.push('once');return {status:'delivered'};};
  await f.owner('resend',{quotedId:f.sends.find(s=>s.options.approvalId===second.id)?.options.id || Object.values(f.e.state.sent).find(s=>s.approvalId===second.id).id});
  assert.equal(f.asks.length,1);assert.ok(preview.text.includes(first.id));
});
test('a crash during an owner release never repeats it, including on the retry timer',async t=>{
  const f=fixture(t);f.ask.deliver=async()=>{throw notDelivered('offline');};await assert.rejects(f.receive());
  const held=Object.values(f.e.state.failures)[0];let finish;
  f.ask.deliver=async()=>{f.asks.push('started');return new Promise(r=>finish=r);};
  const release=f.owner('resend '+held.id);for(let i=0;i<100&&!f.asks.length;i++)await new Promise(r=>setTimeout(r,5));f.restart();await f.e.retry();
  assert.equal(f.e.state.failures[held.id].status,'released-uncertain');
  await assert.rejects(f.owner('resend '+held.id),{code:'RESEND_ALREADY_RELEASED'});assert.equal(f.asks.length,1);
  finish({status:'delivered'});await release;
});
test('only typed temporary transport non-delivery retries automatically and retains the same WhatsApp ID',async t=>{
  const f=fixture(t),attempts=[];let offline=true;
  f.wa.send=async(chat,text,o)=>{if(chat==='jobs@g.us'){attempts.push(o.id);if(offline)throw Object.assign(new Error('reconnecting'),{code:'WA_RECONNECTING',notDelivered:true});}else f.sends.push({chat,text,options:o});};
  await assert.rejects(f.e.request({cmd:'send',chat:'jobs@g.us',confirmed:true,message:'approved message',target:f.target}));
  assert.equal(f.e.retryQueue.count(),1);assert.equal(f.sends.length,0);f.restart();offline=false;await f.e.retry();
  assert.equal(attempts.length,2);assert.equal(attempts[0],attempts[1]);assert.equal(f.e.retryQueue.count(),0);await f.e.retry();assert.equal(attempts.length,2);
});
test('a transport retry becoming auth/quota/uncertain is held and never loops',async t=>{
  const f=fixture(t);let phase=0,calls=0;
  f.wa.send=async(chat,text,o)=>{if(chat==='jobs@g.us'){calls++;throw phase?new Error('quota 429'):Object.assign(new Error('phone offline and transport reconnecting'),{code:'WA_RECONNECTING',notDelivered:true});}f.sends.push({chat,text,options:o});};
  await assert.rejects(f.e.request({cmd:'send',chat:'jobs@g.us',confirmed:true,message:'held bytes',target:f.target}));phase=1;await f.e.retry();await f.e.retry();
  assert.equal(calls,2);assert.equal(f.e.retryQueue.count(),0);assert.equal(Object.values(f.e.state.failures)[0].status,'held');assert.match(f.sends.at(-1).text,/quota.*\nHeld message:\n.*held bytes/);
});
test('uncertain publication needs inspection before resend, then reuses its original ID and media',async t=>{
  const f=fixture(t),unique=new Set(),attempts=[];let lose=true;
  f.wa.send=async(chat,text,o)=>{if(chat==='jobs@g.us'){attempts.push(o);if(lose)throw new Error('lost acknowledgement');unique.add(o.id);}else f.sends.push({chat,text,options:o});};
  await assert.rejects(f.e.request({cmd:'send',chat:'jobs@g.us',confirmed:true,message:'caption',target:f.target,media:{kind:'file',name:'proof.pdf',data:'YWJj'}}));
  const held=Object.values(f.e.state.failures)[0];await f.e.retry();assert.equal(attempts.length,1);lose=false;
  await assert.rejects(f.owner('resend '+held.id),{code:'ACCEPTANCE_UNKNOWN'});assert.equal(attempts.length,1);
  await f.e.request({cmd:'failure-resolve',id:held.id,outcome:'not-delivered',evidence:'Fake transport verified no accepted message'});
  await f.owner('resend '+held.id);await assert.rejects(f.owner('resend '+held.id),{code:'RESEND_ALREADY_RELEASED'});
  assert.equal(attempts.length,2);assert.equal(unique.size,1);assert.equal(attempts[1].media.data,'YWJj');
});
test('failed draft release keeps approval and media; resend cannot widen the rule',async t=>{
  const f=fixture(t,'draft');const incoming=await f.receive();const draft=await f.e.request({cmd:'respond',deliveryId:incoming.deliveryId,message:'draft'});
  const item=f.e.state.drafts[draft.draftId];item.mediaRef=path.join(f.dir,'fixture-media.json');write(item.mediaRef,{kind:'image',name:'photo.png',data:'YWJj'});f.e.save();
  let fail=true;f.wa.send=async(chat,text,o)=>{if(chat==='jobs@g.us'&&fail)throw notDelivered('quota');f.sends.push({chat,text,options:o});};
  await assert.rejects(f.owner('send '+draft.draftId));const held=Object.values(f.e.state.failures)[0];
  assert.ok(f.sends.every(s=>s.chat==='owner@lid'));fail=false;await f.owner('resend '+held.id);
  assert.equal(f.sends.filter(s=>s.chat==='jobs@g.us').length,1);assert.equal(f.sends.at(-1).options.media.data,'YWJj');assert.equal(f.e.state.drafts[draft.draftId].status,'sent');
  assert.ok(f.e.state.deliveries[incoming.deliveryId].responseReceipt.ok);
});
test('script customization failures retain exact argv, arguments and message until owner resend',async t=>{
  const f=fixture(t),r=f.e.rules().rules[0];write(f.options.rulesFile,{version:1,rules:[{...r,commands:{'/usage':{type:'script',host:'mac',argv:['/custom','literal']}}}]});
  f.e.runScript=async()=>{throw notDelivered('customization broken');};await assert.rejects(f.receive({text:'/usage $(literal)',senders:['owner@lid']}));
  const held=Object.values(f.e.state.failures)[0];f.e.runScript=async(argv,args)=>{assert.deepEqual(argv,['/custom','literal']);assert.equal(args,'$(literal)');return 'fixed';};
  await f.owner('resend '+held.id);assert.match(f.sends.at(-1).text,/fixed/);assert.equal(f.asks.length,0);
});
test('legacy pending agent retries become held without delivering under the new policy',async t=>{
  const f=fixture(t);f.e.state.deliveries.old={id:'old',status:'pending',prompt:'old message',target:f.target,chat:'jobs@g.us',rule:f.e.rules().rules[0]};f.e.save();
  write(path.join(f.dir,'retry.json'),[{id:'old',route:{id:'old'},message:'',kind:'text',attempts:0}]);f.restart();await f.e.retry();
  assert.equal(f.asks.length,0);assert.equal(f.e.retryQueue.count(),0);assert.equal(Object.values(f.e.state.failures)[0].status,'held');
});
test('diagnostics wait for a usable link without multiplying message IDs or replaying the held input',async t=>{
  const f=fixture(t);let ready=false;f.wa.status=async()=>({ready});f.ask.deliver=async()=>{throw notDelivered('quota');};
  await assert.rejects(f.receive());const held=Object.values(f.e.state.failures)[0];
  for(let i=0;i<5;i++)await f.e.retry();assert.equal(f.sends.length,0);assert.equal(Object.keys(f.e.state.sent).length,0);assert.equal(held.notice,'waiting');
  ready=true;await f.e.retry();await f.e.retry();assert.equal(f.sends.length,1);assert.equal(held.notice,'sent');assert.equal(f.asks.length,0);
});
test('held messages keep their original thread context when ordinary history reaches its cap',async t=>{
  const f=fixture(t);await f.receive({id:'original'});const incoming=Object.values(f.e.state.deliveries)[0];
  f.wa.send=async(chat,text,o)=>{if(chat==='jobs@g.us')throw notDelivered('quota');f.sends.push({chat,text,options:o});};
  await assert.rejects(f.e.request({cmd:'respond',deliveryId:incoming.id,message:'held answer'}));const held=Object.values(f.e.state.failures)[0];
  for(let i=0;i<650;i++)f.e.state.inbound['noise:'+i]={status:'ignored',ts:Date.now()+i,message:{chat:'noise',id:String(i)}};
  f.e.save();assert.ok(f.e.state.inbound['jobs@g.us:original']);assert.ok(Object.keys(f.e.state.inbound).length<=600);
  f.wa.send=async(chat,text,options)=>f.sends.push({chat,text,options});await f.owner('resend '+held.id);
  assert.equal(f.sends.at(-1).options.quoted.key.id,'original');
});
test('crashing during an automatic transport retry converts it to held uncertain work on restart',async t=>{
  const f=fixture(t);let phase=0,finish,calls=0;
  f.wa.send=async(chat,text,o)=>{if(chat==='jobs@g.us'){calls++;if(!phase)throw Object.assign(new Error('reconnecting'),{code:'WA_RECONNECTING',notDelivered:true});return new Promise(r=>finish=r);}f.sends.push({chat,text,options:o});};
  await assert.rejects(f.e.request({cmd:'send',chat:'jobs@g.us',message:'held',confirmed:true,target:f.target}));phase=1;
  const drain=f.e.retry();await new Promise(r=>setImmediate(r));f.restart();await f.e.retry();
  assert.equal(calls,2);const held=Object.values(f.e.state.failures)[0];assert.equal(held.status,'held');assert.equal(held.error.code,'ACCEPTANCE_UNKNOWN');assert.equal(Object.keys(f.e.state.failures).length,1);
  assert.equal(f.e.retryQueue.count(),0);finish();await drain;
});
test('a typed-looking error without positive non-delivery proof stays held',async t=>{
  const f=fixture(t);f.wa.send=async(chat,text,o)=>{if(chat==='jobs@g.us')throw Object.assign(new Error('reconnecting'),{code:'WA_RECONNECTING'});f.sends.push({chat,text,options:o});};
  await assert.rejects(f.e.request({cmd:'send',chat:'jobs@g.us',message:'held',confirmed:true,target:f.target}));
  assert.equal(f.e.retryQueue.count(),0);assert.equal(Object.values(f.e.state.failures)[0].status,'held');
});
test('a request retry handing off to a failed post retains only one active operation',async t=>{
  const f=fixture(t);let phase=0,count=0;
  const blip=()=>Object.assign(new Error('reconnecting'),{code:'WA_RECONNECTING',notDelivered:true});
  f.wa.resolve=async()=>{if(!phase)throw blip();return 'jobs@g.us';};
  f.wa.send=async(chat,text,o)=>{if(chat==='jobs@g.us'){if(phase===1)throw blip();count++;}else f.sends.push({chat,text,options:o});};
  await assert.rejects(f.e.request({cmd:'send',chat:'jobs@g.us',message:'only once',confirmed:true,target:f.target}));
  phase=1;await f.e.retry();assert.equal(f.e.retryQueue.count(),1);
  phase=2;await f.e.retry();await f.e.retry();assert.equal(count,1);assert.equal(f.e.retryQueue.count(),0);
});
test('crash during a released publication creates no second held item or second release',async t=>{
  const f=fixture(t);let phase=0,finish,calls=0;
  f.wa.send=async(chat,text,o)=>{if(chat==='jobs@g.us'){calls++;if(!phase)throw notDelivered('quota');return new Promise(r=>finish=r);}f.sends.push({chat,text,options:o});};
  await assert.rejects(f.e.request({cmd:'send',chat:'jobs@g.us',message:'approved',confirmed:true,target:f.target}));
  const held=Object.values(f.e.state.failures)[0];phase=1;const release=f.owner('resend '+held.id);await new Promise(r=>setImmediate(r));
  f.restart();await f.e.retry();assert.equal(Object.keys(f.e.state.failures).length,1);assert.equal(f.e.state.failures[held.id].status,'released-uncertain');
  await assert.rejects(f.owner('resend '+held.id),{code:'RESEND_ALREADY_RELEASED'});assert.equal(calls,2);
  const recovery=f.sends.find(s=>s.text.includes('This release was consumed'));assert.ok(recovery);assert.doesNotMatch(recovery.text,/reply resend/);
  await f.owner('discard '+held.id);assert.equal(f.e.state.failures[held.id].status,'discarded');finish();await release;
});
test('a retried request journals its new post ID before I/O and recovers one exact operation',async t=>{
  const f=fixture(t);let phase=0,finish;const ids=[];
  f.wa.resolve=async()=>{if(!phase)throw Object.assign(notDelivered('reconnecting'),{code:'WA_RECONNECTING'});return 'jobs@g.us';};
  f.wa.send=async(chat,text,o)=>{if(chat==='jobs@g.us'){ids.push(o.id);if(phase===1)return new Promise(r=>finish=r);}f.sends.push({chat,text,options:o});};
  await assert.rejects(f.e.request({cmd:'send',chat:'jobs@g.us',message:'approved',confirmed:true,target:f.target}));
  phase=1;const retry=f.e.retry();await new Promise(r=>setImmediate(r));f.restart();await f.e.retry();
  const items=Object.values(f.e.state.failures);assert.equal(items.length,1);assert.equal(items[0].status,'held');
  assert.equal(JSON.parse(fs.readFileSync(items[0].file)).kind,'post');
  await assert.rejects(f.owner('resend '+items[0].id),{code:'ACCEPTANCE_UNKNOWN'});
  await f.e.request({cmd:'failure-resolve',id:items[0].id,outcome:'not-delivered',evidence:'Fake socket confirmed no accepted bytes'});
  phase=2;await f.owner('resend '+items[0].id);assert.equal(new Set(ids).size,1);assert.equal(ids.length,2);finish();await retry;
});
test('a failure persisted before notification is announced once after restart',async t=>{
  const f=fixture(t);f.ask.deliver=async()=>{throw notDelivered('quota');};await assert.rejects(f.receive());
  const item=Object.values(f.e.state.failures)[0];delete item.notice;delete item.noticeMessageId;f.e.state.sent={};f.e.save();f.sends.length=0;
  f.restart();await f.e.retry();await f.e.retry();assert.equal(f.sends.length,1);assert.match(f.sends[0].text,/original question/);
});
test('uncertain failure notification waits for explicit repair reporting and never replays held input',async t=>{
  const f=fixture(t);f.ask.deliver=async()=>{throw notDelivered('quota');};f.wa.send=async()=>{throw new Error('notice acceptance unknown');};
  await assert.rejects(f.receive());const held=Object.values(f.e.state.failures)[0];assert.equal(held.notice,'uncertain');
  f.wa.send=async(chat,text,options)=>f.sends.push({chat,text,options});await f.e.retry();assert.equal(f.sends.length,0);
  assert.equal((await f.e.request({cmd:'failure-notices'})).ok,true);assert.equal(f.sends.length,1);assert.equal(f.sends[0].chat,'owner@lid');assert.equal(f.asks.length,0);
});
test('acceptance inspection can close accepted work; it never acts as an owner resend',async t=>{
  const f=fixture(t);let posts=0;f.wa.send=async(chat,text,o)=>{if(chat==='jobs@g.us'){posts++;throw new Error('lost acknowledgement');}f.sends.push({chat,text,options:o});};
  await assert.rejects(f.e.request({cmd:'send',chat:'jobs@g.us',message:'approved',confirmed:true,target:f.target}));const held=Object.values(f.e.state.failures)[0];
  await assert.rejects(f.e.request({cmd:'failure-resolve',id:held.id,outcome:'accepted'}),{code:'INSPECTION_REQUIRED'});
  await f.e.request({cmd:'failure-resolve',id:held.id,outcome:'accepted',evidence:'Fake server confirms the exact message ID was accepted'});
  await assert.rejects(f.owner('resend '+held.id),{code:'RESEND_ALREADY_RELEASED'});assert.equal(posts,1);
});
test('a killed ask handshake is unknown even with quota prose and cannot be replayed blindly',async t=>{
  const f=fixture(t),ask=new Ask({directory:f.dir,script:'/fake/ask.py',execute:async()=>{throw Object.assign(new Error('timeout'),{killed:true,stderr:'quota log from a running process'});}});
  await assert.rejects(ask.deliver(f.target,'message',{}),e=>e.code==='ASK_ACCEPTANCE_UNKNOWN'&&e.notDelivered===false);
});
test('a home start with missing metadata stays held until an inspected session ID avoids a second start',async t=>{
  const f=fixture(t),home={kind:'home',agent:'claude', homeKey:'fixture-choice', rolePrompt:'Instructions chosen in the fixture setup conversation.', permissions:{allowedTools:[],permissionMode:'default'},host:'mac',cwd:'/project'},r=f.e.rules().rules[0],calls=[];
  write(f.options.rulesFile,{version:1,rules:[{...r,target:home}]});
  f.e.ask=new Ask({directory:f.dir,script:'/fake/ask.py',execute:async(_bin,args)=>{calls.push(args);fs.writeFileSync(args[args.indexOf('--outcome')+1],JSON.stringify({version:1,kind:'accepted',code:'ENQUEUED'}));if(calls.length>1)fs.writeFileSync(args[args.indexOf('--metadata')+1],JSON.stringify({sessionId:f.target.id,status:'delivered'}));return {stdout:'',stderr:''};}});
  await assert.rejects(f.receive(),{code:'ASK_ACCEPTANCE_UNKNOWN'});const held=Object.values(f.e.state.failures)[0];
  await assert.rejects(f.owner('resend '+held.id),{code:'ACCEPTANCE_UNKNOWN'});assert.equal(calls.length,1);
  await assert.rejects(f.e.request({cmd:'failure-resolve',id:held.id,outcome:'accepted',evidence:'Inspected the started home session'}),{code:'ASK_METADATA'});
  await f.e.request({cmd:'failure-resolve',id:held.id,outcome:'accepted',evidence:'Inspected the started home session',receipt:{sessionId:f.target.id}});
  await f.receive();assert.equal(calls.length,2);assert.equal(calls[1].includes('--start'),false);assert.equal(calls[1][calls[1].indexOf('--session')+1],f.target.id);
});
test('an accepted agent reply reconciles a lost ask acknowledgement instead of rejecting its answer',async t=>{
  const f=fixture(t);f.ask.deliver=async()=>{throw new Error('acknowledgement lost after acceptance');};await assert.rejects(f.receive());
  const d=Object.values(f.e.state.deliveries)[0],held=Object.values(f.e.state.failures)[0];
  await f.e.request({cmd:'respond',deliveryId:d.id,message:'the answer',receipt:{sessionId:f.target.id}});
  assert.equal(f.e.state.deliveries[d.id].receipt.evidence,'agent-response');assert.equal(f.e.state.failures[held.id].status,'sent');
  assert.equal(f.sends.filter(s=>s.chat==='jobs@g.us').length,1);await assert.rejects(f.owner('resend '+held.id),{code:'RESEND_ALREADY_RELEASED'});
});
test('crash before prompt creation retains the genuine intake and releases it only once',async t=>{
  const f=fixture(t);f.ask.inspect=()=>new Promise(()=>{});
  void f.receive({id:'before-prompt'}).catch(()=>{});await new Promise(r=>setImmediate(r));
  f.restart();await f.e.retry();const held=Object.values(f.e.state.failures);assert.equal(held.length,1);assert.equal(held[0].error.code,'INTAKE_INTERRUPTED');assert.equal(held[0].acceptance,'not-delivered');
  f.ask.inspect=async()=>({});await f.owner('resend '+held[0].id);assert.equal(f.asks.length,1);assert.match(f.asks[0][1],/original question/);
  await assert.rejects(f.owner('resend '+held[0].id),{code:'RESEND_ALREADY_RELEASED'});assert.equal(fs.readdirSync(f.e.intakeDirectory).length,0);
});
test('WhatsApp redelivery before the first retry tick cannot erase interrupted intake',async t=>{
  const f=fixture(t);f.ask.inspect=()=>new Promise(()=>{});void f.receive({id:'redelivered'}).catch(()=>{});await new Promise(r=>setImmediate(r));
  f.restart();const duplicate=await f.receive({id:'redelivered'});assert.equal(duplicate.duplicate,true);assert.equal(Object.keys(f.e.state.failures).length,1);assert.equal(f.asks.length,0);
  f.ask.inspect=async()=>({});await f.owner('resend '+Object.values(f.e.state.failures)[0].id);assert.equal(f.asks.length,1);
});
test('interrupted DM intake replays through its canonical phone/LID address',async t=>{
  const f=fixture(t),rule=f.e.rules().rules[0];write(f.options.rulesFile,{version:1,rules:[{...rule,chat:'friend@s.whatsapp.net',aliases:['friend@lid']}]});
  f.ask.inspect=()=>new Promise(()=>{});void f.receive({id:'aliased',chat:'friend@lid',chatAliases:['friend@lid','friend@s.whatsapp.net']}).catch(()=>{});await new Promise(r=>setImmediate(r));
  f.restart();await f.e.retry();const held=Object.values(f.e.state.failures)[0];f.ask.inspect=async()=>({});await f.owner('resend '+held.id);assert.equal(f.asks.length,1);
});
test('attachment intake failures hold text and raw metadata until one genuine resend recovers all content',async t=>{
  const f=fixture(t);f.wa.download=async()=>{throw new Error('attachment fetch refused');};
  await assert.rejects(f.receive({text:'voice question',media:{kind:'voice note',extension:'ogg'},raw:{message:{audioMessage:{mediaKey:Buffer.from('key')}}}}),{code:'MEDIA_INTAKE_FAILED'});
  assert.equal(f.asks.length,0);const held=Object.values(f.e.state.failures)[0];assert.equal(held.acceptance,'not-delivered');assert.match(f.sends.at(-1).text,/voice question/);
  f.wa.download=async m=>{assert.equal(Buffer.isBuffer(m.raw.message.audioMessage.mediaKey),true);return '/fake/voice.ogg';};
  const rule=f.e.rules().rules[0];write(f.options.rulesFile,{version:1,rules:[{...rule,expiresAt:'2000-01-01T00:00:00Z'}]});
  await assert.rejects(f.owner('resend '+held.id),{code:'ROUTE_CLOSED'});assert.equal(f.asks.length,0);
  write(f.options.rulesFile,{version:1,rules:[rule]});const next=Object.values(f.e.state.failures).find(x=>x.status==='held');assert.ok(next);
  await f.owner('resend '+next.id);assert.equal(f.asks.length,1);assert.match(f.asks[0][1],/voice question/);assert.match(f.asks[0][1],/Transcribe locally/);
});
test('local confirmation refusals cannot consume held capacity or post failure notices',async t=>{
  const f=fixture(t);write(f.options.rulesFile,{...f.e.rules(),permissions:{'jobs@g.us':{send:'deny'}}});
  for(let i=0;i<105;i++)await assert.rejects(f.e.request({cmd:'send',chat:'jobs@g.us',message:'unconfirmed '+i,target:f.target}),{code:'CONFIRM_REQUIRED'});
  assert.equal(Object.keys(f.e.state.failures).length,0);assert.equal(f.sends.length,0);
  f.ask.deliver=async()=>{throw notDelivered('quota');};await assert.rejects(f.receive());assert.equal(Object.keys(f.e.state.failures).length,1);
});
test('broken rules retain genuine input and explain the exact file in the owner chat',async t=>{
  const f=fixture(t),original=fs.readFileSync(f.options.rulesFile);fs.writeFileSync(f.options.rulesFile,'{broken');
  await assert.rejects(f.receive(),{code:'INVALID_JSON'});const held=Object.values(f.e.state.failures)[0];assert.ok(held);assert.ok(f.sends.at(-1).text.includes(f.options.rulesFile));assert.equal(f.sends.at(-1).chat,'owner@lid');
  fs.writeFileSync(f.options.rulesFile,original);await f.owner('resend '+held.id);assert.equal(f.asks.length,1);
});
test('a completed script keeps its output through a formatter failure without executing twice',async t=>{
  const f=fixture(t),rule=f.e.rules().rules[0];write(f.options.rulesFile,{version:1,rules:[{...rule,commands:{'/usage':{type:'script',host:'mac',argv:['/custom']}}}]});
  let runs=0;f.e.runScript=async()=>{runs++;return 'retained result';};const format=f.e.format;f.e.format=()=>{throw new Error('broken formatter');};
  await assert.rejects(f.receive({text:'/usage',senders:['owner@lid']}));const held=Object.values(f.e.state.failures)[0];f.e.format=format;
  await f.owner('resend '+held.id);assert.equal(runs,1);assert.match(f.sends.at(-1).text,/retained result/);
});
test('a response failing before preview can be held and released without ALREADY_RESPONDED',async t=>{
  const f=fixture(t,'draft'),incoming=await f.receive(),hold=f.e.hold;f.e.hold=async()=>{throw new Error('custom hold broke before publication');};
  const req={cmd:'respond',deliveryId:incoming.deliveryId,message:'retained answer'};
  await assert.rejects(f.e.request(req));const held=Object.values(f.e.state.failures)[0];assert.equal(held.acceptance,'not-delivered');
  f.e.hold=hold;await assert.rejects(f.e.request(req),{code:'HELD_MESSAGE_EXISTS'});
  const draft=await f.owner('resend '+held.id);assert.ok(draft.held);assert.ok(f.sends.every(s=>s.chat==='owner@lid'));
});
test('failed sends return the held ID and repeated local calls cannot act as resend',async t=>{
  const f=fixture(t);let attempts=0;f.wa.send=async(chat,text,o)=>{if(chat==='jobs@g.us'){attempts++;throw notDelivered('quota');}f.sends.push({chat,text,options:o});};
  const req={cmd:'send',chat:'jobs@g.us',message:'one exact message',confirmed:true,target:f.target};let first;
  await assert.rejects(f.e.request({...req}),e=>{first=e;return e.held===true&&!!e.failureId;});
  await assert.rejects(f.e.request({...req}),e=>e.code==='HELD_MESSAGE_EXISTS'&&e.failureId===first.failureId);
  assert.equal(attempts,1);assert.equal(Object.keys(f.e.state.failures).length,1);
});
test('listen reply failures become real held deliveries with their original reply request',async t=>{
  const f=fixture(t,'listen'),incoming=await f.receive();f.ask.deliver=async()=>{throw notDelivered('agent unreachable');};
  await assert.rejects(f.owner('reply '+incoming.deliveryId));const held=Object.values(f.e.state.failures)[0];assert.ok(held);assert.match(f.sends.at(-1).text,new RegExp(held.id));
  f.ask.deliver=async(_target,prompt)=>{f.asks.push(prompt);return {status:'delivered'};};await f.owner('resend '+held.id);assert.match(f.asks.at(-1),/owner now asks for a private reply/);
});
test('a crash between retaining a proven reconnect and enqueueing it repairs the retry journal',async t=>{
  const f=fixture(t);f.wa.send=async(chat,text,o)=>{if(chat==='jobs@g.us')throw Object.assign(notDelivered('reconnecting'),{code:'WA_RECONNECTING'});f.sends.push({chat,text,options:o});};
  await assert.rejects(f.e.request({cmd:'send',chat:'jobs@g.us',message:'queued',confirmed:true,target:f.target}));write(path.join(f.dir,'retry.json'),[]);
  f.wa.send=async(chat,text,options)=>f.sends.push({chat,text,options});f.restart();await f.e.retry();assert.equal(f.sends.filter(s=>s.chat==='jobs@g.us').length,1);assert.equal(f.e.retryQueue.count(),0);
});
test('a hung send becomes held uncertainty; a late acceptance closes it without resend',async t=>{
  const f=fixture(t);f.e.config.sendTimeoutMs=5;let finish,attempts=0;
  f.wa.send=async(chat,text,options)=>{if(chat==='jobs@g.us'){attempts++;return new Promise(r=>finish=r);}f.sends.push({chat,text,options});};
  await assert.rejects(f.e.request({cmd:'send',chat:'jobs@g.us',message:'one pending send',confirmed:true,target:f.target}),{code:'SEND_ACCEPTANCE_UNKNOWN'});
  const held=Object.values(f.e.state.failures)[0],op=JSON.parse(fs.readFileSync(held.file));
  assert.equal((await f.e.request({cmd:'publication-status',id:op.args[6]})).inFlight,true);
  await assert.rejects(f.e.request({cmd:'failure-resolve',id:held.id,outcome:'not-delivered',evidence:'Server record not yet visible'}),{code:'ACCEPTANCE_PENDING'});
  await assert.rejects(f.owner('resend '+held.id),{code:'ACCEPTANCE_UNKNOWN'});assert.equal(attempts,1);
  finish();await new Promise(r=>setImmediate(r));assert.equal(f.e.state.failures[held.id].status,'sent');
  assert.equal((await f.e.request({cmd:'publication-status',id:op.args[6]})).status,'sent');await assert.rejects(f.owner('resend '+held.id),{code:'RESEND_ALREADY_RELEASED'});assert.equal(attempts,1);
});
test('hung transport attempts release bounded retry workers and leave ordinary sends usable',async t=>{
  const f=fixture(t);f.e.config.sendTimeoutMs=5;let hang=false;
  f.wa.send=async(chat,text,options)=>{if(chat==='jobs@g.us'){if(hang)return new Promise(()=>{});throw Object.assign(notDelivered('reconnecting'),{code:'WA_RECONNECTING'});}f.sends.push({chat,text,options});};
  for(let i=0;i<4;i++)await assert.rejects(f.e.request({cmd:'send',chat:'jobs@g.us',message:'pending '+i,confirmed:true,target:f.target}));
  hang=true;await f.e.retry();assert.equal(f.e.retryQueue.count(),0);assert.equal(Object.values(f.e.state.failures).filter(x=>x.status==='held').length,4);
  assert.ok((await f.e.request({cmd:'send',chat:'me',message:'still usable',style:'plain'})).ok);
});
