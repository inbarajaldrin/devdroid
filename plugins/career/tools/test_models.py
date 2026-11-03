"""Role-specific providers/models, installed model discovery, migration and both public entrances."""
import json
import subprocess
import sys

import pytest

import integrations
import models
from runtime import CODE, Refused, read_json, write_json
from test_workflow import api, system


def test_role_routing_is_independent_of_coordinator_and_passes_exact_models(monkeypatch,tmp_path):
    calls=[]
    def run(argv,**kwargs):
        calls.append(argv)
        if argv[0]=='codex':
            from pathlib import Path
            Path(argv[argv.index('-o')+1]).write_text('{"flags":{}}')
            return ''
        return '{"value":"invented response","evidence":[]}'
    monkeypatch.setattr(integrations,'run',run)
    worker=integrations.Agents({'coordinator':{'agent':'codex','session':'invented-thread'},
        'drafter':{'agent':'claude','model':'opus'},'reviewer':{'agent':'codex','model':'synthetic-model'}})
    worker.task('drafter',{});worker.task('reviewer',{})
    assert calls[0][0]=='claude' and calls[0][-2:]==['--model','opus']
    assert calls[1][0]=='codex' and calls[1][calls[1].index('--model')+1]=='synthetic-model'
    worker=integrations.Agents({'drafter':{'agent':'codex','model':'synthetic-model'},'reviewer':{'agent':'claude','model':'sonnet'}})
    worker.task('drafter',{});worker.task('reviewer',{})
    assert calls[-1][0]=='claude' and calls[-1][-1]=='sonnet'


def test_save_models_uses_installed_catalog_and_migrates_both_roles(system, api, monkeypatch):
    original_available=models.available
    catalog=lambda agent,**kwargs:[{'agent':agent,'model':model,'label':model} for model in ('opus','sonnet','synthetic-model')]
    monkeypatch.setattr(models,'available',catalog)
    settings=read_json(system.root/'settings.json');settings['agent']='codex';write_json(system.root/'settings.json',settings)
    assert api('GET','/settings',token='wrong')[0]==401
    assert api('GET','/settings')[1]['drafter']=={'agent':'codex','model':None}
    assert api('POST','/settings',{'role':'drafter','choice':{'agent':'claude','model':'opus'}})[0]==200
    saved=read_json(system.root/'settings.json')
    assert 'agent' not in saved and saved['reviewer']=={'agent':'codex','model':None}
    assert api('POST','/settings',{'role':'reviewer','choice':{'agent':'claude','model':'sonnet'}})[0]==200
    assert api('POST','/settings',{'role':'reviewer','choice':{'agent':'codex','model':'not-listed'}})[0]==400
    assert api('GET','/settings')[1]['reviewer']=={'agent':'claude','model':'sonnet'}
    assert api('POST','/settings',{'role':'agent','choice':{}})[0]==400
    assert api('GET','/models')[1]['models']
    monkeypatch.setattr(models,'available',original_available)
    monkeypatch.setattr(models.shutil,'which',lambda agent:None)
    assert api('GET','/models')[1]['models']==[]


def test_models_cli_and_codex_protocol_are_discovery_only(tmp_path,monkeypatch):
    executable=tmp_path/'codex'
    executable.write_text('#!'+sys.executable+'''\nimport json,sys
for line in sys.stdin:
    req=json.loads(line)
    assert req['method'] in ('initialize','initialized','model/list')
    if req['method']=='initialized':continue
    result={} if req['method']=='initialize' else {'data':[{'model':'synthetic-model','displayName':'Invented model'}],'nextCursor':None}
    print(json.dumps({'id':req['id'],'result':result}),flush=True)
''');executable.chmod(0o700)
    monkeypatch.setattr(models.shutil,'which',lambda agent:str(executable) if agent=='codex' else None)
    assert models.available('codex')==[{'agent':'codex','model':'synthetic-model','label':'Invented model'}]
    import career
    monkeypatch.setattr(models,'catalog',lambda:{'models':[],'errors':[]})
    assert career.main(['--home',str(tmp_path),'models'])==0


def test_setup_migrates_legacy_agent_without_choosing_models(tmp_path):
    from setup import prepare
    write_json(tmp_path/'settings.json',{'agent':'claude'})
    prepare(tmp_path)
    settings=read_json(tmp_path/'settings.json')
    assert settings['drafter']==settings['reviewer']=={'agent':'claude','model':None}
    assert 'agent' not in settings


def test_checker_verifies_both_role_models_and_the_enabled_group(system,monkeypatch):
    import doctor
    from types import SimpleNamespace
    settings=read_json(system.root/'settings.json')
    settings.update(drafter={'agent':'claude','model':'opus'},reviewer={'agent':'codex','model':'synthetic-model'})
    write_json(system.root/'settings.json',settings)
    monkeypatch.setattr(doctor,'script',lambda *a:'/invented/dependency')
    monkeypatch.setattr(doctor.shutil,'which',lambda executable:'/invented/'+executable)
    monkeypatch.setattr(models,'available',lambda agent,**kwargs:[{'model':'opus' if agent == 'claude' else 'synthetic-model'}])
    groups=[]
    monkeypatch.setattr(integrations.Text,'check_group',lambda self,group:groups.append(group) or {})
    def inspect():
        return {row['check']:row for row in doctor.inspect(system.root,run=lambda *a,**k:SimpleNamespace(returncode=0,stdout='{}',stderr=''))['checks']}
    rows=inspect()
    assert rows['drafter']['ok'] and rows['reviewer']['ok'] and rows['WhatsApp group']['ok']
    assert groups==['synthetic-career@g.us']
    settings['reviewer']['model']='unlisted';write_json(system.root/'settings.json',settings)
    assert not inspect()['reviewer']['ok']
    monkeypatch.setattr(doctor.shutil,'which',lambda executable:None if executable == 'codex' else '/invented/'+executable)
    assert 'Install codex' in inspect()['reviewer']['detail']
