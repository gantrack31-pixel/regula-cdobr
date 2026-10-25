from pathlib import Path
import pytest
from backend.app.field_evidence import extract_explicit
from backend.app.llm import LLMClient
from backend.app.templates import TemplateService


@pytest.fixture
def templates():
    return TemplateService(Path(__file__).resolve().parents[1] / 'data')


def test_gender_and_roles_are_not_generated(templates):
    fields = templates.get_template('records_officers')['fields']
    result = extract_explicit('Ответственного работника: специалист отдела закупок Л. Н. Зайцева. Подписант К. М. Соколов.', fields)
    assert result['values']['people'] == [{'role': 'Ответственного работника', 'position': 'специалист отдела закупок', 'department': 'отдела закупок', 'name': 'Л. Н. Зайцева'}]
    assert result['values']['signer'] == 'К. М. Соколов'
    assert 'basis' not in result['values']


def test_negation_is_scoped_to_relevant_clause(templates):
    fields = templates.get_template('handover')['fields']
    result = extract_explicit('Срок не меняется, передача меняет доступ к информационным системам.', fields)
    assert result['values']['access_change'] is True
    result = extract_explicit('Передача не меняет доступ к информационным системам. Передача меняет доступ к информационным системам.', fields)
    assert 'access_change' not in result['values']
    assert result['conflicts'] == ['access_change']


def test_text_conflicts_are_not_resolved_by_order(templates):
    fields = templates.get_template('workgroup')['fields']
    result = extract_explicit('Цель: сокращения ошибок. Цель: ускорения подготовки.', fields)
    assert 'purpose' not in result['values']
    assert result['conflicts'] == ['purpose']


def test_negative_cancellation_cannot_fill_old_act(templates, monkeypatch):
    model = LLMClient()
    monkeypatch.setattr(model, 'extract_fields', lambda *args: {'previous_title': 'Прежний акт не отменяем.', 'previous_number': 'Прежний акт не отменяем.'})
    result = model.propose_fields('Прежний акт не отменяем.', templates.get_template('committee')['fields'], {})
    assert result['values'] == {'cancel_previous': False}


def test_readiness_evidence_is_not_a_false_conflict(templates):
    result = extract_explicit('Документ о готовности — акт от 10 декабря 2026 года № 52.', templates.get_template('is_launch')['fields'])
    assert result['values']['readiness_document'] == 'акта от 10 декабря 2026 года № 52'
    assert result['values']['readiness_date'] == '2026-12-10'
    assert result['conflicts'] == []
