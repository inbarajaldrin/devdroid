const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { createHash } = require('node:crypto');
const { execFile } = require('node:child_process');
const { promisify } = require('node:util');
const { insist } = require('./errors.cjs');
const run = promisify(execFile);
// Codex writes a timestamped compacted record to the thread's rollout. Files only grow, so each read starts where the last ended.
const rollouts = new Map();
function rolloutFor(thread, root) {
  const walk = dir => { for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const file = path.join(dir, entry.name);
    if (entry.isDirectory()) { const found = walk(file); if (found) return found; }
    else if (entry.name.endsWith(`-${thread}.jsonl`)) return file;
  } };
  try { return walk(root); } catch { return undefined; }
}
function codexCompactedAt(thread, home = process.env.CODEX_HOME || path.join(os.homedir(), '.codex')) {
  const known = rollouts.get(thread) || { file: rolloutFor(thread, path.join(home, 'sessions')), offset: 0, at: 0 };
  if (!known.file) return null;
  let fd;
  try {
    fd = fs.openSync(known.file, 'r');
    const size = fs.fstatSync(fd).size, bytes = Buffer.alloc(Math.max(0, size - known.offset));
    fs.readSync(fd, bytes, 0, bytes.length, known.offset);
    const end = bytes.lastIndexOf(10) + 1;
    for (const line of bytes.subarray(0, end).toString('latin1').split('\n')) {
      if (!/^\{[^{]{0,120}"type":"compacted"/.test(line)) continue;
      const when = Date.parse(/"timestamp":"([^"]+)"/.exec(line)?.[1]);
      if (when > known.at) known.at = when;
    }
    known.offset += end; rollouts.set(thread, known); return known.at;
  } catch { return null; } finally { if (fd !== undefined) fs.closeSync(fd); }
}
function locateAsk(env = process.env, skill = path.resolve(__dirname, '../..', 'skills/text')) {
  const home = env.HOME || os.homedir();
  const candidates = [env.ASK_SCRIPT, path.resolve(skill, '../ask/scripts/ask.py'), env.CLAUDE_PLUGIN_ROOT && path.join(env.CLAUDE_PLUGIN_ROOT, 'skills/ask/scripts/ask.py'), path.join(home, '.claude/skills/ask/scripts/ask.py')].filter(Boolean);
  const exists = file => { try { return path.isAbsolute(file) && fs.statSync(file).isFile(); } catch (e) { if (['ENOENT', 'ENOTDIR'].includes(e.code)) return false; throw e; } };
  if (env.ASK_SCRIPT) insist(exists(env.ASK_SCRIPT), 'ASK_MISSING', 'ASK_SCRIPT must point to an existing absolute ask.py');
  const standard = candidates.find(exists); if (standard) return standard;
  // Resolve the installed dependency's plugin root from its catalog, not a vendored copy.
  const installed = path.join(home, '.claude/plugins/installed_plugins.json');
  if (fs.existsSync(installed)) {
    const registry = JSON.parse(fs.readFileSync(installed, 'utf8'));
    for (const [name, entries] of Object.entries(registry.plugins || {})) if (name.split('@')[0] === 'session-tools') {
      for (const entry of Array.isArray(entries) ? entries : [entries]) if (entry.installPath) candidates.push(path.join(entry.installPath, 'skills/ask/scripts/ask.py'));
    }
  }
  const registered = candidates.find(exists); if (registered) return registered;
  for (let dir = path.resolve(skill); ; dir = path.dirname(dir)) {
    const catalog = path.join(dir, '.claude-plugin/marketplace.json');
    if (fs.existsSync(catalog)) {
      const manifest = JSON.parse(fs.readFileSync(catalog, 'utf8'));
      const declarations = (manifest.plugins || []).filter(entry => entry.name === 'session-tools');
      if (declarations.length) {
        const file = declarations.filter(entry => typeof entry.source === 'string' && entry.source.startsWith('.'))
          .map(entry => path.resolve(dir, entry.source, 'skills/ask/scripts/ask.py')).find(exists);
        insist(file, 'ASK_MISSING', 'The marketplace declares session-tools but its local ask.py is missing; install it or set ASK_SCRIPT');
        return file;
      }
    }
    if (dir === path.dirname(dir)) break;
  }
  const file = candidates.find(exists);
  insist(file, 'ASK_MISSING', 'Install the session-tools /ask dependency or set ASK_SCRIPT'); return file;
}
class Ask {
  constructor({ script = locateAsk(), directory, execute = run }) { Object.assign(this, { script, directory, execute }); }
  workers(){return require('./store.cjs').read(path.join(this.directory,'workers.json'),{});}
  saveWorkers(workers){require('./store.cjs').write(path.join(this.directory,'workers.json'),workers);}
  async observe(worker,target,homes){
    const receipt=require('./store.cjs').read(worker.receipt,null);
    if(!receipt || receipt.kind==='started'){
      if(receipt?.observerPid)try{process.kill(receipt.observerPid,0);}catch(e){if(e.code==='ESRCH'){const {Refusal}=require('./errors.cjs');throw new Refusal('ASK_ACCEPTANCE_UNKNOWN','The worker observer stopped before acknowledgement; inspect the worker before releasing the message');}throw e;}
      return {status:'observing',worker};
    }
    if(receipt.kind!=='accepted'){
      const {Refusal}=require('./errors.cjs');const e=new Refusal('ASK_FAILED',receipt.code+': '+(receipt.detail || 'Inspect the worker output before releasing this message'));
      e.notDelivered=receipt.kind==='not-delivered';throw e;
    }
    this.remember(target,receipt,homes);
    if(target.kind==='standing')homes[worker.key]=receipt.sessionId;
    const workers=this.workers();if(!worker.stream)delete workers[worker.key];this.saveWorkers(workers);
    return {...receipt,status:'completed'};
  }
  remember(target, receipt, homes) {
    if (target.kind !== 'home') return;
    insist(receipt.threadId || receipt.sessionId,'ASK_METADATA','Inspect and record the accepted home session/thread id before resolving its failure');
    const key = createHash('sha256').update(JSON.stringify([target.host,target.agent,target.kind,target.cwd,...(target.homeKey?[target.homeKey]:[])])).digest('hex');
    homes[key] = receipt.threadId || receipt.sessionId;
  }
  async deliver(target, prompt, homes, context = {}) {
    const temp = fs.mkdtempSync(path.join(this.directory, 'ask-'));
    const promptFile = path.join(temp, 'prompt.txt'), metadata = path.join(temp, 'metadata.json'), outcome = path.join(temp,'outcome.json');
    fs.writeFileSync(promptFile, prompt, { mode: 0o600 });
    const key = createHash('sha256').update(JSON.stringify([target.host, target.agent, target.kind, target.kind === 'home' ? target.cwd : target.id,...(target.homeKey?[target.homeKey]:[])])).digest('hex');
    const args = [this.script, '--raw', '--no-wait', '--prompt-file', promptFile, '--metadata', metadata,'--outcome',outcome];
    if(target.permissions){for(const [name,value] of Object.entries(target.permissions))if(['sandbox','approvalPolicy','permissionMode'].includes(name))args.push('--'+name.replace(/[A-Z]/g,c=>'-'+c.toLowerCase()),value);for(const tool of target.permissions.allowedTools || [])args.push('--allowed-tools',tool);}
    let pending=this.workers()[key];
    if(pending?.stream){
      const root=require('./store.cjs').read(pending.rootReceipt,null);
      // A worker that has exited, cleanly or not, cannot take more input; its last message was already settled.
      if(root?.closed){const workers=this.workers();delete workers[key];this.saveWorkers(workers);pending=undefined;}
      else{
        if(JSON.stringify(pending.permissions || null)!==JSON.stringify(target.permissions || null)){fs.rmSync(temp,{recursive:true,force:true});insist(false,'ASK_WORKER_PERMISSIONS_CHANGED','The chosen permissions changed. Apply them to the existing agent or choose a new homeKey before the owner resends; the old worker was not reused.');}
        args.push('--worker-input',pending.rootReceipt);
      }
    }else if(pending && !(target.kind==='home' && homes[key])){
      if(!pending.receipt){fs.rmSync(temp,{recursive:true,force:true});insist(false,'ASK_WORKER_PENDING','A previous worker has no completion receipt. Inspect it and record its exact session before starting another.');}
      const observation=await this.observe(pending,target,homes);
      if(observation.status==='observing'){fs.rmSync(temp,{recursive:true,force:true});return {status:'queued',worker:pending};}
    }
    if (target.agent === 'codex') args.push('--codex', '--mid-turn','--exact');
    const saved = homes[key];
    insist(!['home','standing'].includes(target.kind) || saved || Object.keys(homes).length < 500, 'HOME_CAPACITY', 'Too many retained agents; review unused agents before creating another');
    insist(pending || Object.keys(this.workers()).length<500,'HOME_CAPACITY','Too many observed workers; inspect and retire unused agents before starting another');
    if (target.cwd) args.push('--cwd', target.cwd);
    if (target.kind === 'home' && !saved) args.push('--start');
    else {
      let session = saved || target.id;
      if (target.agent === 'claude' && target.kind === 'standing' && !saved) session = await this.currentSession(session) || session;
      args.push('--session', session);
      if (target.agent === 'claude') args.push(target.kind === 'live' ? '--exact' : '--ensure');
    }
    const workerLog=path.join(this.directory, `${key}-${path.basename(temp)}.log`);
    const workerReceipt=workerLog+'.receipt.json';
    if (target.agent === 'claude') {
      args.push('--output', workerLog,'--startup-timeout','2','--worker-receipt',workerReceipt,'--worker-stream');
      if(context.deliveryId)args.push('--allowed-tools',`Bash(node ${JSON.stringify(path.resolve(__dirname,'../../scripts/text.cjs'))} respond --stdin${context.account && context.account!=='main'?' --account '+context.account:''})`);
    }
    try {
      // --no-wait waits only for a transport acceptance, never for the agent's turn.
      // A hung handshake releases its slot; a killed call is uncertain and cannot retry.
      const result = await this.execute('python3', args, { maxBuffer: 1024 * 1024, timeout: 30000, env: { ...process.env, TEXT_TARGET: JSON.stringify(target) } });
      const meta = fs.existsSync(metadata) ? JSON.parse(fs.readFileSync(metadata, 'utf8')) : {};
      const typed = fs.existsSync(outcome) ? JSON.parse(fs.readFileSync(outcome,'utf8')) : null;
      if(typed?.version===1 && (typed.kind==='started' || meta.socket)){
        const worker={key,sessionId:meta.sessionId,receipt:meta.workerReceipt,permissions:target.permissions,...(meta.socket?{stream:true,rootReceipt:pending?.rootReceipt || meta.workerReceipt}:{})};const workers=this.workers();workers[key]=worker;this.saveWorkers(workers);
        if(worker.receipt)return this.observe(worker,target,homes);
      }
      insist(typed?.version===1 && typed.kind==='accepted' && meta.status!=='spawned','ASK_ACCEPTANCE_UNKNOWN','/ask returned no typed acknowledgement for this message; inspect the worker log before resending');
      if (target.kind === 'home') {
        insist(meta.threadId || meta.sessionId || saved, 'ASK_METADATA', 'Home start did not record an agent id');
        homes[key] = meta.threadId || meta.sessionId || saved;
      }
      return { status: meta.status || 'delivered', ...meta, detail: result.stderr || '' };
    } catch (e) {
      const { Refusal } = require('./errors.cjs');
      const reason=(e.stderr || e.message)+(target.agent==='claude'?' (worker log: '+workerLog+')':'');
      let typed; try { typed=JSON.parse(fs.readFileSync(outcome,'utf8')); } catch {}
      if(typed?.version===1 && typed.kind==='not-delivered' && typed.code==='TARGET_BUSY')return {status:'queued'};
      const notDelivered = !e.killed && !e.signal && (['ENOENT','ENOTDIR'].includes(e.code) || typed?.version===1 && typed.kind==='not-delivered');
      const error = new Refusal(notDelivered ? 'ASK_FAILED' : 'ASK_ACCEPTANCE_UNKNOWN', reason);
      error.notDelivered = notDelivered;
      // Agent failures always need repair and the owner's explicit resend.
      // A missing session is not a momentary WhatsApp transport failure.
      error.retryable = false;
      throw error;
    } finally { fs.rmSync(temp, { recursive: true, force: true }); }
  }
  // A Claude session's name gets a new suffix when it restarts (project-9b, then project-fa).
  // An exact name wins; otherwise one running session with the same project name is the same session.
  async currentSession(name) {
    if (/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(name)) return name;
    let agents;
    try { const out = await this.execute('claude', ['agents', '--json'], { maxBuffer: 4 * 1024 * 1024, timeout: 10000 }); const parsed = JSON.parse(out.stdout); agents = Array.isArray(parsed) ? parsed : parsed.agents || []; }
    catch { return null; }
    if (agents.some(a => a.name === name)) return name;
    const project = name.replace(/-[0-9a-f]{2,}$/i, '');
    if (project === name) return null;
    const same = agents.filter(a => typeof a.name === 'string' && a.name.replace(/-[0-9a-f]{2,}$/i, '') === project && a.sessionId);
    return same.length === 1 ? same[0].sessionId : null;
  }
  async inspect(target) {
    if (target.agent === 'codex') return { compactedAt: target.id ? codexCompactedAt(target.id) : null };
    if (target.agent !== 'claude' || target.kind !== 'live') return {};
    const result = await this.execute('python3', [this.script, '--inspect', '--exact', '--session', target.id], { maxBuffer: 1024 * 1024, timeout: 5000 });
    return JSON.parse(result.stdout);
  }
}
module.exports = { Ask, locateAsk, codexCompactedAt };
