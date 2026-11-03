"""Exercise the complete website in the isolated loopback browser fixture."""
import json
import time

from runtime import CODE
from test_form_reader import reader
from test_workflow import api, system, ready, seed


def test_job_inbox_detail_and_questions_render_without_feature_dependencies(reader):
    web = CODE / 'skills/jobs/examples/web'
    posting = {'id':'synthetic-role', 'company':'Example Company', 'title':'Test engineer',
               'status':'new', 'url':'https://careers.example.org/role'}
    boot = """
    window.__errors = [];
    window.addEventListener('error', e => window.__errors.push(e.message));
    window.addEventListener('unhandledrejection', e => window.__errors.push(String(e.reason)));
    location.hash='#/inbox';
    localStorage.setItem('careerapp.token', 'synthetic-token');
    window.fetch = async path => new Response(JSON.stringify(
      path.endsWith('/postings') ? [POSTING] : []
    ), {status:200, headers:{'Content-Type':'application/json'}});
    """.replace('POSTING', json.dumps(posting))
    html = (web / 'index.html').read_text().replace('<script src="app.js" defer></script>', '')
    html = html.replace('</body>', '<script>' + boot + (web / 'app.js').read_text() + '</script></body>')
    reader(html)
    def wait(expression):
        until = time.monotonic() + 10
        while time.monotonic() < until:
            result = reader.command('eval', expression)
            if result:
                return result
            time.sleep(.05)
        raise AssertionError([expression, reader.command('eval', '({errors:window.__errors,text:document.body.innerText,hash:location.hash})')])
    wait("!!document.querySelector('button.company')")
    reader.command('eval', "document.querySelector('button.company').click(); true")
    wait("!!document.querySelector('[data-id=synthetic-role]')")
    reader.command('eval', "location.hash='#/posting/synthetic-role'; true")
    wait("document.querySelector('.pane-detail')?.textContent.includes('Approve') || false")
    reader.command('eval', "location.hash='#/questions'; true")
    wait("document.body.textContent.includes('Saved answers')")
    assert reader.command('eval', 'window.__errors') == []


def test_live_client_preview_edit_comment_and_applied_use_the_real_server(reader, system, api):
    """Source client controls exercise the real routes, exact digest and live refresh."""
    import threading
    from http.server import ThreadingHTTPServer
    import serve
    from runtime import write_json
    ready(system)
    rec=system.record('job-one');system.invalidate(rec);system.save(rec)
    server=ThreadingHTTPServer(('127.0.0.1',0),serve.Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    evaluate=lambda script:reader.command('eval',script)
    def wait(script):
        end=time.monotonic()+18
        while time.monotonic()<end:
            if evaluate(script):return
            time.sleep(.05)
        raise AssertionError([script,evaluate('({text:document.body.innerText,errors:window.__errors || []})')])
    def click(text,root='document'):
        evaluate(f"[...{root}.querySelectorAll('button,a')].find(x=>x.textContent.trim()=={json.dumps(text)}).click();true")
    def refresh():evaluate("window.dispatchEvent(new Event('focus'));true")
    try:
        reader.command('open',f'http://127.0.0.1:{server.server_port}/web/')
        code='synthetic-client-pairing-code'
        write_json(system.root/'.private/phone_pair_code.json',{'code':code,'expires':time.time()+60})
        evaluate("localStorage.removeItem('careerapp.token');location.hash='#/pair';location.reload();true")
        wait("!!document.querySelector('textarea[aria-label=\"Pairing link or token\"]')")
        evaluate("window.__errors=[];window.addEventListener('error',e=>window.__errors.push(e.message));true")
        evaluate("const input=document.querySelector('textarea[aria-label=\"Pairing link or token\"]');input.value='careerapp://pair?host=phone.example.org&code=synthetic-client-pairing-code';input.dispatchEvent(new Event('input',{bubbles:true}));true")
        click('Pair');wait("localStorage.getItem('careerapp.token')==='synthetic-api-token'")
        evaluate("location.hash='#/questions';true")
        wait("!!document.querySelector('a[href=\"#/preview/job-one\"]')")
        assert evaluate("document.querySelector('.pane-list').innerText.includes('Ready to submit')")
        evaluate("document.querySelector('a[href=\"#/preview/job-one\"]').click();true")
        wait("!!document.querySelector('.preview-page .ff')")
        assert evaluate("document.querySelectorAll('.ff button[aria-label^=\"Comment on\"]').length")==len(system.fill('job-one')['rows'])
        assert evaluate("document.querySelector('.preview-head .copy-ref').textContent.includes('Copy ref')")
        field="[...document.querySelectorAll('.ff')].find(s=>s.querySelector('.ff-label')?.textContent.includes('Why this role?'))"
        evaluate(field+".querySelector('.ff-edit').click();true")
        wait("document.querySelector('#dialog').open && !!document.querySelector('.ed-text')")
        evaluate("document.querySelector('.ed-text').value='My exact client edit.';true")
        click('Use my text',"document.querySelector('#dialog')")
        wait("document.querySelector('.preview-page').innerText.includes('On hold')")
        assert next(b for b in system.review('job-one')['boxes'] if b['name']=='Why this role?')['value']=='My exact client edit.'
        system.dispatch('apply-edits','job-one');refresh()
        wait("document.querySelector('.preview-page').innerText.includes('My exact client edit.')")
        evaluate(field+".querySelector('.ff-comment').click();true")
        wait("document.querySelector('#dialog').open && !!document.querySelector('#dialog textarea')")
        evaluate("const t=document.querySelector('#dialog textarea');t.value='Please check this field.';t.dispatchEvent(new Event('input',{bubbles:true}));true")
        click('Send comment',"document.querySelector('#dialog')")
        wait("document.querySelector('.preview-page').innerText.includes('Your comment: being fixed')")
        message=system.messages()[-1]
        assert message['field_name']=='Why this role?'
        system.dispatch('chat',text='Checked the field.',reply=True,resolve=message['n'],posting_id='job-one');refresh()
        wait("!!document.querySelector('.pv-submit:not(:disabled)')")
        click('Approve submit',"document.querySelector('.preview-page')")
        wait("document.querySelector('#dialog').open")
        click('Approve submit',"document.querySelector('#dialog')")
        wait("document.querySelector('.preview-page').innerText.includes('Your agent is submitting')")
        evaluate("document.querySelector('.tabs a[data-tab=applied]').click();true")
        wait("document.querySelector('.pane-list').innerText.includes('Submitting')")
        system.dispatch('submit','job-one');refresh()
        wait("document.querySelector('.pane-list').innerText.includes('Submitted')")
        assert evaluate('window.__errors')==[]
    finally:
        reader.command('open','about:blank');server.shutdown();server.server_close();thread.join()


def test_today_batch_clears_and_action_rows_approve_through_the_real_workflow(reader, system, api):
    import threading
    from http.server import ThreadingHTTPServer
    import serve
    seed(system,'job-two');seed(system,'job-three')
    system.dispatch('batch',operation='set',note='A small review batch.',items=[
        {'id':'job-two','why':'A close match.'},{'id':'job-three','why':'Another relevant team.'}])
    system.dispatch('pick','job-one',decision='approved');system.dispatch('fill','job-one')
    server=ThreadingHTTPServer(('127.0.0.1',0),serve.Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    evaluate=lambda script:reader.command('eval',script)
    def wait(script):
        end=time.monotonic()+18
        while time.monotonic()<end:
            if evaluate(script):return
            time.sleep(.05)
        raise AssertionError([script,evaluate('document.body.innerText')])
    def click(text,root='document'):
        evaluate(f"[...{root}.querySelectorAll('button,a')].find(x=>x.textContent.trim()=={json.dumps(text)}).click();true")
    try:
        reader.command('open',f'http://127.0.0.1:{server.server_port}/web/')
        evaluate("localStorage.setItem('careerapp.token','synthetic-api-token');location.hash='';location.reload();true")
        wait("document.querySelector('.today-count')?.textContent.includes('0 of 2')")
        assert evaluate("document.querySelector('#tbadge').textContent")=='2'
        assert evaluate("document.querySelector('.today-actions').textContent.includes('need your answer')")
        evaluate("document.querySelector('a[href=\"#/today/job-two\"]').click();true")
        wait("!!document.querySelector('.pane-detail button[data-key=a]')")
        evaluate("document.querySelector('.pane-detail button[data-key=a]').click();true")
        wait("document.querySelector('#dialog').open")
        click('Approve',"document.querySelector('#dialog')")
        wait("document.querySelector('.today-count').textContent.includes('1 of 2')")
        assert system.record('job-two')['status']=='approved'
        click('Show cleared · 1')
        assert evaluate("document.querySelectorAll('.today-row.cleared').length")==1
        system.dispatch('box','job-one',name='Why this role?',decision='approve')
        evaluate("location.hash='#/questions';window.dispatchEvent(new Event('focus'));true")
        wait("!!document.querySelector('a.submit-now[href=\"#/preview/job-one\"]')")
        click('Review and submit')
        wait("!!document.querySelector('.preview-page .ff')")
        assert not system.record('job-one')['ruling'].get('submit_approved_at')
        click('Approve submit',"document.querySelector('.preview-page')")
        wait("document.querySelector('#dialog').open")
        click('Approve submit',"document.querySelector('#dialog')")
        wait("document.querySelector('.preview-page').innerText.includes('Your agent is submitting')")
        assert system.record('job-one')['ruling']['submit_digest']==system.review('job-one')['fill_digest']
        assert system.browser.clicks==[]
    finally:
        reader.command('open','about:blank');server.shutdown();server.server_close();thread.join()


def test_floating_chat_unread_attachment_keyboard_delivery_and_no_old_page(reader,system,api):
    import threading
    from http.server import ThreadingHTTPServer
    from runtime import Refused
    import serve
    system.dispatch('chat',text='A synthetic agent reply.',reply=True)
    server=ThreadingHTTPServer(('127.0.0.1',0),serve.Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    def evaluate(script):return reader.command('eval',script)
    def wait(script):
        until=time.monotonic()+15
        while time.monotonic()<until:
            value=evaluate(script)
            if value:return value
            time.sleep(.05)
        raise AssertionError([script,evaluate('({text:document.body.innerText,errors:window.__errors || []})')])
    try:
        reader.command('open',f'http://127.0.0.1:{server.server_port}/web/')
        evaluate("localStorage.setItem('careerapp.token','synthetic-api-token');location.reload();true")
        wait("document.querySelector('#chat-badge')?.textContent==='1' && !document.querySelector('#chat-badge').hidden")
        assert evaluate("getComputedStyle(document.querySelector('#chat-fab')).position")=='fixed'
        assert evaluate("document.querySelectorAll('.tabs a[href*=chat]').length")==0
        evaluate("document.querySelector('#chat-fab').click();true")
        wait("!document.querySelector('#chat-panel').hidden && document.querySelector('#chat-body').innerText.includes('A synthetic agent reply.')")
        assert evaluate("document.querySelector('#chat-badge').hidden")
        evaluate("document.querySelector('#chat-attach-btn').click();true")
        wait("!!document.querySelector('.chat-attach-row')")
        evaluate("document.querySelector('.chat-attach-row').click();true")
        assert 'job: job-one' in evaluate("document.querySelector('#chat-input').value")
        count=len(system.messages())
        assert evaluate("let e=new KeyboardEvent('keydown',{key:'Enter',shiftKey:true,bubbles:true,cancelable:true});document.querySelector('#chat-input').dispatchEvent(e);!e.defaultPrevented")
        assert len(system.messages())==count
        evaluate("document.querySelector('#chat-input').dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',bubbles:true,cancelable:true}));true")
        wait("document.querySelector('#chat-input').value===''")
        assert 'job: job-one' in system.messages()[-1]['text']
        system.agents.message=lambda text:(_ for _ in ()).throw(Refused('DEPENDENCY_MISSING','Synthetic delivery failure.'))
        evaluate("let input=document.querySelector('#chat-input');input.value='<img src=x onerror=alert(1)>';input.dispatchEvent(new Event('input'));document.querySelector('#chat-send').click();true")
        wait("document.querySelector('#chat-body').innerText.includes('Synthetic delivery failure.')")
        assert evaluate("document.querySelector('#chat-body').querySelectorAll('img').length")==0
        evaluate("document.querySelector('#chat-close').click();location.hash='#/inbox';true")
        wait("document.querySelector('.tabs [data-tab=inbox]').getAttribute('aria-current')==='page'")
        assert evaluate("!document.querySelector('#chat-fab').hidden")
        assert not (CODE/'skills/jobs/examples/web/chat.html').exists()
        assert not (CODE/'skills/jobs/examples/web/chat.js').exists()
    finally:
        reader.command('open','about:blank')
        server.shutdown();server.server_close();thread.join()


def test_questions_multiselect_shows_each_fact_and_saves_choices_without_json(reader,system,api):
    import threading
    from http.server import ThreadingHTTPServer
    import serve
    import pytest
    from runtime import Refused
    system.browser.fields.append({'name':'Which offices are you willing to work in?','type':'multiselect','required':True,
                                  'options':['Northport, ZZ','Eastfield, ZZ'],'value':''})
    system.dispatch('pick','job-one',decision='approved')
    with pytest.raises(Refused):system.dispatch('fill','job-one')
    q=api('GET','/questions')[1][0]
    server=ThreadingHTTPServer(('127.0.0.1',0),serve.Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    def evaluate(script):return reader.command('eval',script)
    def wait(script):
        end=time.monotonic()+12
        while time.monotonic()<end:
            if evaluate(script):return
            time.sleep(.05)
        raise AssertionError(evaluate('document.body.innerText'))
    try:
        reader.command('open',f'http://127.0.0.1:{server.server_port}/web/')
        evaluate("localStorage.setItem('careerapp.token','synthetic-api-token');location.hash='#/questions';location.reload();true")
        wait("!!document.querySelector('a[href=\"#/questions/"+q['id']+"\"]')")
        assert evaluate("document.querySelector('#qbadge').textContent")=='1'
        evaluate("document.querySelector('a[href=\"#/questions/"+q['id']+"\"]').click();true")
        wait("document.querySelectorAll('.answer-panel .opt').length===2")
        assert 'JSON' not in evaluate("document.querySelector('.pane-detail').innerText")
        evaluate("[...document.querySelectorAll('.pane-detail button')].find(b=>b.textContent==='Dismiss question').click();true")
        wait("document.querySelector('#dialog').open")
        evaluate("[...document.querySelectorAll('#dialog button')].find(b=>b.textContent==='Dismiss').click();true")
        wait("!![...document.querySelectorAll('.pane-detail button')].find(b=>b.textContent==='Answer anyway')")
        evaluate("[...document.querySelectorAll('.pane-detail button')].find(b=>b.textContent==='Answer anyway').click();true")
        assert evaluate("document.querySelectorAll('.answer-panel .opt').length") == 2
        assert evaluate("document.querySelectorAll('.answer-panel .opt[aria-pressed=true]').length") == 0
        evaluate("document.querySelectorAll('.answer-panel .opt').forEach(i=>i.click());[...document.querySelectorAll('.answer-panel button')].find(b=>b.textContent==='Save choices').click();true")
        wait("document.body.innerText.includes('Change the answer')")
        evaluate("[...document.querySelectorAll('.pane-detail button')].find(b=>b.textContent==='Change the answer').click();true")
        assert evaluate("document.querySelectorAll('.answer-panel .opt[aria-pressed=true]').length")==2
        saved=api('GET','/questions/'+q['id'])[1]
        assert saved['answer']=='Northport, ZZ; Eastfield, ZZ' and saved['applied']
        system.dispatch('fill','job-one')
        assert not system.record('job-one')['ruling'].get('submit_approved_at')
    finally:
        reader.command('open','about:blank')
        server.shutdown();server.server_close();thread.join()
