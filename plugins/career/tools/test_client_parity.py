"""Client contracts use the same workflow, with invented postings and isolated dependencies."""
import copy
import json
import subprocess
import sys

import pytest

import questions
from runtime import CODE, Refused, read_json, write_json
from test_workflow import api, system, ready, MATERIAL


def fields(doc):
    return [f for section in doc['sections'] for group in section['groups'] for f in group['fields']]


def test_preview_controls_and_versioned_review_routes_keep_one_approval_gate(system, api):
    system.browser.fields += [
        dict(name='Degree', type='combobox', required=True, options=['Research doctorate','Bachelor degree'], value=''),
        dict(name='Which offices are you willing to work in?', type='multiselect', required=True, options=['Northport','Eastfield'], value=''),
        dict(name='Work pattern', type='radio', required=True, options=['Onsite','Remote'], value='')]
    profile=system.profile()
    profile['standard_answers']={'Start date':'2027-01-01','Degree':'Research doctorate',
                                'Which offices are you willing to work in?':'Northport','Work pattern':'Onsite'}
    import yaml
    (system.root/'profile.yml').write_text(yaml.safe_dump(profile))
    ready(system)
    doc=api('GET','/postings/job-one/preview')[1]
    by={f['label']:f for f in fields(doc)}
    assert by['Degree']['control']=='select'
    assert by['Work pattern']['control']=='radio' and by['Work pattern']['options']==['Onsite','Remote']
    assert by['Which offices are you willing to work in?']['options']==['Northport']
    assert by['Which offices are you willing to work in?']['edit']['options']==['Northport','Eastfield']
    assert by['Resume']['control']=='file' and by['Resume']['file'].startswith('applications/job-one/uploads/')
    assert api('GET','/files/'+by['Resume']['file'])[1].startswith(b'%PDF')
    box=by['Why this role?']['box'];base='/postings/job-one/review/'+box['key']
    assert api('POST',base+'/approve',{'seen':'stale','by':'phone'})[0]==409
    assert api('POST',base+'/edit',{'text':'My exact edited answer.','by':'phone'})[0]==200
    assert api('POST',base+'/approve',{'seen':box['seen'],'by':'phone'})[0]==409
    system.dispatch('apply-edits','job-one')
    doc=api('GET','/postings/job-one/preview')[1]
    box=next(f['box'] for f in fields(doc) if f['label']=='Why this role?')
    assert api('POST',base+'/approve',{'seen':box['seen'],'by':'phone'})[0]==200
    assert api('POST',base+'/comment',{'text':'Please check the example.','by':'phone'})[0]==200
    doc=api('GET','/postings/job-one/preview')[1]
    assert doc['action']=='hold' and not doc['can_submit']
    assert api('POST','/postings/job-one/approve-submit',{'fill_digest':doc['fill_digest'],'by':'phone'})[0]==409
    assert not system.browser.clicks
    assert api('POST','/postings/job-one/review/unknown/approve',{'seen':'stale'})[0]==404
    assert api('POST',base+'/comment',{'text':'No unauthorized comment'},token='wrong')[0]==401


def test_chat_page_cursor_and_manual_applied_route_do_not_forge_submission(system, api):
    system.dispatch('chat',text='A synthetic reply.',reply=True)
    status,page=api('GET','/chat/page?since=0')
    assert status==200 and page['last']==1 and len(page['messages'])==1
    assert api('GET','/chat/page?since=1')[1]['messages']==[]
    assert api('GET','/chat/page?since=invalid')[0]==400
    assert api('GET','/chat/page',token='wrong')[0]==401
    ready(system)
    assert api('POST','/postings/job-one/applied',{'by':'agent'})[0]==409
    assert api('POST','/postings/job-one/applied',{'by':'phone'})[0]==409
    assert system.record('job-one')['status']=='ready' and not system.browser.clicks
    system.browser.show_confirmation=False
    with pytest.raises(Refused):system.dispatch('submit','job-one')
    system.browser.show_confirmation=True
    assert api('POST','/postings/job-one/applied',{'by':'phone'})[0]==200
    rec=api('GET','/postings/job-one')[1]
    assert rec['application']['state']=='submitted'
    assert rec['application']['days_since_submitted']==0
    assert all(isinstance(e['text'],str) and e['at'] for e in rec['application']['timeline'])
    assert system.browser.clicks==['Submit application']


def test_mixed_flat_motivation_evidence_is_repaired_by_source_and_old_missing_plan_retries(system):
    name='Why do you want to work at Example Company?'
    system.browser.fields=[dict(name=name,type='textarea',required=True,value='')]
    rec=system.record('job-one')
    company='Example Company builds inspection tools for research laboratories.'
    role_text='The team develops reliable robotic inspection systems.'
    summary='The role connects practical instrumentation and applied research.'
    rec.update(posting={'description':role_text},company_info={'what_they_do':company},summary_ai={'text':summary})
    system.save(rec)
    task=system.agents.task
    system.agents.task=lambda role,context:task(role,context) if role=='reviewer' else {'value':None,'evidence':[]}
    system.dispatch('pick','job-one',decision='approved')
    with pytest.raises(Refused):system.dispatch('fill','job-one')
    plan=read_json(system.adir('job-one')/'plan.json')
    for row in plan['rows']:
        row.pop('draft_contract',None);row['material_dependent']=False
    write_json(system.adir('job-one')/'plan.json',plan)
    personal=[MATERIAL[:60],MATERIAL[10:80],MATERIAL[:90],MATERIAL.strip()]
    value='I built a sensor calibration tool. I am interested in these inspection systems.'
    flat={'value':value,'evidence':[company,role_text,*personal,summary]}
    calls=[]
    def draft(role,context):
        if role=='reviewer':return task(role,context)
        calls.append(copy.deepcopy(context))
        if len(calls)==1:return flat
        assert context['revision']['draft']==flat
        return {'value':value,'claims':[
            dict(text='I built a sensor calibration tool.',scope='person',evidence=[personal[-1]]),
            dict(text='I am interested in these inspection systems.',scope='role',evidence=[company,role_text,summary]) ]}
    system.agents.task=draft
    system.dispatch('fill','job-one')
    assert system.review('job-one')['boxes'][0]['value']==value
    assert system.review('job-one')['boxes'][0]['source']=='draft'
    assert len(calls)==2 and sum(r=='reviewer' for r,c in system.agents.tasks)==1
    paths=questions.Paths(questions=system.root/'data/questions',postings=system.root/'data/postings',profile=system.root/'profile.yml',applications=system.root/'data/applications')
    assert not questions.list_q(paths,'open')
    assert not system.browser.clicks


def test_mixed_evidence_repair_is_bounded_and_never_accepts_role_quotes_for_person_claims(system):
    name='Explain your fit for this role'
    system.browser.fields=[dict(name=name,type='textarea',required=True,value='')]
    rec=system.record('job-one');rec['company_info']={'text':'Example Company builds reliable laboratory instruments.'};system.save(rec)
    calls=[];task=system.agents.task
    def draft(role,context):
        if role=='reviewer':return task(role,context)
        calls.append(context)
        return {'value':'I have a doctoral degree.','evidence':[MATERIAL.strip(),rec['company_info']['text']]}
    system.agents.task=draft
    system.dispatch('pick','job-one',decision='approved')
    system.dispatch('fill','job-one')
    box=system.review('job-one')['boxes'][0]
    # One repair at most; a person claim backed only by a role quote stays a proposal the person must check.
    assert len(calls)==2 and not box.get('missing') and box['state']=='pending'
    assert next(row for row in system.fill('job-one')['rows'] if row['name']==name).get('draft_note')


@pytest.mark.skipif(sys.platform != 'darwin', reason='Native Foundation decoding requires macOS')
def test_native_models_decode_the_actual_server_responses(system, api, tmp_path):
    ready(system)
    system.dispatch('submit','job-one')
    system.dispatch('chat',text='Synthetic app reply.',reply=True)
    paths=questions.Paths(questions=system.root/'data/questions',postings=system.root/'data/postings',profile=system.root/'profile.yml',applications=system.root/'data/applications')
    questions.add(paths,topic='profile',title='Office preferences',question='Which offices?',kind='multiselect',options=['Northport','Eastfield'])
    payload={key:api('GET',path)[1] for key,path in {
        'postings':'/postings','preview':'/postings/job-one/preview','review':'/postings/job-one/review',
        'chat':'/chat/page','questions':'/questions','stamp':'/stamp'}.items()}
    data=tmp_path/'client.json';data.write_text(json.dumps(payload))
    sources=CODE/'skills/jobs/examples/app/Sources'
    dates=(sources/'Dates.swift').read_text().split('/// Everything the inbox')[0].replace('import SwiftUI','import Foundation')
    (tmp_path/'Dates.swift').write_text(dates)
    main=tmp_path/'main.swift'
    main.write_text('''import Foundation
let raw = try Data(contentsOf: URL(fileURLWithPath: CommandLine.arguments[1]))
let object = try JSONSerialization.jsonObject(with: raw) as! [String:Any]
func decode<T:Decodable>(_ key:String,_ type:T.Type) throws -> T {
    try JSONDecoder.career.decode(type,from:JSONSerialization.data(withJSONObject:object[key]!))
}
let postings = try decode("postings",[Posting].self)
assert(postings.count == 1 && postings[0].application?.state == "submitted")
assert(postings[0].application?.timeline?.first?.text.isEmpty == false)
let preview = try decode("preview",PreviewDoc.self)
assert(preview.submitted == true && preview.fillDigest?.isEmpty == false)
assert(preview.sections?.first?.groups.first?.fields.isEmpty == false)
let review = try decode("review",ReviewDoc.self)
assert(review.boxes.count == 4 && !review.boxes[0].key.isEmpty)
let questions = try decode("questions",[Question].self)
let chat = try decode("chat",ChatPage.self)
let stamp = try decode("stamp",DataStamp.self)
assert(questions[0].kindValue == .multiselect)
assert(chat.messages.count == 1)
assert(!stamp.stamp.isEmpty)
print("Native client responses decoded")
''')
    binary=tmp_path/'client-model-tests'
    result=subprocess.run(['xcrun','swiftc','-module-cache-path',str(tmp_path/'modules'),
                           str(sources/'Models.swift'),str(tmp_path/'Dates.swift'),str(main),'-o',str(binary)],text=True,capture_output=True)
    assert result.returncode==0,result.stderr
    result=subprocess.run([str(binary),str(data)],text=True,capture_output=True)
    assert result.returncode==0,result.stderr
    assert result.stdout.strip()=='Native client responses decoded'
