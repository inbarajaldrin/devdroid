const { validate } = require('./rules.cjs');
const { insist } = require('./errors.cjs');
function convert(old, host, env = {}, sentIndex = {}) {
  const rules = new Map();
  const legacyTargets = {};
  for (const r of [...(old.routes || []), ...(old.groupRoutes || [])]) {
    insist(!(r.threadId && r.sessionId), 'CONVERSION_CONFLICT', 'Route has both a thread and a session');
    const id = r.threadId || r.sessionId;
    insist(id, 'CONVERSION_TARGET', 'Route has no exact target');
    const target = { kind: 'live', agent: r.threadId ? 'codex' : 'claude', id: id.toLowerCase(), host };
    const chat = r.group || r.jid;
    insist(!rules.has(chat), 'CONVERSION_CONFLICT', `Multiple routes for ${chat}; merge their permissions explicitly`);
    // Missing expiry was disabled by the old matcher; never activate it during conversion.
    insist(r.expiresAt && Number.isFinite(Date.parse(r.expiresAt)), 'CONVERSION_EXPIRY', 'Legacy routes require a valid expiry');
    rules.set(chat, { chat, label: r.groupLabel || r.label || 'This chat', target, allow: r.participants || [r.jid],
      triggers: r.group ? { keywords: r.trigger?.keyword ? [r.trigger.keyword] : [], mention: r.trigger?.mentionOwn !== false } : { every: true },
      answerMode: 'answer', style: r.group ? 'stamped' : 'plain', expiresAt: r.expiresAt });
    insist(!legacyTargets[target.id] || legacyTargets[target.id].agent === target.agent, 'CONVERSION_CONFLICT', 'Legacy UUID identifies different agent types');
    legacyTargets[target.id] = target;
  }
  for (const pair of String(env.WA_INTAKE || '').split(',').filter(Boolean)) {
    const [chat, id, extra] = pair.split('=').map(s => s.trim());
    insist(chat && id && !extra, 'CONVERSION_INTAKE', 'Expected WA_INTAKE chat=UUID entries');
    const previous = rules.get(chat);
    const target = { kind: 'live', agent: 'claude', host, id: id.toLowerCase() };
    insist(!previous || previous.target.id === target.id, 'CONVERSION_CONFLICT', 'WA_INTAKE and external route disagree');
    rules.set(chat, previous ? { ...previous, triggers: { ...previous.triggers, ownerEvery: true } } : { chat, target, private: true, triggers: { ownerEvery: true }, style: 'stamped' });
    legacyTargets[target.id] = target;
  }
  const { bareNumber } = require('./inbound-guard.cjs');
  const ownerNumbers = String(env.WA_OWN || '').split(',').map(bareNumber).filter(Boolean);
  const owners = String(env.WA_OWN || '').split(',').map(s => s.trim()).filter(Boolean).map(s => s.includes('@') ? s : bareNumber(s) + '@s.whatsapp.net');
  const sent = Object.fromEntries(Object.entries(sentIndex).map(([id, item]) => [id, { ...item, id, status: 'sent', legacy: true }]));
  return { bundle: validate({ version: 1, rules: [...rules.values()] }), legacyTargets, config: { legacyHost: host, ownerNumbers, owners, mode: env.WA_MODE || 'dual', agentTag: env.WA_AGENT_TAG || '[agent]' }, sent };
}
function parseEnv(text) {
  const result = {};
  for (const line of String(text).split('\n')) {
    const m = line.match(/^\s*(?:export\s+)?(WA_[A-Z_]+)\s*=\s*(.*?)\s*$/);
    if (m) result[m[1]] = m[2].replace(/^(['"])(.*)\1$/, '$2');
  }
  return result;
}
module.exports = { convert, parseEnv };
