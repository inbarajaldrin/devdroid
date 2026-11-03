"""An optional owner-only WhatsApp group; the local app always retains the outcome."""
from integrations import Text, script
from runtime import Refused, now, read_json, write_json


def configure(root, choice, *, name='Career updates', text=None):
    if choice not in ('Enabled', 'Disabled'):
        raise Refused('BAD_DECISION', 'Choose Enabled or Disabled for WhatsApp notifications.')
    settings = read_json(root / 'settings.json', {})
    settings['notifications'] = choice
    write_json(root / 'settings.json', settings)
    if choice == 'Enabled' and not settings.get('notification_group') and not settings.get('notification_group_attempt'):
        # Persist before the external request. An uncertain group creation must never create duplicates on replay.
        settings['notification_group_attempt'] = now()
        write_json(root / 'settings.json', settings)
        try:
            settings['notification_group'] = (text or Text()).create_group(name)
            settings.pop('notification_error', None)
        except Refused as error:
            settings['notification_error'] = {'code':error.code, 'error':str(error)}
            if error.code == 'DEPENDENCY_MISSING':
                settings.pop('notification_group_attempt', None)  # No request reached a transport.
        write_json(root / 'settings.json', settings)
    if choice == 'Enabled' and settings.get('notification_group'):
        try:
            (text or Text()).add_owners(settings['notification_group']['id'])
            settings.pop('notification_error',None)
        except Refused as error:
            settings['notification_error'] = {'code':error.code,'error':str(error)}
        write_json(root / 'settings.json',settings)
    return status(root)


def status(root):
    settings = read_json(root / 'settings.json', {})
    enabled = settings.get('notifications') == 'Enabled'
    issues = []
    if enabled:
        try:
            Text.group_id((settings.get('notification_group') or {}).get('id'))
            script('TEXT_SCRIPT','scripts/text.cjs','.devdroid/text/runtime/scripts/text.cjs')
        except Refused as error:
            issues.append(str(error))
        if settings.get('notification_error'):
            issues.append(settings['notification_error']['error'])
    rows = read_json(root / 'data/notifications.json', [])
    ota = read_json(root / 'data/ota/notification.json')
    if ota:
        rows = [*rows, ota]
    if enabled:
        issues.extend(row.get('error') or 'Notification delivery is unconfirmed.' for row in rows[-20:]
                      if row.get('state') in ('unconfirmed', 'pending'))
    return {'enabled':enabled, 'choice':settings.get('notifications'), 'group':settings.get('notification_group'),
            'issues':list(dict.fromkeys(issues)), 'messages':rows[-20:]}
