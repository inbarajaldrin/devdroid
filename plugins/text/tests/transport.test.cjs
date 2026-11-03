const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const net = require('node:net');
const { EventEmitter } = require('node:events');
const { PassThrough } = require('node:stream');
const {execFile}=require('node:child_process');
const {promisify}=require('node:util');
const { Ask, locateAsk } = require('../engine/lib/ask.cjs');
const { request, remote } = require('../engine/lib/client.cjs');
const { parse } = require('../scripts/text.cjs');
const { Engine } = require('../engine/lib/engine.cjs');
const { proxy } = require('../engine/lib/proxy.cjs');
const { write } = require('../engine/lib/store.cjs');
function temp(t) { const d = fs.mkdtempSync(path.join(os.tmpdir(), 'text-io-')); t.after(() => fs.rmSync(d, { recursive: true, force: true })); return d; }
test('real Unix client entrance delivers the complete request to a fake WhatsApp socket', async t => {
  const dir = temp(t), socket = path.join(dir, 'fake.sock');
  const payload = { cmd: 'send', chat: 'owner@lid', message: '`$HOME` $(x)\nnext', replyTo: 'reply', media: { kind: 'image', data: 'YWJj' } };
  const server = net.createServer(c => { c.once('data', data => { assert.deepEqual(JSON.parse(data), payload); c.end(JSON.stringify({ ok: true, id: 'sent' }) + '\n'); }); });
  await new Promise(resolve => server.listen(require('../engine/lib/socket.cjs').address(socket), resolve)); t.after(() => server.close());
  assert.deepEqual(await request(socket, payload), { ok: true, id: 'sent' });
});
test('SSH sends identical JSON options and binary attachments without shell interpolation', async t => {
  let actual;
  const payload = { cmd: 'send', account: 'us', replyTo: 'recipient-id', message: '$(not-a-command)', media: { kind: 'file', name: 'a b.txt', data: 'YWJj' } };
  const execute = (command, argv) => {
    assert.equal(command, 'ssh'); assert.equal(argv.at(-2), 'box'); assert.match(argv.at(-1), /accounts\/us\/runtime\/bin\/text-relay/);
    const child = new EventEmitter(); child.stdout = new PassThrough(); child.stderr = new PassThrough(); child.stdin = new PassThrough();
    let buffer = ''; child.stdin.on('data', d => buffer += d); child.stdin.on('finish', () => { actual = JSON.parse(buffer); child.stdout.write('{"ok":true}'); child.emit('close', 0); }); return child;
  };
  assert.equal((await remote('box', payload, execute)).ok, true); assert.deepEqual(actual, payload);
  assert.throws(() => remote('box;bad', payload, execute), { code: 'INVALID_HOST' });
  const file = path.join(temp(t), 'a.bin'); fs.writeFileSync(file, 'abc');
  const parsed = parse(['send', 'Mom', 'hello', '--plain', '--confirmed', '--file', file, '--reply-to', 'recipient-id', '--via', 'box'], {});
  assert.equal(parsed.payload.replyTo, 'recipient-id'); assert.equal(parsed.payload.media.data, 'YWJj'); assert.equal(parsed.via, 'box');
  assert.throws(() => parse(['send', 'Mom', 'hi', '--typo'], {}), { code: 'UNKNOWN_OPTION' });
  assert.throws(() => parse(['find', 'Mom', '--reply-to', 'id'], {}), { code: 'OPTION_NOT_APPLICABLE' });
  const origin = { kind: 'home', agent: 'claude', homeKey:'fixture-choice', rolePrompt:'Instructions chosen in the fixture setup conversation.', permissions:{allowedTools:[],permissionMode:'default'}, host: 'box', cwd: '/project' };
  assert.deepEqual(parse(['send', 'me', 'done'], { TEXT_TARGET: JSON.stringify(origin), CLAUDE_CODE_SESSION_ID: 'uuid' }).payload.target, origin);
});
test('/ask adapter uses prompt files, correct flags and persistent home handles', async t => {
  const directory = temp(t), homes = {}, calls = [];
  const ask = new Ask({ directory, script: '/fake/ask.py', execute: async (bin, args) => {
    assert.equal(bin, 'python3'); assert.equal(args[0], '/fake/ask.py'); assert.ok(!args.includes('--message'));
    assert.equal(fs.readFileSync(args[args.indexOf('--prompt-file') + 1], 'utf8'), '`$(no)`\ntext');
    calls.push(args); if (args.includes('--start')) fs.writeFileSync(args[args.indexOf('--metadata') + 1], '{"threadId":"thread-uuid","status":"accepted"}');
    fs.writeFileSync(args[args.indexOf('--outcome')+1],JSON.stringify({version:1,kind:'accepted',code:'ENQUEUED'}));
    return { stdout: '', stderr: '' };
  } });
  const home = { kind: 'home', agent: 'codex', homeKey:'fixture-choice', rolePrompt:'Instructions chosen in the fixture setup conversation.', permissions:{sandbox:'read-only',approvalPolicy:'on-request'}, host: 'mac', cwd: '/project' };
  await ask.deliver(home, '`$(no)`\ntext', homes); await ask.deliver({ ...home }, '`$(no)`\ntext', homes);
  assert.ok(calls[0].includes('--start')); assert.ok(calls[0].includes('--mid-turn')); assert.ok(calls[0].includes('--exact'));
  assert.equal(calls[1][calls[1].indexOf('--session') + 1], 'thread-uuid');
  assert.equal(calls[1][calls[1].indexOf('--cwd') + 1], '/project');
  await ask.deliver({ kind: 'live', agent: 'claude', host: 'mac', id: 'uuid' }, '`$(no)`\ntext', homes);
  assert.ok(calls[2].includes('--exact'));
  await ask.deliver({ kind: 'standing', agent: 'claude', host: 'mac', id: 'plan/47' }, '`$(no)`\ntext', homes);
  assert.ok(calls[3].includes('--ensure')); assert.ok(calls[3].includes('plan/47'));
  assert.deepEqual(fs.readdirSync(directory), []);
  assert.throws(() => locateAsk({ ASK_SCRIPT: '/missing/ask.py' }), { code: 'ASK_MISSING' });
});
test('signed linked/unlinked forwarding preserves mentions, draft approval, media and reply threads', async t => {
  const dir=temp(t),target={kind:'live',agent:'codex',id:'11111111-1111-4111-8111-111111111111',host:'box'};
  const rulesFile=path.join(dir,'rules.json');write(rulesFile,{version:1,rules:[{chat:'group@g.us',label:'Project',answerMode:'draft',target,allow:['friend@lid'],triggers:{mention:true}}]});
  const common={homeHost:'mac',ownerChat:'owner@lid',owners:['owner@lid'],machines:['mac','box'],aiPrefix:'AI: '};
  const sends=[],asks=[],wire=[];let box;
  const linked=new Engine({config:{...common,host:'mac',proxyReceivers:['box']},rulesFile,stateFile:path.join(dir,'mac.json'),
    wa:{messageId:require('./native-id.cjs').nativeId,resolve:async c=>c,send:async(chat,text,options)=>{sends.push({chat,text,options});return {key:{id:options.id}};}},
    ask:{deliver:async()=>assert.fail('foreign target')},forward:async(host,req)=>{assert.equal(host,'box');wire.push(req);return box.request(req);}});
  box=new Engine({config:{...common,host:'box',linkedVia:'mac'},rulesFile,stateFile:path.join(dir,'box.json'),
    wa:proxy({host:'mac',directory:dir,send:async(_host,req)=>linked.request(req)}),
    forward:async(host,req)=>{assert.equal(host,'mac');wire.push(req);return linked.request(req);},
    ask:{deliver:async(...args)=>{asks.push(args);return {status:'delivered'};}}});
  const m=require('../engine/lib/normalize.cjs').normalize({key:{id:'question',remoteJid:'group@g.us',participant:'friend@lid'},message:{extendedTextMessage:{text:'hi',contextInfo:{mentionedJid:['bot@lid']}}}},['bot@lid']);
  await linked.receive(m);assert.equal(asks.length,1);
  const draft=await box.request({cmd:'respond',deliveryId:Object.keys(box.state.deliveries)[0],message:'answer'});
  assert.equal(sends[0].chat,'owner@lid');
  await linked.receive({id:'approval',chat:'owner@lid',senders:['owner@lid'],text:'send '+draft.draftId});
  assert.equal(sends[1].chat,'group@g.us');assert.equal(sends[1].options.quoted.key.id,'question');
  const signed=wire.find(r=>r.cmd==='relay-event');
  const victim=signed.envelope.payload.to==='mac'?linked:box;
  await assert.rejects(victim.request(signed),{code:'RELAY_REPLAY'});
  const forged=structuredClone(signed);forged.envelope.payload.id='new-id';forged.envelope.payload.data.text='forged';
  await assert.rejects(victim.request(forged),{code:'UNTRUSTED_RELAY'});
});
test('confirmed rule changes replicate with prepare/commit and block unsafe stale routing',async t=>{
  const dir=temp(t),bundle={version:1,rules:[]},engines={},sends=[];
  const network=async(host,req)=>engines[host].request(req);
  for(const host of ['mac','box']){
    const rulesFile=path.join(dir,host+'-rules.json');write(rulesFile,bundle);
    engines[host]=new Engine({config:{host,homeHost:'mac',ownerChat:'owner@lid',owners:['owner@lid'],machines:['mac','box'],aiPrefix:'AI: '},
      rulesFile,stateFile:path.join(dir,host+'-state.json'),forward:network,ask:{},wa:{messageId:require('./native-id.cjs').nativeId,send:async(chat,text,options)=>{sends.push({host,chat,text});return {key:{id:options.id}};}}});
  }
  const next={version:1,rules:[{chat:'project@g.us',label:'Project',private:true,target:{kind:'home',agent:'claude', homeKey:'fixture-choice', rolePrompt:'Instructions chosen in the fixture setup conversation.', permissions:{allowedTools:[],permissionMode:'default'},host:'box',cwd:'/project'},triggers:{every:true}}]};
  const proposed=await engines.mac.request({cmd:'rules-set',bundle:next});
  assert.deepEqual(engines.box.rules(),bundle);
  await engines.mac.receive({id:'approve-rules',chat:'owner@lid',senders:['owner@lid'],text:'confirm '+proposed.proposalId});
  assert.deepEqual(engines.box.rules(),next);assert.deepEqual(engines.mac.rules(),next);
  assert.ok(sends.every(s=>s.chat==='owner@lid'));
  const again=await engines.mac.request({cmd:'rules-set',bundle:{version:1,rules:[]}});
  write(engines.box.rulesFile,{version:1,rules:[{...next.rules[0],label:'Concurrent edit'}]});
  await assert.rejects(engines.mac.receive({id:'conflict',chat:'owner@lid',senders:['owner@lid'],text:'confirm '+again.proposalId}),{code:'RULE_CONFLICT'});
  assert.deepEqual(engines.mac.rules(),next);
});
test('SSH intake failures are held until genuine owner resend and the peer dedupes input',async t=>{
  const dir=temp(t),engines={},sent=[],asks=[];
  const target={kind:'live',agent:'codex',id:'11111111-1111-4111-8111-111111111111',host:'box'};
  const rulesFile=path.join(dir,'rules.json');write(rulesFile,{version:1,rules:[{chat:'jobs@g.us',label:'Jobs',target,answerMode:'answer',allow:['friend@lid'],triggers:{every:true}}]});
  let offline=true;
  const network=async(host,req)=>{if(host==='box'&&offline)throw Object.assign(new Error('SSH offline'),{notDelivered:true});return engines[host].request(req);};
  for(const host of ['mac','box'])engines[host]=new Engine({config:{host,homeHost:'mac',ownerChat:'owner@lid',owners:['owner@lid'],machines:['mac','box'],aiPrefix:'AI: ',...(host==='mac'?{proxyReceivers:['box']}:{linkedVia:'mac'})},rulesFile,stateFile:path.join(dir,host+'.json'),forward:network,
    wa:{messageId:require('./native-id.cjs').nativeId,send:async(chat,text,options)=>{sent.push({chat,text});return {key:{id:options.id}};}},ask:{deliver:async()=>{asks.push('delivered');return {status:'delivered'};}}});
  const m={id:'forward-me',chat:'jobs@g.us',senders:['friend@lid'],text:'hi'};
  await assert.rejects(engines.mac.receive(m),/offline/);assert.equal(engines.mac.retryQueue.count(),0);assert.equal(sent[0].chat,'owner@lid');
  offline=false;await engines.mac.retry();assert.equal(asks.length,0);
  const held=Object.values(engines.mac.state.failures)[0];await engines.mac.receive({id:'resend',chat:'owner@lid',senders:['owner@lid'],text:'resend '+held.id});assert.equal(asks.length,1);
  assert.equal((await engines.mac.receive(m)).duplicate,true);assert.equal(asks.length,1);
});
test('a lost rule-commit acknowledgement cannot cause rollback of an already committed peer',async t=>{
  const dir=temp(t),engines={},bundle={version:1,rules:[]};let loseAck=true;
  const network=async(host,req)=>{const result=await engines[host].request(req);if(loseAck&&req.envelope?.payload.kind==='rules-commit'){loseAck=false;throw new Error('lost commit acknowledgement');}return result;};
  for(const host of ['mac','box']){
    const rulesFile=path.join(dir,host+'rules.json');write(rulesFile,bundle);
    engines[host]=new Engine({config:{host,homeHost:'mac',ownerChat:'owner@lid',owners:['owner@lid'],machines:['mac','box'],aiPrefix:'AI: '},rulesFile,stateFile:path.join(dir,host+'.json'),forward:network,ask:{},wa:{messageId:require('./native-id.cjs').nativeId,send:async(_c,_t,o)=>({key:{id:o.id}})}});
  }
  const next={version:1,rules:[{chat:'project@g.us',private:true,target:{kind:'home',agent:'claude', homeKey:'fixture-choice', rolePrompt:'Instructions chosen in the fixture setup conversation.', permissions:{allowedTools:[],permissionMode:'default'},host:'mac',cwd:'/project'},triggers:{every:true}}]};
  const proposal=await engines.mac.request({cmd:'rules-set',bundle:next});
  const own=(id,text)=>engines.mac.receive({id,chat:'owner@lid',senders:['owner@lid'],text});
  const approved=await own('confirm1','confirm '+proposal.proposalId);assert.deepEqual(approved.pending,['box']);
  assert.deepEqual(engines.box.rules(),next);assert.deepEqual(engines.mac.rules(),next);
  await assert.rejects(own('discard','discard '+proposal.proposalId),{code:'DRAFT_REQUIRED'});
  await engines.mac.retry();assert.deepEqual(engines.mac.state.proposals[proposal.proposalId].pending,[]);assert.deepEqual(engines.mac.rules(),next);assert.equal(engines.mac.state.prepared,undefined);
});
test('configured linkedVia sends enter the local owning engine rather than bypassing it over SSH',async t=>{
  const dir=temp(t),runtime=path.join(dir,'runtime'),shim=path.join(dir,'shim');fs.cpSync(path.resolve(__dirname,'..'),runtime,{recursive:true});fs.mkdirSync(shim);
  const marker=path.join(dir,'ssh-called');fs.writeFileSync(path.join(shim,'ssh'),'#!/bin/sh\ntouch '+JSON.stringify(marker)+'\nexit 78\n',{mode:0o700});
  write(path.join(dir,'config.json'),{host:'box',homeHost:'mac',ownerChat:'owner@lid',owners:['owner@lid'],aiPrefix:'AI: ',linkedVia:'mac'});
  let got;const socket=path.join(dir,'engine.sock'),server=net.createServer(c=>c.once('data',data=>{got=JSON.parse(data);c.end('{"ok":true,"id":"fake"}\n');}));await new Promise(r=>server.listen(require('../engine/lib/socket.cjs').address(socket),r));t.after(()=>server.close());
  const env={...process.env,TEXT_HOME:dir,PATH:shim+':'+process.env.PATH};delete env.TEXT_ACCOUNT;
  const result=await promisify(execFile)(process.execPath,[path.join(runtime,'scripts/text.cjs'),'send','me','local first','--plain','--as','worker','--uuid','11111111-1111-4111-8111-111111111111','--agent','claude'],{env});
  assert.equal(JSON.parse(result.stdout).ok,true);assert.equal(got.cmd,'send');assert.equal(got.message,'local first');assert.equal(fs.existsSync(marker),false);
});
test('proxy keeps the linked transport positive non-delivery signal',async()=>{
  const p=proxy({host:'mac',directory:'/unused',send:async()=>({ok:false,code:'WA_RECONNECTING',notDelivered:true,error:'Not submitted'})});
  await assert.rejects(p.resolve('me'),e=>e.code==='WA_RECONNECTING'&&e.notDelivered===true&&require('../engine/lib/failure.cjs').transient(e));
});
test('a peer-held agent failure has one owner and genuine resend reaches that owner',async t=>{
  const dir=temp(t),engines={},sends=[],rulesFile=path.join(dir,'rules.json'),target={kind:'live',agent:'codex',host:'box',id:'11111111-1111-4111-8111-111111111111'};
  write(rulesFile,{version:1,rules:[{chat:'jobs@g.us',target,answerMode:'answer',allow:['friend@lid'],triggers:{every:true}}]});
  const common={homeHost:'mac',ownerChat:'owner@lid',owners:['owner@lid'],aiPrefix:'AI: ',machines:['mac','box']};
  const forward=async(host,req)=>{try{return await engines[host].request(req);}catch(e){return {ok:false,code:e.code,error:e.message,held:e.held,failureId:e.failureId,notDelivered:e.notDelivered};}};
  engines.mac=new Engine({config:{...common,host:'mac',proxyReceivers:['box']},rulesFile,stateFile:path.join(dir,'mac.json'),forward,ask:{},wa:{messageId:require('./native-id.cjs').nativeId,status:async()=>({ready:true,linked:true}),send:async(chat,text)=>sends.push({chat,text})}});
  let fail=true,calls=0;const prompts=[];engines.box=new Engine({config:{...common,host:'box',linkedVia:'mac'},rulesFile,stateFile:path.join(dir,'box.json'),forward,wa:proxy({host:'mac',directory:dir,send:forward}),ask:{deliver:async(_target,prompt)=>{calls++;prompts.push(prompt);if(fail)throw Object.assign(new Error('unreachable'),{notDelivered:true});return {status:'delivered'};}}});
  await assert.rejects(engines.mac.receive({id:'original',chat:'jobs@g.us',senders:['friend@lid'],text:'question'}));
  assert.equal(Object.keys(engines.mac.state.failures).length,0);const held=Object.values(engines.box.state.failures);assert.equal(held.length,1);assert.ok(sends.every(s=>s.chat==='owner@lid'));
  fail=false;await engines.mac.receive({id:'owner-resend',chat:'owner@lid',senders:['owner@lid'],text:'resend '+held[0].id});assert.equal(calls,2);assert.equal(engines.box.state.failures[held[0].id].status,'sent');
  engines.mac.wa.download=async()=>{throw new Error('fetch failed');};
  await assert.rejects(engines.mac.receive({id:'voice',chat:'jobs@g.us',senders:['friend@lid'],text:'voice question',media:{kind:'voice note',extension:'ogg'}}),{code:'MEDIA_INTAKE_FAILED'});
  const mediaHold=Object.values(engines.mac.state.failures)[0];assert.equal(calls,2);assert.equal(mediaHold.acceptance,'not-delivered');
  const mediaFile=path.join(dir,'source.ogg');fs.writeFileSync(mediaFile,'voice bytes');engines.mac.wa.download=async()=>mediaFile;
  await engines.mac.receive({id:'resend-voice',chat:'owner@lid',senders:['owner@lid'],text:'resend '+mediaHold.id});assert.equal(calls,3);assert.match(prompts.at(-1),/Transcribe locally/);assert.equal(engines.mac.state.failures[mediaHold.id].status,'sent');
});
test('JSON entrance requires cmd and preserves the documented inspection receipt',async t=>{
  const dir=temp(t),socket=path.join(dir,'engine.sock');let calls=0,actual;
  const server=net.createServer(c=>c.once('data',data=>{calls++;actual=JSON.parse(data);c.end('{"ok":true}\n');}));await new Promise(r=>server.listen(require('../engine/lib/socket.cjs').address(socket),r));t.after(()=>server.close());
  const invoke=(payload,options=[])=>{const p=promisify(execFile)(process.execPath,[path.resolve(__dirname,'../scripts/text.cjs'),'request',...options],{env:{...process.env,TEXT_HOME:dir,TEXT_ACCOUNT:'main'}});p.child.stdin.end(JSON.stringify(payload));return p;};
  await assert.rejects(invoke({command:'failure-resolve'}),e=>e.stderr.includes('INVALID_REQUEST'));assert.equal(calls,0);
  await assert.rejects(invoke({cmd:'   '}),e=>e.stderr.includes('INVALID_REQUEST'));
  await assert.rejects(invoke({cmd:'failure-resolve',account:123}),e=>e.stderr.includes('INVALID_ACCOUNT'));
  await assert.rejects(invoke({cmd:'failure-resolve',account:'main'},['--account','us']),e=>e.stderr.includes('ACCOUNT_CONFLICT'));assert.equal(calls,0);
  const expected={cmd:'failure-resolve',id:'held-id',outcome:'accepted',evidence:'Inspected actual transcript',receipt:{sessionId:'actual-id'}};
  assert.equal(JSON.parse((await invoke(expected)).stdout).ok,true);assert.deepEqual(actual,expected);assert.equal(calls,1);
});
test('secondary-account proxy discovery and publication use one bound SSH entrance',async()=>{
  const calls=[],bound=require('../engine/lib/client.cjs').forAccount('us',async(host,payload)=>{calls.push({host,payload});return {ok:true,chat:'owner@lid',id:'message-id',ready:true,linked:true};});
  const p=proxy({host:'mac',directory:'/unused',send:bound});await p.resolve('me');await p.status();await p.messageId();await bound('mac',{cmd:'relay-event',envelope:{signed:'fake'}});
  assert.equal(calls.length,4);assert.ok(calls.every(c=>c.host==='mac'&&c.payload.account==='us'));assert.deepEqual(calls[3].payload.envelope,{signed:'fake'});
  assert.throws(()=>bound('mac',{cmd:'ping',account:'main'}),{code:'ACCOUNT_CONFLICT'});assert.equal(calls.length,4);
});
test('SSH preserves a held JSON receipt even when the remote CLI exits 1',async()=>{
  const expected={ok:false,code:'ASK_FAILED',held:true,failureId:'peer-held',error:'unreachable'};
  const execute=()=>{const child=new EventEmitter();child.stdout=new PassThrough();child.stderr=new PassThrough();child.stdin=new PassThrough();child.stdin.on('finish',()=>{child.stdout.write(JSON.stringify(expected));child.emit('close',1);});return child;};
  assert.deepEqual(await remote('box',{cmd:'send'},execute),expected);
});
test('SSH bounds an unresponsive peer without calling an uncertain send non-delivery',async()=>{
  const children=[];const execute=()=>{const c=new EventEmitter();c.stdout=new PassThrough();c.stderr=new PassThrough();c.stdin=new PassThrough();c.kill=signal=>{c.signal=signal;};children.push(c);return c;};
  await assert.rejects(remote('box',{cmd:'send'},execute,{timeoutMs:5}),e=>e.code==='SSH_ACCEPTANCE_UNKNOWN'&&e.notDelivered===false);
  await assert.rejects(remote('box',{cmd:'resolve',chat:'me'},execute,{timeoutMs:5}),e=>e.code==='SSH_ACCEPTANCE_UNKNOWN'&&e.notDelivered===true);
  assert.ok(children.every(c=>c.signal==='SIGTERM'));
});
