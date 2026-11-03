const fs = require('node:fs');
const net = require('node:net');
const { insist, Refusal } = require('./errors.cjs');
function address(file){const relative=require('node:path').relative(process.cwd(),file),short=relative && Buffer.byteLength(relative)<Buffer.byteLength(file)?relative:file;insist(Buffer.byteLength(short)<104,'SOCKET_PATH_TOO_LONG','Run from the runtime directory to shorten the Unix socket address');return short;}
async function ensureSocketFree(file) {
  let before;
  try { before = fs.lstatSync(file); } catch (e) { if (e.code === 'ENOENT') return; throw e; }
  insist(before.isSocket(), 'SOCKET_PATH_OCCUPIED', 'Runtime socket path is occupied by a non-socket file');
  const live = await new Promise((resolve, reject) => {
    const conn = net.createConnection(address(file));
    conn.setTimeout(1000, () => conn.destroy(new Refusal('SOCKET_UNCERTAIN', 'Existing socket did not answer; refusing to remove it')));
    conn.on('connect', () => { conn.destroy(); resolve(true); });
    conn.on('error', e => ['ECONNREFUSED', 'ENOENT'].includes(e.code) ? resolve(false) : reject(e));
  });
  insist(!live, 'SERVICE_RUNNING', 'This runtime already has a live engine');
  let after;
  try { after = fs.lstatSync(file); } catch (e) { if (e.code === 'ENOENT') return; throw e; }
  insist(after.ino === before.ino && after.isSocket(), 'SOCKET_CHANGED', 'Socket path changed during the liveness check');
  fs.unlinkSync(file);
}
module.exports = { ensureSocketFree,address };
