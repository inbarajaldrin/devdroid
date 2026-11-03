#!/usr/bin/env node
import fs from 'node:fs';
import path from 'node:path';
import {spawnSync} from 'node:child_process';
import {fileURLToPath} from 'node:url';
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..'),scratch=path.join(root,'..','.scratchpad','sign-in-tests');
const home=path.join(scratch,'home'),tmp=path.join(scratch,'tmp');for(const dir of [home,tmp])fs.mkdirSync(dir,{recursive:true,mode:0o700});
const tests=fs.readdirSync(path.join(root,'tests')).filter(n=>n.endsWith('.test.mjs')).map(n=>path.join(root,'tests',n));
const result=spawnSync(process.execPath,['--test',...process.argv.slice(2),...tests],{stdio:'inherit',env:{PATH:process.env.PATH,LANG:'en_US.UTF-8',HOME:home,TMPDIR:tmp,SIGN_IN_HOME:path.join(home,'.devdroid/browser')}});
process.exitCode=result.status??1;
