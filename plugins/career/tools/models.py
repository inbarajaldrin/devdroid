"""Discover installed CLI model choices without starting an agent turn."""
import json
import os
import selectors
import shutil
import subprocess
import time

from runtime import Refused


def roles(settings):
    settings = dict(settings)
    legacy = settings.pop('agent', None)
    for role in ('drafter','reviewer'):
        if settings.get(role) is None and legacy in ('claude','codex'):
            settings[role] = {'agent':legacy,'model':None}
        else:
            settings.setdefault(role,None)
    return settings


def choice(value):
    if not isinstance(value,dict) or value.get('agent') not in ('claude','codex') or not isinstance(value.get('model'),str) or not value['model'].strip():
        raise Refused('AGENT_MODEL','Choose an agent and model separately for the drafter and reviewer.')
    return {'agent':value['agent'],'model':value['model'].strip()}


def available(agent, *, run=subprocess.run, popen=subprocess.Popen):
    executable = shutil.which(agent)
    if agent not in ('claude','codex') or not executable:
        raise Refused('AGENT_MISSING','Install the selected agent CLI: '+str(agent))
    if agent == 'claude':
        result = run([executable,'--help'],capture_output=True,text=True,timeout=20)
        if result.returncode:
            raise Refused('MODEL_DISCOVERY','Claude could not report its model aliases.')
        models = [{'agent':'claude','model':model,'label':model.title()} for model in ('opus','sonnet') if model in result.stdout.lower()]
        if not models:
            raise Refused('MODEL_DISCOVERY','The installed Claude CLI did not list Opus or Sonnet.')
        return models
    process = popen([executable,'app-server'],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,bufsize=0)
    selector = selectors.DefaultSelector()
    selector.register(process.stdout,selectors.EVENT_READ)
    buffer = b''
    deadline = time.monotonic()+20
    def request(method, params, number):
        nonlocal buffer
        process.stdin.write((json.dumps({'id':number,'method':method,'params':params})+'\n').encode());process.stdin.flush()
        while time.monotonic()<deadline:
            if b'\n' not in buffer:
                if not selector.select(max(0,deadline-time.monotonic())): break
                chunk = os.read(process.stdout.fileno(),65536)
                if not chunk: break
                buffer += chunk
                if b'\n' not in buffer: continue
            line,buffer = buffer.split(b'\n',1)
            reply = json.loads(line)
            if reply.get('id') == number:
                if 'error' in reply:
                    raise Refused('MODEL_DISCOVERY','Codex refused model discovery; inspect its configuration.')
                return reply['result']
        raise Refused('MODEL_DISCOVERY','Codex did not report its models within 20 seconds.')
    try:
        request('initialize',{'clientInfo':{'name':'career_models','version':'0.1.0'}},0)
        process.stdin.write(b'{"method":"initialized"}\n');process.stdin.flush()
        models, cursor = [], None
        for page in range(32):
            result = request('model/list',{'limit':100,'includeHidden':False,**({'cursor':cursor} if cursor else {})},page+1)
            models.extend({'agent':'codex','model':row['model'],'label':row.get('displayName') or row['model']} for row in result['data'] if not row.get('hidden'))
            cursor = result.get('nextCursor')
            if not cursor:
                return models
        raise Refused('MODEL_DISCOVERY','Codex model pagination did not finish.')
    except (OSError,ValueError,KeyError) as error:
        raise Refused('MODEL_DISCOVERY','Codex returned an unreadable model catalog.') from error
    finally:
        selector.close()
        process.terminate()
        try: process.wait(timeout=5)
        except subprocess.TimeoutExpired: process.kill();process.wait(timeout=5)


def catalog():
    options, errors = [], []
    for agent in ('claude','codex'):
        try: options.extend(available(agent))
        except (Refused,OSError,subprocess.TimeoutExpired) as error: errors.append({'agent':agent,'error':str(error)})
    return {'models':options,'errors':errors,'recommendations':{'drafter':{'agent':'claude','model':'opus'},'reviewer':{'agent':'claude','model':'sonnet'}}}
