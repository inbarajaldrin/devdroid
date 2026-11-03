const { insist } = require('./errors.cjs');
const MODES = ['answer', 'draft', 'listen', 'tell-me'];
const bare = jid => String(jid || '').replace(/:\d+(?=@)/, '').toLowerCase();
const hostOK = host => typeof host === 'string' && /^[a-zA-Z0-9][a-zA-Z0-9._-]*$/.test(host);
function targetOK(t) {
  return t && hostOK(t.host) && ['claude', 'codex'].includes(t.agent) &&
    ['live', 'standing', 'home'].includes(t.kind) &&
    (t.kind === 'home' ? typeof t.cwd === 'string' && t.cwd.startsWith('/') : typeof t.id === 'string' && !!t.id.trim()) &&
    (t.kind === 'home' || t.kind !== 'live' && t.agent!=='codex' || /^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/i.test(t.id));
}
function validate(bundle) {
  insist(bundle?.version === 1 && Array.isArray(bundle.rules), 'INVALID_RULES', 'Expected version: 1 and rules array');
  const chats = new Set();
  for(const [chat,permissions] of Object.entries(bundle.permissions || {})){
    insist(/@(g\.us|s\.whatsapp\.net|lid)$/.test(chat) && permissions && typeof permissions==='object','INVALID_PERMISSION','Permission needs an exact chat');
    for(const [kind,choice] of Object.entries(permissions))insist(['help','send'].includes(kind)&&['allow','ask','deny'].includes(choice),'INVALID_PERMISSION','Save allow, ask or deny for help/send');
  }
  for (const r of bundle.rules) {
    insist(typeof r.chat === 'string' && /@(g\.us|s\.whatsapp\.net|lid)$/.test(r.chat) && !chats.has(r.chat), 'INVALID_RULE', 'Each chat must have one rule with an exact JID');
    chats.add(r.chat);
    insist(r.aliases === undefined || Array.isArray(r.aliases) && r.aliases.every(j => /@(s\.whatsapp\.net|lid)$/.test(j)), 'INVALID_ALIAS', 'Chat aliases must be exact phone/LID JIDs');
    insist(targetOK(r.target), 'INVALID_TARGET', 'Target needs kind, agent, host and id (or absolute cwd for home)');
    for(const target of [r.target,...Object.values(r.named || {})])if(target.kind==='home'){
      insist(typeof target.homeKey==='string' && !!target.homeKey.trim() && typeof target.rolePrompt==='string' && !!target.rolePrompt.trim(),'AGENT_CHOICE_REQUIRED','Save the fresh agent identity and the role/instructions chosen with the owner');
      insist(target.permissions && (target.agent==='codex'?['read-only','workspace-write','danger-full-access'].includes(target.permissions.sandbox) && ['untrusted','on-request','never'].includes(target.permissions.approvalPolicy):Array.isArray(target.permissions.allowedTools) && ['default','acceptEdits','plan','dontAsk','bypassPermissions'].includes(target.permissions.permissionMode)),'AGENT_PERMISSIONS_REQUIRED','Save the fresh agent permissions chosen with the owner');
    }
    insist(!r.controlHost || hostOK(r.controlHost), 'INVALID_HOST', 'Invalid control host');
    insist(r.expiresAt === undefined || typeof r.expiresAt === 'string' && /^\d{4}-\d{2}-\d{2}T/.test(r.expiresAt) && Number.isFinite(Date.parse(r.expiresAt)), 'INVALID_EXPIRY', 'Expiry must be an ISO timestamp');
    insist(r.answerMode === undefined || MODES.includes(r.answerMode), 'INVALID_MODE', 'Unknown answer mode');
    const otherPeople = (r.allow || []).length || Object.values(r.commands || {}).some(c => c.allow?.length);
    insist(r.private === true && !otherPeople || MODES.includes(r.answerMode), 'MODE_REQUIRED', 'Choose an answer mode for every chat with other people');
    insist(r.style === undefined || ['stamped', 'plain'].includes(r.style), 'INVALID_STYLE', 'Unknown speaking style');
    insist(r.private === undefined || typeof r.private === 'boolean', 'INVALID_RULE', 'private must be boolean');
    insist(r.allow === undefined || Array.isArray(r.allow) && r.allow.every(j => /@(s\.whatsapp\.net|lid)$/.test(j)), 'INVALID_ALLOW', 'allow must contain exact person JIDs');
    const t = r.triggers || {};
    insist(Object.keys(t).every(k => ['keywords', 'mention', 'every', 'ownerEvery', 'ownerContext'].includes(k)) &&
      (t.keywords === undefined || Array.isArray(t.keywords) && t.keywords.every(k => typeof k === 'string' && !!k.trim())) &&
      ['mention', 'every', 'ownerEvery', 'ownerContext'].every(k => t[k] === undefined || typeof t[k] === 'boolean'), 'INVALID_TRIGGER', 'Unknown or malformed trigger');
    for (const [name, target] of Object.entries(r.named || {})) {
      insist(/^[a-zA-Z][\w-]*$/.test(name) && targetOK(target), 'INVALID_TARGET', 'Named agent must have a valid name and target');
    }
    for (const [name, c] of Object.entries(r.commands || {})) {
      insist(/^\/[a-zA-Z][\w-]*$/.test(name) && name !== '/help', 'INVALID_COMMAND', 'Command must be /name; /help is reserved');
      insist(hostOK(c.host) && ['script', 'instruction'].includes(c.type), 'INVALID_COMMAND', 'Command needs type and host');
      insist(c.allow === undefined || Array.isArray(c.allow) && c.allow.every(j => /@(s\.whatsapp\.net|lid)$/.test(j)), 'INVALID_COMMAND', 'Command allow must list person JIDs');
      insist(c.type === 'script' ? Array.isArray(c.argv) && c.argv.length > 0 && c.argv.every(x => typeof x === 'string') && c.argv[0].startsWith('/') : typeof c.text === 'string' && !!c.text.trim() && c.host === r.target.host,
        'INVALID_COMMAND', 'Script needs absolute argv; instruction needs text and target host');
      insist(c.timeoutSeconds === undefined || c.type === 'script' && Number.isInteger(c.timeoutSeconds) && c.timeoutSeconds >= 1 && c.timeoutSeconds <= 600,
        'INVALID_COMMAND', 'A script timeout is 1 to 600 seconds');
      insist(c.image === undefined || c.type === 'script' && c.image === 'first-line', 'INVALID_COMMAND', 'A script image is "first-line": its first output line names the image');
    }
  }
  return bundle;
}
function isOwner(m, config) {
  const { bareNumber } = require('./inbound-guard.cjs');
  return !!m.fromMe || [...(m.senders || []), m.sender].some(s => ((config.owners || []).map(bare).includes(bare(s)) && !!bare(s)) || !!bareNumber(s) && (config.ownerNumbers || []).includes(bareNumber(s)));
}
function canonicalRules(config,bundle){
  const owners=new Set([config.ownerChat,...(config.owners || [])]);
  return validate({...bundle,rules:bundle.rules.map(r=>r.chat!==config.ownerChat && (owners.has(r.chat) || (r.aliases || []).includes(config.ownerChat))?{...r,chat:config.ownerChat,aliases:[...new Set([r.chat,...(r.aliases || [])])].filter(j=>j!==config.ownerChat)}:r)});
}
function validateWiring(config,bundle){
  const hosts=new Set([config.host,...(config.machines || []),...(config.proxyReceivers || []),config.linkedVia].filter(Boolean));
  for(const rule of bundle.rules)for(const host of [rule.target.host,...Object.values(rule.named || {}).map(t=>t.host),...Object.values(rule.commands || {}).map(c=>c.host),rule.controlHost].filter(Boolean))insist(hosts.has(host),'UNKNOWN_RULE_HOST','A rule targets an unconfigured machine: '+host);
  return canonicalRules(config,bundle);
}
function allowed(list, m) { return [...(m.senders || []), m.sender].some(j => (list || []).map(bare).includes(bare(j)) && !!bare(j)); }
function answerMode(rule) { return rule?.answerMode || (rule?.private ? 'answer' : null); }
function stamp(target, name, body, config = {}, about) {
  const label = String(name || target.name || (target.kind === 'standing' ? target.id : target.agent)).replace(/[`\n]/g, '');
  const id = /^[0-9a-f-]{36}$/i.test(target.id || '') ? target.id : '';
  const marker = config.mode === 'single' ? (config.agentTag || '[agent]') + '\n' : '';
  // An agent the owner gave to someone else (a home agent) is shown as just Claude or Codex with its id.
  // The owner's own sessions keep the name the owner gave them.
  const kind = target.agent === 'codex' ? 'Codex' : 'Claude';
  const named = label && label.toLowerCase() !== String(target.agent || '').toLowerCase() && label !== target.id;
  const head = target.kind === 'home' || !named
    ? (id ? kind + (about ? ' · ' + String(about).replace(/[`\n]/g, '') : '') : kind + ' · ' + label + '@' + target.host)
    : kind + ' · ' + label + (id ? '' : '@' + target.host);
  return marker + '`' + head + '`\n\n' + body + (id ? '\n\n_' + id + '_' : '');
}
function legacyRoute(text, config) {
  // Recognize UUID-addressed name-first / UUID-last frames.
  const t = String(text || '');
  const name = t.match(/^[^\`*\n]{0,12}[`*]([^`*\n]{1,160})[`*][ \t]*$/m)?.[1];
  const id = t.match(/^[_`]?([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})[_`]?\s*$/im)?.[1]?.toLowerCase();
  if (!id) return null;
  if (id && config.legacyTargets?.[id]) return config.legacyTargets[id];
  const at = name?.lastIndexOf('@') ?? -1;
  const host = at > 0 ? name.slice(at + 1) : config.legacyHost;
  if (!hostOK(host)) return null;
  const full = at > 0 ? name.slice(0, at) : name, kindLabel = /^(Codex|Claude)(?: · (.+))?$/.exec(full || '');
  const label = kindLabel ? kindLabel[2] || kindLabel[1] : full;
  return { kind: id ? 'live' : 'standing', agent: kindLabel ? kindLabel[1].toLowerCase() : /^codex(?:\b|$)/i.test(label || '') ? 'codex' : 'claude', host, id: id || label, name: label };
}
function parseStamp(text) {
  const m = String(text || '').match(/^_text:([\w-]+)_\s*$/m);
  if (m) { try { const t = JSON.parse(Buffer.from(m[1], 'base64url')); if (targetOK(t)) return t; } catch {} }
  return legacyRoute(text, {}) || null;
}
function indexedRoute(item,config){
  if(targetOK(item?.target))return item.target;
  const id=item?.sessionId || item?.uuid || item?.threadId || item?.session;
  if(!item?.legacy || typeof id!=='string' || !id.trim())return null;
  if(targetOK(config.legacyTargets?.[id]))return config.legacyTargets[id];
  const target={kind:/^[a-f0-9-]{36}$/i.test(id)?'live':'standing',agent:item.agent || (item.threadId?'codex':'claude'),host:item.host || config.legacyHost,id};
  return targetOK(target)?target:null;
}
// Precedence is global: an exact swipe address wins over this chat's default or @name.
function match(bundle, m, config, now = Date.now()) {
  validate(bundle);
  const rule = bundle.rules.find(r => r.chat === m.chat);
  const own = isOwner(m, config);
  const controlHost = rule?.controlHost || rule?.target.host || config.homeHost || config.host;
  if (!own && !rule) return { kind: 'none', reason: 'not-allowed' };
  if (!own && rule?.expiresAt && Date.parse(rule.expiresAt) <= now) return { kind: 'none', reason: 'expired' };
  const text = String(m.text || '').trim();
  let target = m.quotedOwn ? (targetOK(m.quotedTarget) ? m.quotedTarget : parseStamp(m.quotedText)) : null;
  if (!target && m.quotedOwn) target = legacyRoute(m.quotedText, config);
  if (!target) {
  if (text === '/help') return own || allowed(rule?.allow, m) || Object.values(rule?.commands || {}).some(c => allowed(c.allow, m)) ? { kind: 'help', rule, host: controlHost, own } : { kind: 'none', reason: 'not-allowed' };
  const commandName = text.match(/^\/[^\s]+/)?.[0];
  const command = rule?.commands?.[commandName];
  if (command) {
    if (!own && !allowed(command.allow, m)) return { kind: 'none', reason: 'command-not-allowed' };
    return { kind: 'command', rule, command, name: commandName, args: text.slice(commandName.length).trim(), host: command.host, target: rule.target, own, mode: answerMode(rule, own) };
  }
  }
  if (!own && !allowed(rule?.allow, m)) return { kind: 'none', reason: 'not-allowed' };
  let trigger = target ? 'swipe-reply' : null;
  if (!target && rule) {
    for (const [name, t] of Object.entries(rule.named || {})) {
      if (new RegExp(`(?:^|\\s)@${name}(?=$|\\s|[,:!?])`, 'i').test(text)) { target = t; trigger = `@${name}`; break; }
    }
    const t = rule.triggers || {};
    if (!target && (t.keywords || []).some(k => text.toLowerCase().startsWith(k.toLowerCase()) && (text.length === k.length || /\s|[,:!?]/.test(text[k.length])))) trigger = 'keyword';
    if (!target && !trigger && t.mention && (m.mentionsOwn || (m.mentions || []).some(j => (config.linkedJids || []).map(bare).includes(bare(j))))) trigger = 'mention';
    const ordinaryOwnDM=own && m.fromMe && !m.chat.endsWith('@g.us') && m.chat!==config.ownerChat;
    if (!target && !trigger && !ordinaryOwnDM && (t.every || own && t.ownerEvery)) trigger = 'every';
    if (trigger && !target) target = rule.target;
    // The owner chose to let this chat's agent read their own messages here, as context it does not answer.
    if (!target && own && t.ownerContext) return { kind: 'deliver', target: rule.target, rule, host: rule.target.host, trigger: 'owner-context', own, mode: 'listen', style: rule.style || 'plain' };
  }
  return target ? { kind: 'deliver', target, rule, host: target.host, trigger, own, mode: answerMode(rule) || (m.chat===config.ownerChat?'answer':'draft'), style: rule?.style || (rule?.private || m.chat===config.ownerChat?'stamped':'plain') } : { kind: 'none', reason: 'no-match', own, host: controlHost };
}
function help(rule, { shared = false } = {}) {
  if (!rule) return 'Swipe-reply to an agent’s message to reach that agent. Only the user may wake it. /help shows how this chat works.';
  const wake = ['swipe-reply', ...(rule.triggers?.keywords || []), ...(rule.triggers?.mention ? ['mention this account'] : []), ...(rule.triggers?.every ? ['every message'] : []), ...Object.keys(rule.named || {}).map(n => '@' + n)];
  const commands = ['/help', ...Object.keys(rule.commands || {})].join(', ');
  const label = shared && /@(?:g\.us|s\.whatsapp\.net|lid)|\d{8,}/i.test(rule.label || '') ? 'This chat' : rule.label || 'This chat';
  let result = label + ': an AI acting for the user.\nWake: ' + wake.join(', ') + '.\nReplies in this chat: ' + (answerMode(rule) || 'ask the user to choose') + ', whoever speaks.\nCommands: ' + commands + '.' + (shared ? '' : '\nAgent: ' + (rule.target.name || rule.target.id || rule.target.cwd) + ' on ' + rule.target.host);
  if (shared) {
    const targets = [rule.target, ...Object.values(rule.named || {})];
    const hidden = [...targets.flatMap(t => [t.host, t.id, t.cwd]), ...Object.values(rule.commands || {}).map(c => c.host), rule.controlHost].filter(Boolean);
    for (const token of hidden) result = result.replace(new RegExp(token.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), 'gi'), '[private]');
    result = result.replace(/\+?\d{8,}|[^\s]+@(?:s\.whatsapp\.net|lid|g\.us)/gi, '[private]');
  }
  return result;
}
module.exports = { validate,validateWiring,canonicalRules, match, help, stamp, parseStamp, legacyRoute, indexedRoute,targetOK, answerMode, isOwner, bare, hostOK };
