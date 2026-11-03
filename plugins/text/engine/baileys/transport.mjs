import fs from 'node:fs';
import path from 'node:path';
import {createHash,randomUUID} from 'node:crypto';
import { Transform } from 'node:stream';
import { pipeline } from 'node:stream/promises';
import store from '../lib/store.cjs';
import rules from '../lib/rules.cjs';
import errors from '../lib/errors.cjs';
import inbound from '../lib/inbound-guard.cjs';
import authFiles from '../lib/auth.cjs';
import intakeFiles from '../lib/intake.cjs';
import normalization from '../lib/normalize.cjs';
import { toWebpSticker, gifToMp4, MIME } from './media.mjs';
const { insist } = errors;
export async function transport({ directory, config, login = false, phone, onMessage, onBatch, onCode, onQr, onReady, onError, onExit = () => {}, dependencies }) {
  const deps = dependencies || { ...(await import('baileys')), pino: (await import('pino')).default, qrcode: (await import('qrcode-terminal')).default };
  const { default: makeWASocket, useMultiFileAuthState, DisconnectReason, Browsers, fetchLatestBaileysVersion, downloadMediaMessage, pino, qrcode } = deps;
  // Every account under one runtime root shares this list of sent message ids.
  const root = path.basename(path.dirname(directory)) === 'accounts' ? path.dirname(path.dirname(directory)) : directory;
  const guard = inbound.createInboundGuard(new Set(config.ownerNumbers || []), path.join(root, 'engine-sends.log'));
  let sock, ready = false, closed = false, retry = 1000, retryTimer;
  let intake = Promise.resolve();
  const acknowledgements=new Map();
  async function acknowledgedSend(chat,content,options){
    let timer,resolve,reject;
    const ack=new Promise((yes,no)=>{resolve=yes;reject=no;});
    // The handler is installed before writing: an ack can race sendMessage's return.
    acknowledgements.set(options.messageId,{resolve,reject});
    timer=setTimeout(()=>reject(new errors.Refusal('WA_ACK_UNKNOWN','WhatsApp server acknowledgement did not arrive; inspect before resending')),config.serverAckMs || 60000);timer.unref();
    try{
      const [sent]=await Promise.all([sock.sendMessage(chat,content,options),ack]);return sent;
    }finally{clearTimeout(timer);acknowledgements.delete(options.messageId);}
  }
  const contactsFile = path.join(directory, 'contacts.json');
  const contacts = store.read(contactsFile, {}), groups = new Map();
  const GROUP_LIST_TTL = 5 * 60 * 1000; let groupsFetchedAt = 0;
  const linked = [];
  function requireReady() {
    if (ready) return;
    const error = new errors.Refusal(closed ? 'LOGIN_EXPIRED' : 'WA_RECONNECTING', closed ? 'WhatsApp login expired; link again with the user present' : 'WhatsApp is reconnecting; nothing was submitted');
    error.notDelivered = true; throw error;
  }
  async function connect() {
    const authDirectory=path.join(directory,'auth');authFiles.secure(authDirectory);
    const { state: auth, saveCreds: save } = await useMultiFileAuthState(authDirectory);
    if(auth.keys?.set){const set=auth.keys.set.bind(auth.keys);auth.keys.set=async(...args)=>{try{return await set(...args);}finally{authFiles.secure(authDirectory);}};}
    const saveCreds=async()=>{await save();authFiles.secure(authDirectory);};
    // Baileys uses me.id for reconnect; QR links need not set registered.
    if (!login && !auth.creds.me?.id) { const e = new Error('Link this runtime with setup link'); e.code = 'NOT_LINKED'; e.exitCode = 0; throw e; }
    let version;
    try { ({ version } = await fetchLatestBaileysVersion()); } catch { /* Baileys ships a default version. */ }
    sock = makeWASocket({ auth, version, browser: Browsers.macOS('Safari'), logger: pino({ level: 'silent' }),
      markOnlineOnConnect: false, emitOwnEvents: false, syncFullHistory: false, cachedGroupMetadata: async jid => groups.get(jid) });
    sock.ev.on('creds.update', async () => { try { await saveCreds(); } catch (e) { onError(e); } });
    function receipts(items){
      for(const {key,update} of items){const receipt=acknowledgements.get(key?.id);if(!receipt)continue;
        if(update?.status===0){const error=new errors.Refusal('WA_SERVER_REJECTED','WhatsApp refused the message (server code '+(update.messageStubParameters?.join(', ') || 'unknown')+')');error.serverCode=update.messageStubParameters?.[0];error.notDelivered=true;receipt.reject(error);}
        else if(update?.status>=2)receipt.resolve();
      }
    }
    sock.ev.on('messages.update',receipts);
    // The server's positive stanza ack is a socket event; group delivery receipts
    // use message-receipt.update instead of messages.update in this SDK.
    insist(sock.ws?.on,'WA_ACK_UNAVAILABLE','The socket must expose server acknowledgement events');
    sock.ws.on('CB:ack,class:message',({attrs})=>{
      if(attrs?.id)receipts([{key:{id:attrs.id},update:attrs.error?{status:0,messageStubParameters:[attrs.error]}:{status:2}}]);
    });
    let requested = false;
    sock.ev.on('connection.update', async u => {
      try {
        if (u.qr && login) {
          if (!phone) await (onQr ? onQr(u.qr) : qrcode.generate(u.qr, { small: true }));
          else if (!requested) { requested = true; await onCode(await sock.requestPairingCode(phone)); }
        }
        if (u.connection === 'open') {
          ready = true; retry = 1000;
          linked.splice(0, linked.length, ...[sock.user?.id, sock.user?.lid].filter(Boolean).map(rules.bare));
          config.linkedJids = linked;
          await saveCreds();
          await onReady?.(linked);
        }
        if (u.connection === 'close' && !closed) {
          ready = false;
          const code = u.lastDisconnect?.error?.output?.statusCode;
          if (code === DisconnectReason.loggedOut) { closed = true; onExit(0); return; }
          retryTimer = (deps.setTimeout || setTimeout)(() => connect().catch(e => { onError(e); onExit(1); }), code === DisconnectReason.restartRequired ? 0 : retry);
          retry = Math.min(retry * 2, 60000);
        }
      } catch (e) { onError(e); }
    });
    function note(items) {
      for (const c of items) if (c.id && (c.name || c.notify || c.subject)) contacts[c.id] = c.name || c.notify || c.subject;
      store.write(contactsFile, contacts);
    }
    sock.ev.on('contacts.upsert', note); sock.ev.on('contacts.update', note); sock.ev.on('groups.update', note);
    sock.ev.on('messaging-history.set', ({ contacts: cs = [], chats = [] }) => note([...cs, ...chats]));
    sock.ev.on('group-participants.update', ({ id }) => groups.delete(id));
    sock.ev.on('messages.upsert', ({ messages }) => {
      if(login)return;
      // Status posts and channels are broadcasts, never a chat anyone wrote to; they reach no agent.
      const batch=messages.filter(m=>m.message && !/@(broadcast|newsletter)$/.test(m.key?.remoteJid||'') && !guard.consumeDaemonSend(m.key?.id)).map(raw=>({raw,file:intakeFiles.retain(path.join(directory,'intake'),normalization.normalize(raw,linked)).file}));
      if(onBatch){intake=intake.then(async()=>{if(closed)return;const results=await onBatch(batch.map(m=>m.raw),linked);for(let i=0;i<batch.length;i++)if(results?.[i]?.status!=='rejected')fs.rmSync(batch[i].file,{force:true});}).catch(onError);return;}
      // Baileys flushes its offline backlog in bursts. Bound work across batches too.
      for(let i=0;i<batch.length;i+=4){const items=batch.slice(i,i+4);
        intake=intake.then(()=>Promise.all(items.map(async({raw,file})=>{
          if(closed)return;try{await onMessage(raw,linked);fs.rmSync(file,{force:true});}catch(e){onError(e);}
        }))).catch(onError);
      }
    });
  }
  const api = {
    async aliases(jid) {
      const mapping = sock.signalRepository?.lidMapping;
      const alternate = jid.endsWith('@lid') ? await mapping?.getPNForLID(jid) : jid.endsWith('@s.whatsapp.net') ? await mapping?.getLIDForPN(jid) : null;
      return [...new Set([jid, alternate].filter(Boolean))];
    },
    messageId: () => deps.generateMessageIDV2(sock.user?.id),
    status: async () => ({ ready, linked: linked.length > 0 }),
    async find(query) {
      requireReady();
      // WhatsApp rate-limits the full group list, so fetch it at most every few minutes and fall back to the saved names.
      if (Date.now() - groupsFetchedAt > GROUP_LIST_TTL) {
        try {
          const gs = await sock.groupFetchAllParticipating();
          for (const [id, g] of Object.entries(gs)) { contacts[id] = g.subject; groups.set(id, g); }
          store.write(contactsFile, contacts);
          groupsFetchedAt = Date.now();
        } catch (e) { if (!/rate-overlimit/.test(e?.message || '')) throw e; }
      }
      return Object.entries(contacts).filter(([, n]) => n.toLowerCase().includes(query.toLowerCase())).map(([id, name]) => ({ id, name }));
    },
    async resolve(name) {
      requireReady();
      if (name === 'me') return config.userChat || config.ownerChat;
      if (/@(g\.us|s\.whatsapp\.net|lid)$/.test(name || '')) return name;
      const matches = Object.entries(contacts).filter(([, n]) => n.toLowerCase() === String(name).toLowerCase());
      insist(matches.length === 1, 'CHAT_UNRESOLVED', `Resolve one exact chat JID first; ${matches.length} names match`);
      return matches[0][0];
    },
    // Read ticks are the owner's sign that an agent accepted the message.
    async markRead(key) { requireReady(); await sock.readMessages([key]); },
    async createGroup(subject, participants = []) {
      requireReady();
      const own = String(sock.user?.id || '').split('@')[0].split(':')[0], ownLid = String(sock.user?.lid || '').split('@')[0].split(':')[0];
      const others = participants.filter(p => { const n = String(p).split('@')[0].split(':')[0]; return n && n !== own && n !== ownLid; });
      const g = await sock.groupCreate(subject, others);
      contacts[g.id] = g.subject; groups.set(g.id, g); store.write(contactsFile, contacts);
      return { id: g.id, name: g.subject };
    },
    async addToGroup(jid, participant) {
      requireReady();
      const [result] = await sock.groupParticipantsUpdate(jid, [participant], 'add');
      groups.delete(jid);
      insist(String(result?.status) === '200', 'GROUP_ADD_FAILED', `WhatsApp refused to add that member (${result?.status || 'no status'})`);
      return { chat: jid, added: participant };
    },
    async members(jid) { requireReady(); const g = await sock.groupMetadata(jid); return { subject: g.subject, participants: (g.participants || []).map(p => ({ ...p, lid: p.lid || (p.id.endsWith('@lid') ? p.id : null), phoneNumber: p.phoneNumber || (!p.id.endsWith('@lid') ? p.id : null), name: contacts[p.phoneNumber] || contacts[p.id] || null })) }; },
    async send(chat, text, { id, quoted, media }) {
      requireReady();
      let content = { text };
      if (media) {
        insist(['image', 'video', 'gif', 'sticker', 'file', 'audio'].includes(media.kind), 'INVALID_MEDIA', 'Unknown media kind');
        const data = Buffer.from(media.data || '', 'base64');
        insist(data.length > 0 && data.length <= 25 * 1024 * 1024, 'INVALID_MEDIA', 'Media must contain 1 byte–25 MB');
        if (media.kind === 'sticker' || media.kind === 'gif') {
          const temp = fs.mkdtempSync(path.join(directory, 'media-'));
          try {
            const file = path.join(temp, path.basename(media.name || 'media.webp')); fs.writeFileSync(file, data);
            const out = media.kind === 'sticker' ? await (deps.toWebpSticker || toWebpSticker)(file) : { out: file.toLowerCase().endsWith('.gif') ? (deps.gifToMp4 || gifToMp4)(file) : file };
            content = media.kind === 'sticker' ? { sticker: fs.readFileSync(out.out), isAnimated: out.animated } : { video: fs.readFileSync(out.out), gifPlayback: true, caption: text };
            if (out.out !== file) fs.rmSync(out.out, { force: true });
          } finally { fs.rmSync(temp, { recursive: true, force: true }); }
        } else if (media.kind === 'file') content = { document: data, fileName: path.basename(media.name || 'attachment'), mimetype: media.mimetype || MIME[path.extname(media.name || '').toLowerCase()] || 'application/octet-stream', caption: text };
        else {
          const kind = media.kind === 'image' && /\.(mp4|mov|m4v|webm)$/i.test(media.name || '') ? 'video' : media.kind;
          content = { [kind]: data, caption: text, ...(kind === 'audio' ? { mimetype: media.mimetype || 'audio/ogg', ptt: !!media.ptt } : {}) };
        }
      }
      guard.noteDaemonSend(id);
      const sent = await acknowledgedSend(chat, content, { messageId: id, ...(quoted ? { quoted } : {}) });
      // Stickers and audio carry no caption; publish the stamped/plain text separately.
      if (media && ['sticker', 'audio'].includes(media.kind) && text) { guard.noteDaemonSend(id+'A');try{await acknowledgedSend(chat, { text }, { quoted: sent, messageId: `${id}A` });}catch(e){e.notDelivered=false;throw e;} }
      return sent;
    },
    async download(message) {
      const dir = path.join(directory, 'media'); fs.mkdirSync(dir, { recursive: true, mode: 0o700 });
      const key=createHash('sha256').update(JSON.stringify([message.chat,message.id])).digest('hex');
      const file = path.join(dir, `${key}.${message.media?.extension || 'bin'}`),part=file+'.'+randomUUID()+'.part';
      if (fs.existsSync(file)) return file;
      let size = 0;
      const cap = new Transform({ transform(chunk, _enc, done) { size += chunk.length; done(size > 25 * 1024 * 1024 ? new Error('media exceeds 25 MB') : null, chunk); } });
      try {
        const revive=value=>{if(Array.isArray(value))return value.map(revive);if(!value || typeof value!=='object' || Buffer.isBuffer(value))return value;return Object.fromEntries(Object.entries(value).map(([k,v])=>[k,typeof v==='string'&&['mediaKey','fileSha256','fileEncSha256','thumbnailSha256','thumbnailEncSha256'].includes(k)?Buffer.from(v,'base64'):revive(v)]));};
        const source = await downloadMediaMessage(revive(message.raw), 'stream', {}, { logger: pino({ level: 'silent' }), reuploadRequest: raw=>sock.updateMediaMessage(raw) });
        await pipeline(source, cap, fs.createWriteStream(part, { mode: 0o600, flags: 'wx' }));fs.renameSync(part,file);return file;
      } catch (e) { fs.rmSync(part, { force: true }); throw e; }
    },
    close() { closed = true; clearTimeout(retryTimer);for(const ack of acknowledgements.values())ack.reject(new errors.Refusal('WA_ACK_UNKNOWN','Connection closed before server acknowledgement'));sock?.end(); },
  };
  await connect(); return api;
}
