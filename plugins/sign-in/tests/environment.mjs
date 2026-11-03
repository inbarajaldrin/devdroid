// Direct `node --test tests/*.test.mjs` is as isolated as scripts/test.mjs.
import fs from 'node:fs';import os from 'node:os';import path from 'node:path';import {fileURLToPath} from 'node:url';
const repo=fs.realpathSync(path.resolve(path.dirname(fileURLToPath(import.meta.url)),'../..')),scratch=path.join(repo,'.scratchpad','sign-in-tests');
const inside=file=>{if(!file)return false;let parent=path.resolve(file);const tail=[];if(!parent.startsWith(repo+path.sep))return false;while(true){try{return path.join(fs.realpathSync(parent),...tail).startsWith(repo+path.sep);}catch(e){if(e.code!=='ENOENT')return false;tail.unshift(path.basename(parent));const next=path.dirname(parent);if(next===parent)return false;parent=next;}}};
const home=inside(process.env.HOME)?process.env.HOME:path.join(scratch,'home'),tmp=inside(os.tmpdir())?os.tmpdir():path.join(scratch,'tmp');
for(const dir of [home,tmp])fs.mkdirSync(dir,{recursive:true,mode:0o700});
process.env.HOME=home;process.env.TMPDIR=tmp;if(!inside(process.env.SIGN_IN_HOME))process.env.SIGN_IN_HOME=path.join(home,'browser');
// Fixtures never inherit a live agent address or executable notification override.
for(const key of ['CLAUDE_CODE_SESSION_ID','CODEX_THREAD_ID','ASK_SCRIPT'])delete process.env[key];
