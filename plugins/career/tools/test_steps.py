"""One workflow through a wizard with refusing transitions and durable resume."""
import copy
import json

import pytest

import questions
from runtime import Refused, read_json
from test_workflow import Browser, system


class Steps(Browser):
    def __init__(self):
        super().__init__();self.alive=False;self.at=0;self.fail_field=None;self.drop_after=None;self.moves=[]
        self.pages=[{'id':'posting','label':'Posting','fields':[]},
            {'id':'contact','label':'Contact','fields':self.fields[:2]},
            {'id':'replies','label':'Replies','fields':self.fields[2:]},
            {'id':'review','label':'Review','fields':[]}]

    def bind(self,handle):
        if not self.alive:raise Refused('TAB_MISSING','Fake refuses a dead tab.')
        self.handle=handle

    def open(self,url,account,site,handle):
        self.url=url;self.handle=handle;self.alive=True;self.at=0;self.confirmation=False
        self.opened.append((url,account,site));return self.read()

    def read(self):
        if not self.alive:raise Refused('TAB_MISSING','Fake cannot read a dead tab.')
        if self.confirmation:return {'url':self.url+'/confirmation','identity_verified':True,'confirmation':'Application received.'}
        current=self.pages[self.at]
        result={'url':self.url+'/'+current['id'] if self.at else self.url,'identity_verified':True,
            'step':{k:current[k] for k in ('id','label')},'fields':copy.deepcopy(current['fields'])}
        if not self.at:result['apply_control']={'name':'Apply now','enabled':True,'tag':'a','href':self.url+'/contact','in_form':False}
        elif self.at < len(self.pages)-1:result['next_control']={'name':'Next','visible_name':'Next','enabled':True}
        else:result['submit_control']={'name':'Submit application','enabled':True}
        if self.at>1:result['previous_control']={'name':'Back','enabled':True}
        return result

    def fill(self,name,value,kind):
        self.read()
        if self.fail_field==name:raise Refused('READBACK_MISMATCH','The fake refuses this fill.')
        page=self.pages[self.at]
        if not any(f['name']==name for f in page['fields']):raise Refused('FIELD_CONTRACT','Field is on another step.')
        self.writes.append((name,value,kind))
        field=next(f for f in page['fields'] if f['name']==name)
        if kind=='file':
            import hashlib
            from pathlib import Path
            field['value']=Path(value).name
            field['sha256']=hashlib.sha256(Path(value).read_bytes()).hexdigest()
            return {'name':field['value'],'sha256':hashlib.sha256(Path(value).read_bytes()).hexdigest()}
        field['value']=value

    def first(self):
        self.read();self.at=1 if self.at else 0;return self.read()

    def advance(self,key,name):
        control=self.read().get(key)
        if not control or control['name']!=name or not control['enabled']:raise Refused('STEP_CONTROL','Cannot advance this step.')
        if key not in ('apply_control','next_control'):raise Refused('STEP_CONTROL','Never use navigation for a submit.')
        self.moves.append((self.pages[self.at]['id'],name));self.at+=1
        if self.drop_after==self.at:
            self.drop_after=None;self.alive=False;raise Refused('TAB_MISSING','Fake reboot after a transition.')
        return self.read()

    def submit(self,name,approved=None):
        if self.at!=len(self.pages)-1:raise Refused('STEP_CONTROL','Cannot submit from a prior step.')
        if name!=self.read()['submit_control']['name']:raise Refused('STEP_CONTROL','Wrong submit.')
        self.clicks.append(name);self.confirmation=True

    def close(self):
        self.read();self.alive=False;self.closed+=1


def picked(system):
    system.browser=Steps();system.dispatch('pick','job-one',decision='approved')
    return system.dispatch('fill','job-one')


def approve(system):
    for box in system.review('job-one')['boxes']:
        if box['state']!='approved':system.dispatch('box','job-one',name=box['name'],decision='approve')
    system.dispatch('approve-submit','job-one',by='phone', fill_digest=system.review('job-one')["fill_digest"])


def test_steps_apply_fill_review_approval_submit_cover_all_steps(system):
    review=picked(system)
    assert [role for role,_ in system.agents.tasks].count('reviewer')==1
    assert {b['name'] for b in review['boxes']}=={'Contact / Email','Contact / Resume','Replies / Why this role?','Replies / Start date'}
    fill=system.fill('job-one')
    assert [step['id'] for step in fill['steps']]==['contact','replies','review'] and fill['complete']
    assert read_json(system.adir('job-one')/'steps.json')['current']=='review'
    approve(system)
    assert system.dispatch('submit','job-one')['status']=='applied' and len(system.browser.clicks)==1


def test_edit_on_prior_step_is_applied_without_another_review(system):
    picked(system)
    system.dispatch('box','job-one',name='Contact / Email',decision='edit',value='edited@example.org')
    system.dispatch('apply-edits','job-one')
    assert system.browser.pages[1]['fields'][0]['value']=='edited@example.org'
    assert [role for role,_ in system.agents.tasks].count('reviewer')==1
    approve(system);system.dispatch('submit','job-one')


def test_prior_step_tampering_never_uses_the_old_submit_approval(system):
    picked(system);approve(system)
    system.browser.pages[1]['fields'][0]['value']='changed@example.org'
    with pytest.raises(Refused):system.dispatch('submit','job-one')
    assert not system.browser.clicks and not system.record('job-one').get('submission',{}).get('attempted_at')


def test_new_step_invalidates_approval_without_re_reviewing_existing_steps(system):
    picked(system);approve(system)
    system.browser.pages.insert(2,{'id':'extra','label':'Extra','fields':[{'name':'Start date','type':'text','required':True,'value':''}]})
    with pytest.raises(Refused):system.dispatch('submit','job-one')
    review=system.review('job-one')
    assert next(b for b in review['boxes'] if b['name']=='Extra / Start date')['state']=='pending'
    assert 'submit_digest' not in system.record('job-one')['ruling']
    reviews=[c['filled_form'] for role,c in system.agents.tasks if role=='reviewer']
    assert len(reviews)==2 and not system.browser.clicks
    assert [step['id'] for step in reviews[-1]['steps']]==['extra']
    assert [row['name'] for row in reviews[-1]['rows']]==['Extra / Start date']


def test_reboot_between_steps_resumes_saved_drafts_and_releases_dead_tab(system):
    system.browser=Steps();system.browser.drop_after=3
    system.dispatch('pick','job-one',decision='approved')
    with pytest.raises(Refused):system.dispatch('fill','job-one')
    assert (system.adir('job-one')/'plan.json').is_file()
    before=list(system.browser.moves)
    with pytest.raises(Refused) as error:system.dispatch('fill','job-one')
    assert error.value.code=='NAVIGATION_UNCERTAIN' and system.browser.moves==before
    # The person locates the completed transition in the existing task tab; no agent click repeats it.
    system.browser.alive=True;system.browser.at=3
    paths=questions.Paths(questions=system.root/'data/questions',postings=system.root/'data/postings',profile=system.root/'profile.yml',applications=system.root/'data/applications')
    q=next(q for q in questions.list_q(paths) if (q.get('applies_to') or {}).get('human_step')=='NAVIGATION_UNCERTAIN' and q['status']=='open')
    assert questions.answer(paths,q['id'],'Confirmed',by='phone').get('applied')
    review=system.dispatch('fill','job-one')
    assert review['agent_reviewed_at'] and [role for role,_ in system.agents.tasks].count('drafter')==1
    system.browser.alive=False;system.dispatch('release','job-one')
    assert not read_json(system.root/'data/active.json')
