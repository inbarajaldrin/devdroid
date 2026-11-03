const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs'), path = require('node:path'), os = require('node:os');
const { Readable } = require('node:stream');
const {EventEmitter}=require('node:events');
const { normalize } = require('../engine/lib/normalize.cjs');
async function fixture(t) {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'text-baileys-')); t.after(() => fs.rmSync(directory, { recursive: true, force: true }));
  const handlers = {}, sends = [], delivered = [], timers = [], exits = [], errors = [];
  const sock = { ws:new EventEmitter(), user: { id: '100:2@s.whatsapp.net', lid: '200@lid' }, ev: { on: (name, callback) => handlers[name] = callback },
    sendMessage: async (chat, content, options) => { sends.push({ chat, content, options }); if(!deps.manualAck)queueMicrotask(()=>sock.ws.emit('CB:ack,class:message',{tag:'ack',attrs:{class:'message',id:options.messageId}}));return { key: { remoteJid: chat, id: options.messageId, fromMe: true } }; },
    groupFetchAllParticipating: async () => ({ 'project@g.us': { subject: 'Project' } }),
    groupMetadata: async () => ({ subject: 'Project', participants: [{ id: 'friend@lid', phoneNumber: '123@s.whatsapp.net', lid: 'friend@lid' }] }), end() {} };
  const deps = { default: () => sock, useMultiFileAuthState: async () => { if (deps.failAuth) throw new Error('reconnect failed'); return { state: { creds: deps.unlinked ? {} : { me: { id: '100@s.whatsapp.net' } } }, saveCreds: async () => {} }; },
    DisconnectReason: { loggedOut: 401, restartRequired: 515 }, Browsers: { macOS: name => name }, fetchLatestBaileysVersion: async () => ({ version: [1,2,3] }),
    downloadMediaMessage: async (...args) => deps.download ? deps.download(...args) : Readable.from([Buffer.from('media bytes')]), pino: () => ({}), qrcode: {}, setTimeout: fn => { timers.push(fn); },
    toWebpSticker: async file => ({ out: file, animated: true }), gifToMp4: file => file, generateMessageIDV2: require('./native-id.cjs').nativeId };
  const { transport } = await import('../engine/baileys/transport.mjs');
  const options = { directory, config: { ownerChat: 'owner@lid', ownerNumbers: ['123'] }, dependencies: deps,
    onMessage: async (raw, linked) => delivered.push(normalize(raw, linked)), onError: e => errors.push(e), onExit: c => exits.push(c) };
  const wa = await transport(options); t.after(() => wa.close());
  await handlers['connection.update']({ connection: 'open' });
  return { wa, transport, options, directory, handlers, sock, sends, delivered, timers, exits, errors, deps };
}
test('fake Baileys preserves animated WebP, video detection, case-insensitive MIME and ptt', async t => {
  const f = await fixture(t), data = Buffer.from('bytes').toString('base64');
  assert.match(f.wa.messageId(),/^3EB0[A-F0-9]{18}$/);
  for (const media of [{ kind:'sticker',name:'anim.WEBP',data },{ kind:'image',name:'clip.MP4',data },{ kind:'file',name:'REPORT.PDF',data },{ kind:'audio',name:'voice.ogg',data,ptt:true }]) {
    await f.wa.send('project@g.us','AI: caption',{ id:'D3D0'+String(f.sends.length).padStart(28,'0'),media });
  }
  assert.equal(f.sends[0].content.isAnimated,true); assert.ok(f.sends[2].content.video);
  assert.equal(f.sends[3].content.mimetype,'application/pdf'); assert.equal(f.sends[4].content.ptt,true);
  assert.ok(f.sends[1].options.quoted); assert.ok(f.sends[5].options.quoted);
  const { toWebpSticker } = await import('../engine/baileys/media.mjs');
  const passthrough = await toWebpSticker('/fake/animation.webp',{ sharp: () => ({ metadata: async () => ({ pages: 4 }) }) });
  assert.deepEqual(passthrough,{ out:'/fake/animation.webp',animated:true });
});
test('typed inbound voice/images/video/documents retain extensions and own-send echoes are suppressed', async t => {
  const f=await fixture(t);
  const raw={key:{id:'voice',remoteJid:'project@g.us',participant:'friend@lid'},message:{audioMessage:{ptt:true,mimetype:'audio/ogg'}}};
  f.handlers['messages.upsert']({messages:[raw]});
  await new Promise(r=>setImmediate(r));
  const m=f.delivered[0];assert.deepEqual(m.media,{type:'audioMessage',kind:'voice note',extension:'ogg'});
  const file=await f.wa.download(m);assert.equal(path.extname(file),'.ogg');assert.equal(fs.readFileSync(file,'utf8'),'media bytes');
  assert.equal(await f.wa.download(m),file);
  for (const [content,extension] of [[{imageMessage:{mimetype:'image/png'}},'png'],[{videoMessage:{}},'mp4'],[{documentMessage:{fileName:'Report.PDF'}},'PDF']]) assert.equal(normalize({key:{},message:content},[]).media.extension,extension);
  await f.wa.send('owner@lid','done',{id:'own-send'});
  f.handlers['messages.upsert']({messages:[{key:{id:'own-send',remoteJid:'owner@lid',fromMe:true},message:{conversation:'done'}}]});
  assert.equal(f.delivered.length,1);
});
test('H1: Baileys backlog bursts have at most four active deliveries across event batches',async t=>{
  const f=await fixture(t);let active=0,maximum=0,completed=0;
  const wa=await f.transport({...f.options,onMessage:async()=>{active++;maximum=Math.max(active,maximum);await new Promise(r=>setImmediate(r));active--;completed++;}});t.after(()=>wa.close());
  const messages=Array.from({length:260},(_,i)=>({key:{id:'backlog-'+i},message:{conversation:'fixture'}}));
  f.handlers['messages.upsert']({messages});f.handlers['messages.upsert']({messages:messages.slice(0,4)});
  while(completed<264)await new Promise(r=>setImmediate(r));assert.ok(maximum<=4);assert.equal(completed,264);
});
test('F7: every item in a received backlog is durable before waiting behind active work',async t=>{
 const f=await fixture(t);let finish,started=0;const gate=new Promise(r=>finish=r);
 const wa=await f.transport({...f.options,onMessage:async()=>{started++;await gate;}});t.after(()=>wa.close());
 const messages=Array.from({length:40},(_,i)=>({key:{id:'durable-'+i,remoteJid:'project@g.us',participant:'friend@lid'},message:{conversation:'fixture '+i}}));
 f.handlers['messages.upsert']({messages});
 try{const dir=path.join(f.directory,'intake');assert.equal(fs.existsSync(dir)?fs.readdirSync(dir).length:0,40);await new Promise(r=>setImmediate(r));assert.equal(started,4);wa.close();assert.ok(fs.readdirSync(dir).length>=36);}finally{finish();}
});
test('H2: a socket write stays pending until server acknowledgement and ERROR 463 refuses it',async t=>{
  const f=await fixture(t);f.deps.manualAck=true;let done=false;const sending=f.wa.send('project@g.us','fixture',{id:'server-refusal'}).finally(()=>done=true);
  await new Promise(r=>setImmediate(r));assert.equal(done,false);assert.equal(typeof f.handlers['messages.update'],'function');
  f.handlers['messages.update']([{key:{id:'server-refusal'},update:{status:0,messageStubParameters:['463']}}]);
  await assert.rejects(sending,e=>e.code==='WA_SERVER_REJECTED' && e.serverCode==='463' && e.notDelivered===true);
  const accepted=f.wa.send('project@g.us','fixture',{id:'server-ack'});await new Promise(r=>setImmediate(r));
  f.handlers['messages.update']([{key:{id:'server-ack'},update:{status:2}}]);assert.equal((await accepted).key.id,'server-ack');
});
test('H2: native server acks complete group sends without a messages.update receipt',async t=>{
  const f=await fixture(t);f.deps.manualAck=true;f.options.config.serverAckMs=10;
  const sending=f.wa.send('project@g.us','fixture',{id:'native-server-ack'});await new Promise(r=>setImmediate(r));f.sock.ws.emit('CB:ack,class:message',{tag:'ack',attrs:{class:'message',id:'native-server-ack'}});
  assert.equal((await sending).key.id,'native-server-ack');
});
test('M8: linking and credential rewrites tighten an existing readable auth directory',async t=>{
  const f=await fixture(t),auth=path.join(f.directory,'auth');fs.chmodSync(auth,0o755);const creds=path.join(auth,'creds.json');fs.writeFileSync(creds,'{}',{mode:0o644});
  const wa=await f.transport({...f.options,login:true,phone:'00000000000'});t.after(()=>wa.close());await f.handlers['creds.update']({});
  assert.equal(fs.statSync(auth).mode&0o777,0o700);assert.equal(fs.statSync(creds).mode&0o777,0o600);
});
test('a QR link passes every rotated code on, while a phone link asks for one code',async t=>{
  const f=await fixture(t),seen=[];const wa=await f.transport({...f.options,login:true,onQr:async code=>{seen.push(code);}});t.after(()=>wa.close());
  await f.handlers['connection.update']({qr:'first'});await f.handlers['connection.update']({qr:'second'});
  assert.deepEqual(seen,['first','second']);
});
test('L2: interrupted downloads leave no trusted final file and equal ids in different chats cannot share a cache',async t=>{
  const f=await fixture(t);let calls=0;
  f.deps.download=async()=>{calls++;return Readable.from(['complete']);};
  // A legacy partial must never count as a completed download.
  fs.mkdirSync(path.join(f.directory,'media'));fs.writeFileSync(path.join(f.directory,'media/fixture.ogg'),'partial');
  const message={id:'fixture',chat:'one@g.us',media:{extension:'ogg'},raw:{message:{audioMessage:{}}}};
  const a=await f.wa.download(message);assert.equal(fs.readFileSync(a,'utf8'),'complete');const b=await f.wa.download({...message,chat:'two@g.us'});assert.notEqual(a,b);assert.equal(calls,2);
});
test('L8: expired-media replay gives the re-upload path binary media keys',async t=>{
  const f=await fixture(t);let uploaded=false;
  f.sock.updateMediaMessage=async raw=>{assert.ok(raw.message.audioMessage.mediaKey instanceof Uint8Array);uploaded=true;};
  f.deps.download=async(raw,_kind,_options,context)=>{await context.reuploadRequest(raw);return Readable.from(['reuploaded']);};
  const raw={message:{audioMessage:{mediaKey:Buffer.alloc(32,1).toString('base64')}}};await f.wa.download({id:'replay',chat:'one@g.us',media:{extension:'ogg'},raw});assert.equal(uploaded,true);
});
test('disconnect reconnect failure exits 1; logout and unlinked startup exit 0 without restart loops',async t=>{
  const f=await fixture(t);
  await f.handlers['connection.update']({connection:'close',lastDisconnect:{error:{output:{statusCode:500}}}});
  assert.equal(f.timers.length,1);
  f.deps.failAuth=true;
  await f.timers[0]();assert.deepEqual(f.exits,[1]);assert.match(f.errors[0].message,/reconnect failed/);
  const g=await fixture(t);await g.handlers['connection.update']({connection:'close',lastDisconnect:{error:{output:{statusCode:401}}}});
  assert.deepEqual(g.exits,[0]);assert.equal(g.timers.length,0);
  g.deps.unlinked=true;
  await assert.rejects(g.transport(g.options),e=>e.code==='NOT_LINKED'&&e.exitCode===0);
});
test('contact index and members expose the live phone/LID/name map to the user',async t=>{
  const f=await fixture(t);f.handlers['contacts.upsert']([{id:'123@s.whatsapp.net',name:'Friend'}]);
  const members=await f.wa.members('project@g.us');assert.equal(members.participants[0].name,'Friend');assert.equal(members.participants[0].lid,'friend@lid');
  assert.ok((await f.wa.find('Project')).some(c=>c.id==='project@g.us'));
});
test('reconnecting is proven non-delivery; expired login and socket acceptance errors never get automatic retry authority',async t=>{
  const f=await fixture(t);
  await f.handlers['connection.update']({connection:'close',lastDisconnect:{error:{output:{statusCode:500}}}});
  await assert.rejects(f.wa.send('owner@lid','held',{id:'A'}),e=>e.code==='WA_RECONNECTING'&&e.notDelivered===true);assert.equal(f.sends.length,0);
  await f.handlers['connection.update']({connection:'open'});f.sock.sendMessage=async()=>{throw new Error('socket closed after write');};
  await assert.rejects(f.wa.send('owner@lid','uncertain',{id:'B'}),e=>e.notDelivered!==true);
  await f.handlers['connection.update']({connection:'close',lastDisconnect:{error:{output:{statusCode:401}}}});
  await assert.rejects(f.wa.send('owner@lid','held',{id:'C'}),e=>e.code==='LOGIN_EXPIRED'&&e.notDelivered===true);
});
test('status posts and channels never reach an agent',async t=>{
  const f=await fixture(t);
  f.handlers['messages.upsert']({messages:[
    {key:{id:'status',remoteJid:'status@broadcast',remoteJidAlt:'friend@s.whatsapp.net',participant:'friend@lid'},message:{conversation:'my status'}},
    {key:{id:'channel',remoteJid:'news@newsletter'},message:{conversation:'channel post'}},
    {key:{id:'chat',remoteJid:'project@g.us',participant:'friend@lid'},message:{conversation:'hello'}}]});
  await new Promise(r=>setImmediate(r));
  assert.deepEqual(f.delivered.map(m=>m.id),['chat']);
});
test('find reuses the group list for a while and survives a WhatsApp rate limit',async t=>{
  const f=await fixture(t);let fetches=0;
  f.sock.groupFetchAllParticipating=async()=>{fetches++;if(fetches>1)throw new Error('rate-overlimit');return {'project@g.us':{subject:'Project'}};};
  assert.ok((await f.wa.find('Project')).some(c=>c.id==='project@g.us'));
  assert.ok((await f.wa.find('proj')).some(c=>c.id==='project@g.us'));
  assert.equal(fetches,1);
});
test('a new group is created with only the owner and becomes findable',async t=>{
  const f=await fixture(t);const calls=[];
  f.sock.groupCreate=async(subject,participants)=>{calls.push([subject,participants]);return {id:'new@g.us',subject};};
  assert.deepEqual(await f.wa.createGroup('devdroid'),{id:'new@g.us',name:'devdroid'});
  assert.deepEqual(calls,[['devdroid',[]]]);
  assert.ok((await f.wa.find('devdroid')).some(c=>c.id==='new@g.us'));
});
test('adding a member reports WhatsApp refusals',async t=>{
  const f=await fixture(t);let status='200';
  f.sock.groupParticipantsUpdate=async(jid,people,action)=>[{jid:people[0],status,action}];
  assert.deepEqual(await f.wa.addToGroup('project@g.us','123@s.whatsapp.net'),{chat:'project@g.us',added:'123@s.whatsapp.net'});
  status='403';await assert.rejects(f.wa.addToGroup('project@g.us','123@s.whatsapp.net'),{code:'GROUP_ADD_FAILED'});
});
test('a new group holds the account the owner reads and never lists the sending account itself',async t=>{
  const f=await fixture(t);const calls=[];
  f.sock.groupCreate=async(subject,participants)=>{calls.push(participants);return {id:'g2@g.us',subject};};
  await f.wa.createGroup('paper',['reader@s.whatsapp.net','100@s.whatsapp.net','200@lid']);
  assert.deepEqual(calls,[['reader@s.whatsapp.net']]);
});
