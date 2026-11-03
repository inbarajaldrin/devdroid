const net = require('node:net');
const { spawn } = require('node:child_process');
const { insist, Refusal } = require('./errors.cjs');
const { hostOK } = require('./rules.cjs');
const { accountDirectory } = require('./account.cjs');
function request(socket, payload) {
  return new Promise((resolve, reject) => {
    const conn = net.createConnection(require('./socket.cjs').address(socket)); let buffer = '';
    conn.setTimeout(30000, () => conn.destroy(new Refusal('SOCKET_TIMEOUT', 'Engine did not respond; delivery may be uncertain, do not retry automatically')));
    conn.on('connect', () => conn.write(JSON.stringify(payload) + '\n'));
    conn.on('data', data => {
      buffer += data.toString('utf8');
      const end = buffer.indexOf('\n');
      if (end >= 0) { conn.end(); try { resolve(JSON.parse(buffer.slice(0, end))); } catch (e) { reject(e); } }
    });
    conn.on('error', reject);
    conn.on('end', () => { if (!buffer.includes('\n')) reject(new Refusal('SOCKET_CLOSED', 'Engine closed without a receipt')); });
  });
}
function remote(host, payload, execute = spawn, {timeoutMs=30000}={}) {
  insist(hostOK(host), 'INVALID_HOST', 'SSH host must be a configured alias');
  const account = payload.account || 'main'; accountDirectory('/unused', account);
  const runtime = account === 'main' ? '$HOME/.devdroid/text/runtime' : '$HOME/.devdroid/text/accounts/' + account + '/runtime';
  return new Promise((resolve, reject) => {
    // One JSON request, unchanged; no remote flag parser can discard reply/media options.
    const child = execute('ssh', ['-o', 'BatchMode=yes', '-o', 'ConnectTimeout=15', host,
      '"' + runtime + '/bin/text-relay"']);
    let out = '', err = '', settled=false;
    const receipt=()=>{try{const value=JSON.parse(out);return value && typeof value.ok==='boolean'?value:null;}catch{return null;}};
    const finish=(error,result)=>{if(settled)return;settled=true;clearTimeout(timer);error?reject(error):resolve(result);};
    const timer=setTimeout(()=>{
      const result=receipt();const error=new Refusal('SSH_ACCEPTANCE_UNKNOWN','SSH returned no acceptance receipt before its deadline; inspect before resend');
      error.notDelivered=['ping','status','message-id','resolve','aliases','find','members','index','roster','rules-get','rules-status','ingress-key','publication-status'].includes(payload.cmd);
      finish(result?null:error,result);child.kill?.('SIGTERM');
    },timeoutMs);
    child.stdout.on('data', d => {out += d;if(out.length>38*1024*1024){finish(new Refusal('SSH_PROTOCOL','Remote reply exceeded 38 MB'));child.kill?.('SIGTERM');}}); child.stderr.on('data', d => {err=(err+d).slice(-262144);});
    child.on('error', e=>{const error=new Refusal('SSH_FAILED',e.message);error.notDelivered=true;finish(error);});
    child.on('close', code => {
      const result=receipt();
      if(result)finish(null,result);
      else finish(new Refusal(code!==0?'SSH_FAILED':'SSH_PROTOCOL',err || `SSH exited ${code} without an acceptance receipt; no automatic retry`));
    });
    child.stdin.end(JSON.stringify(payload));
  });
}
function forAccount(account='main',send=remote){
  accountDirectory('/unused',account);
  return (host,payload)=>{
    if(payload.account!==undefined && payload.account!==account){const e=new Refusal('ACCOUNT_CONFLICT','Forwarded request account must match this engine');e.notDelivered=true;throw e;}
    return send(host,{...payload,account});
  };
}
module.exports = { request, remote, forAccount };
