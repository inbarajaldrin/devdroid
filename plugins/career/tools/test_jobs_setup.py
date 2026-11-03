"""Chat setup, functional diagnostics and workspace examples through their real entrances."""
import importlib.util
import json
from pathlib import Path
import re
import subprocess

import pytest
import yaml

import doctor
import questions
from runtime import CODE, Refused, read_json, write_json
from setup import prepare
from test_workflow import Agents, Browser, Text, MATERIAL, ready, system
from workflow import Workflow, digest


def test_chat_setup_saves_rulings_without_a_question_or_source_receipt(tmp_path):
    prepare(tmp_path, platform='darwin')
    resume = tmp_path / 'resume.txt'
    resume.write_text(MATERIAL)
    workflow = Workflow(tmp_path, browser=Browser(), agents=Agents(), text=Text())
    proposal = workflow.dispatch('setup', resume=str(resume))
    assert not list((tmp_path / 'data/questions').glob('*.json'))
    profile = proposal['profile']
    workflow.dispatch('save', target={'setup_object': 'profile'}, value=json.dumps(profile))
    workflow.dispatch('save', target={'setting': 'review_min_words'}, value='50\n')
    assert read_json(tmp_path / 'settings.json')['review_min_words'] == 50
    workflow.dispatch('save', target={'profile_key': 'contact.email'}, value='corrected@example.org')
    assert workflow.dispatch('setup', resume=str(resume))['profile']['contact']['email'] == 'corrected@example.org'
    with pytest.raises(questions.AskError):
        workflow.dispatch('save', target={'setup_object': 'profile'}, value=json.dumps(profile))
    workflow.dispatch('save', target={'setup_object': 'profile', 'replace': True}, value=json.dumps(profile))
    assert yaml.safe_load((tmp_path / 'profile.yml').read_text()) == profile


def test_cli_save_round_trip_and_questions_share_validation(tmp_path):
    prepare(tmp_path, platform='linux')
    value = tmp_path / 'decision.txt'
    value.write_text('50\n')
    result = subprocess.run(['python3', str(CODE / 'tools/career.py'), '--home', str(tmp_path),
                             'save', '--target', '{"setting":"review_min_words"}', '--value-file', str(value)],
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert read_json(tmp_path / 'settings.json')['review_min_words'] == 50


def test_copied_workspace_checker_recognizes_its_own_runtime(tmp_path):
    import os
    prepare(tmp_path, platform='linux')
    result = subprocess.run(['python3', str(tmp_path / 'tools/career.py'), 'check', '--offline'],
                            env=dict(os.environ, CAREER_HOME=str(tmp_path)), capture_output=True, text=True)
    report = json.loads(result.stdout)
    assert result.returncode == 1
    assert next(row for row in report['checks'] if row['check'] == 'workspace')['ok']
    missing = subprocess.run(['python3', '-S', str(tmp_path / 'tools/career.py'), 'check', '--offline'],
                             env=dict(os.environ, CAREER_HOME=str(tmp_path)), capture_output=True, text=True)
    report = json.loads(missing.stdout)
    assert missing.returncode == 1 and not next(row for row in report['checks'] if row['check'] == 'python:yaml')['ok']


def test_chat_resolves_an_individual_fact_but_never_approves_submit(system):
    system.browser.fields.append({'name': 'Current availability', 'type': 'text', 'required': True, 'value': ''})
    system.dispatch('pick', 'job-one', decision='approved')
    with pytest.raises(Refused):
        system.dispatch('fill', 'job-one')
    path = next((system.root / 'data/questions').glob('*.json'))
    question = read_json(path)
    assert question['applies_to'].get('form_question')=='Current availability'
    result = system.dispatch('save', target={'question_id': question['id']},
                             value='January 2027')
    assert result.get('applied') and result['status'] == 'answered'
    system.dispatch('fill', 'job-one')
    assert next(f['value'] for f in system.browser.fields if f['name'] == 'Current availability') == 'January 2027'
    assert not system.record('job-one').get('ruling', {}).get('submit_approved_at')


def test_writing_rules_reach_workers_and_changes_invalidate_fill(system):
    (system.root / 'WRITING.md').write_text('Use sourced claims and the exact question limit.')
    ready(system)
    assert all('writing_rules' in context for role, context in system.agents.tasks if role == 'reviewer')
    (system.root / 'WRITING.md').write_text('Use sourced claims and preserve my approved wording.')
    assert system.gate('job-one')


def test_check_exercises_capabilities_without_question_receipts(system, monkeypatch):
    settings = read_json(system.root / 'settings.json')
    settings.update(drafter={'agent':'claude','model':'opus'},reviewer={'agent':'claude','model':'sonnet'},notifications='Disabled', bridge_site='general', coordinator={'agent':'codex', 'session':'synthetic-thread'},
                    apple_team_id='SYNTHETIC', bundle_id_prefix='org.example', phone_device='synthetic-device',
                    ota_base='https://deploy.example.org')
    write_json(system.root / 'settings.json', settings)
    (system.root / 'companies.yml').write_text('companies: [{name: Example, ats: greenhouse, board: example}]')
    import ota
    ipa = system.root / 'sample.ipa'
    ipa.write_bytes(b'PK synthetic IPA')
    ota.publish(ipa, '1', '7', 'org.example.careerapp', system.root / 'data/ota')
    monkeypatch.setattr(doctor, 'script', lambda *args: '/synthetic/sign-in.mjs')
    monkeypatch.setattr(doctor.shutil, 'which', lambda name: '/synthetic/' + name)
    calls = []
    def run(argv, **kwargs):
        calls.append(argv)
        if argv[:2] == ['node', '--version']:
            out = 'v22.0.0'
        elif 'browser-runtime.mjs' in argv[1]:
            out = json.dumps({'ok':True, 'data':{'version':'2.0.2'}})
        elif '--help' in argv:
            out = 'claude --model opus or sonnet'
        elif 'check' in argv:
            out = json.dumps({'ok':True, 'data':{'account':settings['account'], 'site':'general', 'status':'signed-in', 'contextId':'synthetic-context'}})
        elif argv[0] == 'xcodebuild':
            out = 'Xcode 26'
        elif argv[0] == 'security':
            out = '1) Synthetic Identity (SYNTHETIC)'
        else:
            assert argv[-1] == '--dry-run'
            out = 'Scan completed'
        return subprocess.CompletedProcess(argv, 0, stdout=out, stderr='')
    result = doctor.inspect(system.root, run=run)
    assert result['ok'], result
    assert not list((system.root / 'data/questions').glob('*.json'))
    assert any('check' in call for call in calls) and any('--dry-run' in call for call in calls)
    calls.clear()
    offline = doctor.inspect(system.root, offline=True, run=run)
    assert not offline['ok'] and not offline['live_verified']
    assert not any('check' in call or '--dry-run' in call for call in calls)
    assert {row['check'] for row in offline['checks'] if row.get('unverified')} == {'stock OpenCLI','sign-in context','scanner runs'}


def test_examples_have_complete_clients_and_all_skill_links_resolve(tmp_path):
    skill = CODE / 'skills/jobs'
    for link in re.findall(r'\]\(([^)#]+)(?:#[^)]*)?\)', (skill / 'SKILL.md').read_text()):
        assert (skill / link).is_file(), link
    prepare(tmp_path, platform='darwin')
    assert (tmp_path / 'app/Sources/CareerApp.swift').is_file()
    assert (tmp_path / 'web/app.js').is_file()
    import plistlib
    watcher = plistlib.loads((tmp_path / '.private/services/dev.career.phone-deploy.plist').read_bytes())
    assert watcher['StartInterval'] == 180
    assert watcher['ProgramArguments'][-1].endswith('/tools/phone_deploy.py')


def test_screening_example_uses_only_sourced_scoped_facts_and_stops_for_actions():
    examples = CODE / 'skills/jobs/examples'
    spec = importlib.util.spec_from_file_location('screening_example', examples / 'screening.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    rules = yaml.safe_load((examples / 'screening_rules.yaml').read_text())['rules']
    profile = {'standard_answers': {'Currently enrolled in a degree program?': 'Yes'}}
    assert module.suggest('Are you currently enrolled?', profile, rules)['value'] == 'Yes'
    assert module.suggest('I consent; I am currently enrolled', profile, rules)['reason'] == 'human action'
    assert module.suggest('Are you authorized to work?', profile, rules)['value'] is None
    profile['standard_answers']['Currently enrolled in a degree program?'] = 'Ask me every time'
    assert module.suggest('Are you currently enrolled?', profile, rules)['value'] is None
