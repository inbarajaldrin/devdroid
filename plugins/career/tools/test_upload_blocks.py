"""Grouped native uploads, using only the invented application and an owned loopback browser."""
import hashlib

import pytest

from integrations import OpenCLIBrowser
from runtime import CODE, Refused, read_json, write_json
from test_form_reader import reader
from test_workflow import api, system

HTML=(CODE/'tools/fixtures/searchable-application.html').read_text()


def upload_page(reader):
    reader(HTML)
    groups=reader.command('eval',"[...document.querySelectorAll('.file-upload')].map(e=>e.outerHTML).join('')")
    transitions=reader.command('eval',"document.getElementById('upload-widget-fixture').textContent")
    return '<h1>Application</h1><form id="application">'+groups+'''<button type="submit">Submit application</button></form>
<script>
window.uploadAlternatives=[];window.submitClicks=0;
document.addEventListener('click',e=>{const b=e.target.closest('.file-upload button');if(b && b.getAttribute('aria-label')!=='Remove file')window.uploadAlternatives.push(b.textContent);});
document.querySelector('button[type=submit]').onclick=e=>{e.preventDefault();window.submitClicks++;};
</script>'''.replace('</script>',transitions+'</script>')


def test_upload_metadata_is_one_file_field_without_masking_other_controls(reader):
    html=upload_page(reader)
    # Required-state metadata can be exposed on the referenced label as well as the group.
    page=reader(html.replace('class="label upload-label"','class="label upload-label" aria-required="true"'))
    assert not page['unreadable'],page['unreadable']
    assert [(f['name'],f['type'],f['required']) for f in page['fields']]==[
        ('Resume/CV*','file',True),('Cover letter','file',False),('Optional supporting document','file',False)]
    OpenCLIBrowser._safe(page)
    page=reader(html.replace('Accepted file types:','<div role="combobox" aria-required="true" aria-label="Unaccounted question">Choose</div>Accepted file types:'))
    assert 'Unaccounted question' in page['unreadable']
    with pytest.raises(Refused):OpenCLIBrowser._safe(page)
    page=reader(html.replace('id="resume" class="visually-hidden" type="file"','id="resume" class="visually-hidden" type="file" disabled'))
    with pytest.raises(Refused):OpenCLIBrowser._safe(page)


def test_processing_and_filename_views_keep_only_observed_file_evidence(reader,tmp_path):
    reader(upload_page(reader))
    pdf=tmp_path/'example.pdf';pdf.write_bytes(b'%PDF-1.4\nInvented upload\n%%EOF\n')
    reader.command('eval','window.holdUpload=true;true')
    reader.command('upload','#resume',str(pdf))
    source=(CODE/'tools/form-page.js').read_text()
    pending=reader.command('eval',source)
    OpenCLIBrowser._safe(pending)
    field=next(f for f in pending['fields'] if f['name']=='Resume/CV*')
    assert field['upload_pending'] and not field['upload_native'] and field['value']==''
    reader.command('eval','window.finishUpload();true')
    page=reader.command('eval',source)
    field=next(f for f in page['fields'] if f['name']=='Resume/CV*')
    assert not field['upload_pending'] and field['value']==pdf.name
    assert field['sha256']==hashlib.sha256(pdf.read_bytes()).hexdigest()
    reader.command('eval',"window[Symbol.for('devdroid.career.uploads')].files.delete(document.querySelector('[aria-labelledby=upload-label-resume]'));true")
    unobserved=next(f for f in reader.command('eval',source)['fields'] if f['name']=='Resume/CV*')
    assert unobserved['value']==pdf.name and 'sha256' not in unobserved


@pytest.mark.parametrize('supplied',[False,True])
def test_grouped_resume_and_optional_cover_upload_readback_review_and_digest(reader,system,api,monkeypatch,supplied):
    html=upload_page(reader).replace('class="label upload-label"','class="label upload-label" aria-required="true"')
    reader(html)
    entry=system.root/'sign-in.mjs';entry.write_text('// owned fixture entrance')
    monkeypatch.setenv('SIGN_IN_SCRIPT',str(entry))
    settings=read_json(system.root/'settings.json');settings['live_browser_writes']='Enabled';write_json(system.root/'settings.json',settings)
    if supplied:
        cover=system.root/'letter.pdf';cover.write_bytes(b'%PDF-1.4\nInvented cover letter bytes\n%%EOF\n')
        system.dispatch('save',target={'profile_key':'standard_answers.Cover letter'},value=str(cover))
    system.browser=OpenCLIBrowser(settings,root=system.root,runner=reader.runner)
    rec=system.record('job-one');rec.update(url=reader.url);system.save(rec)
    system.dispatch('pick','job-one',decision='approved')
    review=system.dispatch('fill','job-one')
    rows={row['name']:row for row in system.fill('job-one')['rows']}
    resume=rows['Resume/CV*']
    assert resume['upload_name']=='resume.pdf'
    assert resume['upload_sha256']==hashlib.sha256((system.root/'resume.pdf').read_bytes()).hexdigest()
    assert rows['Cover letter']['value']==(str(cover) if supplied else '')
    assert reader.command('eval',"document.querySelector('[aria-labelledby=upload-label-resume] .file-upload__filename p').textContent")=='resume.pdf'
    assert not reader.command('eval',"!!document.getElementById('resume')")
    assert reader.command('eval',"!!document.querySelector('[aria-labelledby=upload-label-cover] .file-upload__filename')")==supplied
    assert reader.command('eval',"document.getElementById('extra').files.length")==0
    preview=api('GET','/postings/job-one/preview')[1]
    boxes={b['name']:b for b in preview['boxes']}
    assert boxes['Resume/CV*']['value']=='resume.pdf' and boxes['Resume/CV*']['required']
    assert boxes['Cover letter']['value']==('letter.pdf' if supplied else '')
    if supplied:
        assert api('GET','/files/'+boxes['Cover letter']['file_link'])[1]==cover.read_bytes()
    assert api('GET','/files/'+boxes['Resume/CV*']['file_link'])[1]==(system.root/'resume.pdf').read_bytes()
    for box in review['boxes']:
        if box['state']!='approved':system.dispatch('box','job-one',name=box['name'],decision='approve')
    digest=system.review('job-one')['fill_digest']
    system.dispatch('approve-submit','job-one',fill_digest=digest)
    assert system.record('job-one')['ruling']['submit_digest']==digest
    contract=system.browser.read()
    approved=(system.fill('job-one')['rows'],None,reader.url,contract)
    assert system.browser.preflight_submit('Submit application',approved)['career_preflight']
    reader.command('eval',"window[Symbol.for('devdroid.career.uploads')].files.delete(document.querySelector('[aria-labelledby=upload-label-resume]'));true")
    with pytest.raises(Refused):system.browser.preflight_submit('Submit application',approved)
    system.browser.fill('Resume/CV*',str(system.root/'resume.pdf'),'file')
    assert system.browser.preflight_submit('Submit application',approved)['career_preflight']
    # A replacement with the same basename still needs a new approval: bytes are part of the contract.
    changed=system.root/'replacement/resume.pdf';changed.parent.mkdir();changed.write_bytes(b'%PDF-1.4\nChanged invented bytes\n%%EOF\n')
    system.browser.fill('Resume/CV*',str(changed),'file')
    with pytest.raises(Refused):system.browser.preflight_submit('Submit application',approved)
    assert reader.command('eval','window.uploadAlternatives')==[]
    assert reader.command('eval','window.submitClicks')==0 and not reader.posts
