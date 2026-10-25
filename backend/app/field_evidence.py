"""Conservative extraction of explicit facts, with a source span for every field.

Names and roles are copied, never inflected by a language model. Unknown facts
stay unknown. A proposal is separate from saving/confirming a document.
"""
from __future__ import annotations

from datetime import date
import re
import unicodedata


MONTHS = dict(zip(('января февраля марта апреля мая июня июля августа сентября октября ноября декабря').split(), range(1, 13)))
DATE = r'(?:\d{4}-\d{2}-\d{2}|\d{1,2}[./]\d{1,2}[./]\d{4}|\d{1,2}\s+(?:' + '|'.join(MONTHS) + r')\s+\d{4}(?:\s+года|\s+г\.)?)'
NAME = r'(?:[А-ЯЁ]\.[ \t]*[А-ЯЁ]\.[ \t]*[А-ЯЁ][а-яё]+(?:-[А-ЯЁ][а-яё]+)?|(?!(?:Подписант|Подписывает|Подпишет|Контролер|Директор|Инженер|Руководитель|Секретарь|Председатель)\b)[А-ЯЁ][а-яё]+(?:-[А-ЯЁ][а-яё]+)?[ \t]+[А-ЯЁ]\.[ \t]*[А-ЯЁ]\.|[А-ЯЁ][а-яё]+[ \t]+[А-ЯЁ][а-яё]+[ \t]+[А-ЯЁ][а-яё]+)'
UNKNOWN = r'не\s+(?:зна\w*|извест\w*|определ\w*|указан\w*|выбран\w*|установлен\w*)|пока\s+нет|неизвест\w*|уточн(?:им|ю|ить)|не\s+могу\s+назвать'
ROLE = r'(?:руководител[ья](?:\s+(?:аудиторской\s+)?группы|\s+комиссии)?|секретар[ья](?:\s+группы|\s+комитета)?|председател[ья]|заместител[ья]\s+председателя|аудитор|член(?:а)?(?:\s+группы|\s+комиссии|\s+комитета)?|ответственно(?:го|й|му)\s+работник[ау]?)'
DEPARTMENT = r'\b(?:департамент(?:а|у|ом|е)?|отдел(?:а|у|ом|е)?|управлени[еяю]|служб[аыуе])\b'


def norm(text: str) -> str:
    return ' '.join(unicodedata.normalize('NFC', text).casefold().replace('ё', 'е').split())


def source_dates(text: str) -> set[str]:
    result = set()
    for match in re.finditer(DATE, text, re.I):
        value = match[0].casefold()
        try:
            if re.match(r'\d{4}-', value):
                result.add(date.fromisoformat(value).isoformat())
            elif re.match(r'\d+[./]', value):
                d, m, y = re.split(r'[./]', value)
                result.add(date(int(y), int(m), int(d)).isoformat())
            else:
                d, month, y = value.split()[:3]
                result.add(date(int(y), MONTHS[month], int(d)).isoformat())
        except (ValueError, KeyError):
            continue
    return result


def sentences(message: str) -> list[str]:
    """Split clauses without splitting initials, decimal dates or abbreviations."""
    result, start = [], 0
    for match in re.finditer(r'[.!?;](?:\s+|$)|[\r\n]+', message):
        end = match.start() + 1
        if message[match.start()] == '.' and re.search(r'(?:\b[А-ЯЁA-Z]|\bг|\bгг)\.$', message[start:end]):
            continue
        part = message[start:end].strip()
        if part:
            result.append(part)
        start = match.end()
    if message[start:].strip():
        result.append(message[start:].strip())
    return result


def clean(text: str) -> str:
    return text.strip(' \t\r\n:;—-').rstrip('.;').strip().strip('«»“”"')


def names(text: str) -> list[str]:
    return [m[0] for m in re.finditer(NAME, text)]


def people_from_message(message: str) -> tuple[list[dict], str]:
    people, evidence = [], []
    for sentence in sentences(message):
        person_names = list(re.finditer(NAME, sentence))
        if len(person_names) != 1:
            continue  # Ambiguous multi-person sentence needs an explicit table.
        person = person_names[0]
        before, after = sentence[:person.start()], sentence[person.end():]
        role = re.search(ROLE, before, re.I)
        if not role or re.search(r'подписант|подписывает|контрол', before[:role.start()], re.I):
            continue
        position = clean(before[role.end():])
        if position.startswith(('—', '-', ':')):
            position = clean(position)
        parts = [clean(p) for p in position.split(',') if clean(p)]
        department = ''
        if len(parts) > 1 and re.search(DEPARTMENT, parts[-1], re.I):
            department, position = parts[-1], ', '.join(parts[:-1])
        elif re.search(DEPARTMENT, after, re.I):
            department = clean(after).lstrip(', ').strip()
        else:
            found = re.search(DEPARTMENT + r'.*', position, re.I)
            if found:
                department = clean(found[0])
        # Keep the full, original job title; never shorten "начальник отдела".
        if re.fullmatch(DEPARTMENT, department, re.I):
            department = ''  # "Директор департамента" does not name a department.
        row = {'role': role[0], 'position': position, 'department': department, 'name': person[0]}
        if not any(norm(p['name']) == norm(row['name']) for p in people):
            people.append(row)
            evidence.append(sentence)
    return people, '\n'.join(evidence)


# Field-specific cues are necessary evidence of meaning, not just shared words.
CUES = {
    'organization': r'организаци[яи]\s*[:—-]|наименование организации|учебн\w*\s+центр',
    'topic': r'предмет работы|рабоч\w*\s+групп\w*.{0,60}\bпо\b|тема\s*[:—-]',
    'purpose': r'\bцель\b|\bв целях\b',
    'action': r'разработать|подготовить|проанализировать|сформировать|проверить|оценить',
    'deliverable': r'представить|результат\s*[:—-]',
    'end_event': r'прекращ|завершает деятельность',
    'controller': r'контрол|возложить на',
    'signer': r'подписант|подписыва|подпишет|за подписью',
    'basis': r'нормативн\w*\s+основ|\bоснование\b|на основании|в связи с|соблюдения требований',
    'system_name': r'информационн\w*\s+систем|\bИС\b|наименование.{0,20}компонент',
    'short_name': r'сокращенн|сокращен[но]+|далее',
    'readiness_document': r'документ о готовности|акт.{0,30}готовност|на основании',
    'readiness_date': r'документ о готовности|акт.{0,30}готовност|готовност.{0,30}акт',
    'development_document': r'техническ\w*\s+задан|документ.{0,30}работ|в соответствии с',
    'customer': r'функциональн\w*\s+заказчик',
    'responsible': r'ответственн.{0,25}за исполнен|исполнение поруч',
    'changes_recipient': r'сведени.{0,20}изменен|получатель сведений',
    'coordinator': r'координаци|координатор',
    'transferor': r'сдающ|передающ|\bсдать\b',
    'transferee': r'принимающ|\bпринять\b',
    'property': r'передаются|передаваем.{0,25}(?:дела|имущество)|имущество\s*[:—-]',
    'transfer_source': r'источник передачи|\bпринять\b.+\bот\b',
    'representative': r'представител',
    'act_name': r'утвердить|название документа|наименование акта',
    'department': r'ответственн\w*\s+подразделен|реализу(?:ется|ются|ет)|реализующее проект подразделение',
    'publication_place': r'разместить|место размещения|опубликовать',
    'process_name': r'аудит.{0,30}процесс|проверяем.{0,20}процесс|проверить процессы',
    'owner': r'предостав.{0,30}доступ|доступ.{0,30}обеспеч',
    'report_recipient': r'отчет.{0,30}(?:представить|направить)|адресат отчета|представить отчет',
    'plan_owner': r'владелец корректир|план.{0,40}поручить|подготовку плана',
    'projects': r'проект[ауоы]?[вм]?\s*[:«—-]|наименовани.{0,20}проект',
    'previous_act': r'отмен|утративш',
    'previous_date': r'отмен|утративш|прежн',
    'previous_number': r'отмен|утративш|прежн',
    'previous_title': r'отмен|утративш|прежн',
}


def field_supported(key: str, evidence: str, label: str = '') -> bool:
    if re.search(UNKNOWN, evidence, re.I) or len(evidence) > 2500:
        return False
    if key.startswith('previous_') and re.search(r'\bне\s+отмен|не\s+призна', evidence, re.I):
        return False
    cue = CUES.get(key)
    if not cue:
        cue = re.escape(label) if label else r'(?!)'
    return bool(re.search(cue, evidence, re.I))


def extract_explicit(message: str, fields: list[dict]) -> dict:
    allowed = {f['key']: f for f in fields}
    values, evidence = {}, {}
    clauses = sentences(message)

    def add(key, value, quote):
        if key not in allowed or value is None or (isinstance(value, str) and not value.strip()):
            return
        unknown = UNKNOWN if not isinstance(value, bool) else r'не\s+(?:зна\w*|извест\w*)|неизвест\w*'
        if re.search(unknown, quote, re.I):
            return
        if key in values and values[key] != value:
            values.pop(key, None); evidence.pop(key, None)
            conflicts.add(key)
            return
        if key not in conflicts:
            values[key], evidence[key] = value, quote

    conflicts = set()
    # Explicit form labels work for custom templates too, with literal values.
    for field in fields:
        for sentence in clauses:
            if field['type'] not in {'text', 'textarea', 'date'}:
                continue
            match = re.match(r'\s*(?:' + re.escape(field['key']) + '|' + re.escape(field['label']) + r')\s*[:=—-]\s*(.+)', sentence, re.I)
            if match:
                value = clean(match[1])
                if field['type'] == 'date':
                    dates = source_dates(value)
                    value = next(iter(dates)) if len(dates) == 1 else None
                add(field['key'], value, sentence)

    patterns = {
        'organization': r'(?:наименование организации|организация)\s*[:—-]\s*(.+)',
        'topic': r'(?:предмет работы\s*[:—-]|тема\s*[:—-]|рабоч\w*\s+групп\w*\s+по)\s*(.+)',
        'purpose': r'(?:\bцель\b(?:\s+принятия документа)?\s*[:—-]?|\bв целях)\s*(.+)',
        'end_event': r'(?:прекращ\w*\s+деятельность|деятельность\s+прекращ\w*|прекращ\w*).{0,30}?\bпосле\s+(.+)',
        'controller': r'(?:контрол\w*\s*[:—-]|возложить на)\s*(.+)',
        'basis': r'(?:нормативная основа\s*[:—-]|основание(?:\s+аудита)?\s*[:—-]|на основании|в связи с|соблюдения требований)\s*(.+)',
        'readiness_document': r'(?:документ о готовности\s*[:—-]|на основании)\s*(.+)',
        'development_document': r'(?:документ,? определивший работы\s*[:—-]|в соответствии с)\s*(.+)',
        'customer': r'функциональн\w*\s+заказчик\w*\s*[:—-]\s*(.+)',
        'responsible': r'(?:ответственн\w*\s+за исполнение\s*[:—-]|исполнение поручить)\s*(.+)',
        'changes_recipient': r'(?:сведени\w* об изменениях\s+(?:направл\w*|переда\w*)\s+в|получатель сведений\s*[:—-])\s*(.+)',
        'coordinator': r'(?:координацию поручить|координатор\s*[:—-])\s*(.+)',
        'transferor': r'(?:сдающая|передающая) сторона\s*[:—-]\s*(.+)',
        'transferee': r'принимающая сторона\s*[:—-]\s*(.+)',
        'property': r'(?:передаются|передаваемые дела и имущество\s*[:—-]|имущество\s*[:—-])\s*(.+)',
        'transfer_source': r'источник передачи\s*[:—-]\s*(.+)',
        'representative': r'(?:представитель организации\s*[:—-]|представителем назначить)\s*(.+)',
        'department': r'(?:ответственное подразделение\s*[:—-]|реализу(?:ются|ется)|реализующее проект подразделение\s*[:—-])\s*(.+)',
        'publication_place': r'(?:разместить|опубликовать|место размещения\s*[:—-])\s*(.+)',
        'process_name': r'(?:аудит\s+(?:(?:двух|трех|нескольких|\d+)\s+)?процессов\s*[:—-]|проверяемые процессы\s*[:—-]|проверить процессы)\s*(.+)',
        'projects': r'(?:проект(?:ов|а|у|ы)?\s*[:—-]|наименования проектов\s*[:—-])\s*(.+)',
        'owner': r'(?:доступ (?:предоставить|обеспечить)|подразделение,? предоставляющее доступ\s*[:—-])\s*(.+)',
        'plan_owner': r'(?:владелец корректирующих мероприятий\s*[:—-]|подготовку плана поручить)\s*(.+)',
    }
    for sentence in clauses:
        for key, pattern in patterns.items():
            if key not in allowed:
                continue
            match = re.search(pattern, sentence, re.I)
            if match:
                value = clean(match[1])
                add(key, value, sentence)
        if 'organization' in allowed and 'organization' not in values:
            match = re.search(r'\b(?:в|для)\s+(учебн\w*\s+центр\w*\s+.+?)\s+(?:нужна|нужен|создаем|создать)', sentence, re.I)
            if match:
                value = re.sub(r'^учебн\w*\s+центр\w*', 'учебный центр', match[1], flags=re.I)
                add('organization', clean(value), sentence)
        for key, cue in [('system_name', r'(?:информационн\w*\s+систем\w*|\bИС\b)\s*'),
                         ('short_name', r'(?:сокращенное наименование|сокращенно|далее)\s*')]:
            match = re.search(cue + r'[:—-]?\s*[«“"]([^»”"]+)[»”"]', sentence, re.I)
            if match:
                add(key, match[1], sentence)
        if 'signer' in allowed and re.search(CUES['signer'], sentence, re.I):
            match = re.search(CUES['signer'], sentence, re.I)
            found = names(sentence[match.end():])
            if len(found) == 1:
                add('signer', found[0], sentence)
        if 'act_name' in allowed:
            match = re.search(r'утвердить\s+(.+?)(?:\s+с началом применения|\s+с\s+' + DATE + r'|$)', sentence, re.I)
            if match:
                add('act_name', clean(match[1]), sentence)
        if 'action' in allowed and re.search(r'\bдо\b|\bсрок\b|\bгрупп', sentence, re.I):
            match = re.search(r'\b((?:разработать|подготовить|проанализировать|сформировать|проверить|оценить)\b.+?)(?:\s+и\s+представить|$)', sentence, re.I)
            if match:
                action = re.sub(r'\s+(?:до|не позднее)\s+(?:' + DATE + r')(?:\s+года)?\s*$', '', clean(match[1]), flags=re.I)
                add('action', clean(action), sentence)
        if 'deliverable' in allowed:
            match = re.search(r'\bпредставить\s+(.+)', sentence, re.I)
            if match:
                add('deliverable', clean(match[1]), sentence)
        if 'report_recipient' in allowed:
            match = re.search(r'(?:отчет представить|представить отчет)\s+до\s+' + DATE + r'\s+(.+)', sentence, re.I)
            if match:
                add('report_recipient', clean(match[1]), sentence)

        date_cues = {
            'deadline': r'\bдо\s+|не позднее\s+|срок.{0,40}(?:на|:)\s*',
            'start_date': r'начало аудита\s*[:—-]?\s*',
            'end_date': r'окончание(?: аудита)?\s*[:—-]?\s*',
            'report_deadline': r'отчет.{0,30}до\s+',
            'plan_deadline': r'план.{0,70}до\s+',
            'effective_date': r'(?:начал\w* применения|начинает действовать)\s*[:—-]?\s*',
            'launch_date': r'(?:вводим|ввести|эксплуатаци).{0,140}?\bс\s+',
        }
        for key, prefix in date_cues.items():
            if key not in allowed:
                continue
            for match in re.finditer('(?:' + prefix + ')(' + DATE + ')', sentence, re.I):
                dates = source_dates(match[1])
                if len(dates) == 1:
                    add(key, next(iter(dates)), sentence)
        if 'readiness_date' in allowed and re.search(CUES['readiness_date'], sentence, re.I):
            dates = source_dates(sentence)
            if len(dates) == 1:
                add('readiness_date', next(iter(dates)), sentence)

        booleans = {
            'it_project': r'(?:групп|комитет|объединен|проект).{0,50}(?:решает|касается|связан|вопрос|реализац).{0,50}(?:информационн\w*\s+систем|\bИТ\b)',
            'access_change': r'(?:доступ.{0,50}(?:меня|изменя)|(?:меняет|изменяет|изменяется).{0,50}доступ)',
            'control_self': r'контрол.{0,40}оставл.{0,20}за собой',
            'cancel_previous': r'прежн.{0,35}отмен|ранее изданн.{0,35}отмен|отмен(?:ить|яем|ять)|признать.{0,40}утративш',
            'attachment_confirmed': r'(?:утверждаем\w*\s+документ|приложение).{0,50}(?:подготовлен|приложен)|подготовлен.{0,25}как приложение',
            'operational_docs_confirmed': r'эксплуатационн\w*\s+документаци.{0,30}(?:утвержден|принят)',
            'developed_new': r'(?:компонент|систем).{0,35}(?:впервые|модернизир)',
            'deadline_in_basis': r'срок.{0,40}основан.{0,30}установлен|срок.{0,20}установлен.{0,25}основан',
        }
        for key, pattern in booleans.items():
            if key not in allowed:
                continue
            for clause in re.split(r',|;|\bно\b|\bа\b', sentence, flags=re.I):
                match = re.search(pattern, clause, re.I)
                if match and not re.search(r'не только', clause, re.I):
                    value = not bool(re.search(r'\bне\b|модернизир', clause, re.I))
                    add(key, value, clause.strip())
        for key, noun in [('multiple_processes', 'процесс'), ('multiple_projects', 'проект')]:
            if re.search(r'\b(?:двух|два|трех|три|нескольк\w*|[2-9]\d*)\s+' + noun, sentence, re.I):
                add(key, True, sentence)
            elif re.search(r'\b(?:один|одного|одному|1)\s+' + noun, sentence, re.I):
                add(key, False, sentence)
    if 'people' in allowed:
        people, quotes = people_from_message(message)
        if people:
            values['people'], evidence['people'] = people, quotes
    if 'readiness_document' in values:
        values['readiness_document'] = re.sub(r'^акт\b', 'акта', values['readiness_document'], flags=re.I)
        values['readiness_document'] = re.sub(r'^протокол\b', 'протокола', values['readiness_document'], flags=re.I)
    return {'values': values, 'evidence': evidence, 'conflicts': sorted(conflicts)}


def sanitize_model_fields(message: str, fields: list[dict], proposed: dict) -> dict:
    """Validate meaning and identity after the primitive shape/span checks."""
    allowed = {f['key']: f for f in fields}
    explicit = extract_explicit(message, fields)
    clean_values, clean_evidence = {}, {}
    for key, record in proposed.items():
        if key not in allowed or not isinstance(record, dict) or set(record) != {'value', 'evidence'}:
            continue
        quote, value = record['evidence'], record['value']
        if not isinstance(quote, str) or not quote.strip() or norm(quote) not in norm(message):
            continue
        field = allowed[key]
        if key in explicit['conflicts']:
            continue
        if field['type'] == 'people':
            continue  # Participant identities and roles are parsed from their own clauses.
        if field['type'] == 'boolean':
            continue  # Polarity must be explicit; a model's boolean alone is not evidence.
        if not field_supported(key, quote, field['label']) and field['type'] != 'date':
            continue
        if key == 'signer' and value not in names(quote):
            continue
        # A full request is not an organization, basis, controller or other slot.
        if isinstance(value, str) and (len(sentences(value)) > 1 or re.search(r'нуж(?:но|ен|на)\s+(?:создать|приказ|распоряжение)|создаем\s+комитет', value, re.I)):
            continue
        if key == 'controller' and re.search(r'контрол.{0,30}за собой', quote, re.I):
            continue
        if key in {'controller', 'responsible', 'transferor', 'transferee', 'representative'}:
            given, selected = names(quote), names(str(value))
            if selected and any(norm(n) not in {norm(x) for x in given} for n in selected):
                continue
        clean_values[key], clean_evidence[key] = value, quote
    # Explicit source spans take precedence over generative interpretation.
    clean_values.update(explicit['values']); clean_evidence.update(explicit['evidence'])
    return {'values': clean_values, 'evidence': clean_evidence, 'conflicts': explicit['conflicts']}
