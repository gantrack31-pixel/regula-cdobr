"""User confirmation, concurrency and extraction failures at the API boundary."""
from test_api import client, login


def make(client, values=None):
    login(client)
    return client.post('/api/drafts', json={'template_id': 'workgroup', 'values': values or {}}).json()


def test_proposal_does_not_save_and_requires_current_revision(client):
    draft = make(client, {'topic': 'сохранённая тема'})
    base = f'/api/drafts/{draft["id"]}'
    proposal = client.post(base + '/suggest', json={'message': 'Предмет работы: актуализации номенклатуры. Выполнить до даты: 9 марта 2027 года.'}).json()
    assert proposal['values'] == {'topic': 'актуализации номенклатуры', 'deadline': '2027-03-09'}
    assert proposal['evidence']['topic'] in 'Предмет работы: актуализации номенклатуры. Выполнить до даты: 9 марта 2027 года.'
    assert client.get(base).json() == draft
    # Apply only the selected field.
    saved = client.patch(base, json={'values': {'deadline': proposal['values']['deadline']}, 'expected_revision': proposal['base_revision']})
    assert saved.status_code == 200
    assert saved.json()['values']['topic'] == 'сохранённая тема'
    assert 'deadline' in saved.json()['confirmed_fields']
    stale = client.patch(base, json={'values': {'topic': 'старое предложение'}, 'expected_revision': proposal['base_revision']})
    assert stale.status_code == 409
    assert client.get(base).json()['values']['topic'] == 'сохранённая тема'


def test_automatic_fields_need_confirmation_and_old_dates_stay(client):
    draft = make(client, {'deadline': '2027-04-03'})
    base = f'/api/drafts/{draft["id"]}'
    auto = client.post(base + '/assist', json={'message': 'Предмет работы: сверке каталога. Подписант А. С. Ветров.'}).json()['draft']
    assert auto['values']['deadline'] == '2027-04-03'
    assert {'topic', 'signer'} <= set(auto['unconfirmed_fields'])
    assert client.post(base + '/export').headers['X-Draft-Incomplete'] == 'true'
    saved = client.patch(base, json={'values': {}, 'expected_revision': auto['revision'], 'confirm_fields': auto['unconfirmed_fields']}).json()
    assert saved['unconfirmed_fields'] == []


def test_proposals_are_private(client):
    draft = make(client)
    login(client, 'curator')
    assert client.post(f'/api/drafts/{draft["id"]}/suggest', json={'message': 'Цель: тест'}).status_code == 404


def test_conflicting_values_are_not_applied(client):
    draft = make(client)
    result = client.post(f'/api/drafts/{draft["id"]}/suggest', json={'message': 'Выполнить до даты: 1 марта 2027 года. Выполнить до даты: 2 марта 2027 года.'}).json()
    assert 'deadline' in result['conflicts']
    assert 'deadline' not in result['values']


def test_model_capacity_is_bounded(client):
    from backend.app.main import model_slots
    draft = make(client)
    with model_slots, model_slots:
        result = client.post(f'/api/drafts/{draft["id"]}/suggest', json={'message': 'Цель: проверки'})
        assert result.status_code == 429
    assert client.get('/api/health').status_code == 200
