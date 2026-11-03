const { bare } = require('./rules.cjs');
function mediaInfo(c) {
  if (c.stickerMessage) return { type: 'stickerMessage', kind: 'sticker', extension: 'webp' };
  if (c.imageMessage) return { type: 'imageMessage', kind: 'image', extension: /png/i.test(c.imageMessage.mimetype || '') ? 'png' : /webp/i.test(c.imageMessage.mimetype || '') ? 'webp' : 'jpg' };
  if (c.videoMessage) return { type: 'videoMessage', kind: 'video', extension: 'mp4' };
  if (c.documentMessage) return { type: 'documentMessage', kind: 'file', extension: require('node:path').extname(c.documentMessage.fileName || '').slice(1).replace(/[^a-z0-9]/gi, '').slice(0,12) || 'bin' };
  if (c.audioMessage) return { type: 'audioMessage', kind: c.audioMessage.ptt ? 'voice note' : 'audio', extension: /mpeg/.test(c.audioMessage.mimetype || '') ? 'mp3' : /mp4/.test(c.audioMessage.mimetype || '') ? 'm4a' : 'ogg' };
  return null;
}
function unwrap(m) {
  for (let i = 0; i < 6; i++) {
    const next = m?.ephemeralMessage?.message || m?.viewOnceMessage?.message || m?.viewOnceMessageV2?.message || m?.documentWithCaptionMessage?.message;
    if (!next) break; m = next;
  }
  return m || {};
}
function textOf(m) {
  const c = unwrap(m);
  for(const [field,kind] of [['locationMessage','location'],['liveLocationMessage','live location'],['contactMessage','contact'],['contactsArrayMessage','contacts']])if(c[field])return '['+kind+']\n'+JSON.stringify(c[field]);
  return c.conversation || c.extendedTextMessage?.text || c.imageMessage?.caption || c.videoMessage?.caption || c.documentMessage?.caption || '';
}
function normalize(raw, linkedJids) {
  const c = unwrap(raw.message);
  const value = Object.values(c).find(v => v && typeof v === 'object' && v.contextInfo);
  const ctx = value?.contextInfo || {};
  const key = raw.key || {};
  const senders = [key.participant, key.participantAlt, key.participantPn, ...(!key.remoteJid?.endsWith('@g.us') ? [key.remoteJid, key.remoteJidAlt] : [])].filter(Boolean);
  return { id: key.id, chat: key.remoteJid, chatAliases: [key.remoteJid, key.remoteJidAlt].filter(Boolean), key, fromMe: !!key.fromMe, senders, senderLabel: String(raw.pushName || '').replace(/\s+/g, ' ').slice(0,64), timestamp: Number(raw.messageTimestamp) || undefined,
    text: textOf(c), mentions: ctx.mentionedJid || [], mentionsOwn: (ctx.mentionedJid || []).some(j => (linkedJids || []).map(bare).includes(bare(j))), quotedId: ctx.stanzaId, quotedText: textOf(ctx.quotedMessage),
    quotedOwn: (linkedJids || []).map(bare).includes(bare(ctx.participant)),
    quotedType: Object.keys(unwrap(ctx.quotedMessage))[0], media: mediaInfo(c), raw };
}
module.exports = { normalize, unwrap, textOf, mediaInfo };
