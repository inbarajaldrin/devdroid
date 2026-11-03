"""Install declared plugin dependencies through the stock plugin CLI, without vendoring."""
import argparse
import json
import os
from pathlib import Path

from integrations import run, script
from runtime import CODE, Refused


def install(*, marketplace=None, runner=run):
    results = []
    entries = json.loads((CODE / 'dependencies.json').read_text())['required']
    # Preflight incubation names before making any installation changes.
    resolved = []
    for entry in entries:
        try:
            path = script(entry['env'], entry['entrance'], entry['fallback'])
        except Refused:
            if os.environ.get(entry["env"]):
                raise
            path = None
        market = entry.get('marketplace') or marketplace
        if path is None and not market:
            raise Refused('DEPENDENCY_CATALOG', 'Register a catalog containing the required plugins, then supply --marketplace. Nothing was installed.')
        resolved.append((entry, path, market))
    for entry, path, market in resolved:
        if path is None:
            runner(['claude', 'plugin', 'install', entry['plugin'] + '@' + market])
        results.append({'plugin':entry['plugin'], 'state':'available' if path else 'installed', 'path':path})
    return results


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--marketplace', help='Registered catalog containing the required plugins')
    try:
        print(json.dumps(install(marketplace=parser.parse_args().marketplace), indent=2))
    except Refused as error:
        parser.exit(1, error.code + ': ' + str(error) + '\n')
