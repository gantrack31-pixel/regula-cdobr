"""Explainable rules from the supplied educational instruction and approval matrix.

The route is a recommendation for a draft, not a record of signatures or visas.
All loaded/user text is data. These rules never execute instructions in it.
"""
from __future__ import annotations

import re
import uuid
from pathlib import Path
from typing import Any

from .templates import NS, W, _read_package, _text, _true, _xml

INSTRUCTION = "Инструкция по ДОУ (учебный пример)"
MATRIX = "Матрица обязательного визирования внутренних и внешних документов"
GUIDANCE = "Методические рекомендации по работе с модульными документами"
LEGAL = "Юридическое подразделение"
DOU = "Департамент ДОУ"
HR = "Кадровое подразделение"
IT = "Департамент информационных технологий"
SEC = "Департамент информационной безопасности"


def _kind(value: str | None) -> str:
    return {"приказ": "order", "распоряжение": "directive", "письмо": "letter"}.get(str(value).lower(), str(value or "order").lower())


def build_route(payload: dict[str, Any]) -> dict:
    kind = _kind(payload.get("document_type"))
    topic = str(payload.get("topic", "")).lower().replace("ё", "е")
    modular = _true(payload.get("modular"))
    changed = _true(payload.get("changed"))
    full = not modular or changed
    departments: dict[str, dict] = {}
    questions: list[str] = []
    notes: list[str] = ["Учебный расчет по материалам кейса. Полномочия подписанта, актуальность модуля и компетенции подразделений подтверждает исполнитель.",
                        "Несколько подходящих строк матрицы применяются совокупно. «Нет» означает отсутствие обязанности только по матрице и не исключает согласование по компетенции."]

    def add(name: str, reason: str, source: str, conditional: bool = False):
        if name in departments:
            entry = departments[name]
            if reason not in entry["reason"]:
                entry["reason"] += " " + reason
                entry["source"] += "; " + source
            entry["conditional"] = entry.get("conditional", False) and conditional
        else:
            departments[name] = {"name": name, "reason": reason, "source": source, "conditional": conditional}

    def row(number: int, names: list[str], reason: str, external: bool = False):
        source = f"{MATRIX}, {'внешние' if external else 'внутренние'} документы, строка {number}"
        for name in names:
            add(name, reason, source)

    handover = bool(re.search(r"прием.*передач|передач.*дел|передач.*имуществ|handover", topic))
    calendar = bool(re.search(r"выходн|празднич|рабоч.{0,6}календар", topic))
    system = bool(re.search(r"ввод.*(систем|ис\b|компонент)|вывод.*(систем|ис\b|компонент)|эксплуатац|изменени.{0,20}компонент|is_launch", topic))
    if not handover and re.search(r"информационн.{0,8}систем", topic):
        system = True
    temporary = _true(payload.get("temporary_group")) or bool(re.search(r"рабоч.{0,6}групп|проектн.{0,6}комитет|создани.{0,20}комисси|временн.{0,20}объедин|workgroup|committee", topic))
    payment = bool(re.search(r"материальн.{0,8}помощ|выплат.*работник", topic))
    audit = bool(re.search(r"аудит", topic))
    it_value = payload.get("it_project")
    if it_value is None and re.search(r"ит-проект|ит проект|информационн.{0,8}систем", topic):
        it_value = True
    if kind == "order":
        matched = False
        if modular and calendar:
            row(1, [LEGAL, DOU, HR], "Модульный приказ по рабочему календарю.")
            matched = True
        if modular and handover:
            row(2, [LEGAL, DOU, HR], "Модульный приказ о приеме и передаче дел и имущества при кадровых изменениях.")
            matched = True
            if payload.get("access_change") is None:
                questions.append("Предусмотрено предоставление, изменение или прекращение доступа к информационным системам?")
                add(SEC, "Если передача затрагивает доступ к ИС; условие пока не подтверждено.", f"{MATRIX}, внутренние документы, строка 2", True)
            elif _true(payload.get("access_change")):
                add(SEC, "Прием и передача связаны с изменением доступа к ИС.", f"{MATRIX}, внутренние документы, строка 2")
        if modular and system:
            row(3, [LEGAL, DOU, SEC, IT], "Модульный приказ по вопросам информационных систем.")
            matched = True
        if not matched:
            row(4, [LEGAL, DOU], "Прочий приказ, включая приказ без применения модуля.")
        if not modular and (system or handover or calendar):
            notes.append("Специальные строки 1–3 матрицы описывают модульные приказы. Для этого немодульного приказа применяется строка 4; затронутые ИТ, ИБ и кадровые вопросы учитываются дополнительно по компетенции.")
            if system:
                add(IT, "Затрагивается эксплуатация информационных систем.", f"{MATRIX}, п. 3; {INSTRUCTION}, п. 10.11")
                add(SEC, "Затрагиваются вопросы безопасности информационных систем.", f"{MATRIX}, п. 3; {INSTRUCTION}, п. 10.11")
            if handover or calendar:
                add(HR, "Затрагиваются кадровые изменения или рабочий календарь.", f"{MATRIX}, п. 3; {INSTRUCTION}, п. 10.11")
            if handover and _true(payload.get("access_change")):
                add(SEC, "Затрагивается изменение доступа к ИС.", f"{MATRIX}, п. 3; {INSTRUCTION}, п. 10.11")
    elif kind == "directive":
        if payment:
            row(5, [DOU, HR], "Распоряжение о материальной помощи или иных выплатах работникам.")
        if temporary:
            row(6, [DOU, HR], "Создание рабочей группы, проектного комитета или иного временного объединения.")
            if it_value is None:
                questions.append("Создается ли временное объединение для ИТ-проекта или решения вопросов информационных систем?")
                add(IT, "Если объединение решает вопросы ИТ/ИС; условие пока не подтверждено.", f"{MATRIX}, внутренние документы, строка 6", True)
            elif _true(it_value):
                add(IT, "Временное объединение создается для ИТ-проекта или вопросов ИС.", f"{MATRIX}, внутренние документы, строка 6")
        if not temporary and not payment:
            row(7, [LEGAL, DOU], "Прочее распоряжение; строки 5–6 не подтверждены.")
        if audit and payload.get("temporary_group") is None:
            questions.append("Считается ли утверждаемая аудиторская группа создаваемым временным объединением для применения строки 6 матрицы?")
            add(HR, "Если утверждение аудиторской группы рассматривается как создание временного объединения.", f"{MATRIX}, внутренние документы, строка 6 (условное применение)", True)
            notes.append("Для аудита предварительно применена строка 7. Совокупное применение строки 6 зависит от статуса создаваемой группы; это требует уточнения, а не является установленным противоречием.")
    elif kind == "letter":
        recipient = payload.get("recipient_type")
        if recipient == "government":
            row(1, [LEGAL, DOU, "Департамент внутреннего контроля"], "Письмо органам власти за подписью Руководства Организации.", True)
            notes.append("Проверьте, что письмо адресовано категории строки 1 и подписывается Руководством Организации.")
        elif recipient == "foreign":
            row(2, [LEGAL, DOU, "Департамент международного сотрудничества"], "Письмо иностранной/международной организации за подписью Руководства.", True)
            notes.append("Строка 2 относится к указанным в матрице адресатам и уровню подписанта; для иной ситуации уточните компетенции.")
        elif recipient == "russian_legal":
            row(3, [LEGAL, DOU], "Письмо российскому юридическому лицу за подписью Руководства, руководства РО или руководителя СП.", True)
            notes.append("Ячейка прочих департаментов в строке 3 не заполнена. Это не запрет на их участие по компетенции.")
        elif recipient == "internal":
            notes.append("Для внутреннего письма внешние строки матрицы не применяются; состав обязательных виз требует проверки вида и компетенции.")
        else:
            questions.append("Кому адресовано письмо: органу власти, иностранной организации, российскому юридическому лицу или внутреннему адресату, и кто его подписывает?")
    else:
        questions.append("Уточните вид документа и применимый модуль: приказ, распоряжение или письмо.")

    if _true(payload.get("personnel")):
        add(HR, "Исполнитель указал затронутые кадровые вопросы.", f"{MATRIX}, п. 3; {INSTRUCTION}, п. 10.11")
    for department in payload.get("additional_departments", []) or []:
        if isinstance(department, str) and department.strip():
            add(department.strip(), "Затронута компетенция подразделения, указанного исполнителем.", f"{MATRIX}, п. 3; {INSTRUCTION}, п. 10.11")
    if _true(payload.get("access_change")) and SEC not in departments:
        add(SEC, "Исполнитель указал изменение доступа к информационным системам.", f"{MATRIX}, п. 3; {INSTRUCTION}, п. 10.11")
    steps: list[dict] = []

    def step(name: str, reason: str, source: str):
        steps.append({"order": len(steps) + 1, "name": name, "reason": reason, "source": source})

    if full:
        step("Подготовка и внутреннее согласование", "Исполнитель и ответственное подразделение готовят проект для рассмотрения.", f"{INSTRUCTION}, пп. 10.1–10.2")
        days = {"order": 10, "directive": 3, "letter": 3}.get(kind, 5)
        step("Одновременное рассмотрение заинтересованными подразделениями", f"Первичное рассмотрение; при доработке — повторное с таблицей замечаний. Каждое рассмотрение: {days} рабочих дней, это не срок всего маршрута.", f"{INSTRUCTION}, пп. 10.1–10.5")
    step("Исполнитель", "Проверяет конкретную версию проекта, реквизиты и корректность применения модуля.", f"{INSTRUCTION}, п. 10.6.1; {GUIDANCE}, п. 4.1")
    step("Участвовавшие работники подразделения", "Визируют проект в части подготовленного ими содержания.", f"{INSTRUCTION}, п. 10.6.2")
    step("Руководитель подразделения-исполнителя", "Подтверждает позицию ответственного подразделения.", f"{INSTRUCTION}, п. 10.6.3")
    step("Руководители заинтересованных подразделений", "Последовательные визы после рассмотрения — по затронутой компетенции.", f"{INSTRUCTION}, пп. 10.6.4, 10.11")
    if departments:
        step("Руководители обязательных согласующих подразделений", "Состав указан ниже; условные участники включаются после подтверждения условия.", f"{INSTRUCTION}, п. 10.6.5; {MATRIX}")
    if LEGAL in departments:
        step("Уполномоченный работник юридического подразделения", "Обязательная юридическая виза в установленной очередности.", f"{INSTRUCTION}, п. 10.6.6")
    linguistic = kind in {"order", "directive"} or modular
    if linguistic:
        step("Лингвистическая экспертиза", "Корректор или уполномоченный ДОУ проверяет язык и ставит отметку в ИИС ДО.", f"{INSTRUCTION}, п. 10.13; {GUIDANCE}, п. 4.2, шаг 3")
    if DOU in departments or modular:
        step("Уполномоченный работник ДОУ", "Проверяет оформление, модуль и маршрут; виза сохраняется при сокращенном согласовании.", f"{INSTRUCTION}, п. 10.6.7; {GUIDANCE}, п. 4.2, шаг 4")
    step("Куратор подразделения-исполнителя", "Если куратор не является подписантом.", f"{INSTRUCTION}, п. 10.6.8")
    step("Уполномоченный подписант", "Подписывается та же версия, которая прошла обязательные визы. Как правило, не позднее трех рабочих дней.", f"{INSTRUCTION}, п. 10.15; {GUIDANCE}, п. 5.1")
    step("Регистрация и дальнейшее исполнение", "Номер присваивается после подписания; затем рассылка, ознакомление, контроль и формирование в дело.", f"{INSTRUCTION}, пп. 7.1–7.3; {GUIDANCE}, пп. 2.1, 5.2–5.3")
    if modular and not changed:
        notes.append("Сокращение освобождает только от повторного полного согласования подразделениями, согласовавшими модуль. Обязательные визы матрицы, лингвистика и ДОУ сохраняются (п. 9.16.1 Инструкции; п. 1.3 рекомендаций).")
        notes.append("В п. 4.2 рекомендаций исходная нумерация шагов 1 → 3 → 4; отсутствующий шаг 2 не восстановлен как правило. Для обязательных ролей использована очередь п. 10.6 Инструкции.")
    if changed:
        notes.append("Изменение постоянных формулировок или смысла переводит проект на полное согласование. Существенные изменения после визирования требуют повторного рассмотрения конкретной версии.")
    notes.append("В пп. 10.1 и 10.2 есть неоднозначность взаимного порядка лингвистики и куратора. В этом расчете лингвистика проводится перед финальной визой ДОУ, а ДОУ — перед куратором по явной очереди п. 10.6; порядок нужно закрепить у владельца процесса.")
    notes.append("Неурегулированные замечания передаются подписанту с последствиями и рисками; отрицательная виза не подменяет процедуру урегулирования по пп. 10.7–10.10.")
    return {"title": "Полный маршрут согласования" if full else "Маршрут модульного проекта", "steps": steps,
            "departments": list(departments.values()), "notes": notes, "missing_questions": questions, "full_approval": full}


def review_text(text: str, document_type: str | None = None) -> dict:
    text = str(text or "")[:500000]
    lower = text.lower()
    # The document heading is stronger evidence than its command formula:
    # the formula itself can be the error that this review must detect.
    heading = re.search(r"(?mi)^\s*(приказ|распоряжение)\s*(?:№[^\n]*)?$", text)
    if document_type:
        kind = _kind(document_type)
    elif heading:
        kind = "order" if heading.group(1).lower() == "приказ" else "directive"
    elif re.search(r"(?mi)^\s*ПРИКАЗЫВАЮ\s*:", text):
        kind = "order"
    elif re.search(r"(?mi)^\s*ОБЯЗЫВАЮ\s*:", text):
        kind = "directive"
    else:
        kind = "letter" if "уважаем" in lower else "unknown"
    findings: list[dict] = []

    def add(severity: str, title: str, detail: str, source: str, suggestion: str = "", location: str = ""):
        findings.append({"id": "f" + str(len(findings) + 1), "severity": severity, "title": title, "detail": detail,
                         "source": source, "suggestion": suggestion, "location": location})

    if not text.strip():
        add("error", "Текст не обнаружен", "Документ пуст или содержит только изображение; автоматический OCR в этой проверке не выполняется.", "Ограничение анализа", "Загрузите DOCX или PDF с текстовым слоем.")
    residual = re.search(r"_{3,}|\{\{[^}]+\}\}|НЕ ЗАПОЛНЕНО|\[\d{1,2}\]", text)
    if residual:
        add("error", "Остались незаполненные поля", "Найдены линии, явные заполнители или номера примечаний модуля.", f"{GUIDANCE}, п. 3.2", "Заполните фактические данные; удалите примечания и неиспользованные варианты.", residual.group(0))
    if re.search(r"МОДУЛЬНЫЙ ДОКУМЕНТ|Примечания по заполнению", text, re.I):
        add("error", "В проекте остались инструкции модуля", "Из итогового проекта удаляют гриф утверждения модуля, отметку «МОДУЛЬНЫЙ ДОКУМЕНТ» и пояснения по заполнению.", f"{GUIDANCE}, п. 3.2")
    if "НАИМЕНОВАНИЕ ОРГАНИЗАЦИИ" in text:
        add("error", "Не указано наименование организации", "Вместо официального наименования остался элемент заготовки.", f"{INSTRUCTION}, п. 8.1")
    if "место для изображения" in lower:
        add("warning", "Не заполнена зона герба", "Предоставленный образец содержит текстовую зону вместо изображения. Проверьте утвержденный электронный бланк.", f"{INSTRUCTION}, пп. 9.5.3, 9.7.3", "Используйте бланк организации; сервис не создает герб или печать.")
    if kind in {"order", "directive"}:
        word = "ПРИКАЗЫВАЮ:" if kind == "order" else "ОБЯЗЫВАЮ:"
        if word not in text:
            add("error", "Нет распорядительной формулы", f"Для этого вида документа ожидается отдельная строка «{word}» прописными буквами.", f"{INSTRUCTION}, п. {'9.5.4' if kind == 'order' else '9.7.4'}")
        if not re.search(r"контроль за исполнением|ответственным за исполнение", lower):
            add("error", "Не определен контроль исполнения", "Укажите контролирующее лицо или контроль подписанта. В модуле ввода ИС отдельно предусмотрен ответственный за исполнение.", f"{INSTRUCTION}, пп. 9.5.5, 9.7.5")
        if not re.search(r"(?m)^\s*(?:О |Об )\S", text):
            add("warning", "Проверьте заголовок", "Отдельный заголовок к тексту не распознан.", f"{INSTRUCTION}, п. 8.15")
        command_at = text.find(word)
        substantive = text[command_at + len(word):] if command_at >= 0 else text
        numbered = [int(n) for n in re.findall(r"(?m)^\s*(\d+)\.\s", substantive)]
        if numbered and numbered != list(range(1, max(numbered) + 1)):
            add("warning", "Непоследовательная нумерация пунктов", f"Обнаружены номера: {', '.join(map(str, numbered))}. После удаления необязательных пунктов нумерацию меняют.", f"{GUIDANCE}, п. 3.3; {INSTRUCTION}, п. 8.16")
    if kind == "unknown":
        add("info", "Вид документа требует уточнения", "Тип не удалось надежно определить по тексту; применены только общие проверки.", f"{INSTRUCTION}, пп. 8.6, 8.15")
    date_zone = "ДАТА / РЕГИСТРАЦИОННЫЙ НОМЕР" in text
    if date_zone:
        add("info", "Служебная зона даты и номера сохранена", "Для проекта это допустимо: официальный регистрационный номер присваивается после подписания, а не генератором проекта.", f"{GUIDANCE}, пп. 2.1, 3.2; {INSTRUCTION}, п. 7.1")
    elif kind in {"order", "directive"} and not re.search(r"\b\d{2}\.\d{2}\.\d{4}\b|\b\d{1,2}\s+[а-я]+\s+20\d{2}", lower):
        add("warning", "Проверьте служебную зону даты", "Не обнаружены дата или предусмотренная модулем зона. Для проекта дата подписания не выдумывается, но соответствующее поле должно сохраняться.", f"{INSTRUCTION}, п. 8.7; {GUIDANCE}, п. 3.2")
    if "ВИЗУАЛИЗАЦИЯ ЭП" in text:
        add("info", "Служебный блок ЭП сохранен", "Это место для визуализации будущей подписи, а не подтверждение наличия действительной ЭП.", f"{GUIDANCE}, п. 3.2; {INSTRUCTION}, п. 8.19")
    elif kind in {"order", "directive"} and not re.search(r"председател|директор|руководител.{0,40}[А-Я]\.", text, re.I):
        add("warning", "Проверьте реквизиты подписанта", "Подпись, ее действительность и полномочия лица не устанавливаются по распознанному тексту.", f"{INSTRUCTION}, п. 8.19")
    if re.search(r"\bдо\s+\d{2}\.\d{2}\.\d{4}", lower):
        add("info", "«До даты» исключает эту дату", "При расчете срока используйте правило кейса: «до» исключает дату, «не позднее» включает; выходной переносит окончание на предшествующий рабочий день.", f"{INSTRUCTION}, пп. 6.3–6.4")
    if re.search(r"согласно приложению|утвердить.*приложени", lower):
        add("warning", "Проверьте комплект приложений", "Ссылка на приложение найдена, но наличие и содержание отдельного файла не подтверждены этой проверкой.", f"{INSTRUCTION}, п. 8.18; модуль локального акта, пункт 1")
    checks = [{"name": "Структура и явные заполнители", "status": "warning" if any(f["severity"] == "error" for f in findings) else "passed"},
              {"name": "Правила маршрута", "status": "calculated"}, {"name": "Шрифт и поля страницы", "status": "not_checked"},
              {"name": "Полная лингвистическая и юридическая экспертиза", "status": "not_checked"}]
    # A retained service zone is not evidence that constants still match an
    # approved module. A file review cannot grant the shortened route by itself.
    route = build_route({"document_type": kind, "topic": text[:5000], "modular": False})
    if date_zone:
        route["notes"].append("Обнаружены служебные зоны модуля, но соответствие постоянному тексту не подтверждено. Для сокращения маршрута проверьте модуль и конкретную редакцию проекта.")
    return {"id": str(uuid.uuid4()), "filename": "Текст проекта", "document_type": kind,
            "summary": _summary(findings), "score": _score(findings), "findings": findings, "route": route, "extracted_text": text,
            "checks": checks, "limitations": ["Оценка — показатель автоматических проверок, не разрешение на подписание и не правовое заключение.",
                                               "Полномочия, действительность ЭП, согласие работников, фактические сведения и полнота приложений автоматически не подтверждаются.",
                                               "Автоматическая проверка не заменяет лингвистическую экспертизу и верификацию ДОУ. Применяются только условные правила учебного кейса.",
                                               "По тексту нельзя надежно установить соответствие постоянной части утвержденному модулю; сокращенный маршрут требует отдельного подтверждения."]}


def _score(findings: list[dict]) -> int:
    return max(0, 100 - sum(18 if f["severity"] == "error" else 6 if f["severity"] == "warning" else 0 for f in findings))


def _summary(findings: list[dict]) -> str:
    errors = sum(f["severity"] == "error" for f in findings)
    warnings = sum(f["severity"] == "warning" for f in findings)
    return f"Автоматически выявлено: ошибок — {errors}, вопросов для проверки — {warnings}." if errors or warnings else "Явных нарушений в выполненных автоматических проверках не обнаружено. Обязательная экспертиза сохраняется."


def review_document(path: Path, document_type: str | None = None) -> dict:
    path = Path(path)
    suffix = path.suffix.lower()
    root = None
    if suffix == ".docx":
        package = _read_package(path)
        root = _xml(package["word/document.xml"])
        text = "\n".join(_text(p) for p in root.findall(".//w:p", NS))
    elif suffix == ".pdf":
        try:
            import fitz
            with fitz.open(path) as pdf:
                text = "\n".join(page.get_text() for page in pdf)
        except ImportError:
            from pypdf import PdfReader
            text = "\n".join(page.extract_text() or "" for page in PdfReader(path).pages)
    elif suffix in {".txt", ".md"}:
        text = path.read_text(encoding="utf-8-sig")
    else:
        raise ValueError("Для проверки поддерживаются DOCX, PDF с текстовым слоем, TXT и MD.")
    result = review_text(text, document_type)
    result["filename"] = path.name
    if suffix != ".docx":
        result["limitations"].append("Проверка размеров шрифта, интервалов, полей страницы и положения реквизитов доступна для DOCX; OCR не выполняется.")
        return result
    findings = result["findings"]

    def add(title: str, detail: str, location: str):
        findings.append({"id": "f" + str(len(findings) + 1), "severity": "warning", "title": title, "detail": detail,
                         "source": f"{INSTRUCTION}, п. 8.16", "location": location, "suggestion": "Исправьте оформление в утвержденном шаблоне и повторите проверку."})

    for i, section in enumerate(root.findall(".//w:sectPr", NS), 1):
        margins = section.find("w:pgMar", NS)
        if margins is None:
            continue
        mismatches = []
        for side, minimum, label in [("left", 30, "левое"), ("right", 20, "правое"), ("top", 25, "верхнее"), ("bottom", 15, "нижнее")]:
            raw = margins.get(f"{{{W}}}{side}")
            if raw:
                mm = int(raw) * 25.4 / 1440
                if mm + .2 < minimum or side == "left" and abs(mm - minimum) > .2:
                    mismatches.append(f"{label} {mm:.1f} мм (норма {'30' if side == 'left' else 'не менее ' + str(minimum)} мм)")
        if mismatches:
            add("Поля страницы отличаются от инструкции", "; ".join(mismatches) + ".", f"Раздел {i}")
    # Resolve direct properties and paragraph/character style inheritance, rather
    # than mistaking an omitted run property for a missing/incorrect font.
    styles = _xml(package["word/styles.xml"]) if "word/styles.xml" in package else None
    style_map = {s.get(f"{{{W}}}styleId"): s for s in styles.findall("w:style", NS)} if styles is not None else {}
    default_style = next((s for s in style_map.values() if s.get(f"{{{W}}}type") == "paragraph" and s.get(f"{{{W}}}default") == "1"), None)
    default_rpr = styles.find("w:docDefaults/w:rPrDefault/w:rPr", NS) if styles is not None else None

    def inherited(style_id: str | None, group: str) -> list:
        found, seen = [], set()
        style = style_map.get(style_id) if style_id else default_style
        while style is not None and id(style) not in seen:
            seen.add(id(style))
            prop = style.find(f"w:{group}", NS)
            if prop is not None:
                found.append(prop)
            based = style.find("w:basedOn", NS)
            style = style_map.get(based.get(f"{{{W}}}val")) if based is not None else None
        return found

    font_errors, size_errors, spacing_errors = [], [], []
    body_started = result["document_type"] not in {"order", "directive"}
    for index, p in enumerate(root.findall(".//w:body//w:p", NS), 1):
        ptext = _text(p)
        if "ПРИКАЗЫВАЮ:" in ptext or "ОБЯЗЫВАЮ:" in ptext:
            body_started = True
        if not body_started or not ptext.strip() or "НЕ УДАЛЯТЬ" in ptext or ptext.startswith("ГЕРБ"):
            continue
        pstyle = p.find("w:pPr/w:pStyle", NS)
        style_id = pstyle.get(f"{{{W}}}val") if pstyle is not None else None
        ppr = p.find("w:pPr", NS)
        pprops = ([ppr] if ppr is not None else []) + inherited(style_id, "pPr")
        spacing = next((pr.find("w:spacing", NS) for pr in pprops if pr.find("w:spacing", NS) is not None and pr.find("w:spacing", NS).get(f"{{{W}}}line")), None)
        if spacing is not None and spacing.get(f"{{{W}}}lineRule", "auto") == "auto":
            amount = int(spacing.get(f"{{{W}}}line")) / 240
            if amount < 1.14 or amount > 1.51:
                spacing_errors.append(index)
        for run in p.findall("w:r", NS):
            if not _text(run).strip():
                continue
            rpr = run.find("w:rPr", NS)
            rstyle = rpr.find("w:rStyle", NS) if rpr is not None else None
            props = ([rpr] if rpr is not None else []) + (inherited(rstyle.get(f"{{{W}}}val"), "rPr") if rstyle is not None else []) + inherited(style_id, "rPr") + ([default_rpr] if default_rpr is not None else [])
            fonts = next((pr.find("w:rFonts", NS) for pr in props if pr.find("w:rFonts", NS) is not None and (pr.find("w:rFonts", NS).get(f"{{{W}}}ascii") or pr.find("w:rFonts", NS).get(f"{{{W}}}hAnsi"))), None)
            size = next((pr.find("w:sz", NS) for pr in props if pr.find("w:sz", NS) is not None), None)
            if fonts is not None:
                name = fonts.get(f"{{{W}}}hAnsi") or fonts.get(f"{{{W}}}ascii")
                if name and name.lower() != "times new roman":
                    font_errors.append(index)
            if size is not None and size.get(f"{{{W}}}val") != "26":
                size_errors.append(index)
    for bad, title, detail in [(font_errors, "Шрифт основного текста", "Обнаружен шрифт, отличный от Times New Roman."),
                                (size_errors, "Размер основного текста", "Обнаружен размер, отличный от 13 пт."),
                                (spacing_errors, "Межстрочный интервал", "Обнаружен автоматический интервал вне диапазона 1,15–1,5.")]:
        if bad:
            add(title, detail + " Для внутренней переписки объемом более пяти листов возможно специальное исключение; объем по OOXML не подтверждается.", "Абзацы " + ", ".join(map(str, sorted(set(bad))[:12])))
    result["checks"][2] = {"name": "Шрифт, интервал и поля страницы по OOXML", "status": "warning" if font_errors or size_errors or spacing_errors or any(f["title"] == "Поля страницы отличаются от инструкции" for f in findings) else "passed"}
    result["limitations"].append("OOXML позволяет проверить свойства оформления, но не подтверждает фактическую пагинацию, отсутствие наложений и расположение подписи на странице. Для этого нужен визуальный просмотр в редакторе.")
    result["score"], result["summary"] = _score(findings), _summary(findings)
    return result
