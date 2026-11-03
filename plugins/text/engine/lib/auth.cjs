const fs=require('node:fs'),path=require('node:path');
const {insist}=require('./errors.cjs');
function secure(folder){
  fs.mkdirSync(folder,{recursive:true,mode:0o700});
  insist(!fs.lstatSync(folder).isSymbolicLink(),'AUTH_PATH','Auth must be a private directory');fs.chmodSync(folder,0o700);
  for(const name of fs.readdirSync(folder)){const file=path.join(folder,name),stat=fs.lstatSync(file);insist(stat.isFile(),'AUTH_PATH','Auth entries must be ordinary files');fs.chmodSync(file,0o600);}
}
function inspect(folder){
  if(!fs.existsSync(folder))return [];
  return [folder,...fs.readdirSync(folder).map(n=>path.join(folder,n))].filter(file=>{const s=fs.lstatSync(file);return s.isSymbolicLink() || (s.mode&0o077)!==0;});
}
module.exports={secure,inspect};
