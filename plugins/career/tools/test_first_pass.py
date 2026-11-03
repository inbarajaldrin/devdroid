"""Resume-derived facts, scoped evidence and individual questions, on invented local applications."""
import copy

import pytest
import yaml

from answer_fields import phone_country,degree_answer,draft_supported
from integrations import OpenCLIBrowser
from runtime import CODE,Refused,read_json,write_json
from test_form_reader import reader
from test_workflow import api,system,seed,MATERIAL
import questions

COUNTRIES=['United States +1','Canada +1','Fictionland +999']
DEGREES=['Bachelor degree','Doctor of Philosophy (Ph.D.)','Ph.D.']
DESCRIPTION='The role develops reliable inspection systems for research laboratories.'
COMPANY='Example Company builds instruments for automated inspection.'
SUMMARY='The team builds practical tools for laboratory research.'


@pytest.mark.parametrize('phone,country,expected',[
    ('+1 202-555-0100',{'value':'United States','alternatives':['US']},'United States +1'),
    ('+1 416-555-0100',{'value':'Canada'},'Canada +1'),
    ('+9995550100',{'value':'Canada'},'Fictionland +999'),
    ('+5585550100',{'value':'United States'},None),
    ('+1 202-555-0100',{'value':'United States','confirm':True},None),
])
def test_phone_prefix_uses_location_only_to_resolve_shared_codes(phone,country,expected):
    assert phone_country({'contact':{'phone':phone},'location':{'country':country}},COUNTRIES)==expected


@pytest.mark.parametrize('options',[[DEGREES[1]],[DEGREES[2]],DEGREES])
def test_in_progress_degree_and_alternatives_match_option_punctuation(options):
    profile={'education':[{'degree':'PhD (in progress)'}], 'answers':{'degree_level':{'value':'PhD (in progress)',
        'alternatives':['Doctor of Philosophy (PhD)','Ph.D.','PhD','Doctorate','Doctoral']}}}
    before=copy.deepcopy(profile)
    value,key=degree_answer(profile,0,options)
    assert value in options and key and profile==before
    assert degree_answer(profile,0,['Bachelor degree'])==(None,None)
    profile['education'].append({'degree':'Unlisted degree'})
    assert degree_answer(profile,1,options)==(None,None)


@pytest.mark.parametrize('source',['description','company_info','summary_ai'])
def test_role_evidence_is_allowed_for_motivation_but_not_person_claims(source):
    rec={'posting':{'description':DESCRIPTION},'company_info':{'what_they_do':COMPANY},'summary_ai':{'text':SUMMARY}}
    material={'resume':MATERIAL,'profile':{},'posting':rec}
    quote={'description':DESCRIPTION,'company_info':COMPANY,'summary_ai':SUMMARY}[source]
    draft={'value':'I am interested in this research work.','evidence':[quote]}
    assert draft_supported(draft,material,{'name':'Why do you want to work at Example Company?'})
    assert not draft_supported({**draft,'value':'I have a doctoral degree.'},material,{'name':'Why this role?'})
    assert not draft_supported(draft,material,{'name':'Are you authorized to work here?'})
    bad={'value':'I built a production system.','claims':[{'text':'I built a production system.','scope':'role','evidence':[quote]}]}
    assert not draft_supported(bad,material,{'name':'Why this role?'})
    assert not draft_supported({'value':'A personal fact.','claims':[{'text':'A personal fact.','scope':'person','evidence':[quote]}]},material,{'name':'Why this role?'})
    multiline='The team builds "inspection tools".\nThe role serves research laboratories.'
    if source=='description':rec['posting']['description']=multiline
    else:rec[source]={'text':multiline}
    assert draft_supported({**draft,'evidence':[multiline]},material,{'name':'Why this role?'})


def test_mixed_claims_cannot_leave_uncovered_text_or_use_unknown_record_data():
    material={'resume':MATERIAL,'profile':{},'posting':{'posting':{'description':DESCRIPTION},'untrusted_note':'An invented qualification in a record note.'}}
    claims=[{'text':'I built a sensor calibration tool.','scope':'person','evidence':[MATERIAL.strip()]},
            {'text':'This inspection work interests me.','scope':'role','evidence':[DESCRIPTION]}]
    draft={'value':' '.join(c['text'] for c in claims),'claims':claims}
    assert draft_supported(draft,material,{'name':'Why this role?'})
    assert not draft_supported({**draft,'value':draft['value']+' I have five degrees.'},material,{'name':'Why this role?'})
    claims[1]['evidence']=[material['posting']['untrusted_note']]
    assert not draft_supported(draft,material,{'name':'Why this role?'})


@pytest.mark.parametrize('name',['What interests you about this team?','Explain your fit for this role'])
def test_interest_and_fit_questions_are_drafted_from_role_material(system,name):
    system.browser.fields=[{'name':name,'type':'textarea','required':True,'value':''}]
    rec=system.record('job-one');rec['posting']={'description':DESCRIPTION};system.save(rec)
    worker=system.agents.task
    def task(role,context):
        if role=='drafter':
            system.agents.tasks.append((role,copy.deepcopy(context)))
            return {'value':'I am interested in this inspection work.','claims':[{
                'text':'I am interested in this inspection work.','scope':'role','evidence':[DESCRIPTION]}]}
        return worker(role,context)
    system.agents.task=task
    system.dispatch('pick','job-one',decision='approved')
    system.dispatch('fill','job-one')
    assert system.review('job-one')['boxes'][0]['value']=='I am interested in this inspection work.'
    assert [r for r,c in system.agents.tasks]==['drafter','reviewer']


def test_first_pass_maps_facts_drafts_motivation_and_asks_only_the_real_gap(reader,system,api,monkeypatch):
    html=(CODE/'tools/fixtures/searchable-application.html').read_text()
    html=html.replace("[['Fictionland +999','zz'],['Northland +999','zy'],['Elsewhere +998','zx']]",
                      "[['United States +1','us'],['Canada +1','ca'],['Fictionland +999','zz']]")
    html=html.replace("[['Associate degree'],['Bachelor degree'],['Doctoral degree']]","[['Bachelor degree'],['Doctor of Philosophy (Ph.D.)']]")
    html=html.replace('Why this role?','Why do you want to work at Example Company?')
    profile={'name':{'first_name':'Example'},'contact':{'email':'person@example.org','phone':'+1 202-555-0100',
        'linkedin':'https://profiles.example.org/engineer','website':'https://portfolio.example.org'},
        'location':{'country':{'value':'United States','alternatives':['US']}},
        'education':[{'degree':'PhD (in progress)','school':'Example College','discipline':'Software systems'}],
        'answers':{'degree_level':{'value':'PhD (in progress)','alternatives':['Doctor of Philosophy (PhD)','Ph.D.','PhD','Doctorate','Doctoral']},'relocation':'Yes','onsite_ok':'Yes'}}
    (system.root/'profile.yml').write_text(yaml.safe_dump(profile))
    entry=system.root/'sign-in.mjs';entry.write_text('// fixture entrance')
    monkeypatch.setenv('SIGN_IN_SCRIPT',str(entry))
    settings=read_json(system.root/'settings.json');settings['live_browser_writes']='Enabled';write_json(system.root/'settings.json',settings)
    paths=questions.Paths(questions=system.root/'data/questions',postings=system.root/'data/postings',profile=system.root/'profile.yml',applications=system.root/'data/applications')
    old=[]
    for code in ('FORM_UNREADABLE','UPLOAD_RECEIPT','FIELD_CONTRACT'):
        old.append(questions.add(paths,topic='application',title='Application needs your help',question='Old generic stop?',posting_id='job-one',applies_to={'human_step':code,'posting_id':'job-one'})['id'])
    seed(system,'job-elsewhere')
    unrelated=questions.add(paths,topic='application',title='Other application',question='Keep this open?',posting_id='job-elsewhere',applies_to={'human_step':'FORM_UNREADABLE','posting_id':'job-elsewhere'})
    task=system.agents.task
    def worker(role,context):
        if role=='drafter':
            system.agents.tasks.append((role,copy.deepcopy(context)))
            claims=[{'text':'I built a sensor calibration tool.','scope':'person','evidence':[MATERIAL.strip()]},
                    {'text':'I am interested in these inspection instruments.','scope':'role','evidence':[COMPANY]}]
            return {'value':' '.join(c['text'] for c in claims),'claims':claims}
        return task(role,context)
    system.agents.task=worker
    def attach(pid):
        reader(html)
        system.browser=OpenCLIBrowser(settings,root=system.root,runner=reader.runner)
        rec=system.record(pid);rec.update(url=reader.url,title='Test engineer',posting={'description':DESCRIPTION},company_info={'what_they_do':COMPANY},summary_ai={'text':SUMMARY});system.save(rec)
        system.dispatch('pick',pid,decision='approved')
    attach('job-one')
    with pytest.raises(Refused) as error:system.dispatch('fill','job-one')
    assert error.value.code=='MISSING_FACT'
    boxes={b['name']:b for b in system.review('job-one')['boxes']}
    assert boxes['Phone country']['value']=='United States +1'
    assert boxes['Education 1 / Degree*']['value']==DEGREES[1]
    assert boxes['LinkedIn Profile']['value']==profile['contact']['linkedin']
    assert boxes['Personal Website or Portfolio']['value']==profile['contact']['website']
    assert boxes['Why do you want to work at Example Company?']['value'].startswith('I built')
    assert all(questions.read_q(paths,qid)['status']=='dismissed' for qid in old)
    assert questions.read_q(paths,unrelated['id'])['status']=='open'
    qs=[q for q in questions.list_q(paths,'open') if q.get('posting_id')=='job-one']
    assert len(qs)==1 and qs[0]['kind']=='multiselect'
    q=qs[0]
    assert q['options']==['Northport, ZZ','Eastfield, ZZ'] and 'JSON' not in q['question']+q['context']
    assert q['applies_to']['form_question']=='Which offices are you willing to work in? *'
    assert not any(role=='drafter' and 'office' in context['field']['name'].lower() for role,context in system.agents.tasks)
    answer=api('POST','/questions/'+q['id']+'/answer',{'answer':['Northport, ZZ','Eastfield, ZZ'],'by':'web'})
    assert answer[0]==200,answer
    system.dispatch('fill','job-one')
    assert len([role for role,context in system.agents.tasks if role=='reviewer'])==1
    filled={f['name']:f for f in system.fill('job-one')['rows']}
    actual={f['name']:f for f in system.browser.read()['fields']}
    for name in ('Phone country','Education 1 / Degree*','LinkedIn Profile','Personal Website or Portfolio','Why do you want to work at Example Company?',q['applies_to']['form_question']):
        assert filled[name]['value']==actual[name]['value']
    for box in system.review('job-one')['boxes']:
        if box['state']!='approved':system.dispatch('box','job-one',name=box['name'],decision='approve')
    assert not system.record('job-one')['ruling'].get('submit_approved_at')
    preview=api('GET','/postings/job-one/preview')[1]
    assert preview['can_submit']
    assert api('POST','/postings/job-one/approve-submit',{'fill_digest':preview['fill_digest'],'by':'web'})[0]==200
    assert system.record('job-one')['ruling']['submit_digest']==system.review('job-one')['fill_digest']
    assert reader.command('eval','window.unexpectedSubmits')==0
    system.dispatch('release','job-one')
    seed(system,'job-two');attach('job-two')
    system.dispatch('fill','job-two')
    assert not [q for q in questions.list_q(paths,'open') if q.get('posting_id')=='job-two']
    assert next(b for b in system.review('job-two')['boxes'] if b['type']=='multiselect')['value']=='Northport, ZZ; Eastfield, ZZ'
    assert reader.command('eval','window.unexpectedSubmits')==0


def test_help_uses_actual_stop_and_never_auto_closes_an_uncertain_outcome(system):
    rec=system.record('job-one')
    system.stop_question(rec,Refused('FORM_UNREADABLE','The reader cannot account for Degree.'))
    system.stop_question(rec,Refused('NAVIGATION_UNCERTAIN','Inspect the existing tab.'))
    paths=questions.Paths(questions=system.root/'data/questions',postings=system.root/'data/postings',profile=system.root/'profile.yml',applications=system.root/'data/applications')
    q=next(q for q in questions.list_q(paths) if q['applies_to']['human_step']=='FORM_UNREADABLE')
    assert 'Degree' in q['question'] and 'consent box' not in q['question']
    system.stop_question(rec,Refused('FORM_UNREADABLE','The reader cannot account for Phone.'))
    q=questions.read_q(paths,q['id'])
    assert 'Phone' in q['question'] and len(questions.list_q(paths,'open'))==2
    reason='Unread fields: '+', '.join('Example field '+str(i) for i in range(100))
    system.stop_question(rec,Refused('FORM_UNREADABLE',reason))
    q=questions.read_q(paths,q['id'])
    assert len(q['question'])<=questions.MAX_QUESTION and q['context']==reason
    system.close_help_questions(rec)
    assert [q['applies_to']['human_step'] for q in questions.list_q(paths,'open')]==['NAVIGATION_UNCERTAIN']
