"""End-to-end API checks use the actual corpus and deterministic no-model mode."""
from pathlib import Path
import shutil

import pytest
from fastapi.testclient import TestClient


class OfflineLLM:
    model = 'test-offline'
    embedding_model = 'test-offline'

    def health(self):
        return {'model': self.model, 'model_available': False, 'embedding_model': self.embedding_model, 'embedding_available': False, 'mode': 'extractive'}

    def chat_json(self, *args, **kwargs):
        raise RuntimeError('Model intentionally unavailable in tests')

    def embed(self, *args, **kwargs):
        raise RuntimeError('No embeddings in deterministic tests')

    def extract_fields(self, *args, **kwargs):
        return {}


@pytest.fixture(scope='module')
def client(tmp_path_factory):
    from backend.app import main
    directory = tmp_path_factory.mktemp('api')
    shutil.copytree(Path(__file__).resolve().parents[1] / 'data' / 'sources', directory / 'sources')
    original = (main.DATA, main.RUNTIME, main.LLMClient, main.DEMO_MODE)
    main.DATA, main.RUNTIME, main.LLMClient, main.DEMO_MODE = directory, directory / 'runtime', OfflineLLM, True
    try:
        with TestClient(main.app) as test_client:
            yield test_client
    finally:
        main.DATA, main.RUNTIME, main.LLMClient, main.DEMO_MODE = original


def login(client, role='employee'):
    response = client.post('/api/auth/demo', json={'role': role})
    assert response.status_code == 200, response.text
    return response.json()


def test_auth_and_csrf(client):
    client.post('/api/auth/logout')
    assert client.get('/api/sources').status_code == 401
    assert client.post('/api/auth/demo', json={'role': 'employee'}, headers={'Origin': 'https://evil.example'}).status_code == 403
    login(client)
    assert client.get('/api/auth/me').json()['role'] == 'employee'
    assert client.get('/api/audit').status_code == 403
    assert client.post('/api/sources/instruction/activate', json={'version_id': 'fake'}).status_code == 403


def test_grounded_answer_and_history_are_private(client):
    login(client)
    result = client.post('/api/chat', json={'message': 'Какой шрифт и размер текста нужно использовать в документе?'} )
    assert result.status_code == 200, result.text
    answer = result.json()
    assert answer['mode'] == 'extractive'
    assert answer['citations'], answer
    cid = answer['conversation_id']
    assert len(client.get('/api/conversations/' + cid).json()['messages']) == 2
    login(client, 'curator')
    assert client.get('/api/conversations/' + cid).status_code == 404


def test_file_validation_and_rbac(client):
    login(client, 'curator')
    invalid = client.post('/api/sources/upload', data={'title': 'Bad', 'kind': 'other', 'access': 'all'}, files={'file': ('bad.docx', b'not a zip', 'application/octet-stream')})
    assert invalid.status_code == 400
    denied = client.post('/api/sources/upload', data={'title': 'Admin', 'kind': 'other', 'access': 'admin'}, files={'file': ('text.txt', 'Тест'.encode(), 'text/plain')})
    assert denied.status_code == 403


def test_source_versions_activation_and_acl(client):
    login(client, 'admin')
    uploaded = client.post('/api/sources/upload', data={'title': 'Закрытая инструкция теста', 'kind': 'instruction', 'access': 'admin'}, files={'file': ('restricted.txt', '1.1. Секретный демонстрационный код КЕДР используется только администраторами.'.encode(), 'text/plain')})
    assert uploaded.status_code == 200, uploaded.text
    record = uploaded.json()
    activated = client.post(f'/api/sources/{record["document_id"]}/activate', json={'version_id': record['version_id']})
    assert activated.status_code == 200, activated.text
    login(client)
    ids = {s['id'] for s in client.get('/api/sources').json()['sources']}
    assert record['document_id'] not in ids
    assert client.get(f'/api/sources/{record["document_id"]}/file').status_code in (403, 404)
    results = client.get('/api/search', params={'q': 'Секретный демонстрационный код КЕДР'}).json()['results']
    assert all(r['document_id'] != record['document_id'] for r in results)


def test_draft_flow_and_export(client):
    login(client)
    templates = client.get('/api/templates').json()['templates']
    assert len(templates) >= 7
    created = client.post('/api/drafts', json={'template_id': 'workgroup', 'values': {}})
    assert created.status_code == 200, created.text
    draft = created.json()
    assert draft['missing_fields']
    exported = client.post(f'/api/drafts/{draft["id"]}/export')
    assert exported.status_code == 200, exported.text
    assert exported.content.startswith(b'PK')
    assert exported.headers['x-draft-incomplete'] == 'true'
    login(client, 'curator')
    assert client.get('/api/drafts/' + draft['id']).status_code == 404


def test_review_and_route(client):
    login(client)
    result = client.post('/api/route', json={'document_type': 'order', 'topic': 'ввод информационной системы', 'it_project': True, 'modular': True})
    assert result.status_code == 200, result.text
    names = ' '.join(x['name'] for x in result.json()['departments']).lower()
    assert 'юрид' in names and 'безопасност' in names and 'технолог' in names
    review = client.post('/api/review/text', json={'text': 'ПРИКАЗ\nО создании группы\nОБЯЗЫВАЮ:\n1. Создать группу.'})
    assert review.status_code == 200, review.text
    assert review.json()['findings']


def test_upload_dynamic_template_becomes_available_only_on_activation(client):
    login(client, 'curator')
    uploaded = client.post('/api/sources/upload', data={'title': 'Новый модуль для демо', 'kind': 'template', 'access': 'all'}, files={'file': ('new-module.txt', 'СЛУЖЕБНАЯ ЗАПИСКА\nТема: {{subject}}\nИсполнитель: {{author}}\n{{body}}'.encode(), 'text/plain')})
    assert uploaded.status_code == 200, uploaded.text
    record = uploaded.json()
    before = client.get('/api/templates').json()['templates']
    assert not any(t.get('source_document_id') == record['document_id'] for t in before)
    activated = client.post('/api/sources/' + record['document_id'] + '/activate', json={'version_id': record['version_id']})
    assert activated.status_code == 200, activated.text
    after = client.get('/api/templates').json()['templates']
    template = next(t for t in after if t.get('source_document_id') == record['document_id'])
    assert {f['key'] for f in template['fields']} >= {'subject', 'author', 'body'}


def test_security_headers_and_host(client):
    result = client.get('/api/health')
    assert result.headers['x-content-type-options'] == 'nosniff'
    assert result.headers['cache-control'] == 'no-store'
    assert client.get('/api/health', headers={'Host': 'untrusted.example'}).status_code == 400


def test_changed_template_requires_draft_revalidation(client):
    login(client, 'curator')
    def upload(text, document_id=None):
        data = {'title': 'Версионный модуль', 'kind': 'template', 'access': 'all'}
        if document_id:
            data['document_id'] = document_id
        response = client.post('/api/sources/upload', data=data,
            files={'file': ('versioned.txt', text.encode(), 'text/plain')})
        assert response.status_code == 200, response.text
        return response.json()
    first = upload('Тема: {{subject}}\nИсполнитель: {{author}}')
    sid = first['document_id']
    assert client.post(f'/api/sources/{sid}/activate', json={'version_id': first['version_id']}).status_code == 200
    template = next(t for t in client.get('/api/templates').json()['templates'] if t.get('source_document_id') == sid)
    draft = client.post('/api/drafts', json={'template_id': template['id'], 'values': {'subject': 'Демо', 'author': 'Тест'}}).json()
    second = upload('Тема: {{subject}}\nИсполнитель: {{author}}\nОснование: {{basis}}', sid)
    assert client.post(f'/api/sources/{sid}/activate', json={'version_id': second['version_id']}).status_code == 200
    assert client.post(f'/api/drafts/{draft["id"]}/export').status_code == 409
    saved = client.patch(f'/api/drafts/{draft["id"]}', json={'values': {'basis': 'Учебный пример'}})
    assert saved.status_code == 200, saved.text
    assert saved.json()['template_version'] != draft['template_version']
    assert client.post(f'/api/drafts/{draft["id"]}/export').status_code == 200


def test_invalid_field_shapes_are_validation_errors_not_server_failures(client):
    login(client)
    for values in ({'people': 'not rows'}, {'people': [{'name': {'nested': 'value'}}]},
                   {'it_project': 'yes'}, {'purpose': 'control\x00character'}):
        response = client.post('/api/drafts', json={'template_id': 'workgroup', 'values': values})
        assert response.status_code == 422, response.text


def test_disabling_demo_rejects_previously_issued_demo_session(client, monkeypatch):
    from backend.app import main
    login(client, 'admin')
    monkeypatch.setattr(main, 'DEMO_MODE', False)
    assert client.get('/api/auth/me').status_code == 401
    assert client.get('/api/audit').status_code == 401
    assert client.post('/api/auth/demo', json={'role': 'admin'}).status_code == 403


def test_chunked_body_is_bounded_and_validation_does_not_echo_password(client):
    import json
    body = json.dumps({'username': 'test', 'password': 'x' * (2 * 1024 * 1024)}).encode()
    response = client.post('/api/auth/login', content=iter([body[:800000], body[800000:]]),
                           headers={'Content-Type': 'application/json'})
    assert response.status_code == 413, response.text[:1000]
    assert len(response.content) < 2000
    secret = 'sensitive-example-' * 50
    response = client.post('/api/auth/login', json={'username': 'test', 'password': secret})
    assert response.status_code == 422
    assert secret not in response.text and len(response.content) < 2000


def test_model_queue_covers_all_generation_endpoints_without_empty_chats(client):
    from backend.app import main
    login(client)
    draft = client.post('/api/drafts', json={'template_id': 'workgroup', 'values': {}}).json()
    count = len(client.get('/api/conversations').json())
    assert main.model_slots.acquire(blocking=False)
    assert main.model_slots.acquire(blocking=False)
    try:
        assert client.post('/api/chat', json={'message': 'Срок рассмотрения приказа?'}).status_code == 429
        assert client.post(f'/api/drafts/{draft["id"]}/assist', json={'message': 'Цель: проверка'}).status_code == 429
        assert client.get('/api/search', params={'q': 'Срок приказа'}).status_code == 429
        assert len(client.get('/api/conversations').json()) == count
    finally:
        main.model_slots.release()
        main.model_slots.release()
