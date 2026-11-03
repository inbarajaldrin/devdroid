"""Optional WhatsApp through a private career group, never a DM or another notice group."""
import json

import pytest

import integrations
import models
from integrations import Text
from runtime import Refused, read_json, write_json
from test_workflow import api, system


@pytest.fixture
def transport(monkeypatch, tmp_path):
    entry=tmp_path/'text.cjs';entry.write_text('// fake entrance')
    monkeypatch.setenv('TEXT_SCRIPT',str(entry))
    state={'calls':[], 'members':[{'id':'owner-one@lid'}], 'offline':False}
    monkeypatch.setattr(Text,'configuration',lambda self:{'owners':['owner-one@lid','owner-two@lid'],
                                                           'noticeChat':'dev-notices@g.us','ownerChat':'owner-one@lid'})
    def run(argv, **kwargs):
        if argv[2]=='request':
            payload=json.loads(kwargs['input']);state['calls'].append(payload)
            if state['offline']: raise Refused('TEXT_UNAVAILABLE','Group is unreachable.')
            if payload['cmd']=='group-create':return json.dumps({'ok':True,'group':{'id':'career-only@g.us','name':payload['subject']}})
            if payload['cmd']=='members':return json.dumps({'ok':True,'participants':state['members']})
            assert payload=={'cmd':'group-add','chat':'career-only@g.us','participant':'owner-two@lid'}
            state['members'].append({'id':payload['participant']})
            return json.dumps({'ok':True})
        state['calls'].append(argv)
        assert argv[2:4]==['send','career-only@g.us']
        return json.dumps({'ok':True,'id':'synthetic-publication'})
    monkeypatch.setattr(integrations,'run',run)
    return state


def test_yes_creates_one_career_group_adds_other_owner_accounts_and_no_replay(system, api, transport):
    settings=read_json(system.root/'settings.json');settings.pop('notification_group');settings['notifications']=None
    write_json(system.root/'settings.json',settings)
    assert api('GET','/notifications',token='wrong')[0]==401
    assert api('POST','/notifications',{'enabled':'yes'})[0]==400
    assert api('POST','/notifications',{'enabled':True})[0]==200
    saved=read_json(system.root/'settings.json')
    assert saved['notification_group']=={'id':'career-only@g.us','name':'Career updates'}
    assert {'cmd':'group-add','chat':'career-only@g.us','participant':'owner-two@lid'} in transport['calls']
    assert api('POST','/notifications',{'enabled':True})[0]==200
    assert len([call for call in transport['calls'] if isinstance(call,dict) and call['cmd']=='group-create'])==1
    system.text=Text()
    assert system.receipt('A private update.')['state']=='sent'
    assert transport['calls'][-1][2:4]==['send','career-only@g.us']
    assert api('POST','/notifications',{'enabled':False})[1]['enabled'] is False
    before=len(transport['calls']);assert system.receipt('Local only.')['state']=='disabled'
    assert len(transport['calls'])==before


def test_missing_group_or_text_never_falls_back_and_failures_show_in_app(system, api, monkeypatch):
    settings=read_json(system.root/'settings.json');settings.pop('notification_group')
    write_json(system.root/'settings.json',settings)
    calls=[];monkeypatch.setattr(integrations,'run',lambda *a,**k:calls.append(a))
    assert system.receipt('Needs attention.')['code']=='NOTIFICATION_GROUP'
    assert not calls and api('GET','/notifications')[1]['issues']
    monkeypatch.setenv('TEXT_SCRIPT',str(system.root/'missing-text.cjs'))
    assert api('POST','/notifications',{'enabled':True})[0]==200
    assert read_json(system.root/'settings.json')['notification_error']['code']=='DEPENDENCY_MISSING'
    assert api('POST','/notifications',{'enabled':False})[1]['issues']==[]
    assert not calls


def test_unreachable_group_and_notice_group_are_not_redirected(system, api, transport):
    system.text=Text()
    settings=read_json(system.root/'settings.json');settings['notification_group']={'id':'career-only@g.us','name':'Career updates'}
    write_json(system.root/'settings.json',settings)
    transport['offline']=True
    assert system.receipt('An update.')['state']=='unconfirmed'
    assert api('GET','/notifications')[1]['issues']
    assert not any(isinstance(call,list) for call in transport['calls'])
    for target in ('me','owner-one@lid','owner-one@s.whatsapp.net','dev-notices@g.us'):
        before=len(transport['calls'])
        with pytest.raises(Refused): Text().send('Never here.',group=target)
        assert len(transport['calls'])==before


def test_uncertain_group_creation_is_not_repeated(system, api, transport, monkeypatch):
    settings=read_json(system.root/'settings.json');settings.pop('notification_group')
    write_json(system.root/'settings.json',settings)
    calls=[]
    def uncertain(self,name):calls.append(name);raise Refused('BAD_RECEIPT','The result is unconfirmed.')
    monkeypatch.setattr(Text,'create_group',uncertain)
    assert api('POST','/notifications',{'enabled':True})[0]==200
    assert api('POST','/notifications',{'enabled':True})[1]['issues']
    assert calls==['Career updates']


def test_text_is_optional_for_install_and_checker_but_enabled_group_is_checked(system, monkeypatch):
    import doctor
    from runtime import CODE
    contract=json.loads((CODE/'dependencies.json').read_text())
    assert 'text' not in [row['skill'] for row in contract['required']]
    assert 'text' in [row['skill'] for row in contract['optional']]
    settings=read_json(system.root/'settings.json');settings['notifications']='Disabled'
    write_json(system.root/'settings.json',settings)
    def missing(env,*args):
        assert env!='TEXT_SCRIPT';raise Refused('DEPENDENCY_MISSING','A fake absent requirement.')
    monkeypatch.setattr(doctor,'script',missing)
    result=doctor.inspect(system.root,offline=True)
    assert next(row for row in result['checks'] if row['check']=='WhatsApp notifications')['ok']
    settings['notifications']='Enabled';settings.pop('notification_group');write_json(system.root/'settings.json',settings)
    result=doctor.inspect(system.root,offline=True)
    assert not next(row for row in result['checks'] if row['check']=='WhatsApp group')['ok']


def test_question_ping_is_local_when_notifications_are_off(system, monkeypatch):
    import questions
    settings=read_json(system.root/'settings.json');settings['notifications']='Disabled'
    write_json(system.root/'settings.json',settings)
    monkeypatch.setattr(Text,'send',lambda *a,**k:pytest.fail('No transport is allowed while off'))
    assert questions.notify('Question waiting.',root=system.root)
    assert read_json(system.root/'data/notifications.json')[-1]['state']=='disabled'


def test_an_alias_whatsapp_refuses_does_not_fail_once_the_owner_is_in(transport, monkeypatch):
    monkeypatch.setattr(Text,'configuration',lambda self:{'owners':['owner-two@lid','alias-of-two@s.whatsapp.net'],
                                                           'noticeChat':'dev-notices@g.us','ownerChat':'owner-two@lid'})
    original=integrations.run
    def run(argv, **kwargs):
        if argv[2]=='request' and json.loads(kwargs['input']).get('participant')=='alias-of-two@s.whatsapp.net':
            raise Refused('GROUP_ADD_FAILED','WhatsApp refused to add that member (400)')
        return original(argv, **kwargs)
    monkeypatch.setattr(integrations,'run',run)
    transport['members'][0]['admin']='superadmin'  # the engine's own account created the group
    Text().add_owners('career-only@g.us')
    assert {'id':'owner-two@lid'} in transport['members']
