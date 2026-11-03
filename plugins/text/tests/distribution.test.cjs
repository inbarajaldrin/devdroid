const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const root=path.resolve(__dirname,'..');
function files(folder){return fs.readdirSync(folder,{withFileTypes:true}).flatMap(e=>e.isDirectory()?files(path.join(folder,e.name)):[path.join(folder,e.name)]);}
test('L4: the distributable has its declared license and no private identifiers or unpublished citations',()=>{
  assert.match(fs.readFileSync(path.join(root,'LICENSE'),'utf8'),/Apache License/);
  for(const file of files(root)){
    const source=fs.readFileSync(file,'utf8');assert.ok(!/\d{10,}@(g\.us|lid|s\.whatsapp\.net)/.test(source),'numeric identity in '+path.relative(root,file));
    assert.ok(!/\/Users\/[a-z]/i.test(source),'private path in '+path.relative(root,file));
    assert.ok(!path.basename(file).startsWith('ported-'),'unpublished lineage in '+path.relative(root,file));
  }
  assert.ok(!fs.existsSync(path.join(root,'VERIFICATION.md')));assert.ok(!fs.existsSync(path.join(root,'.scratchpad')));
});
