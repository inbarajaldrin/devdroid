import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { spawn } from 'node:child_process';
import { HOME, insist, run, read, locked, opencli } from './core.mjs';
import { pins } from '../browser/settings.mjs';

export async function daemonStatus(fetcher = fetch) {
  try {
    const r = await fetcher('http://127.0.0.1:19825/status', { headers: { 'X-OpenCLI': '1' }, signal: AbortSignal.timeout(2000) });
    insist(r.ok, 'DAEMON_PORT_OCCUPIED', 'The OpenCLI port answers with an unexpected service');
    const data = await r.json(); insist(typeof data.daemonVersion === 'string', 'DAEMON_PORT_OCCUPIED', 'Unrecognized daemon on port 19825'); return data;
  } catch (e) { if (e.code || e.name === 'SyntaxError') throw e; return null; }
}
export async function compatible({ start = false, execute = run, status = daemonStatus, launch, cli = opencli() } = {}) {
  const version = (await execute(cli, ['--version'])).stdout.trim();
  insist(version === pins.opencli.version, 'CLI_CONFLICT', 'Select OpenCLI ' + pins.opencli.version + '; no daemon was changed');
  // The extension uses one fixed daemon port. Never let a different CLI replace it.
  const current = await status();
  insist(!current || current.daemonVersion === version, 'DAEMON_CONFLICT', 'A different OpenCLI daemon owns port 19825; align callers before continuing');
  if (current || !start) { insist(current || start, 'DAEMON_STOPPED', 'Daemon is stopped; run bridge ACCOUNT'); return current; }
  return locked(path.join(os.tmpdir(), `devdroid-opencli-${process.getuid?.() || 0}`), async () => {
    const again = await status();
    if (again) { insist(again.daemonVersion === version, 'DAEMON_CONFLICT', 'Another daemon appeared; nothing was replaced'); return again; }
    if (launch) await launch();
    else {
      const executable=path.isAbsolute(cli)?cli:(await execute('which',[cli])).stdout.trim();
      let dir = path.dirname(fs.realpathSync(executable));
      while (!fs.existsSync(path.join(dir, 'package.json')) && dir !== path.dirname(dir)) dir = path.dirname(dir);
      const pkg = read(path.join(dir, 'package.json'));
      insist(pkg.name === pins.opencli.package && pkg.version === version, 'CLI_LAYOUT', 'Cannot locate the pinned stock daemon');
      const child = spawn(process.execPath, [path.join(dir, 'dist/src/daemon.js')], { detached: true, stdio: 'ignore' });
      await new Promise((resolve, reject) => { child.once('spawn', resolve); child.once('error', reject); }); child.unref();
    }
    const end = Date.now() + 10000;
    while (Date.now() < end) {
      const found = await status();
      if (found) { insist(found.daemonVersion === version, 'DAEMON_CONFLICT', 'A different daemon appeared'); return found; }
      await new Promise(r => setTimeout(r, 100));
    }
    throw new Error('Stock OpenCLI daemon did not start');
  });
}

export async function sharedUsers(execute = run, {platform=process.platform,uid=process.getuid?.()}={}) {
  const text = platform === 'darwin' ? (await execute('dscl', ['.', '-list', '/Users', 'UniqueID'])).stdout : fs.readFileSync('/etc/passwd', 'utf8');
  const users=[];
  for(const line of text.split('\n')){
    const fields=platform==='darwin'?line.trim().split(/\s+/):line.split(':'),name=fields[0];
    const id=Number(fields[platform==='darwin'?1:2]);
    if(!(id>=(platform==='darwin'?500:1000)&&id<65534)||name?.startsWith('_')||id===uid)continue;
    if(platform==='darwin'){
      const attributes=(await execute('dscl',['.','-read',`/Users/${name}`,'UserShell','NFSHomeDirectory'])).stdout;
      const shell=attributes.match(/^UserShell:\s*(.*)$/m)?.[1],home=attributes.match(/^NFSHomeDirectory:\s*(.*)$/m)?.[1];
      if(shell&&/(nologin|false)$/.test(shell))continue;
      // PostgreSQL's installer creates an interactive-shell service identity.
      if(name==='postgres'&&home?.startsWith('/Library/PostgreSQL/'))continue;
      users.push(name);
    }else if(!/(nologin|false)$/.test(fields[6]||''))users.push(name);
  }
  return users;
}
export async function privateHolder(machine, inspect = sharedUsers) {
  insist(machine.exclusive === true, 'PRIVATE_HOLDER_REQUIRED', 'Confirm a machine used only by this user before holding accounts here');
  const others = await inspect(); insist(!others.length, 'SHARED_HOST', 'Other human users exist on this machine; do not hold signed-in browsers here');
}
