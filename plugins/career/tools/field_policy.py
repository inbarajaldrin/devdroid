"""Classify personal browser actions independently of where an answer came from."""
import re

ACTION = re.compile(r'\b(?:agree(?:ment)?|accept(?:ance)?|acknowledge(?:ment)?|certif(?:y|ication)|authori[sz]e|sign|consent|attest|swear|affirm)\b', re.I)
EXPERIENCE = re.compile(r'\b(?:experience|project|implement(?:ed|ation)?|develop(?:ed|ment)?|management|programming|source code)\b', re.I)
REQUEST = re.compile(r'\b(?:i|hereby|please|you|your|by typing|sign here|sign below)\b', re.I)
LEGAL_OBJECT = re.compile(r'\b(?:terms|privacy|policy|disclosure|declaration|background check|processing|signature|certification|above|true)\b', re.I)


def action_text(text, *, context=False, choice=False):
    text = re.sub(r'\s+', ' ', str(text or '')).strip(' *?!.').casefold()
    if not text:
        return False
    if LEGAL_OBJECT.search(text) and re.match(r'^i\b.*\b(?:have read|read|understand|understood)\b', text):
        return True
    if EXPERIENCE.search(text) and not re.search(r'\b(?:i|you|please)\s+(?:hereby\s+)?(?:agree|accept|acknowledge|certify|authori[sz]e|sign|consent)\b|by typing', text):
        return False
    if ACTION.search(text) and (choice or (REQUEST.search(text) and (LEGAL_OBJECT.search(text) or not EXPERIENCE.search(text)))):
        return True
    if EXPERIENCE.search(text):
        return False
    if re.search(r'\b(?:password|passphrase|passcode|captcha|authenticator|otp|totp|2fa|mfa|two[- ]factor|one[- ]time)\b', text):
        return True
    if re.search(r'\b(?:create|register|sign up)\b.*\baccount\b', text):
        return True
    if re.search(r'\b(?:user\s*name|security question|verification token|verification number|authentication token)\b', text):
        return True
    if re.search(r'\bcode\b', text) and not re.search(r'\b(?:postal|zip|area|country|source|programming)\b', text) and text != 'pin code':
        return True
    if text in ('verification','authentication','pin','code'):
        return True
    if not context and (re.search(r'\b(?:signature|initials|declaration|certification|consent)\b', text) or
                        re.fullmatch(r'(?:candidate )?(?:data (?:privacy|processing)|privacy (?:notice|policy)|terms(?:\s*(?:&|and)\s*conditions)?|gdpr|i understand)', text)):
        return True
    if not context and ACTION.search(text) and (LEGAL_OBJECT.search(text) or re.match(r'^(?:please|i|sign|agree(?:ment)?|accept(?:ance)?|acknowledge(?:ment)?|confirm your acceptance)\b', text)):
        return True
    return False


def personal_control(field):
    if field.get('human_only') or field.get('type') in ('password','checkbox'):
        return True
    options=field.get('options') or []
    choice=bool(field.get('required') and (options or re.match(r'^(?:do|are|will|would|have|can)\s+you\b', str(field.get('name','')), re.I)))
    if any(action_text(field.get(key,''), choice=choice) for key in ('name','visible_name','section')):
        return True
    return any(action_text(option, choice=True) for option in options) or action_text(field.get('context',''), context=True)


def multiselect_value(value, options):
    """Exact option labels, in form order. Commas belong to labels; semicolons separate selections."""
    from runtime import Refused
    if isinstance(value, str):
        value = [part.strip() for part in value.split(';') if part.strip()]
    if not isinstance(value, list) or any(not isinstance(v,str) or v not in options for v in value) or len(set(value)) != len(value):
        raise Refused('OPTION_MISMATCH', 'Choose exact visible options for this multi-select question.')
    return '; '.join(option for option in options if option in value)
