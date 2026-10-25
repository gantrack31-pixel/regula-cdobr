"""Source-bound answers for high-risk procedures in the supplied case.

This is an explicit policy catalogue, not generated prose or an answer cache.
Every answer is projected from the caller's visible, selected source version.
Numbers, deadlines, exceptions and mandatory roles are never stored here.
If a replacement source no longer matches its clause signature, fail closed.
"""
from __future__ import annotations

from dataclasses import dataclass
import re


@dataclass(frozen=True)
class Rule:
    key: str
    source: str
    sections: tuple[str, ...]
    signature: str
    title: str


RULES = {
    'incoming': Rule('incoming', 'instruction', ('5.6',), r'входящ.+?регистр', 'Регистрация входящего документа'),
    'deadline': Rule('deadline', 'instruction', ('6.4',), r'исчислен.+?срок', 'Как считать срок исполнения'),
    'page': Rule('page', 'instruction', ('8.16',), r'номер.+?колонтитул', 'Нумерация страниц'),
    'format': Rule('format', 'instruction', ('8.16',), r'Times\s+New\s+Roman', 'Оформление документа'),
    'review': Rule('review', 'instruction', ('10.3',), r'первичн.+?повторн.+?рассмотрен', 'Срок рассмотрения проекта'),
    'signature': Rule('signature', 'instruction', ('10.15',), r'подписани.+?рабоч', 'Срок подписания'),
    'sequence': Rule('sequence', 'instruction', ('10.6',), r'визирование.+?последовательност', 'Последовательность визирования'),
    'remarks': Rule('remarks', 'instruction', ('10.9',), r'неурегулированн.+?замечан', 'Неурегулированные замечания'),
    'modular': Rule('modular', 'module-guidance', ('1.3',), r'модул.+?согласован', 'Согласование модульного документа'),
    'module_file': Rule('module_file', 'module-guidance', ('3.1',), r'исходн.+?файл', 'Файл проекта и исходный модуль'),
    'project_number': Rule('project_number', 'module-guidance', ('2.1', '5.2'), r'регистрационн.+?номер', 'Когда присваивается номер'),
}


def intent(query: str) -> Rule | None:
    q = query.casefold().replace('ё', 'е')
    has = lambda p: bool(re.search(p, q))
    key = None
    if has(r'модул') and has(r'исходн|карточк.{0,40}модул') and has(r'файл|замен|измен'):
        key = 'module_file'
    elif has(r'регистрационн.{0,15}номер|присваива.{0,20}номер') and has(r'проект|карточк|подпис'):
        key = 'project_number'
    elif has(r'нумерац|номер.{0,20}страниц|страниц.{0,20}номер|нумерова.{0,20}(?:страниц|титульн)'):
        key = 'page'
    elif has(r'шрифт|кегль|интервал|поля.{0,20}(?:страниц|документ)|отступ.{0,20}кра'):
        key = 'format'
    elif has(r'(?:регистр|систем.{0,10}учет|занес.{0,20}(?:учет|систем))') and has(r'входящ|поступ|приш[её]л|пришло|полученн|контрагент'):
        key = 'incoming'
    elif (has(r'срок|исполн|сдать|поручен') and has(r'выходн|воскресен|суббот|празднич|не позднее|«до|["“]до|(?:^|\s)до\s+\d')) or (has(r'\bдо\s+\d') and has(r'не позднее')):
        key = 'deadline'
    elif has(r'визир|виз(?:\b|[аыуе])') and has(r'последовательн|очередност|поряд(?:ок|к)|с кого|кем заканч'):
        key = 'sequence'
    elif has(r'замечан') and has(r'спорн|неурегулир|скры|подписант|разноглас'):
        key = 'remarks'
    elif has(r'модул') and has(r'соглас|маршрут|постоянн|обязанност|перепис|формулировк|измен'):
        key = 'modular'
    elif has(r'рассмотр|рассматр|вернул.{0,30}исправлен'):
        key = 'review'
    elif has(r'подписан|подписант|подписыв') and has(r'срок|сколько|долго|отведен|времен|лежит'):
        key = 'signature'
    return RULES.get(key)


def matches(rule: Rule, row: dict) -> bool:
    return row['document_id'] == rule.source and row['section'] in {'п. ' + s for s in rule.sections}


def missing_named_source(question: str, sources: list[dict]) -> str | None:
    """A reference in a template is not the referenced regulation itself."""
    q = question.casefold().replace('ё', 'е')
    requested = None
    if re.search(r'положени.{0,70}(?:передач|прием).{0,30}дел', q):
        requested = ('Положение о приеме и передаче дел и имущества', r'положени.{0,100}(?:передач|прием)')
    elif re.search(r'(?:перечн|перечен).{0,35}срок.{0,25}хранен|срок.{0,35}счетов.фактур', q):
        requested = ('Перечень документов со сроками хранения', r'перечен.{0,100}(?:срок|хранен)')
    if requested and not any(re.search(requested[1], s['title'].casefold().replace('ё', 'е'))
                             and s.get('kind') != 'template' for s in sources):
        return requested[0]
    return None


def source_answer(rule: Rule, citations: list[dict], query: str) -> dict | None:
    chosen = [c for c in citations if matches(rule, c)]
    text = '\n'.join(c['quote'] for c in chosen)
    if rule.source == 'module-guidance':
        text = re.sub(r'\n\d+\.\s+[А-ЯЁ][^\n]*$', '', text)
    flat = re.sub(r'\s+', ' ', text).strip()
    if not chosen or not re.search(rule.signature, flat, re.I):
        return None
    body = re.sub(r'^\d+(?:\.\d+)+\.\s*', '', flat)
    if rule.key == 'deadline':
        # Examples in the original contain unrelated October dates. The normative
        # paragraph is sufficient; never transplant those dates to the question.
        body = re.split(r'\bНапример\b', body, maxsplit=1)[0].strip()
    elif rule.key == 'page':
        match = re.search(r'При оформлении многостраничного.+?колонтитула документа\.', body, re.I)
        if not match:
            return None
        body = match[0]
    elif rule.key == 'format':
        # Keep the exception next to the general rule, including a page continuation.
        if not re.search(r'сноск', query, re.I):
            match = re.search(r'Основной текст документа оформляется:.+', body, re.I)
            if match:
                body = match[0]
        body = body.replace(' • ', '\n- ')
    elif rule.key == 'review':
        # The last explicitly mentioned type wins for "а распоряжение?".
        types = list(re.finditer(r'приказ|распоряжени|соглашени|методическ|положени|регламент|протокол|информационн.{0,10}письм|письм|порядок|правил', query, re.I))
        if types:
            token = types[-1][0]
            parts = body.split('•')
            selected = [p.strip() for p in parts[1:] if re.search(re.escape(token), p, re.I)]
            if selected:
                body = parts[0].strip() + '\n\n' + '\n'.join('- ' + p for p in selected)
    elif rule.key == 'sequence':
        body = re.sub(r'\s+(\d\))\s*', r'\n\1 ', body)
    refs = ' '.join(f'[{i + 1}]' for i in range(len(chosen)))
    return {'answer': body.strip() + '\n\n' + refs, 'status': 'grounded', 'mode': 'extractive',
            'answer_kind': 'source_rule', 'answer_label': rule.title, 'citations': chosen,
            'suggested_questions': [], 'rule_key': rule.key}
