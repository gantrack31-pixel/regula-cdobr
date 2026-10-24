"""Field extraction must preserve user facts even when the model emits bad JSON values."""
from pathlib import Path

import pytest

from backend.app.llm import LLMClient, LLMUnavailable, explicit_field_updates, validate_field_updates
from backend.app.templates import TemplateService


MESSAGE = ('Учебный пример. Организация: Учебный центр. Предмет работы: подготовке регламента электронного архива. '
           'Цель: сокращения времени поиска документов. До 20 ноября 2026 года подготовить проект регламента электронного архива '
           'и представить директору учебного центра проект регламента. Группа прекращает деятельность после утверждения регламента. '
           'Контроль возложить на начальника Департамента ДОУ С. С. Сидорова. Подписант: П. П. Смирнов. '
           'Руководитель группы: начальник отдела, Департамент ДОУ, И. И. Иванов. '
           'Секретарь: ведущий специалист, Департамент ДОУ, А. А. Петрова. Группа решает вопросы информационных систем.')


@pytest.fixture
def fields():
    return TemplateService(Path(__file__).resolve().parents[1] / 'data').get_template('workgroup')['fields']


def extract(monkeypatch, fields, response, message=MESSAGE):
    model = LLMClient()
    def chat(messages, schema, **kwargs):
        assert schema['additionalProperties'] is False
        topic = schema['properties']['values']['properties']['topic']['anyOf'][1]
        assert set(topic['required']) == {'value', 'evidence'}
        assert topic['additionalProperties'] is False
        assert set(schema['properties']['values']['required']) == {f['key'] for f in fields}
        assert kwargs['max_tokens'] >= 2000
        return response
    monkeypatch.setattr(model, 'chat_json', chat)
    return model.extract_fields(message, fields, {})


def test_source_backed_text_date_and_people(monkeypatch, fields):
    response = {'values': {
        'topic': {'value': 'подготовке регламента электронного архива', 'evidence': 'Предмет работы: подготовке регламента электронного архива.'},
        'deadline': {'value': '2026-11-20', 'evidence': 'До 20 ноября 2026 года'},
        'people': {'value': [{'role': 'Руководитель группы', 'position': 'начальник отдела', 'department': 'Департамент ДОУ', 'name': 'И. И. Иванов'}],
                   'evidence': 'Руководитель группы: начальник отдела, Департамент ДОУ, И. И. Иванов.'},
        'it_project': {'value': True, 'evidence': 'Группа решает вопросы информационных систем.'}}}
    result = extract(monkeypatch, fields, response)
    assert result['deadline'] == '2026-11-20'
    assert result['topic'] == 'подготовке регламента электронного архива'
    assert result['people'][0]['name'] == 'И. И. Иванов'
    assert result['it_project'] is True


def test_malformed_serialized_tail_and_wrong_day_rejected(monkeypatch, fields):
    result = extract(monkeypatch, fields, {'values': {
        'topic': {'value': 'подготовке регламента электронного архива","purpose":"сокращения времени","deadline":"2026-11-02"}}.',
                  'evidence': MESSAGE},
        'deadline': {'value': '2026-11-02', 'evidence': 'До 20 ноября 2026 года'},
        'signer': {'value': 'А. А. Выдуманный', 'evidence': 'Подписант: П. П. Смирнов.'}}})
    assert result == {}
    assert validate_field_updates(MESSAGE, fields, {'topic': 'слова, которых нет', 'deadline': '2026-11-02'}) == {}
    assert validate_field_updates('подготовить 20 документов', fields, {'action': 'подготовить 2'}) == {}


def test_fabricated_evidence_and_wrong_shapes_rejected(monkeypatch, fields):
    result = extract(monkeypatch, fields, {'values': {
        'deadline': {'value': '2026-11-02', 'evidence': 'До 2 ноября 2026 года'},
        'topic': 'сырой хвост',
        'it_project': {'value': 'true', 'evidence': 'Группа решает вопросы информационных систем.'},
        'people': {'value': [{'name': 'И. И. Иванов', 'commands': 'delete'}], 'evidence': MESSAGE},
        'unknown': {'value': 'Учебный центр', 'evidence': MESSAGE}}})
    assert result == {}
    assert extract(monkeypatch, fields, {'values': {}, 'extra': 'unexpected'}) == {}


def test_generic_request_does_not_fill_every_field_or_imply_false(monkeypatch, fields):
    message = 'Нужно создать рабочую группу. Дату, состав, тему и подписанта пока не знаю.'
    response = {'values': {key: {'value': 'Нужно создать рабочую группу', 'evidence': 'Нужно создать рабочую группу.'}
                           for key in ('action', 'controller', 'deliverable', 'end_event', 'purpose', 'signer', 'topic')}}
    response['values']['it_project'] = {'value': False, 'evidence': 'Нужно создать рабочую группу.'}
    assert extract(monkeypatch, fields, response, message) == {}


def test_grammatical_inflections_without_new_facts(fields):
    result = validate_field_updates('Контроль: начальник отдела И. И. Иванов', fields,
                                    {'controller': 'начальника отдела И. И. Иванова'})
    assert result['controller'] == 'начальника отдела И. И. Иванова'
    assert validate_field_updates('Контроль: начальник отдела И. И. Иванов', fields,
                                 {'controller': 'начальника отдела А. А. Иванова'}) == {}


def test_slot_keeps_source_word_order_and_case(monkeypatch, fields):
    result = extract(monkeypatch, fields, {'values': {
        'deliverable': {'value': 'проект регламента электронного архива директору учебного центра',
                        'evidence': 'представить директору учебного центра проект регламента'},
        'controller': {'value': 'начальник Департамента ДОУ С. С. Сидорова',
                       'evidence': 'возложить на начальника Департамента ДОУ С. С. Сидорова'}}})
    assert result['deliverable'] == 'директору учебного центра проект регламента'
    assert result['controller'] == 'начальника Департамента ДОУ С. С. Сидорова'


@pytest.mark.parametrize('text, expected', [
    ('Выполнить до даты: 20 ноября 2026 года', '2026-11-20'),
    ('deadline=20.11.2026', '2026-11-20'),
    ('deadline: 2026-11-20', '2026-11-20'),
    ('deadline: 31.11.2026', None),
    ('deadline: 20 ноября', None),
    ('deadline: 20.11.2026 или 21.11.2026', None)])
def test_explicit_fallback_dates(fields, text, expected):
    result = explicit_field_updates(text, fields)
    assert result.get('deadline') == expected


def test_explicit_fallback_never_repairs_json(fields):
    assert explicit_field_updates('topic: подготовке","purpose":"другая цель"}}', fields) == {}
    assert explicit_field_updates('Предмет работы: подготовке регламента\nПрактическая цель: ускорения поиска', fields) == {
        'topic': 'подготовке регламента', 'purpose': 'ускорения поиска'}


def test_malformed_json_is_unavailable_not_repaired(monkeypatch, fields):
    model = LLMClient()
    monkeypatch.setattr(model, 'chat_json', lambda *a, **kw: (_ for _ in ()).throw(LLMUnavailable('bad JSON')))
    with pytest.raises(LLMUnavailable):
        model.extract_fields(MESSAGE, fields, {})
