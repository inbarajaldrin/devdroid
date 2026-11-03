// Development-only: the installed stock parser and CDP backend, on a browser owned by this test.
// No daemon, plugin discovery, personal profile or production route is used.
import {pathToFileURL} from 'node:url';
import {join} from 'node:path';
const [root,...argv]=process.argv.slice(2);
const load=relative=>import(pathToFileURL(join(root,relative)).href);
const {Command}=await load('node_modules/commander/esm.mjs');
const {registerBrowserCommands}=await load('dist/src/commands/browser.js');
const {BrowserBridge}=await load('dist/src/browser/bridge.js');
const {CDPBridge}=await load('dist/src/browser/cdp.js');
const {emitFailure}=await load('dist/src/command-output.js');
const endpoint=process.env.CAREER_FIXTURE_CDP;
if(!/^http:\/\/127\.0\.0\.1:\d+$/.test(endpoint||''))throw new Error('Only this test’s loopback CDP endpoint is allowed.');
const target=process.env.CAREER_FIXTURE_TARGET;
const bridge=new CDPBridge();
let page;
BrowserBridge.prototype.connect=async function(opts){
  if(opts.contextId!=='fixture-context')throw new Error('Test refuses another context.');
  const tabs=await (await fetch(endpoint+'/json/list')).json();
  if(!tabs.some(tab=>tab.id===target && tab.type==='page'))throw new Error('Test target was closed.');
  if(!page)page=await bridge.connect({cdpEndpoint:tabs.find(tab=>tab.id===target).webSocketDebuggerUrl});
  // The stock direct-CDP backend does not implement the extension's tab/session facade.
  // Bind it to this one actually owned target; the stock parser still validates every --tab.
  page.session=opts.session;
  page.getActivePage=()=>target;
  page.tabs=async()=>{
    const current=await (await fetch(endpoint+'/json/list')).json();
    return current.filter(tab=>tab.id===target).map(tab=>({page:tab.id,url:tab.url}));
  };
  page.setActivePage=id=>{if(id!==target)throw new Error('Wrong test tab.');};
  page.closeTab=async id=>{
    if(id!==target)throw new Error('Cannot close another tab.');
    const result=await page.cdp('Target.closeTarget',{targetId:id});
    if(!result.success)throw new Error('Owned target did not close.');
  };
  return page;
};
const program=new Command().name('opencli').option('--profile <name>').exitOverride();
registerBrowserCommands(program);
try{await program.parseAsync(['node','opencli',...argv]);}
catch(error){emitFailure(error);}
finally{await bridge.close();}
