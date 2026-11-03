"""Ordered review batches. Selection never changes a posting or grants submit approval."""
import json
from pathlib import Path

from runtime import Refused, now, read_json, slug, write_json


def empty():
    return {'created_at': None, 'note': None, 'items': []}


def load(root):
    try:
        data = read_json(Path(root) / 'data/batch.json', empty())
    except ValueError as error:
        raise Refused('BAD_BATCH', 'The saved batch is not valid JSON.') from error
    if not isinstance(data, dict) or not isinstance(data.get('items'), list) or any(
            not isinstance(item, dict) or not isinstance(item.get('id'), str) for item in data['items']):
        raise Refused('BAD_BATCH', 'The saved batch must contain an items list with posting ids.')
    return data


def resolve(workflow, data):
    from presentation import posting
    open_, cleared, missing = [], [], []
    for item in data['items']:
        try:
            rec = workflow.record(item['id'])
            if any(not isinstance(rec.get(key), str) for key in ('id', 'company', 'title', 'status')):
                raise Refused('NOT_FOUND', 'Incomplete posting.')
        except (Refused, OSError, ValueError):
            missing.append(item['id'])
            continue
        target = open_ if rec['status'] in ('new', 'enriched') else cleared
        target.append({'why': item.get('why'), 'record': posting(workflow, rec)})
    return dict(created_at=data.get('created_at'), note=data.get('note'), items=open_ + cleared,
                open=len(open_), cleared=len(cleared), missing=missing)


def change(workflow, operation='show', items=None, note=None):
    if operation == 'show':
        return resolve(workflow, load(workflow.root))
    if operation not in ('set', 'add', 'clear'):
        raise Refused('BAD_BATCH', 'Choose batch set, add, show or clear.')
    if note is not None and not isinstance(note, str):
        raise Refused('BAD_BATCH', 'The batch note must be text.')
    cleaned = []
    if operation != 'clear':
        if not isinstance(items, list) or not items:
            raise Refused('BAD_BATCH', 'Choose postings with a one-line why for each.')
        for item in items:
            if not isinstance(item, dict):
                raise Refused('BAD_BATCH', 'Each batch item needs an id and a why.')
            pid = slug(item.get('id'))
            rec = workflow.record(pid)
            if any(not isinstance(rec.get(key), str) for key in ('company', 'title', 'status')):
                raise Refused('BAD_BATCH', 'Choose a complete posting record.')
            why = item.get('why')
            if not isinstance(why, str) or not why.strip():
                raise Refused('BAD_BATCH', 'Give a one-line why for each posting.')
            if pid in {row['id'] for row in cleaned}:
                raise Refused('BAD_BATCH', 'Choose each posting once.')
            cleaned.append(dict(id=pid, why=' '.join(why.split()), added_at=now()))
    data = load(workflow.root) if operation == 'add' else empty()
    if operation != 'clear':
        data['created_at'] = data.get('created_at') or now()
        if operation == 'set' or note is not None:
            data['note'] = ' '.join((note or '').split()) or None
        existing = {row['id']: row for row in data['items']}
        for item in cleaned:
            if item['id'] in existing:
                existing[item['id']]['why'] = item['why']
            else:
                data['items'].append(item)
    write_json(workflow.root / 'data/batch.json', data)
    return resolve(workflow, data)


def cli_items(ids, why_file):
    """Read ordered JSON (id -> why or item list) or TSV, without another CLI or storage path."""
    if why_file is None:
        raise Refused('BAD_BATCH', 'Supply --why-file with a reason for every posting.')
    try:
        text = why_file.read_text()
        if why_file.suffix.lower() == '.json' or text.lstrip().startswith(('{', '[')):
            data = json.loads(text)
            rows = [{'id': key, 'why': value} for key, value in data.items()] if isinstance(data, dict) else data
        else:
            rows = [dict(zip(('id', 'why'), line.split('\t', 1))) for line in text.splitlines()
                    if line.strip() and not line.lstrip().startswith('#')]
        if not isinstance(rows, list) or any(not isinstance(row, dict) or not isinstance(row.get('id'), str) for row in rows):
            raise ValueError('Expected ordered ids and reasons.')
        if ids:
            reasons = {row['id']: row.get('why') for row in rows}
            rows = [{'id': pid, 'why': reasons.get(pid)} for pid in ids]
        return rows
    except (OSError, ValueError) as error:
        raise Refused('BAD_BATCH', 'Read --why-file as JSON items, an id-to-why object, or id<TAB>why lines.') from error
