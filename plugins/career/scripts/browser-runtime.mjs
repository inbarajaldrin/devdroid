#!/usr/bin/env node
// Use the dependency's own pin, binary resolver and read-only daemon guard.
import {dirname,resolve,isAbsolute} from 'node:path';
import {pathToFileURL} from 'node:url';
const entry=process.argv[2];
try {
 if (!entry || !isAbsolute(entry)) throw Error('The installed sign-in entrance is required');
 const root=resolve(dirname(entry),'..');
 const load=path=>import(pathToFileURL(resolve(root,path)));
 const {opencli,run}=await load('lib/core.mjs');
 const {compatible}=await load('lib/daemon.mjs');
 const {pins}=await load('browser/settings.mjs');
 const binary=opencli();
 if (!isAbsolute(binary)) throw Error('OPENCLI_BIN must be the absolute pinned executable');
 if (process.argv[3] === '--local-only') {
   const version=(await run(binary,['--version'])).stdout.trim();
   if(version!==pins.opencli.version) throw Error('Pinned executable version differs');
 } else await compatible({start:false,cli:binary});
 console.log(JSON.stringify({ok:true,data:{binary,version:pins.opencli.version}}));
} catch(error) {
 console.error(JSON.stringify({ok:false,code:error.code||'BROWSER_RUNTIME',error:'Pinned browser runtime refused; inspect sign-in doctor. Nothing was started or replaced.'}));
 process.exitCode=1;
}
