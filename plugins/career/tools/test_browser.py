"""Stock command boundary tests with refusing browser and identity fakes."""
import copy
import hashlib
import json
from pathlib import Path

import pytest

import integrations
from runtime import Refused, read_json, write_json
from test_workflow import system


class OpenCLI:
    def __init__(self):
        self.calls=[];self.alive=False;self.fail_fill=False;self.fail_upload=False;self.context='fixture-context';self.version='2.0.2';self.site='example-company'
        self.fields=[{'name':'Email','type':'email','required':True,'value':'','selector':'#email'},
                     {'name':'Resume','type':'file','required':True,'value':'','selector':'#resume'},
                     {'name':'Why this role?','type':'textarea','required':True,'value':'','selector':'#why'},
                     {'name':'Start date','type':'text','required':True,'value':'','selector':'#start'}]
        self.url=None;self.confirmed=False;self.submit_nonce=None;self.submit_control={'name':'Submit application','enabled':True,'selector':'#submit'};self.clicks=[];self.on_submit=None;self.controls=[];self.on_read=None

    def __call__(self,argv):
        self.calls.append(argv)
        if argv[0]=='node' and argv[1].endswith('browser-runtime.mjs'):
            return json.dumps({'ok':True,'data':{'binary':'/fixture/pinned/opencli','version':self.version}})
        if argv[0]=='node':
            assert argv[2:]==['check','demo','--site',self.site]
            return json.dumps({'ok':True,'data':{'account':'demo','site':self.site,'contextId':self.context,'status':'signed-in'}})
        assert argv[:5]==['/fixture/pinned/opencli','--profile',self.context,'browser','--session']
        assert argv[5]=='career-job-one'
        verb=argv[6];args=argv[7:]
        if "--" in args:
            marker=args.index("--")
            assert args[:marker]==["--tab","fixture-tab","--window","background"]
            args=args[marker+1:]+["--tab","fixture-tab","--window","background"]
        assert args[-2:]==["--window","background"]
        args=args[:-2]
        if verb=='open':
            assert not self.alive
            self.alive=True;self.url=args[0];return json.dumps({'ok':True,'data':{'url':self.url,'page':'fixture-tab'}})
        if not self.alive:raise Refused('TAB_MISSING','The fake refuses reads/writes on a dropped tab.')
        assert args[-2:]==['--tab','fixture-tab']
        args=args[:-2]
        if verb=='eval':
            if self.on_read:
                callback=self.on_read;self.on_read=None;callback()
            if 'const careerApproved =' in args[0]:
                raw=args[0].split('const careerApproved =',1)[1].lstrip()
                approved,_=json.JSONDecoder().raw_decode(raw)
                rows=approved['rows']
                valid=len(rows)==len(self.fields) and self.url==approved["url"] and self.submit_control==approved["contract"]["submit_control"] and not self.confirmed
                for field in self.fields:
                    row=next((r for r in rows if r.get('field_name',r['name'])==field['name']),None)
                    valid=valid and row is not None
                    if row is not None:
                        expected=row.get('upload_name','') if field['type']=='file' else row['value']
                        valid=valid and field['value']==expected and field['type']==row['type'] and field.get('options',[])==row['options'] and field.get('required',False)==row['required'] and field.get('context','')==row.get('context','')
                        if field['type']=='file':valid=valid and field.get('sha256')==row.get('upload_sha256')
                if not valid:return json.dumps({'ok':True,'data':{'career_submit':False,'code':'FORM_CHANGED'}})
                if approved.get('dry_run'):return json.dumps({'ok':True,'data':{'career_preflight':True,'nonce':approved['nonce']}})
                if self.submit_nonce:return json.dumps({'ok':True,'data':{'career_submit':False,'code':'ALREADY_ATTEMPTED'}})
                self.submit_nonce=approved['nonce']
                if self.on_submit:self.on_submit()
                self.clicks.append('Submit application');self.confirmed=True
                return json.dumps({'ok':True,'data':{'career_submit':True,'clicked':True,'click_method':'guarded-dom','nonce':approved['nonce']}})
            assert 'querySelectorAll' in args[0]
            data={'url':self.url,'fields':copy.deepcopy(self.fields),'text':'Local job application','controls':self.controls,
                  'submit_control':copy.deepcopy(self.submit_control)}
            if self.confirmed:data.update(fields=[],submit_control=None,text='Application received.',confirmation='Application received.')
        elif verb=='fill':
            if self.fail_fill:raise Refused('value_mismatch','The fake field refuses the value.')
            field=next(f for f in self.fields if f['selector']==args[0]);field['value']=args[1]
            data={'filled':True,'verified':True,'actual':args[1]}
        elif verb=='check':
            field=next(f for f in self.fields if f['selector']==args[0]);assert field['type']=='checkbox'
            if self.fail_fill:raise Refused('CHECK_REFUSED','The fake refuses the check.')
            field['value']='Yes';data={'checked':True}
        elif verb=='type':
            if self.fail_fill:raise Refused('value_mismatch','The fake refuses typed input.')
            field=next(f for f in self.fields if f['selector']==args[0])
            assert field['type'] in ('date','month','number')
            field['value']=args[1];data={'typed':True}
        elif verb=='upload':
            field=next(f for f in self.fields if f['selector']==args[0]);path=Path(args[1])
            if self.fail_upload:data={'uploaded':True,'files':1,'file_names':[]}
            else:
                field.update(value=path.name,sha256=hashlib.sha256(path.read_bytes()).hexdigest())
                data={'uploaded':True,'files':1,'file_names':[path.name]}
        elif verb=='click':
            assert args==['#submit']
            if self.on_submit:self.on_submit()
            self.clicks.append('Submit application');self.confirmed=True;data={'clicked':True}
        elif verb=='screenshot':
            import base64
            from test_workflow import png
            data=base64.b64encode(png()).decode()
        elif verb=='tab':
            assert args==['close','fixture-tab'];self.alive=False;data={'closed':'fixture-tab'}
        else:raise AssertionError('Fake does not implement '+verb)
        return json.dumps({'ok':True,'data':data})


def browser(system,tmp_path,monkeypatch,enabled=True):
    script=tmp_path/'sign-in.mjs';script.write_text('// fake entrance')
    monkeypatch.setenv('SIGN_IN_SCRIPT',str(script))
    settings=read_json(system.root/'settings.json');settings['live_browser_writes']='Enabled' if enabled else 'Disabled'
    write_json(system.root/'settings.json',settings)
    fake=OpenCLI()
    system.browser=integrations.OpenCLIBrowser(settings,root=system.root,runner=fake)
    return fake


def test_one_page_stock_boundary_to_confirmation(system,tmp_path,monkeypatch):
    fake=browser(system,tmp_path,monkeypatch)
    fake.on_submit=lambda: (_ for _ in ()).throw(AssertionError('intent was not durable')) if not system.record('job-one').get('submission',{}).get('attempted_at') else None
    system.dispatch('pick','job-one',decision='approved')
    review=system.dispatch('fill','job-one')
    assert not fake.clicks and [role for role,_ in system.agents.tasks].count('reviewer')==1
    system.dispatch('box','job-one',name='Why this role?',decision='approve',by='phone')
    system.dispatch('approve-submit','job-one',by='phone', fill_digest=system.review('job-one')["fill_digest"])
    assert system.dispatch('submit','job-one')['status']=='applied'
    assert fake.clicks==['Submit application'] and not fake.alive
    assert (system.adir('job-one')/'confirmation.png').is_file()
    assert all(call[2]=='fixture-context' for call in fake.calls if call[0]=='/fixture/pinned/opencli' and len(call)>6)


def test_default_refuses_before_identity_or_browser_process(system,tmp_path,monkeypatch):
    fake=browser(system,tmp_path,monkeypatch,enabled=False)
    system.dispatch('pick','job-one',decision='approved')
    with pytest.raises(Refused) as error:system.dispatch('fill','job-one')
    assert error.value.code=='BROWSER_WRITES_DISABLED' and not fake.calls
    assert not read_json(system.root/'data/active.json')


@pytest.mark.parametrize('failure',['fill','upload','tab'])
def test_stock_boundary_can_fail_instead_of_approving_unverified_work(system,tmp_path,monkeypatch,failure):
    fake=browser(system,tmp_path,monkeypatch)
    if failure=='fill':fake.fail_fill=True
    if failure=='upload':fake.fail_upload=True
    system.dispatch('pick','job-one',decision='approved')
    if failure=='tab':
        system.dispatch('fill','job-one');fake.alive=False
        with pytest.raises(Refused):system.dispatch('apply-edits','job-one')
    else:
        with pytest.raises(Refused):system.dispatch('fill','job-one')
    assert not fake.clicks and system.gate('job-one')


def test_context_must_be_the_one_proven_by_sign_in(system,tmp_path,monkeypatch):
    fake=browser(system,tmp_path,monkeypatch)
    system.dispatch('pick','job-one',decision='approved');system.dispatch('fill','job-one')
    fake.context='some-other-context'
    with pytest.raises(Refused):system.dispatch('apply-edits','job-one')
    assert not fake.clicks


def test_changed_resume_bytes_cannot_use_the_old_approval(system,tmp_path,monkeypatch):
    fake=browser(system,tmp_path,monkeypatch)
    system.dispatch('pick','job-one',decision='approved');system.dispatch('fill','job-one')
    system.dispatch('box','job-one',name='Why this role?',decision='approve')
    system.dispatch('approve-submit','job-one', fill_digest=system.review('job-one')["fill_digest"])
    next(field for field in fake.fields if field['type']=='file')['sha256']='f'*64
    with pytest.raises(Refused):system.dispatch('submit','job-one')
    assert not fake.clicks and not system.record('job-one').get('submission',{}).get('attempted_at')


def test_chosen_account_change_cannot_write_through_the_old_verified_context(system,tmp_path,monkeypatch):
    fake=browser(system,tmp_path,monkeypatch)
    system.dispatch('pick','job-one',decision='approved');system.dispatch('fill','job-one')
    settings=read_json(system.root/'settings.json');settings['account']='different-account';write_json(system.root/'settings.json',settings)
    writes=len([call for call in fake.calls if len(call)>6 and call[6] in ('fill','upload','click')])
    with pytest.raises(Refused):system.dispatch('fill','job-one')
    assert len([call for call in fake.calls if len(call)>6 and call[6] in ('fill','upload','click')])==writes


def test_read_only_terms_link_does_not_become_automatic_consent(system,tmp_path,monkeypatch):
    fake=browser(system,tmp_path,monkeypatch)
    fake.controls=[{'name':'Terms','tag':'a','href':'https://careers.example.org/terms','enabled':True}]
    system.dispatch('pick','job-one',decision='approved');system.dispatch('fill','job-one')
    assert not fake.clicks
    fake.controls=[{'name':'Accept terms','tag':'button','enabled':True}]
    with pytest.raises(Refused):system.dispatch('apply-edits','job-one')


def test_native_date_uses_stock_type_with_verified_readback(system,tmp_path,monkeypatch):
    fake=browser(system,tmp_path,monkeypatch)
    next(field for field in fake.fields if field['name']=='Start date')['type']='date'
    system.dispatch('pick','job-one',decision='approved');system.dispatch('fill','job-one')
    assert any(call[6:9]==['type','#start','2027-01-01'] for call in fake.calls if call[0]=='/fixture/pinned/opencli' and len(call)>6)
    assert next(field for field in fake.fields if field['name']=='Start date')['value']=='2027-01-01'


def test_ambiguous_opt_in_checkbox_is_personal_even_with_a_supplied_answer(system,tmp_path,monkeypatch):
    import yaml
    fake=browser(system,tmp_path,monkeypatch)
    label='Receive job alerts'
    fake.fields.append({'name':label,'type':'checkbox','required':True,'value':'No','selector':'#alerts'})
    profile=yaml.safe_load((system.root/'profile.yml').read_text());profile['standard_answers'][label]='Yes'
    (system.root/'profile.yml').write_text(yaml.safe_dump(profile))
    system.dispatch('pick','job-one',decision='approved')
    with pytest.raises(Refused):system.dispatch('fill','job-one')
    assert not any(call[6]=='check' for call in fake.calls if call[0]=='/fixture/pinned/opencli' and len(call)>6)


def test_autocomplete_one_time_code_cannot_be_filled_under_an_ordinary_label(system,tmp_path,monkeypatch):
    import yaml
    fake=browser(system,tmp_path,monkeypatch)
    fake.fields.append({'name':'Confirmation','type':'text','required':True,'value':'','selector':'#otp','human_only':True})
    profile=yaml.safe_load((system.root/'profile.yml').read_text());profile['standard_answers']['Confirmation']='482913'
    (system.root/'profile.yml').write_text(yaml.safe_dump(profile))
    system.dispatch('pick','job-one',decision='approved')
    with pytest.raises(Refused):system.dispatch('fill','job-one')
    assert not any(call[6:8]==['fill','#otp'] for call in fake.calls if call[0]=='/fixture/pinned/opencli' and len(call)>6)


def test_incompatible_stock_version_stops_before_sign_in_or_browser(system,tmp_path,monkeypatch):
    fake=browser(system,tmp_path,monkeypatch);fake.version='3.0.0'
    system.dispatch('pick','job-one',decision='approved')
    with pytest.raises(Refused) as error:system.dispatch('fill','job-one')
    assert error.value.code=='OPENCLI_VERSION'
    assert fake.calls==[['node',str(integrations.CODE/'scripts/browser-runtime.mjs'),str(tmp_path/'sign-in.mjs')]]


def test_step_wait_observes_progress_without_repeating_the_click(system,monkeypatch):
    before={'identity_verified':True,'step':{'id':'contact'},'url':'https://careers.example.org/contact'}
    after={'identity_verified':True,'step':{'id':'replies'},'url':'https://careers.example.org/replies'}
    actor=integrations.OpenCLIBrowser({},root=system.root)
    frames=iter([before,before,after]);clicks=[]
    observations=[]
    def read(*,transition=False):
        observations.append(transition);return next(frames)
    monkeypatch.setattr(actor,'read',read)
    monkeypatch.setattr(actor,'_click',lambda key,name:clicks.append((key,name)))
    assert actor.advance('next_control','Next')==after
    assert clicks==[('next_control','Next')]
    assert observations==[False,True,True]


def test_account_change_during_final_observation_blocks_before_attempt(system,tmp_path,monkeypatch):
    fake=browser(system,tmp_path,monkeypatch)
    system.dispatch('pick','job-one',decision='approved');system.dispatch('fill','job-one')
    system.dispatch('box','job-one',name='Why this role?',decision='approve');system.dispatch('approve-submit','job-one', fill_digest=system.review('job-one')["fill_digest"])
    def change():
        settings=read_json(system.root/'settings.json');settings['account']='different-account';write_json(system.root/'settings.json',settings)
    fake.on_read=change
    with pytest.raises(Refused):system.dispatch('submit','job-one')
    assert not fake.clicks and not system.record('job-one').get('submission',{}).get('attempted_at')
