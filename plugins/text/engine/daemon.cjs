#!/usr/bin/env node
const fs = require('node:fs');
const path = require('node:path');
const net = require('node:net');
const { promisify } = require('node:util');
const { execFile } = require('node:child_process');
const { Engine } = require('./lib/engine.cjs');
const { Ask } = require('./lib/ask.cjs');
const { read } = require('./lib/store.cjs');
const { normalize } = require('./lib/normalize.cjs');
const { ensureSocketFree } = require('./lib/socket.cjs');
const { runtimeDirectory } = require('./lib/account.cjs');
async function main() {
  process.umask(0o077);
  require('./lib/log.cjs').configure(process.env.TEXT_LOG_FILE);
  const installedDirectory = path.resolve(__dirname,'../..');
  const installed = read(path.join(installedDirectory,'config.json'),{});
  const directory = runtimeDirectory(process.env.TEXT_HOME, process.env.TEXT_ACCOUNT || installed.account || 'main', installedDirectory);
  const config = read(path.join(directory, 'config.json'), null);
  if (!config) throw new Error('CONFIG_REQUIRED: run setup install with a config first');
  const socket = path.join(directory, 'engine.sock');
  await ensureSocketFree(socket);
  const fatal = e => { console.error(JSON.stringify({ code: e.code || 'ENGINE_FAILED', error: e.message })); };
  const forward=require('./lib/client.cjs').forAccount(config.account || process.env.TEXT_ACCOUNT || 'main');
  const engine = new Engine({ config, directory, rulesFile: path.join(directory, 'rules.json'), stateFile: path.join(directory, 'state.json'), wa: null,
    forward,
    ask: new Ask({ directory, ...(config.askScript ? { script: require('./lib/ask.cjs').locateAsk({ ...process.env, ASK_SCRIPT: process.env.ASK_SCRIPT || config.askScript }) } : {}) }), runScript: async (argv, args, { timeoutSeconds = 60 } = {}) => {
      try {
        const result = await promisify(execFile)(argv[0], [...argv.slice(1), ...(args ? [args] : [])], { timeout: timeoutSeconds * 1000, maxBuffer: 1024 * 1024 });
        return result.stdout || result.stderr || '(no output)';
      } catch(e) { e.notDelivered=['ENOENT','ENOTDIR'].includes(e.code); throw e; }
    } });
  engine.rules(); // Validate all local inputs before opening WhatsApp.
  let accepting;
  const listening = new Promise(resolve => { accepting = resolve; });
  let wa;
  wa = config.linkedVia ? require('./lib/proxy.cjs').proxy({ host: config.linkedVia, directory,send:forward }) : await (await import('./baileys/transport.mjs')).transport({ directory, config, onError: fatal, onExit: code => { wa?.close(); process.exit(code); },onReady:async()=>{await listening;await engine.retry();},
    onMessage: async (raw, linked) => {
      await listening;
      return engine.receive(normalize(raw, linked));
    },onBatch:async(raws,linked)=>{await listening;const results=await engine.receiveBatch(raws.map(raw=>normalize(raw,linked)));for(const result of results)if(result.status==='rejected')fatal(result.reason);return results;} });
  engine.wa = wa;
  const retryTimer = setInterval(() => engine.retry().catch(fatal), 30000); retryTimer.unref();
  const server = net.createServer(conn => {
    let buffer = '', handled = false;
    conn.on('data', async data => {
      if (handled) return;
      buffer += data.toString('utf8');
      if (buffer.length > 38 * 1024 * 1024) { conn.destroy(); return; }
      const end = buffer.indexOf('\n'); if (end < 0) return; handled = true;
      let result;
      try { result = await engine.request(JSON.parse(buffer.slice(0, end))); }
      catch (e) { result = { ok: false, code: e.code || 'ENGINE_FAILED', error: e.message, ...(e.notDelivered === true ? {notDelivered:true} : {}), ...(e.held ? {held:true,failureId:e.failureId} : {}) }; }
      conn.end(JSON.stringify(result) + '\n');
    });
    conn.on('error', fatal);
  });
  server.on('error', e => { fatal(e); wa.close(); process.exitCode = 1; });
  server.listen(require('./lib/socket.cjs').address(socket), async () => {
    fs.chmodSync(socket,0o600);
    try{await engine.startup();accepting();console.log(JSON.stringify({listening:socket,host:config.host}));}
    catch(e){fatal(e);wa.close();server.close();process.exitCode=1;}
  });
  for (const signal of ['SIGTERM', 'SIGINT']) process.on(signal, () => { clearInterval(retryTimer); wa.close(); server.close(); fs.rmSync(socket, { force: true }); process.exit(0); });
}
if (require.main === module) main().catch(e => { console.error(JSON.stringify({ ok: false, code: e.code || 'STARTUP_FAILED', error: e.message })); process.exitCode = e.exitCode ?? 1; });
module.exports = { main };
