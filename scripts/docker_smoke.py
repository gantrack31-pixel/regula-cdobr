"""Repeatable HTTP smoke checks for an isolated local Regula QA container.

Creates only demonstration conversations and drafts in the target instance.
Does not print or persist passwords or session cookies.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import io
import json
from pathlib import Path
import time
import zipfile

import httpx


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', default='http://127.0.0.1:8766')
    parser.add_argument('--output', type=Path, default=Path('output/docker-validation.json'))
    args = parser.parse_args()
    report = {'timestamp': datetime.now(timezone.utc).isoformat(), 'base_url': args.base_url, 'checks': []}

    def record(name, passed, detail=None):
        report['checks'].append({'name': name, 'passed': bool(passed), 'detail': detail})
        print(name, 'PASS' if passed else 'FAIL', str(detail or '')[:250], flush=True)

    with httpx.Client(base_url=args.base_url, timeout=90, trust_env=False) as client:
        deadline = time.monotonic() + 45
        while True:
            try:
                health = client.get('/api/health', timeout=5)
                if health.status_code == 200:
                    break
            except httpx.TransportError:
                pass
            if time.monotonic() >= deadline:
                raise RuntimeError('QA-контур не вышел в готовность за 45 секунд.')
            time.sleep(1)
        record('health', health.status_code == 200, health.json())
        report['health'] = health.json()
        if not health.json().get('demo_mode'):
            raise RuntimeError('Этот сценарий требует отдельный QA-контур с DEMO_MODE=1.')
        page = client.get('/')
        record('frontend production HTML', page.status_code == 200 and 'Регула' in page.text)
        record('unauthenticated sources denied', client.get('/api/sources').status_code == 401)
        login = client.post('/api/auth/demo', json={'role': 'employee'})
        record('demo employee login', login.status_code == 200 and login.json()['role'] == 'employee')
        sources = client.get('/api/sources')
        source_items = sources.json()['sources']
        record('sources seeded', sources.status_code == 200 and len(source_items) == 10, len(source_items))
        templates = client.get('/api/templates')
        template_items = templates.json()['templates']
        record('templates seeded', templates.status_code == 200 and len(template_items) == 7, len(template_items))
        record('admin audit denied', client.get('/api/audit').status_code == 403)
        answer = client.post('/api/chat', json={'message': 'В какой срок регистрируют входящий документ?'})
        data = answer.json()
        report['answer'] = {key: data.get(key) for key in ('status', 'mode', 'elapsed_ms')}
        record('chat and grounded answer', answer.status_code == 200 and data.get('status') == 'grounded'
               and bool(data.get('citations')) and data.get('mode') in {'llm', 'extractive'}, report['answer'])
        record('chat registration citation', any(c['section'] == 'п. 5.6' and c['page'] == 16 for c in data.get('citations', [])))
        original = client.get('/api/sources/instruction/file')
        record('authenticated original PDF', original.status_code == 200 and original.content.startswith(b'%PDF'))
        draft = client.post('/api/drafts', json={'template_id': 'workgroup', 'values': {}})
        draft_data = draft.json()
        record('draft creation', draft.status_code == 200 and bool(draft_data.get('missing_fields')))
        report['draft_id'] = draft_data.get('id')
        exported = client.post('/api/drafts/' + draft_data['id'] + '/export')
        is_zip = zipfile.is_zipfile(io.BytesIO(exported.content))
        record('draft DOCX export', exported.status_code == 200 and is_zip
               and exported.headers.get('x-draft-incomplete') == 'true', {'bytes': len(exported.content), 'incomplete': exported.headers.get('x-draft-incomplete')})
        if is_zip:
            with zipfile.ZipFile(io.BytesIO(exported.content)) as archive:
                document = archive.read('word/document.xml').decode()
                record('export preserved Word content', 'ОБЯЗЫВАЮ' in document and 'НЕ УДАЛЯТЬ' in document)
        unknown = client.post('/api/chat', json={'message': 'Какая у меня зарплата за октябрь?'}).json()
        record('unsupported question refused', unknown.get('status') == 'insufficient' and not unknown.get('citations'))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    return 0 if all(check['passed'] for check in report['checks']) else 1


if __name__ == '__main__':
    raise SystemExit(main())
