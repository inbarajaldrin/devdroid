"""Read-only client views derived from the application workflow, never a second submit gate."""
from pathlib import Path
from datetime import datetime, timezone
import re
from runtime import Refused


def box_key(box):
    from workflow import digest
    return 'field-' + digest(box['name'])[:20]


def box_seen(box):
    from workflow import digest
    return digest(box)


def review(workflow, pid):
    doc = workflow.review(pid)
    boxes = []
    for box in doc.get('boxes', []):
        boxes.append(dict(box, key=box_key(box), kind=box.get('type', 'field'),
                          label=box.get('label') or box['name'], value_changed_at=box_seen(box),
                          comments=[m for m in workflow.messages() if m.get('posting_id') == pid and
                                    m.get('field_name') == box['name'] and m.get('from') == 'user']))
    summary = dict(total=len(boxes), approved=sum(b['state'] == 'approved' for b in boxes),
                   pending=sum(b['state'] != 'approved' for b in boxes),
                   blocked=sum(any(f.get('severity') == 'block' for f in b.get('flags', [])) for b in boxes))
    summary['all_approved'] = bool(boxes) and summary['approved'] == len(boxes) and not summary['blocked']
    return dict(doc, boxes=boxes, summary=summary)


def reference(rec):
    return f"job: {rec['id']} · {rec.get('company', '')} · {rec.get('title', '')}"


def action(workflow, rec):
    pid = rec['id']
    doc = workflow.review(pid)
    comments = [m for m in workflow.messages() if m.get('posting_id') == pid and
                m.get('from') == 'user' and not m.get('resolved_at')]
    try:
        gate = workflow.gate(pid)
    except (Refused, OSError, ValueError) as error:
        gate = [str(error)]
    boxes = doc.get('boxes', [])
    if comments:
        state, why = 'hold', 'Your comment: being fixed'
    elif any(b.get('needs_apply') for b in boxes):
        state, why = 'hold', 'updating the form with your edit'
    elif any(b.get('state') != 'approved' for b in boxes):
        waiting = [b for b in boxes if b.get('state') != 'approved']
        names = ', '.join(re.sub(r'\s*\*\s*$','',b.get('label') or b['name']) for b in waiting[:2])
        state, why = 'answer', f"{len(waiting)} question{'s' if len(waiting) != 1 else ''}: {names}"
    elif gate:
        state, why = 'hold', gate[0]
    elif (rec.get('ruling') or {}).get('submit_digest') == doc.get('fill_digest') and (rec.get('ruling') or {}).get('submit_approved_at'):
        state, why = 'submitting', 'Your agent is submitting it'
    else:
        state, why = 'submit', 'Ready to submit'
    questions = []
    from runtime import read_json
    for path in (workflow.root / 'data/questions').glob('*.json'):
        q = read_json(path,{})
        target = q.get('applies_to') or {}
        if q.get('status') == 'open' and (q.get('posting_id') or target.get('posting_id')) == pid and (target.get('form_facts') or target.get('form_question')):
            questions.append(dict(id=q['id'], label=q['title']))
    return dict(review(workflow, pid)['summary'], action=state, why=why, gate=gate, questions=questions), comments


def posting(workflow, rec):
    """Project fields only into the response; never persist projections to posting files."""
    rec = dict(rec, ref=reference(rec))
    summary, comments = action(workflow, rec) if rec.get('status') == 'ready' else ({}, [])
    rec['review_summary'] = summary or None
    status = rec.get('status')
    attempted = bool((rec.get('submission') or {}).get('attempted_at'))
    if status in ('applied', 'under_review', 'interview', 'offer', 'rejected', 'closed') or attempted or summary.get('action') == 'submitting':
        state = ('submitting' if summary.get('action') == 'submitting' else
                 'unconfirmed' if status == 'submission_unconfirmed' else 'submitted' if status == 'applied' else status)
        labels = {'submitting':'Submitting', 'unconfirmed':'Sending unconfirmed', 'submitted':'Submitted',
                  'under_review':'Under review', 'interview':'Interview', 'offer':'Offer', 'rejected':'Rejected', 'closed':'Closed'}
        sub = rec.get('submission') or {}
        timeline = [dict(e, text=e.get('text') or e.get('event') or '', kind=e.get('kind') or 'history') for e in reversed(rec.get('history') or [])]
        submitted_at = sub.get('confirmed_at') or sub.get('attempted_at')
        try:
            submitted_date = datetime.fromisoformat(submitted_at.replace('Z','+00:00'))
            submitted_date = submitted_date if submitted_date.tzinfo else submitted_date.replace(tzinfo=timezone.utc)
            days_since = max(0, (datetime.now(timezone.utc) - submitted_date).days)
        except (ValueError, AttributeError):
            days_since = None
        rec['application'] = dict(state=state, label=labels.get(state, state), active=state not in ('rejected','closed'),
                                  submitted_at=submitted_at, days_since_submitted=days_since,
                                  timeline=timeline, last_event=timeline[0] if timeline else None,
                                  last_activity_at=timeline[0].get('at') if timeline else submitted_at or (rec.get('ruling') or {}).get('submit_approved_at'))
    else:
        rec['application'] = None
    return rec


def preview(workflow, pid):
    rec = workflow.record(pid)
    fill = workflow.fill(pid) or {}
    review = workflow.review(pid)
    summary, comments = action(workflow, rec)
    attempted = bool((rec.get('submission') or {}).get('attempted_at'))
    submitted = rec.get('status') in ('applied','under_review','interview','offer','rejected','closed')
    fields = {b['name']: b for b in review.get('boxes', [])}
    boxes = []
    for row in fill.get('rows', []):
        box = dict(row, **{k:v for k,v in fields.get(row['name'], {}).items() if k != 'value'})
        box['label'] = re.sub(r'\s*\*\s*$','',str(box.get('label') or box['name']))
        box['edit_value'] = fields.get(row['name'], row).get('value', '')
        if box.get('type') == 'file':
            box['value'] = row.get('upload_name') or Path(str(row.get('value') or '')).name
            box['edit_value'] = box['value']
        box['comments'] = [m for m in comments if m.get('field_name') == row['name']]
        boxes.append(box)
    sections = []
    for box in boxes:
        section_title = box.get('section') or 'Application'
        section = next((s for s in sections if s['title'] == section_title), None)
        if section is None:
            section = dict(title=section_title, groups=[dict(title=None, fields=[])])
            sections.append(section)
        kind = box.get('type', 'text')
        control = {'combobox':'select', 'multiselect':'checks', 'checkbox':'check', 'email':'input',
                   'tel':'input', 'text':'input', 'url':'input', 'number':'input', 'date':'input'}.get(kind, kind)
        value = str(box.get('value') or '')
        owned_file = box.get('file_link') if kind == 'file' else None
        nonempty = bool(value) and not (kind == 'checkbox' and value.casefold() in ('no','false','0','off'))
        field = dict(label=box['label'], value=value, kind='value' if nonempty else 'empty', control=control,
                     required=box.get('required', False), options=value.split('; ') if kind == 'multiselect' and value else box.get('options', []),
                     file=owned_file, box=dict(key=box_key(box), state=box.get('state'), seen=box_seen(fields.get(box['name'], box)),
                          waits=box.get('state') != 'approved', fixing=bool(box.get('needs_apply')),
                          notes=box.get('flags', []), comments=box['comments']))
        if not attempted and not submitted and kind != 'file' and not box.get('human_completed'):
            field['edit'] = dict(value=box.get('edit_value', value), options=box.get('options', []),
                                 multi=kind == 'multiselect', long=kind == 'textarea')
        section['groups'][0]['fields'].append(field)
    pending = sum(b.get('state') != 'approved' for b in boxes)
    return dict(posting_id=pid, company=rec.get('company'), title=rec.get('title'), ref=reference(rec), sections=sections,
                submit_approved_at=(rec.get('ruling') or {}).get('submit_approved_at'),
                warnings=list(dict.fromkeys([str(value) for value in (rec.get('preview_notes') or []) +
                    ((rec.get('summary_ai') or {}).get('flags') or [])])),
                info_notes=review.get('info_notes',[]) + [dict(label=box.get('label',box['name']),text=flag['text']) for box in boxes for flag in box.get('flags',[]) if flag.get('severity') == 'info'],
                screenshots=[step['screenshot'] for step in fill.get('steps',[]) if step.get('screenshot')] +
                    ([dict(file=rec['materials']['confirmation_png'],caption='Confirmation')] if (rec.get('materials') or {}).get('confirmation_png') else []),
                status=rec.get('status'), boxes=boxes, fill_digest=review.get('fill_digest', ''), destinations=review.get('destinations', []),
                submission_terms=fill.get('submission_terms'), gate=summary['gate'], action=summary['action'],
                can_submit=not attempted and not submitted and summary['action'] == 'submit', submitted=submitted,
                read_only=attempted or submitted,
                status_line='Submitted' if submitted else 'Sending is unconfirmed. Inspect the existing tab; never repeat Submit.' if attempted
                    else 'Everything checked, ready to submit' if summary['action']=='submit'
                    else 'On hold: '+summary['why'] if summary['action']=='hold'
                    else f"{pending} answer{'s' if pending != 1 else ''} needed" if summary['action']=='answer'
                    else summary['why'], open_comments=comments)
