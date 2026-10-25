"""Critical source rules work offline and retain negations and exceptions."""
import pytest
from test_knowledge import corpus


@pytest.mark.parametrize('message,section,terms', [
    ('Письмо от поставщика пришло ночью. Когда занести в систему учета?', '5.6', ['день его поступления', 'следующего рабочего дня', 'до завершения регистрации запрещается']),
    ('Последний день исполнения выпал на воскресенье. Можно в понедельник?', '6.4', ['предшествующий', 'рабочий']),
    ('До 18 ноября и не позднее 18 ноября — одинаково?', '6.4', ['не включает указанную дату', 'включает указанную дату']),
    ('В какой очередности собираются визы?', '10.6', ['Департамента ДОУ', 'координирующее']),
    ('Можно скрыть неурегулированные замечания от подписанта?', '10.9', ['замечан']),
])
def test_critical_norms(corpus, message, section, terms):
    result = corpus.answer(message)
    assert result['status'] == 'grounded'
    assert result['answer_kind'] == 'source_rule'
    assert all(c['section'] == 'п. ' + section for c in result['citations'])
    assert all(term in result['answer'] for term in terms), result['answer']
    if section == '6.4':
        assert '13 октября' not in result['answer']


def test_reference_is_not_missing_regulation(corpus):
    result = corpus.answer('Какие пункты Положения о передаче дел разрешают внешнего консультанта?')
    assert result['status'] == 'insufficient'
    assert 'нет документа' in result['answer']


def test_model_cannot_invent_quote_or_source(corpus):
    citations = corpus.search('Где располагается виза на бумажном документе?')
    for claim in ({'source_id': citations[0]['id'], 'quote': 'Регистрация в течение ста двадцати рабочих дней.'},
                  {'source_id': 'invented', 'quote': citations[0]['quote']}):
        assert corpus._validate_claims({'status': 'grounded', 'claims': [claim], 'question': ''}, citations) is None
