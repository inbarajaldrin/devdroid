#!/usr/bin/env node
import fs from 'node:fs';
import path from 'node:path';
import { ROOT } from '../lib/core.mjs';
import { digest, verifyRuntime } from '../lib/setup.mjs';
const files=[];
function walk(dir){for(const entry of fs.readdirSync(path.join(ROOT,dir),{withFileTypes:true})){const rel=path.join(dir,entry.name);if(entry.name==='node_modules'||rel==='browser/runtime.sha256.json')continue;if(entry.isDirectory())walk(rel);else files.push(rel);}}
for(const dir of ['lib','browser','scripts','skills','.claude-plugin'])walk(dir);
files.push('README.md','LICENSE','NOTICE','accounts.example.json');files.sort();
if(process.argv.includes('--update'))fs.writeFileSync(path.join(ROOT,'browser/runtime.sha256.json'),JSON.stringify(Object.fromEntries(files.map(file=>[file,digest(fs.readFileSync(path.join(ROOT,file)))])),null,2)+'\n');
else {verifyRuntime();const recorded=Object.keys(JSON.parse(fs.readFileSync(path.join(ROOT,'browser/runtime.sha256.json')))).sort();if(JSON.stringify(recorded)!==JSON.stringify(files))throw Error('Runtime manifest file list differs');console.log('Runtime checksums and file list match.');}
