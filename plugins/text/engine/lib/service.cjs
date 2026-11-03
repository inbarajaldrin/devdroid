const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const { insist } = require('./errors.cjs');
const xml = s => s.replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;').replaceAll('"', '&quot;');
// Both managers restart failures after 30s and leave intentional exit 0 stopped.
// A service outlives this shell, so per-session folders must not be saved into it.
function lastingPath(value) {
  const temporary = [require('node:os').tmpdir(), '/tmp', '/private/tmp', '/var/folders', '/private/var/folders'].map(root => root.replace(/\/$/, ''));
  return value.split(':').filter(entry => entry && !temporary.some(root => entry === root || entry.startsWith(root + '/'))).join(':');
}
function definition({ directory, account = 'main', platform = process.platform, node = process.execPath, env = process.env,linger }) {
  insist(['darwin','linux'].includes(platform), 'SERVICE_PLATFORM', 'No service adapter is configured');
  insist(/^[a-zA-Z0-9_-]+$/.test(account), 'INVALID_ACCOUNT', 'Invalid service account');
  const label = 'com.devdroid.text' + (account === 'main' ? '' : '.' + account);
  const unit = 'devdroid-text' + (account === 'main' ? '' : '-' + account) + '.service';
  const daemon = path.join(directory, 'runtime/engine/daemon.cjs');
  const vars = { TEXT_HOME: directory, TEXT_ACCOUNT: account, PATH: lastingPath(env.PATH || '/usr/bin:/bin'),...(platform==='darwin'?{TEXT_LOG_FILE:path.join(directory,'service.log')}:{}) };
  const q = s => { insist(!/[\r\n\0]/.test(s), 'SERVICE_VALUE', 'Service paths must fit on one line'); return JSON.stringify(s.replaceAll('%','%%')); };
  const plist = `<?xml version="1.0"?><plist version="1.0"><dict><key>Label</key><string>${label}</string><key>ThrottleInterval</key><integer>30</integer><key>ProgramArguments</key><array><string>${xml(node)}</string><string>${xml(daemon)}</string></array><key>EnvironmentVariables</key><dict>${Object.entries(vars).map(([k,v]) => '<key>'+k+'</key><string>'+xml(v)+'</string>').join('')}</dict><key>RunAtLoad</key><true/><key>KeepAlive</key><dict><key>SuccessfulExit</key><false/></dict><key>StandardOutPath</key><string>${xml(path.join(directory,'service.log'))}</string><key>StandardErrorPath</key><string>${xml(path.join(directory,'service.log'))}</string></dict></plist>\n`;
  const systemd = `[Unit]\nDescription=devdroid text\nStartLimitIntervalSec=0\n[Service]\nType=simple\nExecStart=${q(node.replaceAll('$',()=> '$$'))} ${q(daemon.replaceAll('$',()=> '$$'))}\n${Object.entries(vars).map(([k,v]) => 'Environment='+q(k+'='+v)).join('\n')}\nRestart=on-failure\nRestartSec=30\nUMask=0077\n[Install]\nWantedBy=default.target\n`;
  const filename=platform==='darwin'?label+'.plist':unit,quote=s=>"'"+s.replaceAll("'","'\\''")+"'",source=quote(path.join(directory,'service',filename));
  const activation=platform==='linux'?['mkdir -p ~/.config/systemd/user',`install -m 600 ${source} ~/.config/systemd/user/${unit}`,'systemctl --user daemon-reload',...(linger===true?['loginctl enable-linger "$USER"']:[]),`systemctl --user enable --now ${unit}`]:['mkdir -p ~/Library/LaunchAgents',`install -m 600 ${source} ~/Library/LaunchAgents/${filename}`,`launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/${filename}`];
  const boundedPlist=plist.replace(/(<key>Standard(?:Out|Error)Path<\/key><string>)[^<]*(<\/string>)/g,'$1/dev/null$2');
  return { platform,node, label, unit, filename,activation,logs:platform==='linux'?`journalctl --user -u ${unit} -n 100 --no-pager`:path.join(directory,'service.log')+' (5 MB, 3 backups)', templates: { [label+'.plist']: boundedPlist, [unit]: systemd } };
}
function stage(options) {
  const d = definition(options), folder = path.join(options.directory,'service'); fs.mkdirSync(folder,{recursive:true,mode:0o700});
  const preserved=[];
  for (const [name, source] of Object.entries(d.templates)) {const file=path.join(folder,name);if(fs.existsSync(file)){preserved.push(file);continue;}fs.writeFileSync(file, source, {mode:0o600});}
  return { manager: d.platform === 'linux' ? 'systemd-user' : 'launchd', template: path.join(folder,d.filename), installed: false,preserved,activation:d.activation,logs:d.logs,...(d.platform==='linux'?{lingering:'loginctl enable-linger "$USER" (only after the user chooses to keep it running after logout)'}:{}) };
}
function registered(options){const d=definition(options),env=options.env || process.env,home=env.HOME || os.homedir();return d.platform==='linux'?path.join(env.XDG_CONFIG_HOME || path.join(home,'.config'),'systemd/user',d.unit):path.join(home,'Library/LaunchAgents',d.filename);}
async function inspect({ execute, uid = process.getuid(), ...options }) {
  const d = definition(options);
  try{fs.accessSync(d.node,fs.constants.X_OK);}catch{return {ok:false,code:'NODE_MISSING',error:'The service Node executable was removed; repair the staged service and entrances before activation',logs:d.logs};}
  const command = d.platform === 'linux' ? ['systemctl',['--user','show',d.unit,'--property=ActiveState,SubState,ExecMainStatus,Restart,RestartUSec,Environment','--no-pager']] : ['launchctl',['print',`gui/${uid}/${d.label}`]];
  try {
    const {stdout} = await execute(...command, {timeout:5000,maxBuffer:262144});
    const running = d.platform === 'linux' ? /^ActiveState=active$/m.test(stdout) && /^SubState=running$/m.test(stdout) : /state = running/.test(stdout);
    let lingering=true;
    if(d.platform==='linux'){try{const result=await execute('loginctl',['show-user',String(uid),'--property=Linger'],{timeout:5000,maxBuffer:262144});lingering=/^Linger=yes$/m.test(result.stdout);}catch{lingering=false;}}
    return { ok:running&&(lingering || options.linger===false),lingering, logs:d.logs,next:lingering?undefined:options.linger===false?'Saved choice: run only while logged in.':'Ask once whether to keep the engine running after logout; save linger, then with the user present run loginctl enable-linger "$USER".',manager:d.platform === 'linux' ? 'systemd-user' : 'launchd', detail:stdout.slice(0,4000), staleAskOverride:stdout.includes('ASK_SCRIPT') && options.askScript && !stdout.includes(options.askScript) };
  } catch (e) { return {ok:false, manager:d.platform === 'linux' ? 'systemd-user' : 'launchd', error:e.stderr || e.message}; }
}
module.exports = { lastingPath, definition, stage, inspect, registered };
