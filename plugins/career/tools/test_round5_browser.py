"""Review 3 regressions on owned loopback pages, using the installed stock parser."""
import copy
import hashlib
import json
from pathlib import Path
import pytest
from test_form_reader import reader
from test_workflow import system
from runtime import Refused, read_json, write_json
import integrations

EMAIL='<label for="email">Email</label><input id="email" name="email" type="email" required>'

def form(body, extra=''):
    return '<h1>Fixture role</h1><form id="application">'+body+'<button id="submit" type="submit">Submit application</button></form>'+extra


def test_r5_h1_tall_document_keeps_fields_above_and_below_viewport(reader):
    html=form('<style>.field{height:180px}</style>'+''.join(f'<div class="field"><label for="f{i}">Field {i}</label><input id="f{i}" name="f{i}"></div>' for i in range(20)))
    page=reader(html)
    assert reader.command('eval','document.documentElement.scrollHeight > innerHeight*3')
    assert len(page['fields'])==20
    reader.command('eval','scrollTo(0,document.documentElement.scrollHeight); true')
    after=reader.command('eval',Path(__file__).with_name('form-page.js').read_text())
    assert [f['name'] for f in after['fields']]==[f['name'] for f in page['fields']]

@pytest.mark.parametrize('button',['<button aria-label="Next">Submit application</button>','<button name="Next">Send it</button>'])
def test_r5_h2_hidden_names_never_make_submit_safe_navigation(reader,button):
    page=reader('<h1>Fixture role</h1><form>'+EMAIL+button+'</form>')
    assert page['next_control'] is None

@pytest.mark.parametrize('widget',[
 '<label><input style="opacity:0" type="radio" name="s" required>Yes</label>',
 '<label><input style="opacity:0" type="checkbox" name="consent" required>I agree</label>',
 '<label><input style="opacity:0" type="checkbox" name="partners" checked>Share my data with partners</label>',
 '<div><div>Do you require sponsorship?</div><button type="button">Yes</button><button type="button">No</button></div>',
 '<div>Cover letter</div><div contenteditable style="height:80px;border:1px solid"></div>',
 '<x-question></x-question><script>customElements.define("x-question",class extends HTMLElement{connectedCallback(){this.attachShadow({mode:"open"}).innerHTML="<label>Salary<input required></label>"}})</script>',
 '<details><summary>Additional questions</summary><label>Willing to relocate?<input required></label></details>'])
def test_r5_h3_unread_native_and_custom_questions_refuse(reader,widget):
    page=reader(form(EMAIL+widget))
    assert page['unreadable']
    with pytest.raises(Refused):integrations.OpenCLIBrowser._safe(page)


def attach(system,reader,tmp_path,monkeypatch,html):
    reader(html)
    script=tmp_path/'sign-in.mjs';script.write_text('// Synthetic sign-in entrance\n')
    monkeypatch.setenv('SIGN_IN_SCRIPT',str(script))
    settings=read_json(system.root/'settings.json');settings['live_browser_writes']='Enabled';write_json(system.root/'settings.json',settings)
    system.browser=integrations.OpenCLIBrowser(settings,root=system.root,runner=reader.runner)
    pid='fixture-'+hashlib.sha256(reader.url.encode()).hexdigest()[:12]
    rec=system.record('job-one');rec.update(id=pid,url=reader.url)
    write_json(system.root/'data/postings'/f'{pid}.json',rec)
    system.dispatch('pick',pid,decision='approved')
    return pid


def approve_all(system,pid):
    for box in system.review(pid)['boxes']:
        if box['state']!='approved':system.dispatch('box',pid,name=box['name'],decision='approve',by='web')
    system.dispatch('approve-submit',pid,by='web',fill_digest=system.review(pid)['fill_digest'])


def test_r5_h4_preceding_heading_preserves_other_person_and_employer_meaning(reader,system,tmp_path,monkeypatch):
    import yaml
    profile=yaml.safe_load((system.root/'profile.yml').read_text());profile.update(name={'full_name':'Synthetic Applicant'},location={'city':'Synthetic City'});profile['contact']['phone']='5550100123'
    (system.root/'profile.yml').write_text(yaml.safe_dump(profile))
    pid=attach(system,reader,tmp_path,monkeypatch,form(EMAIL+'<h3>Emergency contact</h3><div><label for="en">Name</label><input id="en" name="en" required></div><label for="ep">Phone</label><input id="ep" name="ep" required><h3>Previous employer</h3><label for="city">City</label><input id="city" name="city" required>'))
    with pytest.raises(Refused):system.dispatch('fill',pid)
    assert reader.command('eval',"[en.value,ep.value,city.value]")==['','','']
    boxes=system.review(pid)['boxes']
    assert any('Emergency contact' in b['name'] and b['missing'] for b in boxes)


def test_r5_h5_certification_context_reaches_wrapped_name_field(reader):
    from workflow import personal_control
    page=reader(form(EMAIL+'<h3>Certification</h3><p>I certify that the information above is true.</p><div class="field"><label for="full">Full name</label><input id="full" required></div>'))
    assert personal_control(next(f for f in page['fields'] if f['selector']=='#full'))

@pytest.mark.parametrize('case',['notice','banner','storage','hash'])
def test_r5_h6_refusals_are_checked_before_intent(reader,system,tmp_path,monkeypatch,case):
    extra={'notice':'<div role="alert">Applications close on Friday.</div>',
           'banner':'<div style="position:fixed;inset:auto 0 0;height:300px;background:#ddd">Cookies <button>Accept all cookies</button></div>',
           'storage':'<script>Object.defineProperty(window,"sessionStorage",{get(){throw new DOMException("Denied")}})</script>',
           'hash':''}[case]
    html=form(EMAIL+('<div style="height:520px"></div>' if case=='banner' else ''),extra).replace('<form id="application">','<form id="application" method="post" action="/confirmation">')
    pid=attach(system,reader,tmp_path,monkeypatch,html)
    system.dispatch('fill',pid);approve_all(system,pid)
    if case=='hash':reader.command('eval',"history.replaceState({},'',location.href+'#changed');true")
    before=len(reader.posts)
    if case=='notice':
        assert system.dispatch('submit',pid)['status']=='applied'
        assert len(reader.posts)==before+1
    else:
        with pytest.raises(Refused):system.dispatch('submit',pid)
        assert not system.record(pid).get('submission',{}).get('attempted_at')
        assert len(reader.posts)==before
        assert not (system.adir(pid)/'submit-intent.json').exists()

@pytest.mark.parametrize('source,height',[('/recaptcha/api2/anchor?size=normal',78),('/cdn-cgi/challenge-platform/turnstile',65),('/hcaptcha/widget?size=normal',50)])
def test_r5_m7_small_visible_captcha_frames_stop(reader,source,height):
    page=reader(form(EMAIL+f'<iframe src="{source}" width="304" height="{height}"></iframe>'))
    assert page['challenge']

@pytest.mark.parametrize('control,expected',[
 ('<input type="submit" name="submit" value="Save and continue">',None),
 ('<input type="submit" name="commit" value="Submit Application">','Submit Application')])
def test_r5_m8_visible_submit_value_owns_control_meaning(reader,control,expected):
    page=reader('<h1>Fixture</h1><form>'+EMAIL+control+'</form>')
    assert (page['submit_control'] or {}).get('name')==expected
    assert not page['unreadable']

@pytest.mark.parametrize('agreement',[
 '<p>By applying, you consent to data processing and binding arbitration.</p>',
 '<div>By submitting this application, you agree to our arbitration policy.</div>'])
def test_r5_m9_agreement_variants_inside_or_beside_form_are_in_approval(reader,agreement):
    page=reader(form(EMAIL,agreement))
    assert 'arbitration' in page['submission_terms']

@pytest.mark.parametrize('heading',['Sign up','Register to apply','Sign up / Register to apply'])
def test_r5_m10_email_only_account_heading_is_a_stop(reader,heading):
    page=reader(f'<h1>{heading}</h1><form>'+EMAIL+'<button>Next</button></form>')
    assert page['account_creation']

@pytest.mark.parametrize('style',['display:none','opacity:0;position:absolute;width:1px;height:1px','visibility:hidden'])
def test_r5_hidden_native_resume_upload_is_supported_with_verified_bytes(reader,system,tmp_path,monkeypatch,style):
    html=form(EMAIL+f'<label for="cv">Resume</label><input id="cv" name="resume" type="file" style="{style}" required>').replace('<form id="application">','<form id="application" method="post" enctype="multipart/form-data" action="/confirmation">')
    pid=attach(system,reader,tmp_path,monkeypatch,html)
    system.dispatch('fill',pid)
    row=next(b for b in system.review(pid)['boxes'] if b['type']=='file')
    assert row['upload_sha256']==hashlib.sha256(Path(system.settings['resume_pdf']).read_bytes()).hexdigest()
    approve_all(system,pid);before=len(reader.posts)
    assert system.dispatch('submit',pid)['status']=='applied'
    assert len(reader.posts)==before+1 and Path(system.settings['resume_pdf']).read_bytes() in reader.posts[-1][1]


def test_r5_leading_dash_is_a_value_through_the_stock_parser(reader,system,tmp_path,monkeypatch):
    import yaml
    profile=yaml.safe_load((system.root/'profile.yml').read_text());profile['standard_answers']['Summary']='- Led a synthetic robotics project'
    (system.root/'profile.yml').write_text(yaml.safe_dump(profile))
    pid=attach(system,reader,tmp_path,monkeypatch,form(EMAIL+'<label for="summary">Summary</label><textarea id="summary"></textarea>'))
    system.dispatch('fill',pid)
    assert reader.command('eval','summary.value')=='- Led a synthetic robotics project'


def test_r5_h1_real_pre_review_shrink_preserves_observed_fields(reader,system,tmp_path,monkeypatch):
    import yaml
    profile=yaml.safe_load((system.root/'profile.yml').read_text());profile['standard_answers']['Field zero']='synthetic'
    (system.root/'profile.yml').write_text(yaml.safe_dump(profile))
    pid=attach(system,reader,tmp_path,monkeypatch,form(EMAIL+'<div id="salarybox"><label for="salary">Expected salary</label><input id="salary" required></div><label for="zero">Field zero</label><input id="zero" oninput="salarybox.remove()">'))
    with pytest.raises(Refused):system.dispatch('fill',pid)
    assert not system.review(pid).get('agent_reviewed_at')
    with pytest.raises(Refused,match='observed'):system.dispatch('fill',pid)
    assert not system.record(pid).get('submission',{}).get('attempted_at')


def test_r5_h1_three_screen_form_first_pass_and_submit_cover_every_field(reader,system,tmp_path,monkeypatch):
    import questions,yaml
    profile=yaml.safe_load((system.root/'profile.yml').read_text());profile['standard_answers'].update({f'Field {i}':f'v{i}' for i in range(18)})
    (system.root/'profile.yml').write_text(yaml.safe_dump(profile))
    fields='<label for="salary">Expected salary</label><input id="salary" name="salary" required>'+''.join(f'<div class="field"><label for="f{i}">Field {i}</label><input id="f{i}" name="f{i}"></div>' for i in range(18))
    html=form('<style>.field{min-height:160px}header{position:fixed;top:0;left:0;right:0;height:64px;background:white;z-index:10}</style><header>Fixture header</header>'+EMAIL+fields).replace('<form id="application">','<form id="application" method="post" action="/confirmation">')
    pid=attach(system,reader,tmp_path,monkeypatch,html)
    assert reader.command('eval','document.documentElement.scrollHeight > innerHeight*3')
    with pytest.raises(Refused) as error:system.dispatch('fill',pid)
    assert error.value.code=='MISSING_FACT' and len(system.review(pid)['boxes'])==20
    assert len(next(c['filled_form']['rows'] for role,c in system.agents.tasks if role=='reviewer'))==20
    paths=questions.Paths(questions=system.root/'data/questions',postings=system.root/'data/postings',profile=system.root/'profile.yml',applications=system.root/'data/applications')
    q=next(q for q in questions.list_q(paths) if q.get('posting_id')==pid and (q.get('applies_to') or {}).get('form_question'))
    questions.answer(paths,q['id'],'90000',by='web')
    system.dispatch('fill',pid)
    assert len(system.review(pid)['boxes'])==20 and sum(role=='reviewer' for role,_ in system.agents.tasks)==1
    approve_all(system,pid);before=len(reader.posts)
    assert system.dispatch('submit',pid)['status']=='applied' and len(reader.posts)==before+1
    body=reader.posts[-1][1]
    assert b'salary=90000' in body and all(f'f{i}=v{i}'.encode() in body for i in range(18))


def test_r5_h6_post_intent_replay_refusal_remains_honestly_uncertain(reader,system,tmp_path,monkeypatch):
    html=form(EMAIL).replace('<form id="application">','<form id="application" method="post" action="/confirmation">')
    pid=attach(system,reader,tmp_path,monkeypatch,html);system.dispatch('fill',pid);approve_all(system,pid)
    original=system.browser.runner
    def runner(argv):
        result=original(argv)
        if len(argv)>7 and argv[6]=='eval' and 'const careerApproved =' in argv[7]:
            data=json.JSONDecoder().raw_decode(argv[7].split('const careerApproved =',1)[1].lstrip())[0]
            if not data.get('dry_run'):return original(argv)
        return result
    system.browser.runner=runner;before=len(reader.posts)
    with pytest.raises(Refused):system.dispatch('submit',pid)
    assert len(reader.posts)==before+1 and system.record(pid)['submission']['attempted_at']
    assert system.record(pid)['status']=='submission_unconfirmed'
    message=read_json(system.root/'data/notifications.json')[-1]['message']
    assert 'not proof' in message and 'Do not reapply' not in message
    assert system.dispatch('confirm',pid)['status']=='applied' and len(reader.posts)==before+1


def test_r5_optional_checkbox_may_stay_unticked_after_person_confirmation(reader,system,tmp_path,monkeypatch):
    import questions
    pid=attach(system,reader,tmp_path,monkeypatch,form(EMAIL+'<label for="alerts">Send me job alerts</label><input id="alerts" type="checkbox">'))
    with pytest.raises(Refused):system.dispatch('fill',pid)
    paths=questions.Paths(questions=system.root/'data/questions',postings=system.root/'data/postings',profile=system.root/'profile.yml',applications=system.root/'data/applications')
    q=next(q for q in questions.list_q(paths) if q.get('posting_id')==pid)
    assert q['kind']=='confirm' and 'Confirmed' in q['question']
    questions.answer(paths,q['id'],'Confirmed',by='web')
    system.dispatch('fill',pid)
    assert not reader.command('eval','alerts.checked')
    assert next(b for b in system.review(pid)['boxes'] if b['type']=='checkbox')['value']=='No'


def test_r5_csp_native_submit_and_approval_guard_still_work(reader,system,tmp_path,monkeypatch):
    html='<meta http-equiv="Content-Security-Policy" content="script-src &apos;none&apos;; require-trusted-types-for &apos;script&apos;">'+form(EMAIL).replace('<form id="application">','<form id="application" method="post" action="/confirmation">')
    pid=attach(system,reader,tmp_path,monkeypatch,html);system.dispatch('fill',pid);approve_all(system,pid)
    assert system.dispatch('submit',pid)['status']=='applied'

@pytest.mark.parametrize('heading',['Thanks for applying!','Your application was successfully submitted.'])
def test_r5_success_heading_variants_are_recognized(reader,heading):
    assert reader(f'<h1>{heading}</h1>')['confirmation']==heading


def test_r5_uppercase_next_and_invisible_badge_and_footer_signup(reader):
    page=reader('<h1>Fixture role</h1><form>'+EMAIL+'<button style="text-transform:uppercase">Next</button></form><iframe src="/recaptcha/api2/anchor?size=invisible" width="304" height="78"></iframe><footer><h2>Sign up for our newsletter</h2></footer>')
    assert page['next_control']['name']=='NEXT' and not page['challenge'] and not page['account_creation']

@pytest.mark.parametrize('confirmed',[True,False])
def test_r5_next_submitting_outside_posting_is_never_replayed_after_release(reader,system,tmp_path,monkeypatch,confirmed):
    reader.pages['/done']='<h1>Application submitted.</h1>' if confirmed else '<h1>Receipt pending</h1>'
    html='''<h1>Fixture role</h1><h2 id="heading">Contact</h2><form id="application"><div id="one">'''+EMAIL+'''</div>
    <div id="two" hidden><label for="start">Start date</label><input id="start" name="start"></div><button id="b" type="button">Next</button></form>
    <script>let step=0;b.onclick=async()=>{if(step++===0){one.hidden=true;two.hidden=false;heading.textContent='Details';history.pushState({},'',location.pathname+'?step=2');}
    else{await fetch('/submit',{method:'POST',body:new FormData(document.getElementById('application'))});location.href='/done';}}</script>'''
    pid=attach(system,reader,tmp_path,monkeypatch,html);before=len(reader.posts)
    with pytest.raises(Refused):system.dispatch('fill',pid)
    assert len(reader.posts)==before+1
    journal=read_json(system.adir(pid)/'steps.json')
    assert journal['moving'] and journal['outcomes'][-1]['url']==reader.base+'/done'
    if confirmed:
        assert system.record(pid)['submission']['attempted_at']
        with pytest.raises(Refused):system.dispatch('release',pid)
    else:
        system.dispatch('release',pid)
        reader('<h1>New owned target for another attempt</h1>')
    with pytest.raises(Refused) as error:system.dispatch('fill',pid)
    assert error.value.code in ('SUBMISSION_UNCONFIRMED','NAVIGATION_UNCERTAIN')
    assert len(reader.posts)==before+1


def test_r5_small_embedded_question_cannot_disappear(reader):
    page=reader(form(EMAIL+'<iframe title="Additional questions" src="/embedded" height="80"></iframe>'))
    assert page['unreadable']

@pytest.mark.parametrize('label,style',[
 ('Resume/CV','display:none'),
 ('Upload resume','position:absolute;opacity:0;width:1px;height:1px'),
 ('Attach resume (required)','visibility:hidden')])
def test_r5_resume_section_native_uploads_have_real_received_bytes(reader,system,tmp_path,monkeypatch,label,style):
    html=form(EMAIL+f'<h2>Resume</h2><div class="upload-control"><label for="file">{label}</label><input id="file" name="resume" type="file" required accept=".pdf" style="{style}"><button type="button" onclick="file.click()">Attach file</button></div>').replace('<form id="application">','<form id="application" method="post" enctype="multipart/form-data" action="/confirmation">')
    pid=attach(system,reader,tmp_path,monkeypatch,html)
    system.dispatch('fill',pid);approve_all(system,pid)
    assert system.dispatch('submit',pid)['status']=='applied'
    assert Path(system.settings['resume_pdf']).read_bytes() in reader.posts[-1][1]
