#!/usr/bin/env node
import fs from 'node:fs';
import path from 'node:path';
import net from 'node:net';
import os from 'node:os';
import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { HOME, insist, run, json, privateDir, locked, opencli, read, local, hostKey, processStart, atomicJSON } from '../lib/core.mjs';
import { load, accountsPath, recordContext } from '../lib/accounts.mjs';
import { compatible, privateHolder } from '../lib/daemon.mjs';
import { browserCDP, ensureExtension, extensionContext } from './opencli-bridge.mjs';
import { binary, pins } from './settings.mjs';
import {bridgeWork,lifecycleFile} from '../lib/bridge-lifecycle.mjs';

// Login browsers adopt by exact profile; only positively owned processes may stop.
export async function profilePid(profileDir, execute = run, expectedPort) {
  const { stdout } = await execute('ps', ['-ww', '-axo', 'pid=,command=']);
  for (const line of stdout.split('\n')) {
    const match = line.trim().match(/^(\d+)\s+(.+)$/);
    if (!match || match[2].includes('--type=')) continue;
    const configured = match[2].match(/(?:^| )--user-data-dir=(.*?)(?= --[a-z-]+(?:=| |$)| about:blank(?: |$)|$)/)?.[1];
    if (configured !== profileDir) continue;
    const port = Number(match[2].match(/(?:^| )--remote-debugging-port=(\d+)(?: |$)/)?.[1]);
    insist(expectedPort === undefined || port === expectedPort, 'PROFILE_PORT_MISMATCH', 'The existing profile uses another debugging port; no launch was attempted');
    return Number(match[1]);
  }
  return null;
}
async function until(work, deadline = 30000) {
  const end = Date.now() + deadline;
  let last;
  while (Date.now() < end) {
    try { const found = await work(); if (found) return found; } catch (e) { last = e; }
    await new Promise(r => setTimeout(r, 200));
  }
  throw last || new Error('Bridge readiness timed out');
}
async function portFree(port) {
  await new Promise((resolve, reject) => {
    const s = net.createServer(); s.once('error', reject);
    s.listen(port, '127.0.0.1', () => s.close(resolve));
  });
}
export function launchArgs(b, extension) {
  return [`--user-data-dir=${b.profileDir}`, `--load-extension=${extension}`,
    `--remote-debugging-port=${b.port}`, '--remote-debugging-address=127.0.0.1',
    '--no-first-run', '--no-default-browser-check', '--host-resolver-rules=MAP localhost 127.0.0.1',
    '--disable-backgrounding-occluded-windows', '--disable-renderer-backgrounding',
    ...((b.headless ?? (process.platform==='linux'&&!process.env.DISPLAY&&!process.env.WAYLAND_DISPLAY)) ? ['--headless=new'] : []),
    ...(b.offscreen ? ['--window-position=-32000,-32000', '--window-size=1280,860'] : []), 'about:blank'];
}
export async function wake(port, fetcher = fetch) {
  const response = await fetcher(`http://127.0.0.1:${port}/json/list`, { signal: AbortSignal.timeout(5000) });
  insist(response.ok, 'CDP_FAILED', 'Cannot list bridge tabs');
  const tabs = await response.json();
  insist(Array.isArray(tabs), 'CDP_FAILED', 'Invalid tab list');
  if (tabs.some(t => t.type === 'page')) return { openedBlankTab: false };
  const newTab = await fetcher(`http://127.0.0.1:${port}/json/new?about:blank`, { method: 'PUT', signal: AbortSignal.timeout(5000) });
  insist(newTab.ok, 'CDP_FAILED', 'Cannot wake bridge');
  return { openedBlankTab: true };
}
export async function show(port, cdp = browserCDP, mode = 'show') {
  return cdp(port, async send => {
    const { targetInfos } = await send('Target.getTargets');
    const ids = new Set();
    for (const t of targetInfos.filter(t => t.type === 'page')) ids.add((await send('Browser.getWindowForTarget', { targetId: t.targetId })).windowId);
    let index = 0;
    for (const windowId of ids) {
      await send('Browser.setWindowBounds', { windowId, bounds: { windowState: 'normal' } });
      await send('Browser.setWindowBounds', { windowId, bounds: { left: mode==='park'?-32000:80+30*index++, top: mode==='park'?-32000:60, width: 1280, height: 860 } });
    }
    return { windows: ids.size };
  });
}
export const park = (port, cdp=browserCDP)=>show(port,cdp,'park');
export async function recoverChromeLock(profile, {alive=pid=>{try{process.kill(pid,0);return true;}catch(e){if(e.code==='ESRCH')return false;throw e;}}, hosts=[os.hostname()]}={}) {
  const lock=path.join(profile,'SingletonLock');let stat;
  try{stat=fs.lstatSync(lock);}catch(e){if(e.code==='ENOENT')return false;throw e;}
  insist(stat.isSymbolicLink()&&stat.uid===process.getuid?.(),'PROFILE_LOCKED','Unrecognized Chrome lock; nothing was changed');
  const target=fs.readlinkSync(lock),match=target.match(/^(.*)-(\d+)$/);
  if(process.platform==='darwin')try{hosts.push((await run('scutil',['--get','LocalHostName'])).stdout.trim());}catch{}
  insist(match&&hosts.some(h=>hostKey(h)===hostKey(match[1])),'PROFILE_LOCKED','Chrome lock belongs to another host');
  insist(!alive(Number(match[2])),'PROFILE_LOCKED','Chrome lock owner is still alive');
  insist(fs.readlinkSync(lock)===target,'PROFILE_LOCKED','Chrome lock changed');
  for(const name of ['SingletonLock','SingletonSocket','SingletonCookie']){
    const file=path.join(profile,name);let s;try{s=fs.lstatSync(file);}catch(e){if(e.code==='ENOENT')continue;throw e;}
    insist(s.isSymbolicLink()&&s.uid===process.getuid?.(),'PROFILE_LOCKED','Unexpected Chrome singleton file');fs.unlinkSync(file);
  }
  return true;
}
function layer() {
  return {
    extension: path.join(HOME, 'opencli-extension', pins.extension.version),
    binary: binary(),
  };
}
export async function verifyBridge(b, dependencies = {}) {
  const d = { pid: profilePid, execute: run, cdp: browserCDP, layer: layer(), ...dependencies };
  const pid = await d.pid(b.profileDir, d.execute, b.port);
  insist(pid, 'BRIDGE_NOT_RUNNING', 'The configured profile is not running; no browser was opened');
  const extension = fs.realpathSync(d.layer.extension);
  insist(read(path.join(extension, 'manifest.json')).version === pins.extension.version, 'EXTENSION_VERSION', 'The registered extension differs from the pin');
  const contextId = await d.cdp(b.port, send => extensionContext(send, extension));
  insist(contextId, 'BRIDGE_SLEEPING', 'The official extension has no live context; no browser was changed');
  const result = json((await d.execute(opencli(), ['profile', 'list'], { timeout: 10000 })).stdout);
  insist(!result.profiles?.some(p=>p.alias===contextId&&p.contextId!==contextId),'BRIDGE_ALIAS_MISMATCH','A saved alias shadows the exact bridge context');
  const alias = result.profiles?.find(p => p.alias === b.name);
  insist(alias?.contextId === contextId && alias.connected === true && alias.extensionVersion === pins.extension.version, 'BRIDGE_ALIAS_MISMATCH', 'OpenCLI alias is not this configured bridge; no alias was changed');
  return { name: b.name, port: b.port, pid, contextId, verified: true };
}
export async function up(name, b, dependencies = {}) {
  const d = { root: HOME, pid: profilePid, cdp: browserCDP, execute: run, wake, ready: until, portFree, spawn, layer: layer(), compatible, recover:recoverChromeLock, park,
    ownership:async(profile,pid,port)=>await profilePid(profile,run,port)===pid?processStart(pid):null,
    probe: async port => {
      const r = await fetch(`http://127.0.0.1:${port}/json/version`, { signal: AbortSignal.timeout(5000) });
      return r.ok && (await r.json()).Browser;
    }, ...dependencies };
  let pid = await d.pid(b.profileDir, d.execute, b.port);
  const launched = !pid;
  await d.compatible({start:true});
  if (launched) {
    const aliases=json((await d.execute(opencli(),['profile','list'])).stdout).profiles||[];
    const reserved=aliases.find(p=>p.alias===name);
    insist(!reserved||(b.contextIds||[]).includes(reserved.contextId),'ALIAS_COLLISION','Alias is already reserved; migrate it before launch');
    await d.portFree(b.port);
    await d.recover(b.profileDir);
    insist(fs.existsSync(d.layer.binary) && fs.existsSync(path.join(d.layer.extension, 'manifest.json')), 'SETUP_REQUIRED', 'Run sign-in setup on the holding machine');
    privateDir(b.profileDir); privateDir(path.join(d.root, 'logs'));
    const output = fs.openSync(path.join(d.root, 'logs', `${name}.log`), 'a', 0o600);
    try {
      const child = d.spawn(d.layer.binary, launchArgs(b, d.layer.extension), { detached: true, stdio: ['ignore', output, output] });
      // An executable failure is observed before readiness polling starts.
      await new Promise((resolve, reject) => { child.once('spawn', resolve); child.once('error', reject); });
      child.unref(); pid = child.pid;
      const start=await d.ownership(b.profileDir,pid,b.port);
      insist(start,'BRIDGE_OWNERSHIP_UNKNOWN','Launched process could not be positively identified; inspect it before retrying');
      privateDir(path.join(d.root,'owned'));atomicJSON(path.join(d.root,'owned',name+'.json'),{pid,start,profile:b.profileDir,port:b.port});
    } finally { fs.closeSync(output); }
  }
  await d.ready(() => d.probe(b.port));
  const woke = await d.wake(b.port);
  await d.cdp(b.port, send => ensureExtension(send, fs.realpathSync(d.layer.extension)));
  const contextId = await d.ready(() => d.cdp(b.port, send => extensionContext(send, fs.realpathSync(d.layer.extension))));
  const before=json((await d.execute(opencli(),['profile','list'])).stdout).profiles||[];
  const existing=before.find(p=>p.alias===name);
  insist(!existing||existing.contextId===contextId||(b.contextIds||[]).includes(existing.contextId),'ALIAS_COLLISION','Alias belongs to an unrecognized browser; nothing was renamed');
  if(existing?.contextId!==contextId)json((await d.execute(opencli(), ['profile', 'rename', contextId, name], { timeout: 10000 })).stdout);
  const connected = await d.ready(async () => {
    const result = json((await d.execute(opencli(), ['profile', 'list'], { timeout: 10000 })).stdout);
    return result.profiles?.find(p => p.contextId === contextId && p.alias === name && p.connected === true);
  });
  insist(connected, 'BRIDGE_DISCONNECTED', 'Extension context is not connected');
  if(b.offscreen)await d.park(b.port);
  return { name, pid, launched, adopted: !launched, contextId, port: b.port, ...woke };
}
export async function command(accountsFile, cmd, name, {leaseLocked=false}={}) {
  const data = load(accountsFile);
  const pairs = Object.entries(data.accounts);
  const entry = pairs.find(([account]) => account === name);
  if(cmd==='bridge-up-all'){const result=[];for(const [key,a]of pairs)if(local(a.machine))result.push(await (await import('../scripts/sign-in.mjs')).main(['--accounts',accountsFile,'bridge',key]));return result;}
  if (cmd === 'list' || cmd === 'state') return Promise.all(pairs.map(async ([account, a]) => ({ account, ...a.bridge, machine: a.machine.id, local: local(a.machine), pid: local(a.machine) ? await profilePid(a.bridge.profileDir) : null,...(local(a.machine)?{work:bridgeWork(a)}:{}) })));
  insist(entry, 'BRIDGE_UNKNOWN', `Unknown bridge: ${name}`);
  const [, a] = entry;
  insist(local(a.machine), 'WRONG_MACHINE', 'Run the launcher on the holding machine');
  if (cmd === 'verify') {await compatible();return verifyBridge(a.bridge);}
  if (cmd === 'bridge-up') {await privateHolder(a.machine);return locked(path.join(HOME, 'locks', a.bridge.name), async()=>{
    const result=await up(a.bridge.name,a.bridge);
    // A verified adoption enters this login's managed lifecycle too. Preserve the
    // exact PID/start/profile proof; an arbitrary browser can never be claimed.
    if(result.adopted){const start=processStart(result.pid);insist(start&&await profilePid(a.bridge.profileDir,run,a.bridge.port)===result.pid,'BRIDGE_OWNERSHIP_UNKNOWN','Adopted bridge could not be positively identified');atomicJSON(path.join(HOME,'owned',name+'.json'),{pid:result.pid,start,profile:a.bridge.profileDir,port:a.bridge.port});}
    await recordContext(accountsFile,name,result.contextId);return result;
  });}
  if (cmd === 'show') return show(a.bridge.port);
  if (cmd === 'park') return park(a.bridge.port);
  if (cmd === 'wake') {const result=await wake(a.bridge.port);if(a.bridge.offscreen)await park(a.bridge.port);return result;}
  if(cmd==='bridge-down'){
    const close=async()=>{
    insist(!bridgeWork(a).reasons.length,'BRIDGE_LEASED','Another operation or human handoff holds this bridge');
    const pid=await profilePid(a.bridge.profileDir,run,a.bridge.port);if(!pid)return {name,closed:false,alreadyStopped:true};
    const receipt=path.join(HOME,'owned',name+'.json');insist(fs.existsSync(receipt),'EXTERNAL_BRIDGE','Only a positively registered login browser may stop');
    const owned=read(receipt);
    insist(pid===owned.pid&&owned.start&&processStart(pid)===owned.start&&owned.profile===a.bridge.profileDir&&owned.port===a.bridge.port,'EXTERNAL_BRIDGE','The login process is not positively owned; no browser was stopped');
    await browserCDP(a.bridge.port,send=>send('Browser.close'));return {name,closed:true};
    };return leaseLocked?close():locked(lifecycleFile(a),close);
  }
  throw new Error('Use bridge-up ACCOUNT, verify ACCOUNT, show ACCOUNT, wake ACCOUNT, list, state or serve');
}
export function control(socket, accountsFile, execute = command) {
    socket.setTimeout(5000, () => socket.destroy());
    let buf = '', used = false;
    socket.on('data', async bytes => {
      if (used) return; buf += bytes;
      if (buf.length > 8192) return socket.destroy();
      if (!buf.includes('\n')) return;
      used = true;
      socket.setTimeout(180000, () => socket.destroy());
      try {
        const req = JSON.parse(buf.split('\n')[0]);
        if (req.cmd === 'list') return socket.end(JSON.stringify({ ok: true, data: Object.keys(load(accountsFile).accounts).flatMap(name => ['up', 'show','park'].map(action => ({ id: `bridge.${name}.${action}`, type: 'action' }))) }) + '\n');
        const id = req.id?.match(/^bridge\.([a-z][a-z0-9-]*)\.(up|show|park)$/);
        const data = req.cmd === 'state' ? await execute(accountsFile, 'state')
          : req.cmd === 'invoke' && id ? execute===command
            ? await (await import('../scripts/sign-in.mjs')).main(['--accounts',accountsFile,id[2]==='up'?'bridge':id[2],id[1]])
            : await execute(accountsFile, id[2] === 'up' ? 'bridge-up' : id[2], id[1]) : null;
        insist(data, 'CONTROL_COMMAND', 'Use list, state or invoke bridge.NAME.up|show');
        socket.end(JSON.stringify({ ok: true, data }) + '\n');
      } catch (e) { socket.end(JSON.stringify({ ok: false, code: e.code || 'LAUNCHER_FAILED', error: e.message }) + '\n'); }
    });
    socket.on('error', () => {});
}
export function serve(accountsFile, port = Number(process.env.SIGN_IN_CONTROL_PORT || 47616)) {
  insist(Number.isInteger(port) && port >= 0 && port <= 65535, 'CONTROL_PORT', 'Invalid control port');
  const server = net.createServer(socket => control(socket, accountsFile));
  server.listen(port, '127.0.0.1');
  return server;
}
if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const args = process.argv.slice(2);
  let file;
  if (args[0] === '--accounts') { args.shift(); file = args.shift(); }
  if (args[0] === 'serve') {
    load(accountsPath(file));
    serve(accountsPath(file)).on('error', e => { console.error(JSON.stringify({ ok: false, error: e.message })); process.exitCode = 1; });
  } else command(accountsPath(file), ...args).then(data => console.log(JSON.stringify({ ok: true, data }))).catch(e => { console.error(JSON.stringify({ ok: false, code: e.code || 'LAUNCHER_FAILED', error: e.message })); process.exitCode = 1; });
}
