#!/usr/bin/env node
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { request, remote } = require('../engine/lib/client.cjs');
const { read } = require('../engine/lib/store.cjs');
const { insist } = require('../engine/lib/errors.cjs');
const { accountDirectory, runtimeDirectory } = require('../engine/lib/account.cjs');
const { identity } = require('../engine/lib/identity.cjs');
function parse(argv, env = process.env) {
  const cmd = argv[0];
  const flags = {}, positional = [];
  const booleans = new Set(['--plain', '--confirmed', '--relay', '--ptt','--stdin']);
  const values = new Set(['--account', '--via', '--reply-to', '--as', '--uuid', '--agent', '--target-host', '--image', '--video', '--gif', '--sticker', '--file', '--audio', '--delivery-id']);
  for (let i = 1; i < argv.length; i++) {
    const value = argv[i];
    if (booleans.has(value)) { insist(!flags[value], 'DUPLICATE_OPTION', value); flags[value] = true; }
    else if (values.has(value)) {
      insist(argv[i + 1] && !argv[i + 1].startsWith('--') && !flags[value], 'OPTION_VALUE', `${value} requires one value`); flags[value] = argv[++i];
    } else { insist(!value.startsWith('--'), 'UNKNOWN_OPTION', value); positional.push(value); }
  }
  const mediaOptions = ['--image', '--video', '--gif', '--sticker', '--file', '--audio'];
  const options = {
    send: ['--via', '--plain', '--confirmed', '--reply-to', '--as', '--uuid', '--agent', '--target-host', '--ptt', ...mediaOptions],
    respond: ['--delivery-id', '--stdin', '--ptt', ...mediaOptions], request: ['--via', '--relay'],
    find: ['--via'], members: ['--via'], resolve: ['--via'], ping: ['--via'], status: ['--via'], index: ['--via'], roster: ['--via'], 'rules-get': [],
  };
  insist(options[cmd], 'CLI_COMMAND', 'Use send, respond, find, resolve, members, ping, rules-get or request');
  for (const flag of Object.keys(flags)) insist(flag === '--account' || options[cmd].includes(flag), 'OPTION_NOT_APPLICABLE', `${flag} does not apply to ${cmd}`);
  insist(!(flags['--relay'] && flags['--via']), 'RELAY_LOOP', 'A relayed request must use the local engine');
  const mediaFlags = ['image', 'video', 'gif', 'sticker', 'file', 'audio'].filter(k => flags[`--${k}`]);
  insist(mediaFlags.length <= 1, 'MEDIA_CONFLICT', 'Choose one attachment');
  let media;
  if (mediaFlags.length) {
    const kind = mediaFlags[0], file = flags[`--${kind}`];
    const size = fs.statSync(file).size; insist(size > 0 && size <= 25 * 1024 * 1024, 'MEDIA_SIZE', 'Attachment must be 1 byte–25 MB');
    media = { kind, name: path.basename(file), source: path.resolve(file), data: fs.readFileSync(file).toString('base64'), ...(flags['--ptt'] ? { ptt: true } : {}) };
  }
  insist(!flags['--ptt'] || media?.kind === 'audio', 'MEDIA_CONFLICT', '--ptt requires --audio');
  const id = flags['--uuid'] || env.CODEX_THREAD_ID || env.CODEX_SESSION_ID || env.CLAUDE_CODE_SESSION_ID;
  const agent = flags['--agent'] || (env.CODEX_THREAD_ID || env.CODEX_SESSION_ID ? 'codex' : 'claude');
  let inherited;
  if (env.TEXT_TARGET) { try { inherited = JSON.parse(env.TEXT_TARGET); } catch { insist(false, 'INVALID_TARGET', 'TEXT_TARGET must contain JSON'); } }
  const target = inherited || (id ? { kind: 'live', agent, host: flags['--target-host'] || env.TEXT_HOST || os.hostname(), id } : undefined);
  const payload = cmd === 'send' ? { cmd, chat: positional[0], message: positional[1] || '', style: flags['--plain'] ? 'plain' : undefined, confirmed: !!flags['--confirmed'], replyTo: flags['--reply-to'], target, name: flags['--as'] || (target && identity(target, env)), media }
    : cmd === 'respond' ? { cmd, deliveryId: flags['--delivery-id'], message: positional[0] || '', media, ...(id ? {receipt:agent === 'codex' ? {threadId:id} : {sessionId:id}} : {}) }
    : cmd === 'find' ? { cmd, query: positional[0] || '' }
    : cmd === 'members' || cmd === 'resolve' ? { cmd, chat: positional[0] }
    : { cmd };
  insist(positional.length <= (cmd === 'send' ? 2 : ['find', 'members', 'resolve', 'respond'].includes(cmd) ? 1 : 0), 'EXTRA_ARGUMENT', 'Too many positional arguments');
  const account = flags['--account'] || env.TEXT_ACCOUNT || 'main'; accountDirectory('/unused', account);
  insist(!flags['--stdin'] || positional.length===0,'EXTRA_ARGUMENT','--stdin reads the entire response from standard input');
  return { payload, via: flags['--via'], relay: flags['--relay'], account,stdin:!!flags['--stdin'] };
}
async function main(argv = process.argv.slice(2)) {
  if (argv.length === 1 && argv[0] === '--help') { console.log('text send <chat> <message> | respond --stdin --delivery-id ID < answer.txt | find <name> | resolve <name> | members <chat> | ping | status | index | roster | rules-get | request < JSON\nPlace options after the command: --account NAME --via HOST --plain --confirmed --reply-to ID --as NAME --uuid ID --agent claude|codex --target-host HOST\nMedia: --image PATH --video PATH --gif PATH --sticker PATH --file PATH --audio PATH [--ptt]\nrequest reads {"cmd":"operation",...}; operation-specific options are documented in references/operations.md.'); return; }
  const base = process.env.TEXT_HOME;
  const installedDirectory = path.resolve(__dirname, '../..');
  const installed = read(path.join(installedDirectory, 'config.json'), {});
  const argAccount = argv.includes('--account') ? argv[argv.indexOf('--account') + 1] : process.env.TEXT_ACCOUNT || installed.account || 'main';
  let directory = runtimeDirectory(base, argAccount, installedDirectory);
  const config = read(path.join(directory, 'config.json'), {});
  const parsed = parse(argv, { ...process.env, TEXT_HOST: process.env.TEXT_HOST || config.host, TEXT_ACCOUNT: argAccount });
  if(parsed.stdin){const chunks=[];for await(const chunk of process.stdin)chunks.push(chunk);responseFromStdin(parsed.payload,Buffer.concat(chunks).toString('utf8'));}
  if (argv[0] === 'request') {
    const chunks = []; for await (const chunk of process.stdin) chunks.push(chunk);
    parsed.payload = JSON.parse(Buffer.concat(chunks).toString('utf8'));
    insist(parsed.payload && typeof parsed.payload.cmd==='string' && parsed.payload.cmd.trim(),'INVALID_REQUEST','JSON request needs a nonempty string cmd naming the operation');
    if (parsed.payload.account!==undefined) {
      accountDirectory('/unused',parsed.payload.account);
      insist(!argv.includes('--account') || parsed.payload.account===parsed.account,'ACCOUNT_CONFLICT','JSON account and --account must match');
      parsed.account=parsed.payload.account;directory = runtimeDirectory(base, parsed.account, installedDirectory);
    }
  }
  if (parsed.account !== 'main') parsed.payload.account = parsed.account;
  const via = parsed.via;
  // Responses and local rule edits always enter the engine that owns the agent.
  const result = via ? await remote(via, parsed.payload) : await request(path.join(directory, 'engine.sock'), parsed.payload);
  console.log(JSON.stringify(result)); if (!result.ok) process.exitCode = 1;
}
if (require.main === module) main().catch(e => { console.error(JSON.stringify({ ok: false, code: e.code || 'CLIENT_FAILED', error: e.message })); process.exitCode = 1; });
function responseFromStdin(payload,text){
  if(payload.deliveryId){payload.message=text;return payload;}
  const data=JSON.parse(text);
  insist(data && Object.keys(data).every(k=>['deliveryId','message'].includes(k)) && typeof data.deliveryId==='string' && typeof data.message==='string','INVALID_RESPONSE','stdin without --delivery-id needs only deliveryId and message as JSON');
  Object.assign(payload,data);return payload;
}
module.exports = { parse, main, responseFromStdin };
