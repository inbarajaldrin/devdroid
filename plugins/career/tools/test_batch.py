"""Today through the real CLI, workflow and authenticated HTTP entrance; all data is invented."""
import json
import subprocess
import sys

import pytest

from runtime import CODE, Refused, read_json, write_json
from test_workflow import api, ready, seed, system


def test_batch_route_projects_current_records_and_never_rules_on_them(system, api):
    seed(system, 'job-two')
    seed(system, 'job-three')
    for pid,status in (('job-two','enriched'),('job-three','skipped')):
        rec=system.record(pid);rec['status']=status;system.save(rec)
    before = {p.name: p.read_bytes() for p in (system.root/'data/postings').glob('*.json')}
    assert api('GET','/batch',token='wrong')[0] == 401
    assert api('GET','/batch')[1] == dict(created_at=None,note=None,items=[],open=0,cleared=0,missing=[])
    stamp = api('GET','/stamp')[1]['stamp']
    system.dispatch('batch',operation='set',note='Review these first.',items=[
        {'id':'job-three','why':'Previously ruled.'}, {'id':'job-two','why':'Closes soon.'},
        {'id':'job-one','why':'Relevant work.'}])
    view = api('GET','/batch')[1]
    assert [i['record']['id'] for i in view['items']] == ['job-two','job-one','job-three']
    assert (view['open'],view['cleared'],view['missing']) == (2,1,[])
    assert view['items'][0]['why']=='Closes soon.' and view['items'][0]['record']['ref']
    assert before == {p.name: p.read_bytes() for p in (system.root/'data/postings').glob('*.json')}
    assert api('GET','/stamp')[1]['stamp'] != stamp
    assert api('POST','/postings/job-one/ruling',{'decision':'approved','by':'web'})[0]==200
    view=api('GET','/batch')[1]
    assert (view['open'],view['cleared'])==(1,2)
    assert system.browser.clicks==[]
    (system.root/'data/postings/job-two.json').unlink()
    view=api('GET','/batch')[1]
    assert view['missing']==['job-two'] and view['open']==0


def test_batch_bad_inputs_refuse_atomically_and_bad_saved_ids_never_escape_workspace(system):
    system.dispatch('batch',operation='set',items=[{'id':'job-one','why':'Relevant work.'}])
    path=system.root/'data/batch.json';before=path.read_bytes()
    for items in ([],[{'id':'../outside','why':'No.'}],[{'id':'unknown','why':'No.'}],
                  [{'id':'job-one','why':''}],[{'id':'job-one','why':True}],
                  [{'id':'job-one','why':'One'},{'id':'job-one','why':'Two'}]):
        with pytest.raises(Refused): system.dispatch('batch',operation='set',items=items)
        assert path.read_bytes()==before
    data=read_json(path);data['items'].append({'id':'../../outside','why':'Invalid saved id.'});write_json(path,data)
    assert system.batch()['missing']==['../../outside']
    path.write_text('not json')
    with pytest.raises(Refused,match='valid JSON'): system.batch()
    system.dispatch('batch',operation='clear')
    assert system.batch()['items']==[]


def test_batch_cli_set_add_show_clear_and_refusal_use_the_workspace(system, tmp_path):
    seed(system,'job-two')
    why=tmp_path/'reasons.json';why.write_text(json.dumps({'job-one':'Specific\n relevant work.'}))
    def command(*args,ok=True):
        result=subprocess.run([sys.executable,str(CODE/'tools/career.py'),'--home',str(system.root),'batch',*args],
                              text=True,capture_output=True)
        assert result.returncode==(0 if ok else 1),result.stderr
        return json.loads(result.stdout if ok else result.stderr)
    first=command('set','--why-file',str(why),'--note','For today.')
    assert first['items'][0]['why']=='Specific relevant work.' and first['note']=='For today.'
    why.write_text(json.dumps([{'id':'job-two','why':'Deadline approaching.'},{'id':'job-one','why':'Revised reason.'}]))
    added=command('add','--why-file',str(why))
    assert [row['record']['id'] for row in added['items']]==['job-one','job-two']
    assert added['items'][0]['why']=='Revised reason.' and added['created_at']==first['created_at']
    assert command('show')==added
    why=tmp_path/'reasons.tsv';why.write_text('# Reasons\njob-two\tA stated match.\njob-one\tA second choice.\n')
    ordered=command('set','job-one','job-two','--why-file',str(why))
    assert [row['record']['id'] for row in ordered['items']]==['job-one','job-two']
    assert command('set','unknown','--why-file',str(why),ok=False)['code']=='NOT_FOUND'
    assert command('show')==ordered
    why.write_text('job-two\t')
    assert command('set','--why-file',str(why),ok=False)['code']=='BAD_BATCH'
    assert command('clear')['items']==[] and command('show')['created_at'] is None


def test_batch_ready_records_have_the_same_submit_state_as_other_clients(system, api):
    ready(system)
    system.dispatch('batch',operation='set',items=[{'id':'job-one','why':'Review the prepared form.'}])
    rec=api('GET','/batch')[1]['items'][0]['record']
    assert rec['application']['state']=='submitting'
    system.dispatch('chat',text='Please change the example.',posting_id='job-one')
    rec=api('GET','/batch')[1]['items'][0]['record']
    assert rec['application'] is None and rec['review_summary']['action']=='hold'
