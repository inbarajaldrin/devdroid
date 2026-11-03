// One compact line telling an agent which message a WhatsApp swipe-reply points at.
// Saved sends retain the id, kind, file and opening text;
// a reply carries the quoted message's id (contextInfo.stanzaId), so the agent learns exactly
// which message and which attached file was meant, without the whole quoted prompt.

// Drop the routing frame an agent message carries: the `name` header, an optional _title_ line,
// and the trailing _uuid_ line.
function stripFrame(text) {
  const lines = String(text || '').split('\n');
  if (lines[0] && /^\[[^\]\n]{1,40}\]$/.test(lines[0].trim())) lines.shift();
  if (lines[0] && /^[`*][^`*\n]{1,80}[`*]\s*$/.test(lines[0].trim())) {
    lines.shift();
    if (lines[0] && /^_[^_\n]{1,80}_\s*$/.test(lines[0].trim())) lines.shift();
  }
  while (lines.length && (!lines[lines.length - 1].trim()
    || /^[_`]?[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}[_`]?\s*$/i.test(lines[lines.length - 1].trim()))) lines.pop();
  return lines.join('\n').trim();
}

function snippet(text, max = 140) {
  const flat = stripFrame(text).replace(/\s+/g, ' ').trim();
  return flat.length > max ? `${flat.slice(0, max - 1).trimEnd()}…` : flat;
}

const KIND = { imageMessage: 'image', videoMessage: 'video', documentMessage: 'file', stickerMessage: 'sticker',
  audioMessage: 'voice note', conversation: 'message', extendedTextMessage: 'message' };

// hit: the sent-index entry for the quoted id, or null. quotedType: Baileys content type of the quoted
// message. quotedText: its text or caption. fromAgent: true when the quoted message was sent by this account.
function quotedSummary({ hit, quotedType, quotedText, fromAgent }) {
  if (!hit && !quotedType && !quotedText) return '';
  const kind = hit ? String(hit.kind || 'message').toLowerCase().replace('text', 'message') : (KIND[quotedType] || 'message');
  const whose = hit || fromAgent ? 'your' : 'a';
  const when = hit?.ts ? ` sent ${new Date(hit.ts).toTimeString().slice(0, 5)}` : '';
  const text = snippet(hit ? hit.text : quotedText);
  const file = hit?.file ? ` (file: ${hit.file})` : '';
  return `[Replying to ${whose} ${kind}${when}${text ? `: "${text}"` : ''}${file}]`;
}

// A minimal message object Baileys accepts as `quoted`: the original key plus its text, enough for
// WhatsApp to draw the reply bubble. Media is shown by its caption (or a kind label) as text.
function quotedStub(key, text, kind) {
  if (!key || !key.id || !key.remoteJid) return null;
  const k = { remoteJid: key.remoteJid, id: key.id, fromMe: !!key.fromMe };
  if (key.participant) k.participant = key.participant;
  const body = String(text || '').trim() || (kind ? `[${kind}]` : '');
  return { key: k, message: { conversation: body } };
}

// What a swipe-reply tells the receiving agent about the message it quotes : nothing when the
// agent sent that message after its context was last compacted (it still has it, so a header is noise); the FULL
// quoted text when the agent sent it before its last compaction (it no longer has it) or when the quoted message
// is not the agent's own (it may never have seen it). Never a truncated snippet.
//   hit: sent-index entry for the quoted id (agent's own send) or null; quotedText: text from the reply itself;
//   compactedAt: ms timestamp of the receiving session's last compaction, 0 if none, null if unknown.
//   latest: true when the quoted message is the newest one that session sent in that chat.
// Three tiers : the latest message, still in context: nothing. An older one, still in context: the
// one-line pointer (quotedSummary) so the agent knows WHICH message is meant. Before the last compaction, or not the
// agent's own: the full text.
function quotedBlock({ hit, quotedType, quotedText, fromAgent, compactedAt, latest }) {
  if (!hit && !quotedType && !quotedText) return '';
  const own = !!hit || !!fromAgent;
  const sentAt = hit?.ts || 0;
  if (own && sentAt && compactedAt !== null && compactedAt !== undefined && sentAt > compactedAt) {
    return latest ? '' : quotedSummary({ hit, quotedType, quotedText, fromAgent });
  }
  const full = stripFrame(hit && hit.text ? hit.text : quotedText);
  const kind = hit ? String(hit.kind || 'message').toLowerCase().replace('text', 'message') : (KIND[quotedType] || 'message');
  const when = sentAt ? ` sent ${new Date(sentAt).toISOString().replace('T', ' ').slice(0, 16)} UTC` : '';
  const file = hit?.file ? ` (file: ${hit.file})` : '';
  const why = own ? (sentAt ? ', from before your context was compacted' : '') : '';
  const head = `[Replying to ${own ? 'your' : 'a'} ${kind}${when}${file}${why}${full ? ':' : ''}]`;
  return full ? `${head}\n${full}\n[end of quoted message]` : head;
}

module.exports = { stripFrame, snippet, quotedSummary, quotedBlock, quotedStub };
