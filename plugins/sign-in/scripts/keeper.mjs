#!/usr/bin/env node
import { keepSignIn, expireTunnel } from '../lib/sign-in.mjs';
const [mode,file,generation]=process.argv.slice(2);
const ready=()=>{if(process.connected)process.send?.({ready:true},()=>{});};
try { if(mode==='sign-in')await keepSignIn(file,{ready});else if(mode==='sign-in-notice'){ready();await (await import('../lib/notifications.mjs')).notifyHandoff(file,generation);}else if(mode==='bridge-idle')await (await import('../lib/bridge-lifecycle.mjs')).keepBridges(file,{ready});else if(mode==='tunnel')await expireTunnel(file,{ready});else if(mode==='human')await (await import('../lib/human-lease.mjs')).keepHuman(file,{ready});else throw Error('Unknown keeper mode'); }
catch { process.exitCode=1; }
