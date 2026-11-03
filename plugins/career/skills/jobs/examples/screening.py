#!/usr/bin/env python3
"""Suggest a sourced screening value; never fills a form. Copy beside screening_rules.yaml.

python screening.py --profile profile.yml --question 'Are you currently enrolled?'
Exact profile labels and scope context are required. Missing facts return null.
"""
import argparse
import json
from pathlib import Path
import re

import yaml


def suggest(question, profile, rules, *, country=None, employer=None, application=None):
    # Personal actions take precedence over every structural match.
    if re.search(r'password|verification|one.time|consent|terms|agree|acknowledge|certify|signature|create.*account', question, re.I):
        return {"value": None, "reason": "human action"}
    words = set(re.findall(r'\w+', question.casefold()))
    for rule in rules:
        if not all(set(re.findall(r'\w+', token.casefold())) <= words for token in rule['match']):
            continue
        scope = rule['scope']
        context = {"person": "person", "country": country, "employer": employer, "application": application}.get(scope)
        if not context:
            return {"value": None, "reason": "missing scope context"}
        answers = profile.get('standard_answers', {}) if scope == 'person' else profile.get('screening_answers', {}).get(scope, {}).get(context, {})
        label = rule['profile_label']
        node = answers.get(label)
        value = node.get('value') if isinstance(node, dict) and not node.get('confirm') else node if not isinstance(node, dict) else None
        if value == 'Ask me every time':
            value = None
        return {"value": value, "profile_label": label, "scope": scope, "context": context,
                "reason": "proposal only; compare polarity and options with the live question"}
    return {"value": None, "reason": "no rule"}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile', type=Path, required=True)
    parser.add_argument('--question', required=True)
    for key in ('country', 'employer', 'application'):
        parser.add_argument('--' + key)
    args = vars(parser.parse_args())
    profile = yaml.safe_load(args.pop('profile').read_text()) or {}
    rules = yaml.safe_load(Path(__file__).with_name('screening_rules.yaml').read_text())['rules']
    print(json.dumps(suggest(profile=profile, rules=rules, **args)))
