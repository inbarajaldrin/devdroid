const fs=require('node:fs'),path=require('node:path'),{createHash}=require('node:crypto'),{write}=require('./store.cjs');
function retain(folder,message,trustedRelay=false){
 const inputKey=createHash('sha256').update(JSON.stringify([message.chat,message.id])).digest('hex'),file=path.join(folder,inputKey+'.json');
 if(!fs.existsSync(file))write(file,{message,trustedRelay,inputKey});
 return {file,inputKey};
}
module.exports={retain};
