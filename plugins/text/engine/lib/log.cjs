const fs=require('node:fs');
// launchd has no journal. Bound its diagnostic log using the same daemon entrance.
function append(file,text,limit=5*1024*1024){
  if(fs.existsSync(file)&&fs.statSync(file).size+Buffer.byteLength(text)>limit){
    fs.rmSync(file+'.3',{force:true});for(let i=2;i>=1;i--)if(fs.existsSync(file+'.'+i))fs.renameSync(file+'.'+i,file+'.'+(i+1));fs.renameSync(file,file+'.1');
  }
  fs.appendFileSync(file,text,{mode:0o600});
}
function configure(file){if(file)for(const name of ['log','error','warn'])console[name]=(...args)=>append(file,args.map(v=>typeof v==='string'?v:JSON.stringify(v)).join(' ')+'\n');}
module.exports={append,configure};
