"""Review 3 workflow and boundary regressions with refusing dependency fakes."""
import copy
import json
from pathlib import Path
import pytest
from runtime import Refused, read_json, write_json
from test_workflow import system, ready
from test_steps import Steps, picked, approve
from test_browser import browser


def test_r5_h1_shrinking_unreviewed_plan_never_silently_drops_a_box(system):
    original=system.browser.fill
    def fail(name,value,kind):raise Refused('READBACK_MISMATCH','Synthetic failed fill')
    system.browser.fill=fail
    system.dispatch('pick','job-one',decision='approved')
    with pytest.raises(Refused):system.dispatch('fill','job-one')
    assert not system.review('job-one').get('agent_reviewed_at')
    system.browser.fields=system.browser.fields[1:]
    system.browser.fill=original
    with pytest.raises(Refused,match='observed'):system.dispatch('fill','job-one')
    assert not system.browser.clicks


def test_r5_h2_confirmation_outside_posting_is_recorded_before_url_refusal(system):
    system.browser=Steps();old=system.browser.advance
    def advance(key,name):
        if system.browser.at==2:
            system.browser.clicks.append('Unexpected Next');system.browser.confirmation=True
            return dict(url='https://careers.example.org/done',identity_verified=True,confirmation='Application submitted.')
        return old(key,name)
    system.browser.advance=advance
    system.dispatch('pick','job-one',decision='approved')
    with pytest.raises(Refused):system.dispatch('fill','job-one')
    assert system.record('job-one')['submission']['attempted_at']
    with pytest.raises(Refused):system.dispatch('release','job-one')
    assert len(system.browser.clicks)==1


def test_r5_h2_release_and_fresh_tab_do_not_clear_an_uncertain_next(system):
    system.browser=Steps();system.dispatch('pick','job-one',decision='approved')
    original=system.browser.advance
    def fail(key,name):
        if system.browser.at==2:raise Refused('TAB_MISSING','Lost outcome of Next')
        return original(key,name)
    system.browser.advance=fail
    with pytest.raises(Refused):system.dispatch('fill','job-one')
    system.dispatch('release','job-one')
    before=list(system.browser.moves)
    with pytest.raises(Refused) as e:system.dispatch('fill','job-one')
    assert e.value.code=='NAVIGATION_UNCERTAIN' and system.browser.moves==before

LEGAL_CODE_LABELS=[
 'Confirmation code','Code from your email','Verification','Do you accept the terms?','Please acknowledge',
 'I hereby certify','I authorize a background check','Initials','Sign here','Data processing','Terms & Conditions','Username',
 'Enter the code sent to your phone','Enter verification token','Authentication code','Security code','Email code','SMS code',
 'One-time password','One time code','MFA code','2FA code','Two-factor code','Login code','Access code','Verification token',
 'I certify that the information above is true','By typing your name below you certify','Electronic signature','Digital signature',
 'Candidate declaration','Certification','Consent','Data privacy','Privacy notice','GDPR','I agree','I understand',
 'I consent to data processing','I accept these terms','Acknowledge this disclosure','Please sign below','Type your name to sign',
 'Agree to the terms','Accept terms','Confirm your acceptance','Password','Choose a username','Captcha','Authenticator code']

@pytest.mark.parametrize('label',LEGAL_CODE_LABELS)
def test_r5_h5_legal_and_authentication_actions_stop_as_actions(label):
    from workflow import personal_control
    assert personal_control(dict(name=label,type='text',required=True))

@pytest.mark.parametrize('label',['Enter your postal code','In terms of salary','Terms of employment','Consent management experience','Signature project','Describe GDPR experience','Describe OTP experience'])
def test_r5_h5_ordinary_facts_are_not_legal_actions(label):
    from workflow import personal_control
    assert not personal_control(dict(name=label,type='text',required=True))

@pytest.mark.parametrize('options',[['Yes, I agree','No'],['Agree','Disagree'],['Accept','Decline']])
def test_r5_h5_choice_shapes_are_personal_even_without_a_legal_field_name(options):
    from workflow import personal_control
    assert personal_control(dict(name='Continue with data',type='select',required=True,options=options))


def test_r5_h5_questions_never_record_a_supplied_code_as_a_fact(system):
    import questions
    paths=questions.Paths(questions=system.root/'data/questions',postings=system.root/'data/postings',profile=system.root/'profile.yml',applications=system.root/'data/applications')
    q=questions.add(paths,topic='application',title='Legacy prompt',question='Supply facts',posting_id='job-one',applies_to={'posting_id':'job-one','form_facts':True})
    with pytest.raises(questions.AskError):questions.answer(paths,q['id'],json.dumps({'Confirmation code':'482913'}),by='phone')
    assert questions.read_q(paths,q['id'])['answer'] is None


def test_r5_web_dismiss_has_no_unrelated_posting_reference():
    import subprocess
    source=(Path(__file__).parents[1]/'skills/jobs/examples/web/app.js').read_text()
    start=source.index('  async function dismissQuestion(q) {');end=source.index('\n  // An answer can approve',start)
    program=source[start:end]+'''
let calls=[];
const ask=async()=>({ok:true});
const api=async(...args)=>{calls.push(args);return {id:'fixture'};};
const upsertQuestion=()=>{};const toast=()=>{};const render=()=>{};const BY='web';
const S={get reviews(){throw Error('Dismiss must not read posting reviews')}};
await dismissQuestion({id:'fixture'});
if(calls.length!==1 || calls[0][0]!=='/questions/fixture/dismiss')throw Error('Dismiss did not reach its API');
'''
    subprocess.run(['node','--input-type=module','-e',program],check=True,capture_output=True,text=True)


def test_r5_unreadable_page_refuses_at_workflow_boundary(system):
    page=system.browser.read();page['unreadable']=['Unknown custom question']
    with pytest.raises(Refused) as error:system.page(page,page['url'])
    assert error.value.code=='FORM_UNREADABLE'

@pytest.mark.parametrize('target',['http://outside.invalid/done','https://www.linkedin.com/jobs/123','about:blank'])
def test_r5_outside_transition_is_recorded_even_when_destination_itself_refuses(system,target):
    ready(system)
    rec=system.record('job-one');page=system.browser.read();page.update(url=target,confirmation=None)
    system.navigation_outcome(rec,page)
    journal=read_json(system.adir('job-one')/'steps.json')
    assert journal['moving'] and journal['outcomes'][-1]['url']==target
