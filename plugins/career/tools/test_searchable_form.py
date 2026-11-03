"""Searchable application controls on an invented, isolated page; never a public form write."""
import pytest
import yaml

from integrations import OpenCLIBrowser
from runtime import CODE, Refused, read_json, write_json
from test_form_reader import reader
from test_workflow import api, system

HTML=(CODE/'tools/fixtures/searchable-application.html').read_text()


def test_unreadable_diagnostics_keep_the_readers_reasons():
    with pytest.raises(Refused) as error:
        OpenCLIBrowser._safe({'unreadable':['Country','Degree*','Country','Unread native field: Search']})
    assert error.value.code=='FORM_UNREADABLE'
    assert str(error.value).endswith('Country; Degree*; Unread native field: Search')
    with pytest.raises(Refused) as error:
        OpenCLIBrowser._safe({'unreadable':True})
    assert error.value.code=='FORM_UNREADABLE'


@pytest.mark.parametrize('stop',['challenge','origin','consent'])
def test_dropdown_inspection_stops_all_keys_at_a_human_action_or_changed_origin(tmp_path,monkeypatch,stop):
    browser=OpenCLIBrowser({},root=tmp_path)
    browser.state={'choice_url':'https://careers.example.org/role'}
    field={'name':'Degree','type':'combobox','selector':'#degree','choice':{'search':'','expanded':False,'open_selector':'#degree-control'},'value':''}
    page={'url':browser.state['choice_url'],'fields':[{**field,'choice':{**field['choice'],'expanded':True}}]}
    if stop=='challenge':page['challenge']=True
    if stop=='origin':page['url']='https://elsewhere.example.org/role'
    if stop=='consent':page['fields'].append({'name':'I agree to terms','type':'checkbox'})
    calls=[]
    monkeypatch.setattr(browser,'_command',lambda *args:calls.append(args))
    monkeypatch.setattr(browser,'_raw_read',lambda:page)
    with pytest.raises(Refused) as error:browser._inspect_choice(field)
    assert error.value.code=={'challenge':'HUMAN_SIGN_IN','origin':'IDENTITY_OR_PAGE','consent':'SENSITIVE_FIELD'}[stop]
    assert calls==[('click','#degree-control')]


def test_reader_accounts_for_required_uploads_helpers_and_captcha_metadata(reader):
    page=reader(HTML)
    assert page['unreadable']==[],page['unreadable']
    fields={field['name']:field for field in page['fields']}
    assert fields['First Name']['required'] and fields['Email']['required']
    assert fields['Why this role?']['required']
    assert fields['Resume/CV*']['required']
    assert not fields['Optional supporting document']['required']
    assert fields['Phone country']['type']=='combobox'
    assert {'Education 1 / School*','Education 1 / Degree*','Education 1 / Discipline*'} <= fields.keys()
    assert not any('Search' in f['name'] or 'recaptcha' in f['name'] or f['name']=='Attach' for f in fields.values())
    assert not page['challenge']
    reader.command('eval',"document.querySelector('[name=g-recaptcha-response]').value='opaque-response';true")
    after=reader.command('eval',(CODE/'tools/form-page.js').read_text())
    assert not after['unreadable'] and after['fields']==page['fields']
    # An unrelated invisible answer is still an unreadable field, even beside a known widget.
    reader.command('eval',"document.querySelector('form').insertAdjacentHTML('beforeend','<input aria-label=\"Unaccounted fact\" style=\"opacity:0\">');true")
    assert 'Unread native field: Unaccounted fact' in reader.command('eval',(CODE/'tools/form-page.js').read_text())['unreadable']


def open_fixture(reader,system,monkeypatch):
    reader(HTML)
    entry=system.root/'sign-in.mjs';entry.write_text('// fixture public entrance')
    monkeypatch.setenv('SIGN_IN_SCRIPT',str(entry))
    settings=read_json(system.root/'settings.json');settings['live_browser_writes']='Enabled';write_json(system.root/'settings.json',settings)
    calls=[]
    def runner(argv):
        calls.append(argv)
        return reader.runner(argv)
    browser=OpenCLIBrowser(settings,root=system.root,runner=runner)
    browser.open(reader.url,settings['account'],'example-company','owned-searchable')
    return browser,calls


def test_searchable_controls_use_exact_options_and_survive_review_and_approval(reader,system,api,monkeypatch):
    browser,calls=open_fixture(reader,system,monkeypatch)
    page=browser.read();fields={f['name']:f for f in page['fields']}
    assert fields['Education 1 / School*']['options']==['Example Institute','Example University','Example College']
    assert fields['Phone country']['options']==['Fictionland +999','Northland +999','Elsewhere +998']
    # Arbitrary strings never reach a typing command, even for an optional country picker.
    before=len(calls)
    with pytest.raises(Refused,match='exact'):browser.fill('Phone country','Unsupported place','combobox')
    assert not any('fill' in call or 'type' in call for call in calls[before:])
    reader.command('eval',"document.getElementById('country').value='Unfinished search';true")
    before=len(calls)
    with pytest.raises(Refused,match='active dropdown search'):browser.fill('Phone country','','combobox')
    assert not any('click' in call or 'keys' in call for call in calls[before:])
    reader.command('eval',"document.getElementById('country').value='';true")
    system.browser=browser;system.settings=read_json(system.root/'settings.json')
    rec=system.record('job-one');rec.update(url=reader.url,title='Test engineer');system.save(rec)
    profile={
        'name':{'first_name':'Example'},'contact':{'email':'person@example.org','phone':'+9995550100','phone_country':'Fictionland'},
        'education':[{'school':'Example College','degree':'Bachelor degree','discipline':'Software systems'}],
        'standard_answers':{'Which offices are you willing to work in? *':['Northport, ZZ']},
    }
    (system.root/'profile.yml').write_text(yaml.safe_dump(profile))
    system.dispatch('pick','job-one',decision='approved')
    review=system.dispatch('fill','job-one')
    expected={'Phone country':'Fictionland +999','Education 1 / School*':'Example College',
              'Education 1 / Degree*':'Bachelor degree','Education 1 / Discipline*':'Software systems'}
    boxes={b['name']:b for b in review['boxes']}
    actual={f['name']:f for f in browser.read()['fields']}
    for name,value in expected.items():
        assert actual[name]['value']==boxes[name]['value']==value
        assert boxes[name]['options']==actual[name]['options'] and boxes[name]['type']=='combobox'
    assert reader.command('eval','window.freeEntryAttempts')==0
    assert reader.command('eval','window.unexpectedSubmits')==0 and not reader.posts
    assert not any('type' in call for call in calls)
    combo_selectors={f['selector'] for f in fields.values() if f['type']=='combobox'}
    assert not any('fill' in call and any(selector in call for selector in combo_selectors) for call in calls)
    for box in review['boxes']:
        if box['state']!='approved':system.dispatch('box','job-one',name=box['name'],decision='approve')
    doc=api('GET','/postings/job-one/preview')[1]
    assert doc['can_submit']
    system.dispatch('approve-submit','job-one',fill_digest=doc['fill_digest'])
    assert system.record('job-one')['ruling']['submit_digest']==doc['fill_digest']
    fill=system.fill('job-one')
    contract=browser.read()
    approved=(fill['rows'],None,reader.url,contract)
    # Native `required` on each checkbox is represented by one required group, not "tick all".
    assert browser.preflight_submit('Submit application',approved)['career_preflight']
    browser.fill('Phone country','Northland +999','combobox')  # Same dial code, different flag/meaning.
    assert next(f for f in browser.read()['fields'] if f['name']=='Phone country')['value']=='Northland +999'
    with pytest.raises(Refused):browser.preflight_submit('Submit application',approved)
    browser.fill('Phone country','Fictionland +999','combobox')
    browser.fill('Phone country','','combobox')
    assert next(f for f in browser.read()['fields'] if f['name']=='Phone country')['value']==''
    browser.fill('Phone country','Fictionland +999','combobox')
    assert api('POST','/postings/job-one/review',{'by':'web','name':'Education 1 / Degree*','decision':'edit','value':'Doctoral degree'})[0]==200
    assert not system.record('job-one')['ruling'].get('submit_approved_at')
    system.dispatch('apply-edits','job-one')
    after=system.review('job-one')
    assert after['fill_digest']!=doc['fill_digest']
    assert next(b for b in after['boxes'] if b['name']=='Education 1 / Degree*')['value']=='Doctoral degree'
    assert len([role for role,context in system.agents.tasks if role=='reviewer'])==1
    assert reader.command('eval','window.unexpectedSubmits')==reader.command('eval','window.freeEntryAttempts')==0


def test_visible_challenges_and_unsupported_comboboxes_still_stop(reader):
    page=reader(HTML+'<iframe title="Verification" src="https://captcha.example.org/recaptcha/challenge"></iframe>')
    with pytest.raises(Refused) as error:OpenCLIBrowser._safe(page)
    assert error.value.code=='HUMAN_SIGN_IN'
    page=reader(HTML+'<input role="combobox" aria-label="Unsupported choice">')
    with pytest.raises(Refused) as error:OpenCLIBrowser._safe(page)
    assert error.value.code=='FORM_UNREADABLE' and 'Unsupported choice' in str(error.value)
    page=reader(HTML.replace('class="select__value-container"','class="select__value-container select__value-container--is-multi"'))
    with pytest.raises(Refused,match='Multiple-selection dropdown'):OpenCLIBrowser._safe(page)


def test_phone_country_uses_its_own_fact_and_never_borrows_residence(system):
    system.browser.fields.append({'name':'Phone country','type':'combobox','required':False,'options':['Fictionland +999'],
                                  'value':'','choice':{'phone_country':True}})
    profile=yaml.safe_load((system.root/'profile.yml').read_text())
    profile['contact']['phone']='+9985550100';profile['location']={'country':'Fictionland'}
    (system.root/'profile.yml').write_text(yaml.safe_dump(profile))
    system.dispatch('pick','job-one',decision='approved')
    with pytest.raises(Refused) as error:system.dispatch('fill','job-one')
    assert error.value.code=='MISSING_FACT'
    assert next(b for b in system.review('job-one')['boxes'] if b['name']=='Phone country')['state']=='pending'
    assert not any(write[0]=='Phone country' for write in system.browser.writes)


