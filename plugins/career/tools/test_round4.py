"""Regression cases from the second independent review; synthetic data only."""
import json
import os
import subprocess
from pathlib import Path
import pytest
import integrations
from runtime import Refused
from test_workflow import system
from test_browser import browser


def test_h1_sign_in_real_cli_envelope(system, tmp_path, monkeypatch):
    dependency = Path(os.environ.get('SIGN_IN_PLUGIN') or Path(__file__).resolve().parents[2] / 'sign-in')
    if not dependency.is_dir():
        pytest.skip('Dependency checkout required for real CLI contract check')
    env = dict(os.environ, TMPDIR=str(tmp_path), HOME=str(tmp_path), SIGN_IN_HOME=str(tmp_path/'sign-in-home'))
    result = subprocess.run(['node', str(Path(__file__).resolve().parent / 'testing/sign-in-output.mjs'), str(dependency)],
                            env=env, text=True, capture_output=True, check=True)
    proof = json.loads(result.stdout)
    assert proof['ok'] is True and proof['data']['contextId'] == 'ctx-work'
    fake = browser(system, tmp_path, monkeypatch)
    original = fake.__call__
    system.browser.runner = lambda argv: result.stdout if argv[0] == 'node' and not argv[1].endswith('browser-runtime.mjs') else original(argv)
    assert system.browser._proof('work', 'handshake') == 'ctx-work'


def test_h1_bare_identity_is_not_a_cli_receipt(system, tmp_path, monkeypatch):
    fake = browser(system, tmp_path, monkeypatch)
    original = fake.__call__
    system.browser.runner = lambda argv: json.dumps({'account':'demo','site':'example-company',
        'status':'signed-in','contextId':'fixture-context'}) if argv[0]=='node' and not argv[1].endswith('browser-runtime.mjs') else original(argv)
    with pytest.raises(Refused, match='sign-in'):
        system.browser._proof('demo','example-company')

from test_steps import Steps, picked, approve
from runtime import write_json, read_json


def test_h2_one_click_apply_is_never_fill_navigation(system):
    system.browser=Steps()
    old=system.browser.read
    def read():
        page=old()
        if 'apply_control' in page:page['apply_control'].update(tag='button',href=None)
        return page
    system.browser.read=read
    system.dispatch('pick','job-one',decision='approved')
    with pytest.raises(Refused):system.dispatch('fill','job-one')
    assert not system.browser.moves and not system.browser.writes


def test_h2_continue_is_not_next(system):
    system.browser=Steps();old=system.browser.read
    def read():
        page=old()
        if page.get('next_control'):page['next_control']['name']='Continue'
        return page
    system.browser.read=read
    system.dispatch('pick','job-one',decision='approved')
    with pytest.raises(Refused):system.dispatch('fill','job-one')
    assert all(name!='Continue' for _,name in system.browser.moves)


def test_h2_email_only_registration_stops_before_fill(system):
    old=system.browser.read
    system.browser.read=lambda:dict(old(),account_creation=True,text='Create your candidate profile to continue')
    system.dispatch('pick','job-one',decision='approved')
    with pytest.raises(Refused):system.dispatch('fill','job-one')
    assert not system.browser.writes

@pytest.mark.parametrize('label',['Enter the 6-digit code','Code','2FA code','MFA code','Two-factor code','Email code','Access code','Verification token','Login code','Signature','Electronic signature','Type your full name to sign','Data privacy','I have read the privacy policy','I understand','GDPR','Candidate declaration'])
def test_h3_never_write_sensitive_even_with_person_value(system,tmp_path,monkeypatch,label):
    fake=browser(system,tmp_path,monkeypatch)
    fake.fields.append(dict(name=label,type='text',required=True,value='',selector='#secret'))
    import yaml
    profile=yaml.safe_load((system.root/'profile.yml').read_text());profile['standard_answers'][label]='482913'
    (system.root/'profile.yml').write_text(yaml.safe_dump(profile))
    system.dispatch('pick','job-one',decision='approved')
    with pytest.raises(Refused):system.dispatch('fill','job-one')
    assert not any(c[6:8]==['fill','#secret'] for c in fake.calls)

@pytest.mark.parametrize('kind',['select','radio'])
def test_h3_consent_options_are_personal(system,tmp_path,monkeypatch,kind):
    fake=browser(system,tmp_path,monkeypatch)
    fake.fields.append(dict(name='Data processing',type=kind,required=True,value='',selector='#privacy',options=['I agree','I do not agree']))
    system.dispatch('pick','job-one',decision='approved')
    with pytest.raises(Refused):system.dispatch('fill','job-one')
    assert not any(c[6] in ('fill','select','check') for c in fake.calls if len(c)>6)


def test_h3_signature_context_blocks_contact_alias(system,tmp_path,monkeypatch):
    fake=browser(system,tmp_path,monkeypatch)
    fake.fields.append(dict(name='Full name',type='text',required=True,value='',selector='#signature',context='By typing your name you agree to this declaration'))
    system.dispatch('pick','job-one',decision='approved')
    with pytest.raises(Refused):system.dispatch('fill','job-one')
    assert not any(c[6:8]==['fill','#signature'] for c in fake.calls)


def test_h6_apply_destination_cannot_change_job_on_same_host(system):
    system.browser=Steps();original=system.browser.read
    def read():
        page=original()
        if 'apply_control' in page:page['apply_control']['href']='https://careers.example.org/job/other/apply'
        return page
    system.browser.read=read
    system.dispatch('pick','job-one',decision='approved')
    with pytest.raises(Refused):system.dispatch('fill','job-one')
    assert not system.browser.moves and not system.browser.writes


def test_h7_proof_delay_cannot_change_approved_values(system,tmp_path,monkeypatch):
    fake=browser(system,tmp_path,monkeypatch)
    from test_workflow import ready
    ready(system)
    original=fake.__call__
    proofs=0
    def run(argv):
        nonlocal proofs
        if argv[0]=='node' and not argv[1].endswith('browser-runtime.mjs'):
            proofs+=1
            if proofs==2:fake.fields[0]['value']='changed@example.net'
        return original(argv)
    system.browser.runner=run
    with pytest.raises(Refused):system.dispatch('submit','job-one')
    assert not fake.clicks


def test_h8_changed_short_box_stays_pending_on_second_refill(system):
    picked(system);approve(system)
    system.browser.pages.insert(2,dict(id='extra',label='Extra',fields=[dict(name='Start date',type='text',required=True,value='')]))
    with pytest.raises(Refused):system.dispatch('submit','job-one')
    assert next(b for b in system.review('job-one')['boxes'] if b['name']=='Extra / Start date')['state']=='pending'
    system.dispatch('fill','job-one')
    assert next(b for b in system.review('job-one')['boxes'] if b['name']=='Extra / Start date')['state']=='pending'


def test_h9_individual_fact_questions_cover_every_reachable_step_before_person(system):
    import questions
    system.browser=Steps()
    for at,label in [(1,'Fact one'),(2,'Fact two')]:
        system.browser.pages[at]['fields'].append(dict(name=label,type='select',required=True,value='',options=['A','B']))
    old=system.agents.task
    system.agents.task=lambda role,context:dict(value=None,evidence=[]) if role=='drafter' and context['field']['name'].startswith('Fact') else old(role,context)
    system.dispatch('pick','job-one',decision='approved')
    with pytest.raises(Refused):system.dispatch('fill','job-one')
    reviews=[c for role,c in system.agents.tasks if role=='reviewer']
    assert len(reviews)==1
    assert {'Contact / Fact one','Replies / Fact two'} <= {r['name'] for r in reviews[0]['filled_form']['rows']}
    qs=[read_json(p) for p in (system.root/'data/questions').glob('*.json')]
    facts=[q for q in qs if (q.get('applies_to') or {}).get('form_question')]
    assert len(facts)==2 and {q['applies_to']['form_question'] for q in facts}=={'Contact / Fact one','Replies / Fact two'}


def test_h10_approval_must_include_the_digest_the_person_saw(system):
    picked(system);approve(system)
    stale=system.review('job-one')['fill_digest']
    system.dispatch('fill','job-one')
    # Every fill has its own identity, even when values happen to be unchanged.
    with pytest.raises(Refused):system.dispatch('approve-submit','job-one',fill_digest=stale)
    with pytest.raises(Refused):system.dispatch('approve-submit','job-one')

@pytest.mark.parametrize('label',['Do you have authorization to work in the US?','Are you legally able to work?','Work eligibility','Employment authorization','Immigration status','Green card holder?','Do you hold a security clearance?','Are you 18 years of age or older?','Are you over 18?','Do you have a criminal record?','Sexual orientation','Date of birth','Race *','Do you own a vehicle?'])
def test_h11_unclassified_personal_facts_never_use_drafter(system,label):
    system.browser.fields.append(dict(name=label,type='text',required=True,value=''))
    system.dispatch('pick','job-one',decision='approved')
    with pytest.raises(Refused):system.dispatch('fill','job-one')
    assert not any(role=='drafter' and c['field']['name']==label for role,c in system.agents.tasks)


def test_h11_reference_contact_is_not_the_person(system):
    system.browser=Steps();system.browser.pages.insert(3,dict(id='reference',label='Reference',fields=[dict(name='Email',type='email',required=True,value='')]))
    system.dispatch('pick','job-one',decision='approved')
    with pytest.raises(Refused):system.dispatch('fill','job-one')
    row=next(b for b in system.review('job-one')['boxes'] if b['name']=='Reference / Email')
    assert row['missing'] and not row['value']


def test_h11_employer_sponsorship_scope_wins_over_country(system):
    label='Are you currently sponsored by this company?'
    a=dict(id='one',url='https://careers.example.org/one',country='US')
    b=dict(id='two',url='https://careers.example.org/two',country='US')
    assert system.cache_key({'name':label},a,personal=True)!=system.cache_key({'name':label},b,personal=True)


def test_h12_browser_uses_dependency_pinned_binary_and_checks_every_command(system,tmp_path,monkeypatch):
    fake=browser(system,tmp_path,monkeypatch)
    system.dispatch('pick','job-one',decision='approved');system.dispatch('fill','job-one')
    commands=[c for c in fake.calls if len(c)>6 and c[3]=='browser']
    assert commands and all(Path(c[0]).is_absolute() and c[0]!='opencli' for c in commands)
    assert len([c for c in fake.calls if c[0]=='node' and c[1].endswith('browser-runtime.mjs')])>=len(commands)


def test_h12_incompatible_daemon_refuses_before_browser_command(system,tmp_path,monkeypatch):
    fake=browser(system,tmp_path,monkeypatch)
    original=fake.__call__
    def runner(argv):
        if argv[0]=='node' and argv[1].endswith('browser-runtime.mjs'):raise Refused('DAEMON_CONFLICT','Fake mismatched shared daemon; never replace it')
        return original(argv)
    system.browser.runner=runner
    system.dispatch('pick','job-one',decision='approved')
    with pytest.raises(Refused):system.dispatch('fill','job-one')
    assert not any(len(c)>6 and c[3]=='browser' for c in fake.calls)


def test_h13_person_can_confirm_a_manually_ticked_consent_without_agent_write(system,tmp_path,monkeypatch):
    import questions
    fake=browser(system,tmp_path,monkeypatch)
    fake.fields.append(dict(name='I agree to the privacy notice',type='checkbox',required=True,value='No',selector='#consent',human_only=True))
    system.dispatch('pick','job-one',decision='approved')
    with pytest.raises(Refused):system.dispatch('fill','job-one')
    fake.fields[-1]['value']='Yes'
    with pytest.raises(Refused):system.dispatch('fill','job-one')
    q=next(read_json(p) for p in (system.root/'data/questions').glob('*.json'))
    paths=questions.Paths(questions=system.root/'data/questions',postings=system.root/'data/postings',profile=system.root/'profile.yml',applications=system.root/'data/applications')
    questions.answer(paths,q['id'],'Confirmed',by='phone')
    system.dispatch('fill','job-one')
    assert not any(c[6] in ('check','select','fill') and c[7]=='#consent' for c in fake.calls if len(c)>7)
    assert next(b for b in system.review('job-one')['boxes'] if b['name']=='I agree to the privacy notice')['value']=='Yes'


@pytest.mark.parametrize('kind',['search','time','datetime-local','week','color','range'])
def test_h13_ordinary_input_types_are_readable(system,kind):
    system.browser.fields[-1]['type']=kind
    system.dispatch('pick','job-one',decision='approved')
    system.dispatch('fill','job-one')
    assert system.review('job-one')['boxes'][-1]['verified']


def test_h13_optional_placeholder_retains_what_person_will_see(system):
    system.browser.fields.append(dict(name='How did you hear',type='select',required=False,value='Select one',options=['Select one','Web']))
    system.dispatch('pick','job-one',decision='approved');system.dispatch('fill','job-one')
    row=next(b for b in system.review('job-one')['boxes'] if b['name']=='How did you hear')
    assert row['value']=='Select one' and row['verified']


def test_m14_question_record_writer_serializes_with_attempt_writer(system):
    import threading
    import questions
    paths=questions.Paths(questions=system.root/'data/questions',postings=system.root/'data/postings',profile=system.root/'profile.yml',applications=system.root/'data/applications')
    loaded=threading.Event();release=threading.Event();saved=threading.Event();errors=[]
    def edit(rec):
        loaded.set();assert release.wait(5);rec['country']='US'
    def answer():
        try:questions._update_record(paths,'job-one',edit)
        except BaseException as e:errors.append(e)
    stale=system.record('job-one')
    stale.update(status='submission_unconfirmed',submission={'attempted_at':'synthetic-attempt'})
    a=threading.Thread(target=answer);a.start();assert loaded.wait(5)
    b=threading.Thread(target=lambda:(system.save(stale),saved.set()));b.start()
    try:assert not saved.wait(.1), 'Submit record writer must wait for the same record lock'
    finally:release.set();a.join(5);b.join(5)
    assert not errors and not a.is_alive() and not b.is_alive()
    assert system.record('job-one')['submission']['attempted_at']=='synthetic-attempt'


def test_m14_stale_record_cannot_erase_a_durable_attempt(system):
    from test_workflow import ready
    stale=system.record('job-one');ready(system);system.dispatch('submit','job-one')
    write_json(system.root/'data/postings/job-one.json',stale)
    assert system.record('job-one')['submission']['attempted_at']
    with pytest.raises(Refused):system.dispatch('fill','job-one')
    assert len(system.browser.clicks)==1


def test_m15_chosen_pdf_path_and_bytes_are_material(system,tmp_path):
    from test_workflow import ready
    ready(system)
    before=system.fill('job-one')['material_digest']
    replacement=tmp_path/'new.pdf';replacement.write_bytes(b'%PDF-1.4\nSynthetic replacement\n%%EOF\n')
    settings=read_json(system.root/'settings.json');settings['resume_pdf']=str(replacement);write_json(system.root/'settings.json',settings)
    assert system.gate('job-one') and before!=system.material_digest(system.record('job-one'))
    with pytest.raises(Refused):system.dispatch('submit','job-one')
    assert not system.browser.clicks

@pytest.mark.parametrize('code',['UNTRUSTED_POSTING','FORM_CHANGED','READBACK_MISMATCH','AMBIGUOUS_FIELDS','WRONG_APPLICATION','STEP_CONTRACT','STEP_CONTROL','SUBMIT_CONTROL','SUBMISSION_UNCONFIRMED'])
def test_m16_each_browser_stop_reaches_questions_and_can_recur(system,code):
    import questions
    paths=questions.Paths(questions=system.root/'data/questions',postings=system.root/'data/postings',profile=system.root/'profile.yml',applications=system.root/'data/applications')
    def refuse():raise Refused(code,'Synthetic stop needing personal attention')
    system.browser.read=refuse
    system.dispatch('pick','job-one',decision='approved')
    with pytest.raises(Refused):system.dispatch('fill','job-one')
    qs=questions.list_q(paths);assert len(qs)==1
    questions.answer(paths,qs[0]['id'],'Handled',by='phone')
    with pytest.raises(Refused):system.dispatch('fill','job-one')
    assert len([q for q in questions.list_q(paths) if q['status']=='open'])==1
    assert len(questions.list_q(paths))==2


def test_m17_comment_cli_cannot_spoof_person_without_terminal(system,monkeypatch):
    import questions
    from types import SimpleNamespace
    paths=questions.Paths(questions=system.root/'data/questions',postings=system.root/'data/postings',profile=system.root/'profile.yml',applications=system.root/'data/applications')
    question=questions.add(paths,topic='application',title='Synthetic fact',question='What is the answer?',applies_to={'posting_id':'job-one','form_question':'Synthetic fact'})
    args=SimpleNamespace(by='phone',id=question['id'],text=['Agent value'],questions=paths.questions,postings=paths.postings,profile=paths.profile,applications=paths.applications)
    monkeypatch.setattr(questions.sys.stdin,'isatty',lambda:False)
    with pytest.raises(questions.AskError,match='person'):questions.cmd_comment(args)


def test_m17_agent_cannot_resolve_uncertain_submit(system):
    from test_workflow import ready
    system.browser.show_confirmation=False;ready(system)
    with pytest.raises(Refused):system.dispatch('submit','job-one')
    with pytest.raises(Refused):system.dispatch('resolve-uncertain','job-one',by='agent',reason='Agent wants release')
    assert not system.record('job-one')['submission'].get('resolved_by')


def test_m18_common_sponsorship_aliases_share_meaning(system):
    rec=dict(id='one',url='https://careers.example.org/jobs/one',country='US')
    assert system.cache_key({'name':'Do you require sponsorship?'},rec,personal=True)==system.cache_key({'name':'Will you now or in the future require sponsorship for employment visa status?'},rec,personal=True)


def test_m18_stored_answers_are_named_changeable_and_keep_ask_policy(system):
    label='Do you require sponsorship?'
    rec=system.record('job-one');rec['country']='US';system.save(rec)
    system.remember_answer('job-one',label,'No','phone')
    answer=system.answers()[0]
    assert answer['name']==label and answer['scope']=='country: US'
    system.change_answer(answer['id'],'Ask me every time',by='phone')
    system.remember_answer('job-one',label,'Yes','phone')
    assert system.answers()[0]['value']=='Ask me every time'
    system.change_answer(answer['id'],'No',by='chat')
    assert system.answers()[0]['value']=='No'


def test_m19_resume_change_invalidates_saved_draft_plan(system):
    system.dispatch('pick','job-one',decision='approved');system.dispatch('fill','job-one')
    count=sum(role=='drafter' for role,_ in system.agents.tasks)
    replacement='I built a synthetic marine sensor and documented the synthetic experiment. '*3
    (system.root/'resume.txt').write_text(replacement)
    old=system.agents.task
    system.agents.task=lambda role,c:dict(value=replacement,evidence=[replacement]) if role=='drafter' else old(role,c)
    system.dispatch('release','job-one');system.dispatch('fill','job-one')
    row=next(b for b in system.review('job-one')['boxes'] if b['name']=='Why this role?')
    assert row['value']==replacement and row['state']=='pending'
    assert sum(role=='reviewer' for role,_ in system.agents.tasks)==1


def test_m20_expired_tab_restores_exact_approval_without_new_review(system):
    from test_workflow import ready
    system.browser=Steps();picked(system);approve(system)
    seen=system.record('job-one')['ruling']['submit_digest']
    system.browser.alive=False
    result=system.dispatch('submit','job-one')
    assert result['status']=='applied' and result['ruling']['submit_digest']==seen
    assert len(system.browser.clicks)==1
    assert sum(role=='reviewer' for role,_ in system.agents.tasks)==1


def test_m21_public_form_proves_general_bridge_account(system,tmp_path,monkeypatch):
    fake=browser(system,tmp_path,monkeypatch)
    settings=read_json(system.root/'settings.json');settings['bridge_site']='general';write_json(system.root/'settings.json',settings)
    rec=system.record('job-one');rec.update(ats='greenhouse',site='careers.example.org');system.save(rec)
    fake.site='general'
    system.dispatch('pick','job-one',decision='approved');system.dispatch('fill','job-one')
    assert any(c[2:]==['check','demo','--site','general'] for c in fake.calls)
    assert system.browser.state['origin']==rec['url']


def test_m21_missing_employer_identity_is_a_changeable_question(system):
    import questions
    rec=system.record('job-one');rec.pop('site');system.save(rec)
    system.dispatch('pick','job-one',decision='approved')
    with pytest.raises(Refused):system.dispatch('fill','job-one')
    q=next(read_json(p) for p in (system.root/'data/questions').glob('*.json') if (read_json(p).get('applies_to') or {}).get('job_site'))
    paths=questions.Paths(questions=system.root/'data/questions',postings=system.root/'data/postings',profile=system.root/'profile.yml',applications=system.root/'data/applications')
    questions.answer(paths,q['id'],'example-company',by='phone')
    system.dispatch('fill','job-one')
    assert system.record('job-one')['site']=='example-company'

@pytest.mark.parametrize('variant',['https://careers.example.org/jobs/123/','https://careers.example.org/jobs/123/apply','https://careers.example.org/jobs/123?gh_jid=123'])
def test_m22_same_application_url_variants_have_one_key(system,variant):
    assert system.application_keys({'url':'https://careers.example.org/jobs/123'})==system.application_keys({'url':variant})


def test_m22_different_greenhouse_job_is_not_a_duplicate(system):
    assert system.application_keys({'url':'https://careers.example.org/jobs/123'})!=system.application_keys({'url':'https://careers.example.org/jobs/123?gh_jid=456'})


def test_m23_yaml_numbers_and_dates_become_readable_field_text(system):
    import yaml
    profile=yaml.safe_load((system.root/'profile.yml').read_text())
    profile['contact']['phone']=5550100123
    import datetime
    profile['standard_answers']['Start date']=datetime.date(2027,1,1)
    (system.root/'profile.yml').write_text(yaml.safe_dump(profile))
    system.browser.fields.append(dict(name='Phone',type='tel',required=True,value=''))
    system.dispatch('pick','job-one',decision='approved');system.dispatch('fill','job-one')
    rows={b['name']:b['value'] for b in system.review('job-one')['boxes']}
    assert rows['Phone']=='5550100123' and rows['Start date']=='2027-01-01'


def test_h9_later_blocked_steps_get_their_one_review_before_person(system):
    import questions
    system.browser=Steps();b=system.browser
    for index,label in [(1,'Fact one'),(2,'Fact two')]:b.pages[index]['fields'].append(dict(name=label,type='select',required=True,value='',options=['A','B']))
    old=b.advance
    def advance(key,name):
        if key=='next_control' and any(f.get('required') and not f.get('value') for f in b.pages[b.at]['fields']):raise Refused('STEP_CONTRACT','Required fields block Next')
        return old(key,name)
    b.advance=advance
    paths=questions.Paths(questions=system.root/'data/questions',postings=system.root/'data/postings',profile=system.root/'profile.yml',applications=system.root/'data/applications')
    system.dispatch('pick','job-one',decision='approved')
    with pytest.raises(Refused):system.dispatch('fill','job-one')
    first=[c for role,c in system.agents.tasks if role=='reviewer'];assert len(first)==1
    q=next(q for q in questions.list_q(paths) if (q.get('applies_to') or {}).get('form_question'))
    questions.answer(paths,q['id'],'A',by='phone')
    with pytest.raises(Refused):system.dispatch('fill','job-one')
    reviews=[c for role,c in system.agents.tasks if role=='reviewer']
    assert len(reviews)==2
    assert all(row['name'].startswith('Replies / ') for row in reviews[-1]['filled_form']['rows'])
    facts=[q for q in questions.list_q(paths,'open') if (q.get('applies_to') or {}).get('form_question')]
    assert len(facts)==1 and facts[0]['applies_to']['form_question']=='Replies / Fact two'


from integrations import run as production_run

@pytest.mark.parametrize('payload',[{'ok':False,'error':{'code':'TAB_NOT_FOUND','message':'Tab could not be validated in this session'}}, {'ok':False,'error':'private diagnostic'}, None])
def test_low_production_subprocess_error_parser_with_fake_process(monkeypatch,payload):
    from subprocess import CompletedProcess
    monkeypatch.setattr(integrations.subprocess,'run',lambda *a,**k:CompletedProcess(a[0],1,stdout=json.dumps(payload),stderr='synthetic private diagnostic'))
    with pytest.raises(Refused) as caught:production_run(['/fixture/opencli','browser'],structured_errors=True)
    assert 'private diagnostic' not in str(caught.value)
    if isinstance(payload,dict) and isinstance(payload.get('error'),dict):assert caught.value.code=='TAB_MISSING'

@pytest.mark.parametrize('conflict',[False,True])
def test_h12_real_dependency_compatibility_guard_with_fake_daemon(tmp_path,conflict):
    dependency=Path(os.environ.get('SIGN_IN_PLUGIN') or Path(__file__).resolve().parents[2]/'sign-in')
    if not dependency.is_dir():pytest.skip('Dependency checkout required')
    helper=Path(__file__).resolve().parents[1]/'scripts/browser-runtime.mjs'
    result=subprocess.run(['node',str(Path(__file__).resolve().parent/'testing/sign-in-output.mjs'),str(dependency),str(helper),*(['conflict'] if conflict else [])],env=dict(os.environ,HOME=str(tmp_path),TMPDIR=str(tmp_path),SIGN_IN_HOME=str(tmp_path/'sign-in-home')),text=True,capture_output=True,check=True)
    receipt=json.loads(result.stdout)
    assert all(c['args']==['--version'] for c in receipt['calls'])
    if conflict:
        assert receipt['status']==1 and json.loads(receipt['stderr'])['code']=='DAEMON_CONFLICT'
    else:
        proof=json.loads(receipt['stdout']);assert receipt['status']==0 and proof['ok'] is True
        assert Path(proof['data']['binary']).is_absolute() and proof['data']['version']=='2.0.2'


def test_m20_production_runner_sets_owned_session_idle_timeout(system,tmp_path,monkeypatch):
    fake=browser(system,tmp_path,monkeypatch);seen=[]
    def process(argv,**kwargs):
        seen.append(kwargs)
        return fake(argv)
    monkeypatch.setattr(integrations,'run',process)
    system.browser=integrations.OpenCLIBrowser(system.settings,root=system.root)
    system.dispatch('pick','job-one',decision='approved');system.dispatch('fill','job-one')
    assert seen and all(k['env']['OPENCLI_BROWSER_IDLE_TIMEOUT']=='86400' for k in seen)
    commands=[c for c in fake.calls if len(c)>6 and c[3]=='browser']
    assert all(c[-2:]==['--window','background'] for c in commands)


def test_h13_back_cleared_upload_is_reattached_with_only_approved_bytes(system):
    picked(system);approve(system)
    first=system.browser.first
    def reset():
        page=first()
        field=system.browser.pages[1]['fields'][1];field.update(value='',sha256=None)
        return system.browser.read()
    system.browser.first=reset
    assert system.dispatch('submit','job-one')['status']=='applied'
    assert len(system.browser.clicks)==1
    assert len([w for w in system.browser.writes if w[2]=='file'])==2
    assert sum(role=='reviewer' for role,_ in system.agents.tasks)==1


def test_low_removed_box_stays_visible_pending_until_person_acknowledges(system):
    picked(system);approve(system)
    system.browser.pages[2]['fields']=[f for f in system.browser.pages[2]['fields'] if f['name']!='Start date']
    with pytest.raises(Refused):system.dispatch('submit','job-one')
    removed=next(b for b in system.review('job-one')['boxes'] if b['name']=='Replies / Start date')
    assert removed['removed'] and removed['state']=='pending'
    system.dispatch('fill','job-one')
    assert next(b for b in system.review('job-one')['boxes'] if b.get('removed'))['state']=='pending'
    system.dispatch('box','job-one',name=removed['name'],decision='approve')
    assert next(b for b in system.review('job-one')['boxes'] if b.get('removed'))['state']=='approved'


def test_h13_phone_formatter_refusal_is_observed_without_retyping(system,tmp_path,monkeypatch):
    import yaml
    fake=browser(system,tmp_path,monkeypatch)
    fake.fields.append(dict(name='Phone',type='tel',required=True,value='',selector='#phone'))
    profile=yaml.safe_load((system.root/'profile.yml').read_text());profile['contact']['phone']='5550100123';(system.root/'profile.yml').write_text(yaml.safe_dump(profile))
    original=fake.__call__
    def runner(argv):
        if len(argv)>8 and argv[6:8]==['fill','#phone']:
            fake.calls.append(argv);fake.fields[-1]['value']='(555) 010-0123'
            raise Refused('value_mismatch','Stock rejects a phone formatter')
        return original(argv)
    system.browser.runner=runner
    system.dispatch('pick','job-one',decision='approved');system.dispatch('fill','job-one')
    row=next(b for b in system.review('job-one')['boxes'] if b['name']=='Phone')
    assert row['value']=='(555) 010-0123' and row['verified']
    assert len([c for c in fake.calls if len(c)>8 and c[6:8]==['fill','#phone']])==1


def test_m19_a_reused_draft_also_expires_with_its_resume(system):
    from test_workflow import ready
    ready(system);system.dispatch('release','job-one');system.dispatch('fill','job-one')
    assert next(b for b in system.review('job-one')['boxes'] if b['name']=='Why this role?')['source']=='reused'
    replacement='I documented a synthetic thermal experiment and validated a synthetic sensor. '*3
    (system.root/'resume.txt').write_text(replacement)
    old=system.agents.task
    system.agents.task=lambda role,c:dict(value=replacement,evidence=[replacement]) if role=='drafter' else old(role,c)
    system.dispatch('release','job-one');system.dispatch('fill','job-one')
    assert next(b for b in system.review('job-one')['boxes'] if b['name']=='Why this role?')['value']==replacement


def test_m21_general_bridge_identity_change_invalidates_old_form(system,tmp_path,monkeypatch):
    fake=browser(system,tmp_path,monkeypatch)
    settings=read_json(system.root/'settings.json');settings['bridge_site']='example-company';write_json(system.root/'settings.json',settings)
    rec=system.record('job-one');rec['ats']='greenhouse';system.save(rec)
    system.dispatch('pick','job-one',decision='approved');system.dispatch('fill','job-one')
    settings['bridge_site']='different-general-site';write_json(system.root/'settings.json',settings)
    with pytest.raises(Refused):system.dispatch('apply-edits','job-one')
    assert not fake.clicks


def test_h13_own_back_navigation_can_restore_exact_approved_text(system):
    picked(system);approve(system)
    first=system.browser.first
    def reset():
        first()
        system.browser.pages[1]['fields'][0]['value']=''
        system.browser.pages[2]['fields'][0]['value']=''
        return system.browser.read()
    system.browser.first=reset
    assert system.dispatch('submit','job-one')['status']=='applied'
    assert len(system.browser.clicks)==1 and sum(role=='reviewer' for role,_ in system.agents.tasks)==1


def test_h9_a_new_step_gets_one_review_without_re_reviewing_existing_steps(system):
    picked(system);approve(system)
    system.browser.pages.insert(2,dict(id='extra',label='Extra',fields=[dict(name='Start date',type='text',required=True,value='')]))
    with pytest.raises(Refused):system.dispatch('submit','job-one')
    reviews=[c['filled_form'] for role,c in system.agents.tasks if role=='reviewer']
    assert len(reviews)==2
    assert [step['id'] for step in reviews[-1]['steps']]==['extra']
    assert [row['name'] for row in reviews[-1]['rows']]==['Extra / Start date']
    system.dispatch('fill','job-one')
    assert sum(role=='reviewer' for role,_ in system.agents.tasks)==2


def test_h2_a_lost_tab_never_replays_an_uncertain_next(system):
    system.browser=Steps();system.browser.drop_after=3
    system.dispatch('pick','job-one',decision='approved')
    with pytest.raises(Refused):system.dispatch('fill','job-one')
    before=list(system.browser.moves)
    with pytest.raises(Refused) as caught:system.dispatch('fill','job-one')
    assert caught.value.code=='NAVIGATION_UNCERTAIN' and system.browser.moves==before

from test_workflow import api


def test_m18_saved_answers_api_requires_auth_and_accepts_chat_relay(system,api):
    system.remember_answer('job-one','Email','person@example.org','phone',{'name':'Email'})
    assert api('GET','/answers',token='wrong')[0]==401
    status,answers=api('GET','/answers');assert status==200 and answers[0]['name']=='Email'
    key=answers[0]['id']
    assert api('POST','/answers/'+key,{'value':'new@example.org','by':'chat'})[0]==200
    assert api('POST','/answers/'+key,{'value':'new@example.org','by':'phone'})[0]==200
    assert api('GET','/answers')[1][0]['value']=='new@example.org'


def test_h10_stale_api_client_cannot_approve_a_replaced_fill(system,api):
    picked(system);approve(system)
    status,displayed=api('GET','/postings/job-one/review');assert status==200
    system.dispatch('fill','job-one')
    status,error=api('POST','/postings/job-one/approve-submit',{'by':'phone','fill_digest':displayed['fill_digest']})
    assert status==409 and error['code']=='STALE_APPROVAL'
    assert api('POST','/postings/job-one/approve-submit',{'by':'phone'})[0]==409
    assert not system.browser.clicks


def test_h13_optional_native_defaults_are_observed_without_rewriting(system):
    for name,kind,value in [('Highlight','color','#000000'),('Level','range','50')]:system.browser.fields.append(dict(name=name,type=kind,required=False,value=value))
    old=system.browser.fill
    def fill(name,value,kind):
        if name in ('Highlight','Level'):raise Refused('READBACK_MISMATCH','Native defaults must be retained when no answer was chosen')
        return old(name,value,kind)
    system.browser.fill=fill
    system.dispatch('pick','job-one',decision='approved');system.dispatch('fill','job-one')
    values={b['name']:b['value'] for b in system.review('job-one')['boxes']}
    assert values['Highlight']=='#000000' and values['Level']=='50'


def test_h7_change_after_runtime_guard_never_reaches_submit(system,tmp_path,monkeypatch):
    from test_workflow import ready
    fake=browser(system,tmp_path,monkeypatch);ready(system)
    original=fake.__call__
    def runner(argv):
        if len(argv)>7 and (argv[6]=='click' or (argv[6]=='eval' and 'const careerApproved =' in argv[7])):
            if argv[6]=='click' or not json.JSONDecoder().raw_decode(argv[7].split('const careerApproved =',1)[1].lstrip())[0].get('dry_run'):
                fake.fields[0]['value']='changed-after-guard@example.net'
        return original(argv)
    system.browser.runner=runner
    with pytest.raises(Refused):system.dispatch('submit','job-one')
    assert not fake.clicks
    assert system.record('job-one')['submission']['attempted_at']


def test_runtime_stderr_refusal_retains_code_without_private_diagnostics(monkeypatch):
    from subprocess import CompletedProcess
    monkeypatch.setattr(integrations.subprocess,'run',lambda *a,**k:CompletedProcess(a[0],1,stdout='',stderr=json.dumps({'ok':False,'code':'DAEMON_CONFLICT','error':'Synthetic private diagnostic'})))
    with pytest.raises(Refused) as caught:production_run(['node','/fixture/browser-runtime.mjs'],structured_errors=True)
    assert caught.value.code=='DAEMON_CONFLICT' and 'private diagnostic' not in str(caught.value)


def test_m18_saved_contact_edit_takes_precedence_over_older_profile(system):
    from test_workflow import ready
    ready(system)
    answer=next(a for a in system.answers() if a['name']=='Email')
    system.change_answer(answer['id'],'updated@example.org',by='phone')
    assert system.gate('job-one')
    system.dispatch('fill','job-one')
    box=next(b for b in system.review('job-one')['boxes'] if b['name']=='Email')
    assert box['value']=='updated@example.org' and box['state']=='pending'


def test_m18_saved_fact_edit_supersedes_its_older_question_answer(system):
    import questions
    label='Do you require sponsorship?'
    rec=system.record('job-one');rec['country']='US';system.save(rec)
    system.browser.fields.append(dict(name=label,type='select',options=['Yes','No'],required=True,value=''))
    system.dispatch('pick','job-one',decision='approved')
    with pytest.raises(Refused):system.dispatch('fill','job-one')
    paths=questions.Paths(questions=system.root/'data/questions',postings=system.root/'data/postings',profile=system.root/'profile.yml',applications=system.root/'data/applications')
    q=next(q for q in questions.list_q(paths) if (q.get('applies_to') or {}).get('form_question'))
    questions.answer(paths,q['id'],'No',by='phone');system.dispatch('fill','job-one')
    answer=next(a for a in system.answers() if a['name']==label)
    system.change_answer(answer['id'],'Yes',by='phone');system.dispatch('fill','job-one')
    row=next(b for b in system.review('job-one')['boxes'] if b['name']==label)
    assert row['value']=='Yes' and row['state']=='pending'
    for box in system.review('job-one')['boxes']:
        if box['state']!='approved':system.dispatch('box','job-one',name=box['name'],decision='approve')
    assert not system.gate('job-one')
