import test from 'node:test';
import assert from 'node:assert/strict';
import {extractPay,normalizePay,payFields} from './pay.mjs';
import {mkdtempSync,writeFileSync,readFileSync,readdirSync,rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
test('compensation is extracted from prose and normalized with explicit conversion assumptions',()=>{
  const description='Build reliable systems.\nThe base pay range is $32–48 per hour.\nOur team works on sensors.';
  const pay=extractPay(description);
  assert.equal(pay,'The base pay range is $32–48 per hour.');
  assert.deepEqual(normalizePay(pay),{hourly:[32,48],yearly:[67000,100000],unit:'hour',currency:'USD',basis:'stated'});
  assert.deepEqual(normalizePay('Annual salary $120-150K per year').yearly,[120000,150000]);
  assert.equal(normalizePay('$6000 per month').basis,'converted');
  assert.equal(normalizePay('CAD $45 per hour'),null);
  assert.equal(normalizePay('AUD $45 per hour'),null);
  assert.equal(normalizePay('$40 health benefit'),null);
  assert.deepEqual(normalizePay('36 USD - 52 USD per hour').hourly,[36,52]);
  assert.deepEqual(normalizePay('USD 40 / hour').hourly,[40,40]);
  assert.deepEqual(normalizePay('Hourly rate: USD 40 - 55').hourly,[40,55]);
  assert.equal(extractPay('Annual salary €60000 per year.'),'Annual salary €60000 per year.');
  assert.equal(normalizePay('$2000 bonus'),null);
  assert.equal(extractPay('Build software. No pay stated.'),null);
  assert.equal(payFields({pay:'$40 per hour',description:'Annual salary $120K per year'}).pay,'$40 per hour');
});

test('scanner saves and backfills pay from descriptions while retaining a chosen pay value',async()=>{
  const home=mkdtempSync(join(tmpdir(),'career-pay-'));
  const oldHome=process.env.CAREER_HOME,fetch=globalThis.fetch,args=process.argv;
  process.env.CAREER_HOME=home;process.argv=['node','scan'];
  writeFileSync(join(home,'companies.yml'),JSON.stringify({companies:[{name:'Example Company',ats:'greenhouse',board:'example'}]}));
  globalThis.fetch=async()=>new Response(JSON.stringify({jobs:[{title:'Test engineer',absolute_url:'https://careers.example.org/test-role',content:'<p>The base pay is $32-48 per hour.</p>'}]}));
  try {
    const {main}=await import('./scan.mjs?pay-fixture');
    await main();
    const path=join(home,'data/postings',readdirSync(join(home,'data/postings'))[0]);
    let rec=JSON.parse(readFileSync(path));
    assert.equal(rec.posting.pay,'The base pay is $32-48 per hour.');
    assert.deepEqual(rec.posting.pay_normalized.hourly,[32,48]);
    delete rec.posting.pay;delete rec.posting.pay_normalized;
    writeFileSync(path,JSON.stringify(rec));await main();
    rec=JSON.parse(readFileSync(path));assert.deepEqual(rec.posting.pay_normalized.hourly,[32,48]);
    rec.posting.pay='$72 per hour';writeFileSync(path,JSON.stringify(rec));await main();
    assert.deepEqual(JSON.parse(readFileSync(path)).posting.pay_normalized.hourly,[72,72]);
  } finally {
    globalThis.fetch=fetch;process.argv=args;
    if(oldHome===undefined)delete process.env.CAREER_HOME;else process.env.CAREER_HOME=oldHome;
    rmSync(home,{recursive:true,force:true});
  }
});
