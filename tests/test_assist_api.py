"""The persistence boundary rejects unsupported model field updates."""
from test_api import client, login


def test_assist_rejects_model_serialized_tail_and_invented_date(client, monkeypatch):
    login(client)
    draft = client.post('/api/drafts', json={'template_id': 'workgroup', 'values': {'topic': 'сохранённый предмет'}}).json()
    monkeypatch.setattr(client.app.state.llm, 'extract_fields', lambda *a, **kw: {
        'topic': 'подготовке регламента","purpose":"случайное значение"}}',
        'deadline': '2026-11-02', 'signer': 'А. А. Выдуманный'})
    response = client.post(f'/api/drafts/{draft["id"]}/assist', json={
        'message': 'Подготовить регламент до 20 ноября 2026 года.'})
    assert response.status_code == 200, response.text
    result = response.json()
    assert set(result['updated_fields']) == {'deadline', 'action'}
    assert result['draft']['values']['action'] == 'Подготовить регламент'
    assert result['draft']['values']['topic'] == 'сохранённый предмет'
    assert result['draft']['values']['deadline'] == '2026-11-20'
    assert 'signer' not in result['draft']['values']


def test_assist_offline_fallback_normalizes_only_explicit_date(client, monkeypatch):
    login(client)
    draft = client.post('/api/drafts', json={'template_id': 'workgroup', 'values': {}}).json()
    def unavailable(*args, **kwargs):
        raise RuntimeError('offline')
    monkeypatch.setattr(client.app.state.llm, 'extract_fields', unavailable)
    response = client.post(f'/api/drafts/{draft["id"]}/assist', json={
        'message': 'Предмет работы: подготовке регламента\nВыполнить до даты: 20 ноября 2026 года'})
    assert response.status_code == 200, response.text
    result = response.json()
    assert set(result['updated_fields']) == {'topic', 'deadline'}
    assert result['draft']['values']['deadline'] == '2026-11-20'
    assert result['draft']['values']['topic'] == 'подготовке регламента'
