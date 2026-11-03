const fs=require('node:fs'),path=require('node:path');
const root=process.env.TEXT_BAILEYS_PACKAGE || [path.resolve(__dirname,'../engine/baileys/node_modules/baileys'),path.resolve(__dirname,'../../.scratchpad/sdk/node_modules/baileys')].find(p=>fs.existsSync(path.join(p,'package.json')));
if(!root)throw new Error('Install the pinned Baileys test dependency outside the plugin and set TEXT_BAILEYS_PACKAGE');
if(JSON.parse(fs.readFileSync(path.join(root,'package.json'))).version!=='7.0.0-rc14')throw new Error('Tests require the pinned Baileys release');
const {generateMessageIDV2}=require(path.join(root,'lib/Utils/generics.js'));
module.exports={nativeId:()=>generateMessageIDV2('fixture@s.whatsapp.net')};
