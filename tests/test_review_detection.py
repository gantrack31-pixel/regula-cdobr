from backend.app.rules import review_text


def test_order_heading_wins_over_incorrect_directive_formula():
    result = review_text('ПРИКАЗ\nО вводе информационной системы\nОБЯЗЫВАЮ:\n1. Ввести систему.')
    assert result['document_type'] == 'order'
    formula = next(f for f in result['findings'] if f['title'] == 'Нет распорядительной формулы')
    assert 'ПРИКАЗЫВАЮ:' in formula['detail']
    assert any('10 рабочих дней' in step['reason'] for step in result['route']['steps'])


def test_directive_heading_wins_over_incorrect_order_formula():
    result = review_text('РАСПОРЯЖЕНИЕ\nО рабочей группе\nПРИКАЗЫВАЮ:\n1. Создать группу.')
    assert result['document_type'] == 'directive'
    assert any('ОБЯЗЫВАЮ:' in f['detail'] for f in result['findings'])


def test_reference_to_order_does_not_classify_unrelated_text_as_order():
    result = review_text('СЛУЖЕБНАЯ ЗАПИСКА\nПросим подготовить приказ о проведении обучения.')
    assert result['document_type'] == 'unknown'
