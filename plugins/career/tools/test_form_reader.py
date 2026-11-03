"""Real stock parser/DOM reads on pages served only by this test's loopback server."""
import json
import os
from pathlib import Path
import subprocess
import ssl
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import pytest


OFFICE_GROUP = '''<h1>Application</h1><form id="application">
<div class="field"><label for="question_1000[]">Which offices are you willing to work in? *</label>
<div role="group" aria-required="true">
<label for="question_1000[]_2001"><input style="opacity:0" id="question_1000[]_2001" type="checkbox" name="question_1000[]" value="2001" checked>Riverton, ZZ</label>
<label for="question_1000[]_2002"><input style="opacity:0" id="question_1000[]_2002" type="checkbox" name="question_1000[]" value="2002">Northport, ZZ</label>
<label for="question_1000[]_2003"><input style="opacity:0" id="question_1000[]_2003" type="checkbox" name="question_1000[]" value="2003">Eastfield, ZZ</label>
</div></div><button type="submit">Submit application</button></form>'''


def test_named_checkbox_group_is_one_multiselect_with_stock_ticks_and_guarded_readback(reader,tmp_path,monkeypatch):
    from integrations import OpenCLIBrowser
    from runtime import write_json
    entry=tmp_path/'sign-in.mjs';entry.write_text('// fake public entrance')
    monkeypatch.setenv('SIGN_IN_SCRIPT',str(entry))
    page=reader(OFFICE_GROUP)
    assert not page['unreadable'],page
    field=page['fields'][0]
    assert field['type']=='multiselect' and field['required'] and not field['human_only']
    assert field['options']==['Riverton, ZZ','Northport, ZZ','Eastfield, ZZ']
    assert field['value']=='Riverton, ZZ'
    write_json(tmp_path/'settings.json',{'live_browser_writes':'Enabled','account':'synthetic'})
    browser=OpenCLIBrowser({'live_browser_writes':'Enabled','account':'synthetic'},root=tmp_path,runner=reader.runner)
    browser.open(reader.url,'synthetic','example','owned-group')
    browser.fill(field['name'],'Northport, ZZ; Eastfield, ZZ','multiselect')
    after=browser.read()
    assert after['fields'][0]['value']=='Northport, ZZ; Eastfield, ZZ'
    row={**after['fields'][0],'human_completed':False,'missing':False}
    assert browser.preflight_submit('Submit application',approved=([row],None,after['url'],after))['career_preflight']
    reader.command('eval',"document.querySelectorAll('input')[0].checked=true;true")
    with pytest.raises(Exception): browser.preflight_submit('Submit application',approved=([row],None,after['url'],after))


def test_checkbox_groups_cannot_disguise_terms_or_standalone_consent(reader):
    from integrations import OpenCLIBrowser
    from runtime import Refused
    page=reader(OFFICE_GROUP.replace('Which offices are you willing to work in?','I agree to the terms and consent to data processing'))
    with pytest.raises(Refused,match='person'): OpenCLIBrowser._safe(page)
    page=reader('<h1>Application</h1><form><label><input type="checkbox" required>I agree to the terms</label><button>Submit application</button></form>')
    assert page['fields'][0]['type']=='checkbox'
    with pytest.raises(Refused): OpenCLIBrowser._safe(page)


@pytest.fixture(scope='module')
def reader(tmp_path_factory):
    chrome=os.environ.get('CAREER_TEST_CHROME');package=os.environ.get('CAREER_TEST_OPENCLI')
    if not chrome or not package:pytest.skip('Set owned local Chrome and installed package paths for DOM checks')
    root=tmp_path_factory.mktemp('owned-browser');profile=root/'profile';pages={};posts=[]
    class Page(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_GET(self):
            self.send_response(200);self.send_header('Content-Type','text/html');self.end_headers();self.wfile.write(pages.get(self.path,'<h1>Application received.</h1>' if self.path=='/confirmation' else '').encode())
        def do_POST(self):
            posts.append((self.path,self.rfile.read(int(self.headers.get('Content-Length') or 0))))
            self.send_response(303);self.send_header('Location','/confirmation');self.end_headers()
    server=ThreadingHTTPServer(('127.0.0.1',0),Page)
    cert,key=root/'cert.pem',root/'key.pem'
    subprocess.run(['openssl','req','-x509','-newkey','rsa:2048','-nodes','-keyout',str(key),'-out',str(cert),'-days','1','-subj','/CN=localhost','-addext','subjectAltName=IP:127.0.0.1'],check=True,capture_output=True)
    tls=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);tls.load_cert_chain(cert,key);server.socket=tls.wrap_socket(server.socket,server_side=True)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    log=(root/'chrome.log').open('w')
    env=dict(os.environ,HOME=str(root),TMPDIR=str(root),OPENCLI_CONFIG_DIR=str(root/'config'),OPENCLI_CACHE_DIR=str(root/'cache'))
    env.pop('OPENCLI_DAEMON_PORT',None)
    process=subprocess.Popen([chrome,'--headless=new','--window-size=1280,900','--ignore-certificate-errors','--no-first-run','--disable-background-networking','--disable-extensions','--disable-sync','--disable-component-update','--disable-breakpad','--disable-crash-reporter','--metrics-recording-only','--password-store=basic','--use-mock-keychain','--remote-debugging-port=0','--user-data-dir='+str(profile),'--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1','--proxy-server=http://127.0.0.1:9','--proxy-bypass-list=127.0.0.1','about:blank'],env=env,stdout=log,stderr=log)
    try:
        deadline=time.monotonic()+20
        while not (profile/'DevToolsActivePort').exists():
            assert process.poll() is None and time.monotonic()<deadline;time.sleep(.05)
        endpoint='http://127.0.0.1:'+(profile/'DevToolsActivePort').read_text().splitlines()[0]
        target=next(t['id'] for t in json.load(urllib.request.urlopen(endpoint+'/json/list')) if t['type']=='page')
        env.update(CAREER_FIXTURE_CDP=endpoint,CAREER_FIXTURE_TARGET=target)
        helper=Path(__file__).resolve().parent/'testing/opencli_local.mjs'
        def command(verb,*args):
            result=subprocess.run(['node',str(helper),package,'--profile','fixture-context','browser','--session','owned-reader',verb,*args,'--tab',target],env=env,text=True,capture_output=True,check=True)
            receipt=json.loads(result.stdout);assert receipt['ok'] is True;return receipt['data']
        counter=0
        def read(html):
            nonlocal counter,target
            tabs=json.load(urllib.request.urlopen(endpoint+'/json/list'))
            if not any(tab['id']==target for tab in tabs):
                request=urllib.request.Request(endpoint+'/json/new?about:blank',method='PUT')
                target=json.load(urllib.request.urlopen(request))['id']
                env['CAREER_FIXTURE_TARGET']=target
            counter+=1;pages['/case'+str(counter)]='<!doctype html><title>Fixture</title>'+html
            read.url=f'https://127.0.0.1:{server.server_port}/case{counter}'
            command('open',read.url)
            return command('eval',Path(__file__).with_name('form-page.js').read_text())
        def runner(argv):
            from runtime import Refused
            if argv[0]=='node' and argv[1].endswith('browser-runtime.mjs'):
                return json.dumps({'ok':True,'data':{'binary':'/fixture/pinned/opencli','version':'2.0.2'}})
            if argv[0]=='node':
                assert argv[2]=='check'
                return json.dumps({'ok':True,'data':{'account':argv[3],'site':argv[5],'status':'signed-in','contextId':'fixture-context'}})
            result=subprocess.run(['node',str(helper),package,*argv[1:]],env=env,text=True,capture_output=True,timeout=60)
            if result.returncode:
                try:code=json.loads(result.stdout).get('error',{}).get('code','FIXTURE_REFUSED')
                except ValueError:code='FIXTURE_REFUSED'
                raise Refused(code,'The owned fixture command refused')
            return result.stdout
        read.runner=runner
        read.pages=pages
        read.posts=posts
        read.base=f'https://127.0.0.1:{server.server_port}'
        read.command=command
        yield read
    finally:
        process.terminate()
        try:process.wait(timeout=10)
        except subprocess.TimeoutExpired:process.kill();process.wait(timeout=5)
        log.close();server.shutdown();server.server_close();thread.join()


def test_h4_submit_and_fields_belong_to_application_form(reader):
    page=reader('''<h1>Application</h1><form id="application"><label>Email<input type="email"></label>
    <button>Send my application</button></form><footer><form><label>Work address<input></label><button>Submit</button></form></footer>''')
    assert page['submit_control']['name']=='Send my application'
    assert [f['name'] for f in page['fields']]==['Email']
    assert page['submit_control']['form']==page['fields'][0]['form']


def test_h4_accessible_name_cannot_hide_visible_code_or_account(reader):
    from integrations import OpenCLIBrowser
    from runtime import Refused
    page=reader('''<h1>Application</h1><form><label>Email verification code<input aria-label="Preferred name"></label>
    <button aria-label="Submit application">Create account</button></form>''')
    with pytest.raises(Refused):OpenCLIBrowser._safe(page)

@pytest.mark.parametrize('widget',[
 '<label>Resume<input type="file" style="display:none"></label>',
 '<div role="combobox" aria-required="true" aria-label="Work authorization" tabindex="0">Choose</div>',
 '<div contenteditable="true" role="textbox" aria-label="Cover letter"></div>'])
def test_h5_unread_widgets_refuse_instead_of_disappearing(reader,widget):
    from integrations import OpenCLIBrowser
    from runtime import Refused
    page=reader('<h1>Application</h1><form><label>Email<input type="email"></label>'+widget+'<button>Submit application</button></form>')
    if 'type="file"' in widget:
        assert not page['unreadable']
        assert any(f['type']=='file' and f['name']=='Resume' for f in page['fields'])
        OpenCLIBrowser._safe(page)
    else:
        assert page['unreadable']
        with pytest.raises(Refused):OpenCLIBrowser._safe(page)


def test_h5_radio_group_uses_visible_question(reader):
    page=reader('''<h1>Application</h1><form><div role="radiogroup" aria-labelledby="question"><span id="question">Do you require sponsorship?</span>
    <label><input type="radio" name="q_8841" value="y">Yes</label><label><input type="radio" name="q_8841" value="n">No</label></div><button>Submit application</button></form>''')
    assert page['fields'][0]['name']=='Do you require sponsorship?'


def test_h13_badges_links_and_confirmation_are_not_challenges(reader):
    page=reader('''<h1>Application</h1><form><label>Email<input></label><button>Submit application</button></form>
    <small>This site is protected by reCAPTCHA and the Google Privacy Policy and Terms of Service apply.</small><iframe src="about:blank#recaptcha" hidden></iframe>
    <footer><a href="/register">Create account</a><button>Accept all cookies</button></footer>''')
    assert not page['challenge']
    page=reader('<h1>Application received.</h1><a href="/register">Create an account to track your application</a>')
    from integrations import OpenCLIBrowser
    OpenCLIBrowser._safe(page)
    assert page['confirmation']


def test_h13_submission_agreement_is_visible_in_approval(reader):
    page=reader('''<h1>Application</h1><form><label>Email<input></label><p>By submitting you agree to the applicant privacy policy.</p><button>Submit application</button></form>''')
    assert 'By submitting you agree' in page['submission_terms']


def test_low_boilerplate_is_not_confirmation_and_invisible_traps_are_not_fields(reader):
    page=reader('<h1>Job closed</h1><p>After you finish, application received will appear here.</p>')
    assert not page['confirmation']
    page=reader('''<h1>Application</h1><form><label>Email<input></label><label style="opacity:0">Website<input></label><label style="position:absolute;left:-9999px">Website<input></label><button>Submit application</button></form>''')
    assert [f['name'] for f in page['fields']]==['Email']

@pytest.mark.parametrize('html',[
 '<h1>Fixture role</h1><button onclick="fetch(\'/submit\',{method:\'POST\'})">Apply now</button>',
 '<h1>Fixture role</h1><form><label>Email<input></label><button>Continue</button></form>',
 '<h1>Join Example Careers</h1><h2>Create your candidate profile to continue</h2><form><label>Email<input></label><button>Continue</button></form>'])
def test_h2_real_reader_never_marks_submit_like_buttons_as_fill_navigation(reader,html):
    page=reader(html)
    assert not page['next_control'] and not page['apply_control']
    if 'candidate profile' in html:assert page['account_creation']


def test_h13_duplicate_labels_keep_their_section_and_signature_context(reader):
    page=reader('''<h1>Application</h1><form><fieldset><legend>Undergraduate</legend><label>School<input></label></fieldset><fieldset><legend>Graduate</legend><label>School<input></label></fieldset><button>Submit application</button></form>''')
    assert {f['name'] for f in page['fields']}=={'Undergraduate / School','Graduate / School'}
    page=reader('''<h1>Application</h1><form><p>By typing your name you agree to this declaration.</p><label>Full name<input></label><button>Submit application</button></form>''')
    from integrations import OpenCLIBrowser
    from runtime import Refused
    with pytest.raises(Refused):OpenCLIBrowser._safe(page)


def test_reader_keeps_consent_context_with_its_own_control(reader):
    page=reader('''<h1>Application</h1><form><fieldset><legend>Contact</legend><label>Email<input type="email"></label><label><input type="checkbox">I agree to the privacy notice</label></fieldset><button>Submit application</button></form>''')
    from workflow import personal_control
    email=next(f for f in page['fields'] if f['name']=='Email')
    assert not personal_control(email)
    assert personal_control(next(f for f in page['fields'] if f['type']=='checkbox'))


def test_reader_label_wrapping_select_does_not_include_its_options(reader):
    page=reader('''<h1>Application</h1><form><label>Country<select><option>Choose</option><option>United States</option></select></label><button>Submit application</button></form>''')
    assert page['fields'][0]['name']=='Country'


def test_guarded_submit_checks_real_dom_values_and_refuses_expression_replay(reader):
    page=reader('''<h1>Application</h1><form onsubmit="event.preventDefault();window.fixtureClicks=(window.fixtureClicks||0)+1"><label for="email">Email</label><input id="email" type="email" value="person@example.org" required><button>Submit application</button></form>''')
    field=page['fields'][0]
    row={key:field.get(key) for key in ('name','type','required','value','context')};row['options']=[]
    payload={'rows':[row],'url':page['url'],'contract':page,'handle':'owned-guard-test','nonce':'synthetic-local-nonce'}
    source=Path(__file__).with_name('form-page.js').read_text().strip()[:-2]
    expression=Path(__file__).with_name('submit-page.js').read_text().replace('__READER__',source).replace('__APPROVED__',json.dumps(payload))
    reader.command('eval',"document.querySelector('#email').value='changed@example.net'")
    assert reader.command('eval',expression)=={'career_submit':False,'code':'FORM_CHANGED'}
    assert reader.command('eval','window.fixtureClicks || 0')==0
    reader.command('eval',"document.querySelector('#email').value='person@example.org'")
    assert reader.command('eval',expression)['career_submit'] is True
    assert reader.command('eval',expression)=={'career_submit':False,'code':'ALREADY_ATTEMPTED'}
    assert reader.command('eval','window.fixtureClicks')==1
