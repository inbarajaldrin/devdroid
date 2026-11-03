// Inspect literal dependencies without executing customized code or loading sockets.
function literals(source) {
  const found=[];let i=0;
  function code(nested=false){
    const tokens=[];let braces=0;
    const push=t=>{tokens.push(t);if(tokens.length>4)tokens.shift();};
    while(i<source.length){
      const c=source[i];
      if(/\s/.test(c)){i++;continue;}
      if(source.startsWith('//',i)){i=source.indexOf('\n',i);if(i<0)i=source.length;continue;}
      if(source.startsWith('/*',i)){const end=source.indexOf('*/',i+2);i=end<0?source.length:end+2;continue;}
      if(c==='"'||c==="'"){
        const quote=c;let value='';i++;
        while(i<source.length&&source[i]!==quote){if(source[i]==='\\'){i++;value+=source[i++] || '';}else value+=source[i++];}i++;
        const following=source.slice(i).trimStart()[0];
        if(['from','import'].includes(tokens.at(-1)) || tokens.at(-1)==='(' && ['require','import'].includes(tokens.at(-2)) && tokens.at(-3)!=='.' && [')',','].includes(following))found.push(value);
        push('literal');continue;
      }
      if(c==='`'){
        i++;while(i<source.length){if(source[i]==='\\'){i+=2;continue;}if(source[i]==='`'){i++;break;}if(source.startsWith('${',i)){i+=2;code(true);}else i++;}
        push('literal');continue;
      }
      if(c==='/' && (!tokens.length || ['(','=','[',',',':','return','!',';','{','}','?','|','&'].includes(tokens.at(-1)))){
        i++;let group=false;while(i<source.length){const ch=source[i++];if(ch==='\\'){i++;continue;}if(ch==='[')group=true;if(ch===']')group=false;if(ch==='/'&&!group)break;}while(/[a-z]/i.test(source[i] || '')&&i<source.length)i++;push('regex');continue;
      }
      const word=source.slice(i).match(/^[A-Za-z_$][\w$]*/);if(word){i+=word[0].length;push(word[0]);continue;}
      if(c==='{')braces++;
      if(c==='}'){if(nested && braces===0){i++;return;}braces--;}
      push(c);i++;
    }
  }
  code();return [...new Set(found)];
}
module.exports={literals};
