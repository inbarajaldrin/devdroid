"""Match confirmed profile facts to form options and check the sources of drafted claims."""
import json
import re
import unicodedata

DRAFT_CONTRACT = 2


def profile_node(profile, key):
    node = profile
    for part in key.split('.'):
        if isinstance(node,list) and part.isdigit():
            node = node[int(part)] if int(part)<len(node) else None
        elif isinstance(node,dict):
            node = node.get(part)
        else:
            return None
    return node


def profile_value(profile, key):
    node = profile_node(profile,key)
    return None if isinstance(node,dict) and node.get('confirm') else node.get('value') if isinstance(node,dict) else node


def alternatives(node):
    if isinstance(node,dict):
        if node.get('confirm'): return []
        return [node.get('value'),*(node.get('alternatives') or [])]
    return [node]


def option_key(value):
    value=unicodedata.normalize('NFKC',str(value or '')).casefold().replace('(',' ').replace(')',' ')
    return ' '.join(re.sub(r'[^\w\s]','',value).split())


def phone_country(profile, options):
    phone=profile_value(profile,'contact.phone')
    explicit=alternatives(profile_node(profile,'contact.phone_country'))
    countries=alternatives(profile_node(profile,'location.country'))
    choices=[]
    for option in options:
        match=re.fullmatch(r'(.*?)\s*\+(\d+)\s*',option)
        if match: choices.append((option,option_key(match[1]),match[2]))
    if isinstance(phone,str) and phone.strip().startswith('+'):
        digits=re.sub(r'\D','',phone)
        choices=[c for c in choices if digits.startswith(c[2])]
        if choices:
            longest=max(len(c[2]) for c in choices)
            choices=[c for c in choices if len(c[2])==longest]
        if not choices: return None
    elif not any(explicit):
        return None
    for candidates in (explicit,countries):
        matches=[c[0] for c in choices if c[1] in {option_key(v) for v in candidates if v} or c[0] in candidates]
        if len(matches)==1:return matches[0]
        if any(candidates) and candidates is explicit:return None
    return choices[0][0] if len(choices)==1 else None


def degree_key(value):
    value=re.sub(r'\bin[ -]progress\b','',str(value or ''),flags=re.I)
    key=option_key(value)
    return 'doctorate' if key in ('phd','doctor of philosophy','doctor of philosophy phd','doctorate','doctoral','doctoral degree') else key


def degree_answer(profile, index, options):
    for key in (f'education.{index}.degree',*(['answers.degree_level'] if index==0 else [])):
        values=[v for v in alternatives(profile_node(profile,key)) if v]
        for value in values:
            exact=[o for o in options if option_key(o)==option_key(value)]
            if len(exact)==1:return exact[0],key
        for value in values:
            matches=[o for o in options if option_key(o)==option_key(re.sub(r'\bin[ -]progress\b','',str(value),flags=re.I))]
            if len(matches)==1:return matches[0],key
        matches=[o for o in options if degree_key(o) in {degree_key(v) for v in values}]
        if len(matches)==1:return matches[0],key
    return None,None


def office_question(field):
    return field.get('type')=='multiselect' and bool(re.search(r'\boffice',field['name'],re.I) and re.search(r'\bwilling\b',field['name'],re.I))


def motivation_question(field):
    return bool(re.search(r'\b(?:why|motivation|fit|interest(?:s|ed)?)\b',field['name'],re.I))


def draft_material(material):
    person=material['resume']+json.dumps(material['profile'],ensure_ascii=False,default=str)
    rec=material['posting']
    def text(value):
        if isinstance(value,str):return value
        if isinstance(value,dict):return '\n'.join(text(v) for v in value.values())
        if isinstance(value,list):return '\n'.join(text(v) for v in value)
        return ''
    role='\n'.join(text(v) for v in ((rec.get('posting') or {}).get('description'),rec.get('company_info'),rec.get('summary_ai')))
    return person, role


def mixed_evidence(draft, material, field):
    """A supported flat list needs claim attribution, never automatic permission for person claims."""
    if not motivation_question(field) or draft.get('claims') is not None or not draft.get('value'):return False
    evidence=draft.get('evidence')
    if not isinstance(evidence,list) or not evidence:return False
    person,role=draft_material(material)
    if not all(isinstance(q,str) and len(q.strip())>=12 and (q in person or q in role) for q in evidence):return False
    return any(q in person and q not in role for q in evidence) and any(q in role and q not in person for q in evidence)


def draft_supported(draft, material, field):
    """Public evidence supports role claims; every personal claim retains personal evidence."""
    person,role=draft_material(material)
    def quotes(values,source):
        return isinstance(values,list) and bool(values) and all(isinstance(q,str) and len(q.strip())>=12 and q in source for q in values)
    motivation=motivation_question(field)
    personal_claim=re.compile(r'\b(?:I|we)\s+(?:have|built|led|worked|studied|earned|graduated|developed|created|am\s+(?:a|an))\b|\bmy\s+(?:experience|degree|doctorate|skills|background|research|projects)\b',re.I)
    claims=draft.get('claims')
    if claims is None:
        return quotes(draft.get('evidence'),person) or bool(motivation and not personal_claim.search(str(draft.get('value') or '')) and quotes(draft.get('evidence'),role))
    if not isinstance(claims,list) or not claims:return False
    if any(not isinstance(c,dict) or not isinstance(c.get('text'),str) for c in claims):return False
    if ' '.join(' '.join(c['text'] for c in claims).split())!=' '.join(str(draft.get('value') or '').split()):return False
    for claim in claims:
        scope=claim.get('scope')
        if scope=='person':
            if not quotes(claim.get('evidence'),person):return False
        elif scope=='role' and motivation:
            if personal_claim.search(claim['text']) or not quotes(claim.get('evidence'),role):return False
        else:return False
    return True


def free_text(field):
    """A field the person writes in, not one chosen from a list."""
    return field.get("type", "text") in ("text", "textarea") and not field.get("options")


INSTRUCTION = re.compile(r"^\s*(?:please\s+)?(?:you\s+)?(?:can\s+you\s+)?(?:fill|draft|write|propose|answer|do)(?:\s+(?:it|this|that|one))?(?:\s+for\s+me)?(?:\s+yourself)?\s*[.!]?\s*$|^\s*(?:you\s+decide|your\s+call|up\s+to\s+you)\s*[.!]?\s*$", re.I)


def instruction_only(answer):
    """A reply that tells the agent to write the answer ("fill it") rather than giving its content."""
    return isinstance(answer, str) and bool(INSTRUCTION.match(answer))
