"""Independent review crash sweep, retained with its original at-most-one-click assertion."""
import copy, json, os, subprocess, sys, hashlib
from pathlib import Path
import pytest, yaml
import questions, integrations, runtime, workflow as wf
from runtime import Refused, read_json, write_json
from test_workflow import system, seed, ready, Browser, Agents, Text, MATERIAL, png
from test_browser import OpenCLI, browser
from test_steps import Steps, picked, approve
from workflow import Workflow

class Crash(BaseException): pass

class Counter:
    def __init__(s,at=None): s.n=0;s.at=at;s.log=[]
    def tick(s,what):
        s.n+=1;s.log.append(what)
        if s.at==s.n: raise Crash(what)

def workspace(p):
    p.mkdir(parents=True,exist_ok=True)
    (p/"resume.txt").write_text(MATERIAL);(p/"resume.pdf").write_bytes(b"%PDF-1.4\n%%EOF\n")
    (p/"profile.yml").write_text(yaml.safe_dump({"contact":{"email":"person@example.org"},"standard_answers":{"Start date":"2027-01-01"}}))
    write_json(p/"settings.json",{"resume":str(p/"resume.txt"),"resume_pdf":str(p/"resume.pdf"),"account":"demo","resume_mode":"one resume","review_min_words":25,"application_assistance":"Enabled","notifications":"Enabled","recipes":"Enabled","live_browser_writes":"Enabled"})
    return p

def stepbrowser(c):
    b=Steps()
    class Wrapped: pass
    def wrap(name):
        f=getattr(b,name)
        def g(*a,**k):
            c.tick('before '+name);r=f(*a,**k);c.tick('after '+name);return r
        return g
    w=type('W',(),{})()
    for n in ('bind','open','read','fill','first','advance','submit','snapshot','close'):
        setattr(w,n,wrap(n))
    w.real=b
    return w

def lifecycle(root,c,tasks,brow=None):
    w=Workflow(root,browser=brow,agents=Agents(),text=Text())
    return w

ACTIONS=['submit','confirm','fill','apply','approve','submit','confirm','release','fill','apply','approve','submit','confirm']
def recover(w):
    """A careless agent: tries every documented command, repeatedly, after the crash."""
    for act in ACTIONS:
        try:
            if act=='apply':
                for b in w.review('job-one')['boxes']:
                    if b['state']!='approved': w.dispatch('box','job-one',name=b['name'],decision='approve',by='phone')
            elif act=='approve': w.dispatch('approve-submit','job-one',by='phone', fill_digest=w.review('job-one').get("fill_digest"))
            else: w.dispatch(act,'job-one')
        except Refused: pass

@pytest.mark.parametrize("drop_tab",[False,True])
def test_crash_fuzz_steps(tmp_path,monkeypatch,drop_tab):
    real_replace=os.replace
    base=Counter()
    def happy(c,root):
        b=stepbrowser(c)
        w=Workflow(root,browser=b,agents=Agents(),text=Text())
        seed(w,'job-one');w.dispatch('pick','job-one',decision='approved',by='phone')
        w.dispatch('fill','job-one')
        for box in w.review('job-one')['boxes']:
            if box['state']!='approved': w.dispatch('box','job-one',name=box['name'],decision='approve',by='phone')
        w.dispatch('approve-submit','job-one',by='phone', fill_digest=w.review('job-one').get("fill_digest"))
        w.dispatch('submit','job-one')
        return w,b
    monkeypatch.setattr(runtime.os,'replace',lambda a,b:(base.tick('rep '+Path(b).name),real_replace(a,b))[1])
    w,b=happy(base,workspace(tmp_path/'base'))
    assert len(b.real.clicks)==1
    total=base.n
    summary={}
    for k in range(1,total+1):
        c=Counter(k);root=workspace(tmp_path/f'k{k}')
        monkeypatch.setattr(runtime.os,'replace',lambda a,bb,c=c:(c.tick('rep '+Path(bb).name),real_replace(a,bb))[1])
        brow=stepbrowser(c);wfl=Workflow(root,browser=brow,agents=Agents(),text=Text());c.at=None;seed(wfl,'job-one');c.at=k;c.n=0;c.log=[]
        crashed=None
        try:
            wfl.dispatch('pick','job-one',decision='approved',by='phone')
            wfl.dispatch('fill','job-one')
            for box in wfl.review('job-one')['boxes']:
                if box['state']!='approved': wfl.dispatch('box','job-one',name=box['name'],decision='approve',by='phone')
            wfl.dispatch('approve-submit','job-one',by='phone', fill_digest=wfl.review('job-one').get("fill_digest"))
            wfl.dispatch('submit','job-one')
        except Crash as e: crashed=str(e)
        except Refused as e: crashed='refused '+e.code
        c.at=None
        if drop_tab and crashed: brow.real.alive=False
        w2=Workflow(root,browser=brow,agents=Agents(),text=Text())
        recover(w2)
        clicks=len(brow.real.clicks)
        st=w2.record('job-one')
        key=(clicks,st.get('status'))
        summary.setdefault(key,[]).append(c.log[k-1] if k<=len(c.log) else '?')
        assert clicks<=1,(k,crashed)
    print({str(k):(len(v),v[:4]) for k,v in summary.items()})
