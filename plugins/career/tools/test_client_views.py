"""Client contracts against the actual server and workflow, with invented records and dependencies."""
import json

import questions
from runtime import read_json, write_json
from test_workflow import system, api, ready, seed


def test_preview_is_authenticated_readback_and_edit_revokes_approval(system, api):
    ready(system)
    assert api('GET','/postings/job-one/preview',token='wrong')[0] == 401
    status, doc = api('GET','/postings/job-one/preview')
    assert status == 200 and doc['action'] == 'submitting' and not doc['can_submit']
    assert doc['ref'] == 'job: job-one · Example Company · Software engineer'
    assert next(b for b in doc['boxes'] if b['type']=='file')['value'] == 'resume.pdf'
    assert api('POST','/postings/job-one/review',{'name':'Why this role?','decision':'edit','value':'My exact revised reply.','by':'phone'})[0] == 200
    doc = api('GET','/postings/job-one/preview')[1]
    box = next(b for b in doc['boxes'] if b['name']=='Why this role?')
    assert box['value'] != 'My exact revised reply.' and box['edit_value'] == 'My exact revised reply.'
    assert box['needs_apply'] and doc['action']=='hold' and not doc['can_submit']
    assert api('GET','/postings/job-one')[1]['application'] is None
    system.dispatch('apply-edits','job-one')
    doc=api('GET','/postings/job-one/preview')[1]
    assert next(b for b in doc['boxes'] if b['name']=='Why this role?')['value']=='My exact revised reply.'
    assert doc['can_submit'] and doc['action']=='submit'
    assert api('POST','/postings/job-one/approve-submit',{'by':'web','fill_digest':'stale'})[0] == 409
    assert api('POST','/postings/job-one/approve-submit',{'by':'web','fill_digest':doc['fill_digest']})[0] == 200
    assert api('GET','/postings/job-one')[1]['application']['state'] == 'submitting'


def test_field_and_application_comments_hold_and_resolve_through_one_engine(system, api):
    ready(system)
    assert api('POST','/chat',{'text':'Use a different example.','posting_id':'job-one','field_name':'missing-field'})[0] == 409
    assert api('POST','/chat',{'text':'Use a different example.','posting_id':'job-one','field_name':['invalid']})[0] == 409
    status, comment = api('POST','/chat',{'text':'Use a different example.','posting_id':'job-one','field_name':'Why this role?','by':'web'})
    assert status == 200
    doc=api('GET','/postings/job-one/preview')[1]
    assert doc['action']=='hold' and doc['open_comments'][0]['field_name']=='Why this role?'
    assert api('GET','/postings/job-one')[1]['application'] is None
    assert api('POST','/postings/job-one/approve-submit',{'by':'phone','fill_digest':doc['fill_digest']})[0] == 409
    system.dispatch('chat',text='The example is corrected.',reply=True,resolve=comment['n'],posting_id='job-one')
    assert api('GET','/postings/job-one/preview')[1]['can_submit']
    api('POST','/chat',{'text':'Please check the whole form.','posting_id':'job-one','by':'phone'})
    assert not api('GET','/postings/job-one/preview')[1]['can_submit']


def test_applied_states_are_derived_from_current_approval_and_submission(system, api):
    ready(system)
    rec=system.record('job-one')
    rec['ruling']['submit_digest']='stale'
    system.save(rec)
    assert api('GET','/postings/job-one')[1]['application'] is None
    system.dispatch('approve-submit','job-one',fill_digest=system.review('job-one')['fill_digest'])
    system.dispatch('submit','job-one')
    rec=api('GET','/postings/job-one')[1]
    assert rec['application']['state']=='submitted'
    assert rec['application']['timeline']
    assert api('GET','/postings/job-one/preview')[1]['submitted']
    rec=system.record('job-one');rec['status']='rejected';system.save(rec)
    assert api('GET','/postings/job-one')[1]['application']['active'] is False


def test_stamp_tracks_all_client_data_and_assets_without_disclosing_paths(system, api, monkeypatch, tmp_path):
    import serve
    web=tmp_path/'static';web.mkdir();(web/'app.js').write_text('synthetic')
    monkeypatch.setattr(serve,'WEB_ROOT',web)
    assert api('GET','/stamp',token='wrong')[0]==401
    first=api('GET','/stamp')[1]
    assert set(first)=={'stamp','web'} and len(first['stamp'])==64
    ready(system)
    second=api('GET','/stamp')[1]
    assert second['stamp']!=first['stamp'] and second['web']==first['web']
    write_json(system.root/'data/questions/one.json',{'id':'one','status':'open'})
    third=api('GET','/stamp')[1]
    assert third['stamp']!=second['stamp']
    system.dispatch('chat',text='A synthetic message.')
    fourth=api('GET','/stamp')[1]
    assert fourth['stamp']!=third['stamp']
    write_json(system.root/'data/notifications.json',[{'message':'A local update.','state':'disabled'}])
    fifth=api('GET','/stamp')[1]
    assert fifth['stamp']!=fourth['stamp']
    write_json(system.root/'data/ota/notification.json',{'message':'App updated.','state':'disabled'})
    assert api('GET','/stamp')[1]['stamp']!=fifth['stamp']
    (web/'app.js').write_text('new synthetic client')
    assert api('GET','/stamp')[1]['web']!=fourth['web']
    assert str(tmp_path) not in json.dumps(first)


def test_optional_field_left_empty_by_person_is_retained_across_refill(system, api):
    system.browser.fields.append({'name':'Optional website','type':'text','required':False,'value':''})
    ready(system)
    assert api('POST','/postings/job-one/review',{'name':'Optional website','decision':'edit','value':'','by':'web'})[0]==200
    system.dispatch('apply-edits','job-one')
    system.dispatch('fill','job-one')
    box=next(b for b in system.review('job-one')['boxes'] if b['name']=='Optional website')
    assert box['intentional_empty'] and box['state']=='approved' and not box['flags']
    assert api('POST','/postings/job-one/review',{'name':'Start date','decision':'edit','value':'','by':'web'})[0]==409


def test_phone_pairing_trades_a_scoped_one_time_code_in_the_body(system, api):
    import time
    import serve
    code='synthetic-single-use-code'
    path=system.root/'.private/phone_pair_code.json'
    write_json(path,{'code':code,'expires':time.time()+60})
    url=serve.pairing_url('https://phone.example.org/api',None,code)
    assert 'token=' not in url and code in url and 'synthetic-api-token' not in url
    status, receipt=api('POST','/pair',{'code':code},token='')
    assert status==200 and receipt=={'token':'synthetic-api-token'}
    assert api('POST','/pair',{'code':code},token='')[0]==401
    write_json(path,{'code':code,'expires':time.time()-1})
    assert api('POST','/pair',{'code':code},token='')[0]==401
    write_json(path,{'code':code,'expires':time.time()+60})
    assert api('POST','/pair',{'code':'wrong'},token='')[0]==401
    assert api('POST','/pair',{'code':code},token='')[0]==401


def test_preview_fact_edits_resolve_the_existing_question_without_reasking(system, api):
    import pytest
    from runtime import Refused
    system.browser.fields.append({'name':'Current availability','type':'text','required':True,'value':''})
    system.dispatch('pick','job-one',decision='approved')
    with pytest.raises(Refused):system.dispatch('fill','job-one')
    assert api('POST','/postings/job-one/review',{'name':'Current availability','decision':'edit','value':'January 2027','by':'web'})[0]==200
    q=next(read_json(path) for path in (system.root/'data/questions').glob('*.json'))
    assert q['status']=='answered' and q.get('applied')
    system.dispatch('fill','job-one')
    assert next(f['value'] for f in system.browser.fields if f['name']=='Current availability')=='January 2027'


def test_uncertain_preview_is_read_only_without_claiming_the_application_was_sent(system, api):
    import pytest
    from runtime import Refused
    ready(system)
    system.browser.show_confirmation=False
    with pytest.raises(Refused):system.dispatch('submit','job-one')
    doc=api('GET','/postings/job-one/preview')[1]
    assert doc['read_only'] and not doc['submitted'] and not doc['can_submit']
    assert 'unconfirmed' in doc['status_line'] and 'What was sent' not in doc['status_line']
    assert api('POST','/postings/job-one/resolve-uncertain',{'by':'phone','reason':'Inspected and closed the existing tab.'})[0]==200
    doc=api('GET','/postings/job-one/preview')[1]
    assert doc['read_only'] and not doc['submitted']


def test_preview_opens_owned_uploads_and_screenshots_and_reports_warnings(system,api):
    ready(system)
    rec=system.record('job-one');rec['preview_notes']=['Read the stated eligibility requirement.'];system.save(rec)
    doc=api('GET','/postings/job-one/preview')[1]
    upload=next(box for box in doc['boxes'] if box['type']=='file')
    assert upload['file_link'].startswith('applications/job-one/uploads/')
    assert api('GET','/files/'+upload['file_link'])[1].startswith(b'%PDF')
    assert doc['screenshots'] and api('GET','/files/'+doc['screenshots'][0]['file'])[1].startswith(b'\x89PNG')
    assert doc['warnings']==['Read the stated eligibility requirement.']
    assert str(system.root) not in upload['file_link']


def test_manual_confirmation_route_still_requires_observed_positive_confirmation(system,api):
    import pytest
    from runtime import Refused
    ready(system)
    assert api('POST','/postings/job-one/confirm',{'by':'phone'})[1]['code']=='NO_SUBMIT'
    system.browser.show_confirmation=False
    with pytest.raises(Refused): system.dispatch('submit','job-one')
    clicks=len(system.browser.clicks)
    assert api('POST','/postings/job-one/confirm',{'by':'agent'})[0]==409
    assert api('POST','/postings/job-one/confirm',{'by':'phone'})[0]==409
    assert len(system.browser.clicks)==clicks
    system.browser.show_confirmation=True
    status,rec=api('POST','/postings/job-one/confirm',{'by':'web'})
    assert status==200 and rec['status']=='applied',rec
    assert len(system.browser.clicks)==clicks and rec['submission']['confirmed_at']
    rec=system.record('job-one');rec['status']='under_review';system.save(rec)
    assert api('GET','/postings/job-one')[1]['application']['state']=='under_review'


def test_incremental_chat_retains_order_and_validates_cursor(system,api):
    first=api('POST','/chat',{'text':'A synthetic note.'})[1]
    reply=system.dispatch('chat',text='A synthetic reply.',reply=True)
    assert [row['n'] for row in api('GET','/chat?since='+str(first['n']))[1]]==[reply['n']]
    assert api('GET','/chat?since=invalid')[0]==400


def test_reviewer_notes_are_read_only_and_survive_the_single_review_pass(system,api):
    task=system.agents.task
    def with_notes(role,context):
        result=task(role,context)
        if role=='reviewer':result['notes']=[{'label':'Application','text':'Reviewed the provided evidence.'}]
        return result
    system.agents.task=with_notes
    ready(system)
    assert api('GET','/postings/job-one/preview')[1]['info_notes']==[{'label':'Application','text':'Reviewed the provided evidence.'}]
    system.dispatch('fill','job-one')
    assert len([role for role,context in system.agents.tasks if role=='reviewer'])==1
    assert len(api('GET','/postings/job-one/preview')[1]['info_notes'])==1
