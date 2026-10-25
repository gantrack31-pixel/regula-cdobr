"""Deterministic document assembly from the seven supplied, immutable DOCX modules.

No model is allowed to rewrite module constants. Variable values are plain text;
DOCX is an OOXML package, never a program or an instruction source.
"""
from __future__ import annotations

import copy
import hashlib
import io
import json
import re
import shutil
import threading
import unicodedata
import zipfile
from datetime import date, datetime
from pathlib import Path
from typing import Any

from lxml import etree

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
REL = "http://schemas.openxmlformats.org/package/2006/relationships"
NS = {"w": W}
XML_SPACE = "{http://www.w3.org/XML/1998/namespace}space"
TOKEN = re.compile(r"\{\{\s*([\w.-]+)\s*\}\}", re.UNICODE)
GUIDANCE = "Методические рекомендации по работе с модульными документами, пп. 3.2–3.3"


def field(key: str, label: str, type: str = "text", required: bool = True,
          help: str = "", default: Any = None) -> dict:
    result = {"key": key, "label": label, "type": type, "required": required}
    if help:
        result["help"] = help
    if default is not None:
        result["default"] = default
    return result


ORG = field("organization", "Наименование организации", default="Организация",
            help="Учебное наименование. Для реального документа нужен утвержденный бланк.")
SIGNER = field("signer", "Инициалы и фамилия подписанта", help="Укажите фактическое уполномоченное лицо. Подпись и номер документа не генерируются.")
CONTROL = [field("control_self", "Контроль оставляет за собой подписант", "boolean", False, default=False),
           field("controller", "Контролирующее лицо", required=False,
                 help="После «возложить на» — в винительном падеже: начальника департамента С. С. Сидорова. Обязательно, если контроль не у подписанта.")]
CANCEL = [field("cancel_previous", "Отменить прежний акт", "boolean", False, default=False),
          field("previous_act", "Реквизиты отменяемого акта", required=False,
                help="Вид, дата, номер и название; обязательно, если включена отмена.")]
PEOPLE_HELP = "Официальные должности и подразделения, инициалы и фамилии. Добавляйте строки; внешних участников укажите с пометкой «по согласованию», когда она требуется."


def _specs() -> list[dict]:
    return [
        {"id": "is_launch", "title": "Ввод информационной системы в эксплуатацию", "document_type": "order",
         "filename_part": "о_вводе_в_эксплуатацию", "description": "Приказ с единым названием ИС, проверкой дат готовности и ввода и обязательными визами ИТ и ИБ.",
         "tags": ["ИС", "эксплуатация", "ИТ", "приказ"],
         "fields": [ORG, field("system_name", "Полное наименование ИС или компонента"), field("short_name", "Сокращенное наименование", help="Без внешних кавычек; повторяется автоматически во всех пунктах."),
                    field("readiness_document", "Документ о готовности", "textarea", help="После «На основании» — в родительном падеже: акта, протокола. Укажите дату, номер, при наличии — заголовок."),
                    field("readiness_date", "Дата документа о готовности", "date", help="Для проверки хронологии; должна совпадать с реквизитами основания."),
                    field("developed_new", "Компонент разработан впервые", "boolean", False, default=True),
                    field("development_document", "Документ, определивший работы", "textarea", help="После «в соответствии с» — в творительном падеже: техническим заданием, заявкой, спецификацией. Укажите дату и номер."),
                    field("launch_date", "Дата начала эксплуатации", "date"), field("customer", "Функциональный заказчик", help="Полное наименование подразделения в дательном падеже."),
                    field("purpose", "Функциональное назначение", "textarea"), field("responsible", "Ответственный за исполнение", help="Должность и ФИО в именительном падеже."),
                    field("operational_docs_confirmed", "Эксплуатационная документация утверждена или принята", "boolean", False, default=False), SIGNER],
         "hints": ["Дата ввода не должна предшествовать документу о готовности без прямо установленного исключения.", "Короткое название связано в пяти местах; не требуется заполнять его повторно.", "В исходнике исправлена непарная кавычка перед сокращенным названием; смысл постоянного текста сохранен."]},
        {"id": "records_officers", "title": "Назначение ответственных за делопроизводство", "document_type": "order", "filename_part": "о_назначении_ответственных",
         "description": "Ответственные по подразделениям, замещение, координация и необязательная отмена прежнего приказа.", "tags": ["делопроизводство", "ответственные", "приказ"],
         "fields": [ORG, field("basis", "Нормативная основа", "textarea", help="В родительном падеже после «соблюдения требований»: Инструкции, Правил; при необходимости укажите реквизиты."),
                    field("people", "Ответственные работники", "people", help=PEOPLE_HELP), field("changes_recipient", "Получатель сведений об изменениях", help="Подразделение в винительном падеже после «в», при необходимости — срок уведомления."),
                    field("coordinator", "Подразделение, осуществляющее координацию", help="В дательном падеже."), *CANCEL, *CONTROL, SIGNER],
         "hints": ["До издания проверьте согласие работников и соответствие обязанностей должностным функциям.", "При исключении пункта об отмене нумерация восстанавливается автоматически."]},
        {"id": "handover", "title": "Прием и передача дел и имущества", "document_type": "order", "filename_part": "о_приеме_и_передаче",
         "description": "Передающие стороны, состав имущества, представитель, сроки и условная виза ИБ при изменении доступа.", "tags": ["передача", "имущество", "кадры", "приказ"],
         "fields": [ORG, field("basis", "Обстоятельство и документ-основание", "textarea", help="После «В связи с»: назначением, переводом, освобождением от должности и т. п."),
                    field("transferor", "Сдающая сторона", help="Должность, подразделение и ФИО в дательном падеже."), field("transferee", "Принимающая сторона", help="Должность, подразделение и ФИО в дательном падеже."),
                    field("property", "Передаваемые дела и имущество", "textarea"), field("transfer_source", "Источник передачи, если нужен", required=False, help="После «от» — в родительном падеже. Оставьте пустым, если источник уже однозначен из сдающей стороны."),
                    field("representative", "Представитель организации", help="Должность, подразделение и ФИО в винительном падеже."),
                    field("deadline", "Передать не позднее", "date", False), field("deadline_in_basis", "Срок уже установлен основанием или локальным актом", "boolean", False, default=False),
                    field("access_change", "Передача изменяет доступ к информационным системам", "boolean", False, default=False), *CONTROL, SIGNER],
         "hints": ["Упомянутое Положение о передаче дел не приложено к кейсу; его требования и полномочия представителя проверяет исполнитель.", "При изменении доступа в маршрут добавляется ИБ."]},
        {"id": "local_act", "title": "Утверждение и введение локального акта", "document_type": "order", "filename_part": "об_утверждении_и_введении",
         "description": "Утверждение приложенного документа, начало применения, ознакомление и публикация актуальной редакции.", "tags": ["локальный акт", "регламент", "утверждение", "приказ"],
         "fields": [ORG, field("purpose", "Цель принятия документа", "textarea"), field("act_name", "Вид и полное название документа", help="В именительном падеже, без внешних кавычек: например, Порядок обработки обращений."),
                    field("effective_date", "Дата начала применения", "date"), field("department", "Ответственное подразделение", help="В дательном падеже."),
                    field("publication_place", "Официальное место размещения", help="Например: на корпоративном портале; в системе электронного документооборота."),
                    field("attachment_confirmed", "Утверждаемый документ подготовлен как приложение", "boolean", False, default=False), *CANCEL, *CONTROL, SIGNER],
         "hints": ["Сервис создает распорядительную часть. Сам утверждаемый документ нужно приложить отдельно и проверить совпадение названия.", "Ознакомление организуют способом, позволяющим подтвердить его проведение."]},
        {"id": "audit", "title": "Внутренний аудит процессов", "document_type": "directive", "filename_part": "о_проведении_внутреннего_аудита",
         "description": "Объект аудита, независимая группа, период, отчет и план корректирующих мероприятий.", "tags": ["аудит", "процессы", "проверка", "распоряжение"],
         "fields": [ORG, field("basis", "Основание аудита", "textarea", help="План, поручение или решение с датой и номером."), field("process_name", "Наименования проверяемых процессов"),
                    field("multiple_processes", "Проверяется несколько процессов", "boolean", False, default=False), field("start_date", "Начало аудита", "date"), field("end_date", "Окончание аудита", "date"),
                    field("people", "Состав аудиторской группы", "people", help="Обязательны руководитель и хотя бы один аудитор. " + PEOPLE_HELP),
                    field("owner", "Подразделение, предоставляющее доступ", help="В дательном падеже."), field("report_deadline", "Представить отчет до даты", "date", help="По п. 6.4 Инструкции слово «до» исключает указанную дату."),
                    field("report_recipient", "Адресат отчета", help="В дательном падеже."), field("plan_owner", "Владелец корректирующих мероприятий", help="В дательном падеже."),
                    field("plan_deadline", "Подготовить план до даты", "date"), field("controller", "Контролирующее лицо", help="После «возложить на» — должность, подразделение и ФИО в винительном падеже."), SIGNER],
         "hints": ["Проверьте независимость группы от проверяемых операций и компетентность аудиторов.", "Программа аудита факультативна; в маршруте отдельно уточняется статус создаваемой аудиторской группы."]},
        {"id": "committee", "title": "Создание проектного комитета", "document_type": "directive", "filename_part": "о_создании_проектного_комитета",
         "description": "Один или несколько проектов, состав комитета, связанная отмена прежнего распоряжения и контроль.", "tags": ["комитет", "проект", "распоряжение"],
         "fields": [ORG, field("projects", "Полные наименования проектов", help="Для нескольких проектов перечислите названия через запятую."), field("multiple_projects", "Комитет создается по нескольким проектам", "boolean", False, default=False),
                    field("purpose", "Управленческая цель", "textarea"), field("basis", "Причина создания, если нужна", "textarea", False, help="После «в связи с». Если поле пустое, оборот удаляется целиком."),
                    field("department", "Реализующее проект подразделение", help="В творительном падеже; можно указать ответственных и участвующих."), field("people", "Состав комитета", "people", help="Председатель, заместитель, секретарь и члены; неиспользуемые роли можно убрать. " + PEOPLE_HELP),
                    field("it_project", "Комитет решает вопросы ИТ или информационных систем", "boolean", False, default=False),
                    field("cancel_previous", "Отменить прежнее распоряжение", "boolean", False, default=False), field("previous_date", "Дата прежнего распоряжения", "date", False),
                    field("previous_number", "Номер прежнего распоряжения", required=False), field("previous_title", "Название прежнего распоряжения", required=False), *CONTROL,
                    {**SIGNER, 'help': 'Для карточки проекта. В DOCX сохраняется служебный блок ЭП; печатная подпись не добавляется.'}],
         "hints": ["Наименования проектов и единственное/множественное число согласуются автоматически в трех местах.", "В этом модуле имеется только служебный блок ЭП; он не заменяется печатной подписью."]},
        {"id": "workgroup", "title": "Создание рабочей группы", "document_type": "directive", "filename_part": "о_создании_рабочеи",
         "description": "Временная группа с конкретной задачей, результатом, сроком и событием прекращения деятельности.", "tags": ["рабочая группа", "задача", "срок", "распоряжение"],
         "fields": [ORG, field("topic", "Предмет работы", help="После «по»; одинаково используется в заголовке и пункте 1."), field("purpose", "Практическая цель", "textarea"),
                    field("people", "Состав рабочей группы", "people", help="Укажите руководителя, секретаря и членов. " + PEOPLE_HELP), field("deadline", "Выполнить до даты", "date", help="По п. 6.4 Инструкции «до» исключает дату."),
                    field("action", "Конкретное действие группы", "textarea", help="Инфинитив: разработать, проанализировать, подготовить и т. п."), field("deliverable", "Итоговый результат и адресат", "textarea", help="После «представить»: отчет, заключение, проект или предложения и кому их передать."),
                    field("end_event", "Событие прекращения деятельности", help="После «после»: утверждения документа, представления результата и т. п."),
                    field("it_project", "Группа решает вопросы ИТ или информационных систем", "boolean", False, default=False), field("controller", "Контролирующее лицо", help="После «возложить на» — должность, подразделение и ФИО в винительном падеже."), SIGNER],
         "hints": ["Рабочая группа временная; постоянно действующий орган требует другой формы и проверки полномочий.", "Нельзя ограничиться формулировкой «проработать вопрос» без проверяемого результата."]},
    ]


def _xml(data: bytes) -> etree._Element:
    return etree.fromstring(data, etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False))


def _text(node: etree._Element) -> str:
    return "".join(node.itertext()) if node.tag == f"{{{W}}}t" else "".join(node.xpath(".//w:t/text()", namespaces=NS))


def _paragraph_text(p: etree._Element, text: str) -> None:
    """Change one editable slot, keeping its paragraph geometry and run style."""
    style = p.find("w:r/w:rPr", NS)
    props = copy.deepcopy(style) if style is not None else etree.Element(f"{{{W}}}rPr")
    # Variables must not inherit red note-marker color, underline or superscript.
    for element in list(props):
        if etree.QName(element).localname in {"color", "u", "vertAlign"}:
            props.remove(element)
    for child in list(p):
        if child.tag != f"{{{W}}}pPr":
            p.remove(child)
    r = etree.SubElement(p, f"{{{W}}}r")
    r.append(props)
    for i, line in enumerate(str(text).split("\n")):
        if i:
            etree.SubElement(r, f"{{{W}}}br")
        t = etree.SubElement(r, f"{{{W}}}t")
        t.set(XML_SPACE, "preserve")
        t.text = line


def _date(value: Any) -> date | None:
    if not value:
        return None
    for fmt in ("%Y-%m-%d", "%d.%m.%Y"):
        try:
            return datetime.strptime(str(value).strip(), fmt).date()
        except ValueError:
            pass
    return None


def _true(value: Any) -> bool:
    return value is True or value == 1 or isinstance(value, str) and value.lower() in {"true", "1", "да"}


def _read_package(path: Path) -> dict[str, bytes]:
    with zipfile.ZipFile(path) as archive:
        if sum(i.file_size for i in archive.infolist()) > 40 * 1024 * 1024:
            raise ValueError("Распакованный DOCX превышает допустимый размер 40 МБ.")
        if any(i.flag_bits & 1 for i in archive.infolist()):
            raise ValueError("Защищенные паролем DOCX не поддерживаются.")
        return {i.filename: archive.read(i) for i in archive.infolist()}


def _fit_tables(root: etree._Element) -> None:
    """The instruction's margins take precedence over over-wide source tables."""
    section = root.find(".//w:sectPr", NS)
    if section is None:
        return
    size, margins = section.find("w:pgSz", NS), section.find("w:pgMar", NS)
    if size is None or margins is None:
        return
    available = int(size.get(f"{{{W}}}w")) - int(margins.get(f"{{{W}}}left")) - int(margins.get(f"{{{W}}}right"))
    for table in root.findall(".//w:body/w:tbl", NS):
        grid = table.find("w:tblGrid", NS)
        if grid is None:
            continue
        columns = grid.findall("w:gridCol", NS)
        widths = [int(c.get(f"{{{W}}}w", "0")) for c in columns]
        total = sum(widths)
        if total <= available or not total:
            continue
        # Keep the source column proportions, correcting only overflow.
        scaled = [round(w * available / total) for w in widths]
        scaled[-1] += available - sum(scaled)
        for column, width in zip(columns, scaled):
            column.set(f"{{{W}}}w", str(width))
        props = table.find("w:tblPr", NS)
        if props is None:
            props = etree.Element(f"{{{W}}}tblPr")
            table.insert(0, props)
        for tag, values in [("tblW", {"w": str(available), "type": "dxa"}), ("tblInd", {"w": "0", "type": "dxa"}), ("jc", {"val": "left"})]:
            element = props.find(f"w:{tag}", NS)
            if element is None:
                element = etree.Element(f"{{{W}}}{tag}")
                before = next((i for i, child in enumerate(props) if etree.QName(child).localname in {"tblBorders", "shd", "tblLayout", "tblCellMar", "tblLook"}), len(props))
                props.insert(before, element)
            for key, value in values.items():
                element.set(f"{{{W}}}{key}", value)
        for row in table.findall("w:tr", NS):
            column = 0
            for cell in row.findall("w:tc", NS):
                tcpr = cell.find("w:tcPr", NS)
                if tcpr is None:
                    tcpr = etree.Element(f"{{{W}}}tcPr")
                    cell.insert(0, tcpr)
                span = tcpr.find("w:gridSpan", NS)
                count = int(span.get(f"{{{W}}}val", "1")) if span is not None else 1
                width = sum(scaled[column:column + count])
                cell_width = tcpr.find("w:tcW", NS)
                if cell_width is None:
                    cell_width = etree.SubElement(tcpr, f"{{{W}}}tcW")
                cell_width.set(f"{{{W}}}w", str(width))
                cell_width.set(f"{{{W}}}type", "dxa")
                column += count


def _instruction_footer(package: dict[str, bytes], root: etree._Element) -> None:
    """Instruction 8.16: number at bottom right, beginning on page two."""
    relationships = _xml(package["word/_rels/document.xml.rels"])
    first_name = "footerRegulaFirst.xml"
    first_id = "rIdRegulaFirstFooter"
    relation = etree.SubElement(relationships, f"{{{REL}}}Relationship")
    relation.set("Id", first_id)
    relation.set("Type", R + "/footer")
    relation.set("Target", first_name)
    empty = etree.Element(f"{{{W}}}ftr", nsmap={"w": W})
    etree.SubElement(empty, f"{{{W}}}p")
    package["word/" + first_name] = etree.tostring(empty, xml_declaration=True, encoding="UTF-8", standalone=True)
    content_types = _xml(package["[Content_Types].xml"])
    override = etree.SubElement(content_types, "{http://schemas.openxmlformats.org/package/2006/content-types}Override")
    override.set("PartName", "/word/" + first_name)
    override.set("ContentType", "application/vnd.openxmlformats-officedocument.wordprocessingml.footer+xml")
    for section in root.findall(".//w:sectPr", NS):
        for old in list(section.findall("w:footerReference", NS)):
            if old.get(f"{{{W}}}type") == "first":
                section.remove(old)
        first = etree.Element(f"{{{W}}}footerReference")
        first.set(f"{{{W}}}type", "first")
        first.set(f"{{{R}}}id", first_id)
        section.insert(0, first)
        title_page = section.find("w:titlePg", NS)
        if title_page is None:
            title_page = etree.Element(f"{{{W}}}titlePg")
            before = next((i for i, child in enumerate(section) if etree.QName(child).localname in {"textDirection", "bidi", "rtlGutter", "docGrid", "printerSettings"}), len(section))
            section.insert(before, title_page)
        title_page.set(f"{{{W}}}val", "true")
    for name in list(package):
        if re.fullmatch(r"word/footer\d+\.xml", name):
            footer = _xml(package[name])
            for p in footer.findall(".//w:p", NS):
                ppr = p.find("w:pPr", NS)
                if ppr is None:
                    ppr = etree.Element(f"{{{W}}}pPr")
                    p.insert(0, ppr)
                alignment = ppr.find("w:jc", NS)
                if alignment is None:
                    alignment = etree.SubElement(ppr, f"{{{W}}}jc")
                alignment.set(f"{{{W}}}val", "right")
                for fld in p.findall(".//w:fldChar", NS):
                    if fld.get(f"{{{W}}}fldCharType") == "begin":
                        fld.set(f"{{{W}}}dirty", "true")
            package[name] = etree.tostring(footer, xml_declaration=True, encoding="UTF-8", standalone=True)
    package["word/_rels/document.xml.rels"] = etree.tostring(relationships, xml_declaration=True, encoding="UTF-8", standalone=True)
    package["[Content_Types].xml"] = etree.tostring(content_types, xml_declaration=True, encoding="UTF-8", standalone=True)


class TemplateService:
    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir)
        self.template_dir = self.data_dir / "runtime" / "templates"
        self.template_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._templates: dict[str, dict] = {}
        manifest_path = self.data_dir / "sources" / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig")) if manifest_path.exists() else []
        originals = list((self.data_dir / "sources").glob("*.docx"))
        for spec in _specs():
            part = unicodedata.normalize("NFD", spec.pop("filename_part"))
            path = next((p for p in originals if part in unicodedata.normalize("NFD", p.name)), None)
            retained = self.data_dir / "templates" / "originals" / f"{spec['id']}.docx"
            if path is None and retained.exists():
                path = retained
            if path is None:
                continue
            source = next((s for s in manifest if s.get("filename") == path.name), {})
            spec.update(version=1, source_document_id=source.get("id"), source_filename=path.name, _path=str(path), _builtin=True,
                        hints=spec["hints"] + ["Учебный образец организаторов. Официальный реестр актуальности и полномочия подписанта не предоставлены."])
            self._templates[spec["id"]] = spec
        self._builtins = copy.deepcopy(self._templates)
        registry = self.template_dir / "registry.json"
        if registry.exists():
            for item in json.loads(registry.read_text(encoding="utf-8")):
                # Stored paths are relative to the data directory for portability.
                item["_path"] = str(self.data_dir / item["_path"])
                self._templates[item["id"]] = item

    def list_templates(self) -> list[dict]:
        with self._lock:
            return [self.get_template(tid) for tid in self._templates]

    def _persist_registry(self) -> None:
        persisted = []
        for item in self._templates.values():
            if not item.get("_builtin") or item.get("_persist"):
                copied = copy.deepcopy(item)
                copied["_path"] = str(Path(copied["_path"]).relative_to(self.data_dir))
                persisted.append(copied)
        registry = self.template_dir / "registry.json"
        temp = registry.with_suffix(".tmp")
        temp.write_text(json.dumps(persisted, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(registry)

    def get_template(self, template_id: str) -> dict:
        with self._lock:
            if template_id not in self._templates:
                raise KeyError(f"Шаблон {template_id} не найден")
            return copy.deepcopy({k: v for k, v in self._templates[template_id].items() if not k.startswith("_")})

    def suggest(self, query: str) -> list[str]:
        q = query.lower().replace("ё", "е")
        patterns = {"is_launch": ["информацион", "эксплуатац", "запуск ис", "ввод ис"], "records_officers": ["ответственн", "делопроизвод"],
                    "handover": ["передач", "прием дел", "имуществ"], "local_act": ["локальн", "утвержден", "утвердить", "регламент"],
                    "audit": ["аудит", "проверку процесс"], "committee": ["комитет"], "workgroup": ["рабоч", "групп"]}
        scores = []
        for tid, t in self._templates.items():
            tokens = patterns.get(tid, t.get("tags", []) + t["title"].lower().split())
            score = sum(1 for token in tokens if token.lower() in q)
            if score:
                scores.append((score, tid))
        return [tid for _, tid in sorted(scores, key=lambda x: -x[0])[:3]]

    def _values(self, template_id: str, supplied: dict) -> dict:
        values = {f["key"]: f.get("default", False if f["type"] == "boolean" else "") for f in self._templates[template_id]["fields"]}
        values.update({k: v for k, v in supplied.items() if k in values})
        return values

    def validate(self, template_id: str, values: dict) -> dict:
        template = self.get_template(template_id)
        v = self._values(template_id, values)
        missing: list[str] = []
        validation: list[dict] = []
        fields = {f["key"]: f for f in template["fields"]}
        required = {f["key"] for f in fields.values() if f["required"]}
        if "controller" in fields and not _true(v.get("control_self")):
            required.add("controller")
        if _true(v.get("cancel_previous")):
            required.update(["previous_date", "previous_number", "previous_title"] if template_id == "committee" else ["previous_act"])
        if template_id == "handover" and not _true(v.get("deadline_in_basis")):
            required.add("deadline")

        def finding(key: str, message: str, severity: str = "warning", rule: str = GUIDANCE):
            validation.append({"field": key, "severity": severity, "message": message, "rule": rule})

        for key in fields:
            if key not in required:
                continue
            value = v.get(key)
            empty = value is None or isinstance(value, str) and not value.strip() or isinstance(value, list) and not value
            if empty:
                missing.append(key)
                finding(key, f"Заполните: {fields[key]['label']}", "error")
        for key, f in fields.items():
            value = v.get(key)
            if f["type"] == "date" and value and _date(value) is None:
                finding(key, "Некорректная дата. Используйте ГГГГ-ММ-ДД или ДД.ММ.ГГГГ.", "error")
            if f["type"] in {"text", "textarea"} and value and not isinstance(value, str):
                finding(key, "Ожидается текстовое значение.", "error")
            if isinstance(value, str) and len(value) > 10000:
                finding(key, "Поле превышает 10 000 символов; вынесите подробности в приложение.", "error")
            if isinstance(value, str) and re.search(r"_{3,}|\{\{.*?\}\}|НЕ ЗАПОЛНЕНО", value):
                finding(key, "В значении остался незаполненный шаблонный фрагмент.", "error")
            if f["type"] == "people" and value:
                if not isinstance(value, list) or any(not isinstance(row, dict) for row in value):
                    finding(key, "Состав должен быть списком строк с должностью, подразделением и ФИО.", "error")
                    continue
                if len(value) > 100:
                    finding(key, "Не более 100 участников в одном проекте.", "error")
                for i, row in enumerate(value):
                    needed = ["department", "position", "name"] + ([] if template_id == "records_officers" else ["role"])
                    absent = [k for k in needed if not str(row.get(k, "")).strip()]
                    if absent:
                        finding(key, f"Строка {i + 1}: заполните " + ", ".join({"department": "подразделение", "position": "должность", "name": "ФИО", "role": "роль"}[k] for k in absent) + ".", "error")
                roles = " ".join(str(row.get("role", "")) for row in value).lower()
                if template_id in {"audit", "workgroup"} and "руководител" not in roles:
                    finding(key, "В составе не указан руководитель.", "error")
                if template_id == "workgroup" and "секретар" not in roles:
                    finding(key, "В составе не указан секретарь, которому поручены обязанности пункта 4.", "error")
                if template_id == "audit" and "аудитор" not in roles:
                    finding(key, "Укажите аудитора в составе группы.", "error")
                if template_id == "committee" and "председател" not in roles:
                    finding(key, "Проверьте наличие председателя комитета.")
        if set(values) - fields.keys():
            finding("", "Неизвестные поля не включены в документ: " + ", ".join(sorted(set(values) - fields.keys())), "warning")
        if template_id == "is_launch":
            a, b = _date(v.get("readiness_date")), _date(v.get("launch_date"))
            if a and b and b < a:
                finding("launch_date", "Дата ввода раньше даты документа о готовности. Укажите корректную дату либо проверьте прямо предусмотренное исключение.", "error", "Модуль ввода ИС, примечание 8")
            if not _true(v.get("operational_docs_confirmed")):
                finding("operational_docs_confirmed", "До ввода подтвердите утверждение или принятие эксплуатационной документации.", rule="Модуль ввода ИС, примечание 12")
        if template_id == "local_act" and not _true(v.get("attachment_confirmed")):
            finding("attachment_confirmed", "Утверждаемый документ необходимо подготовить как приложение; сам файл приложения сервис не создает.", "error", "Модуль локального акта, пункт 1 и примечание 2")
        if template_id == "handover" and v.get("transferor") and v.get("transferor") == v.get("transferee"):
            finding("transferee", "Сдающая и принимающая стороны совпадают; проверьте участников передачи.", "error")
        if template_id == "audit":
            pairs = [("start_date", "end_date", False), ("end_date", "report_deadline", True), ("report_deadline", "plan_deadline", False)]
            for first, last, strict in pairs:
                a, b = _date(v.get(first)), _date(v.get(last))
                if a and b and (b < a or strict and b == a):
                    finding(last, f"Проверьте хронологию: «{fields[last]['label']}» должна быть позже «{fields[first]['label']}»" + ("; «до» исключает указанную дату." if strict else "."), "error", "Модуль аудита, пункты 1, 5–6; Инструкция по ДОУ, п. 6.4")
        errors = sum(f["severity"] == "error" for f in validation)
        readiness = round(100 * (len(required) - len(missing)) / max(1, len(required)))
        if errors and readiness == 100:
            readiness = 90
        return {"missing_fields": sorted(missing, key=lambda k: list(fields).index(k)), "validation": validation,
                "preview": self._preview(template_id, v), "readiness": readiness}

    def _builtin_tree(self, tid: str, v: dict) -> tuple[dict[str, bytes], etree._Element]:
        template = self._templates[tid]
        package = _read_package(Path(template["_path"]))
        root = _xml(package["word/document.xml"])
        body = root.find("w:body", NS)
        blocks = list(body)
        fields = {f["key"]: f for f in template["fields"]}

        def val(key: str) -> str:
            raw = v.get(key)
            if raw is None or not str(raw).strip():
                return f"«НЕ ЗАПОЛНЕНО: {fields.get(key, {}).get('label', key)}»"
            parsed = _date(raw) if fields.get(key, {}).get("type") == "date" else None
            return parsed.strftime("%d.%m.%Y") if parsed else str(raw).strip()[:10000]

        def put(index: int, text: str):
            _paragraph_text(blocks[index], text)

        def remove(index: int):
            if blocks[index].getparent() is body:
                body.remove(blocks[index])

        def people(index: int):
            table = blocks[index]
            rows = table.findall("w:tr", NS)
            header_props = rows[0].find("w:trPr", NS)
            if header_props is None:
                header_props = etree.Element(f"{{{W}}}trPr")
                rows[0].insert(0, header_props)
            if header_props.find("w:tblHeader", NS) is None:
                etree.SubElement(header_props, f"{{{W}}}tblHeader")
            pattern = copy.deepcopy(rows[1])
            for row in rows[1:]:
                table.remove(row)
            roster = v.get("people")
            if not isinstance(roster, list) or not roster:
                roster = [{"role": "«НЕ ЗАПОЛНЕНО: роль»", "department": "«НЕ ЗАПОЛНЕНО: подразделение»", "position": "«НЕ ЗАПОЛНЕНО: должность»", "name": "«НЕ ЗАПОЛНЕНО: ФИО»"}]
            for person in roster[:100]:
                if not isinstance(person, dict):
                    continue
                row = copy.deepcopy(pattern)
                cells = row.findall("w:tc", NS)
                left = str(person.get("department" if tid == "records_officers" else "role") or "«НЕ ЗАПОЛНЕНО»")
                keys = ["position", "name"] if tid == "records_officers" else ["position", "department", "name"]
                department = str(person.get('department') or '').strip().casefold()
                position = str(person.get('position') or '').strip().casefold()
                if department and department in position and 'department' in keys:
                    keys.remove('department')
                labels = {'position': 'должность', 'department': 'подразделение', 'name': 'ФИО'}
                right = ", ".join(str(person.get(k) or f"«НЕ ЗАПОЛНЕНО: {labels[k]}»") for k in keys)
                for cell, text in zip(cells, [left, right]):
                    paragraphs = cell.findall("w:p", NS)
                    _paragraph_text(paragraphs[0], text)
                    for extra in paragraphs[1:]:
                        cell.remove(extra)
                # Let rows grow and prevent a short person record splitting across pages.
                trpr = row.find("w:trPr", NS)
                if trpr is None:
                    trpr = etree.Element(f"{{{W}}}trPr")
                    row.insert(0, trpr)
                for height in trpr.findall("w:trHeight", NS):
                    trpr.remove(height)
                if trpr.find("w:cantSplit", NS) is None:
                    etree.SubElement(trpr, f"{{{W}}}cantSplit")
                table.append(row)

        def control(index: int, number: int):
            kind = "приказа" if template["document_type"] == "order" else "распоряжения"
            tail = "оставляю за собой" if _true(v.get("control_self")) else "возложить на " + val("controller")
            put(index, f"{number}. Контроль за исполнением настоящего {kind} {tail}.")

        # Source-approved cleanup. Service date, registration and EP blocks remain byte-for-byte.
        remove(0)
        remove(1)
        put(3, val("organization"))
        # The case supplies an empty emblem zone, not an authorized emblem image.
        # Preserve this source zone; flag its status in the UI rather than inventing a seal.
        if tid == "is_launch":
            put(7, "О вводе в эксплуатацию «" + val("system_name").strip('«»') + "»")
            put(8, f"На основании {val('readiness_document')}, содержащего решение о готовности к вводу в эксплуатацию компонента информационной системы «{val('system_name').strip('«»')}» (далее - «{val('short_name')}»), {'разработанного' if _true(v.get('developed_new')) else 'измененного'} в соответствии с {val('development_document')},")
            put(10, f"1. Ввести «{val('short_name')}» в эксплуатацию с {val('launch_date')}.")
            put(11, "2. " + val("customer"))
            put(12, f"обеспечить применение «{val('short_name')}» по следующему назначению: {val('purpose')}.")
            put(14, f"3.1. функционирование «{val('short_name')}» в соответствии с эксплуатационной документацией;")
            put(15, f"3.2. сопровождение «{val('short_name')}» с привлечением при необходимости внешних подрядных организаций.")
            put(16, f"4. Ответственным за исполнение настоящего приказа является {val('responsible')}.")
            signature, notes_start = 17, 18
        elif tid == "records_officers":
            put(8, f"В целях организации делопроизводства и соблюдения требований {val('basis')},")
            people(11)
            put(16, f"3. Руководителям подразделений обеспечивать замещение ответственных лиц на период отсутствия и сообщать об изменениях в {val('changes_recipient')}.")
            put(17, f"4. {val('coordinator')} осуществлять методическое сопровождение и координацию работы ответственных лиц.")
            if _true(v.get("cancel_previous")):
                put(18, f"5. Признать утратившим силу {val('previous_act')}.")
            else:
                remove(18)
            control(19, 6 if _true(v.get("cancel_previous")) else 5)
            signature, notes_start = 20, 21
        elif tid == "handover":
            put(8, f"В связи с {val('basis')},")
            source = f" от {val('transfer_source')}" if v.get("transfer_source") else ""
            deadline = f" не позднее {val('deadline')}" if v.get("deadline") or not _true(v.get("deadline_in_basis")) else ""
            put(10, f"1. {val('transferor')} сдать, а {val('transferee')} принять дела и имущество ({val('property')}){source}{deadline} в порядке, предусмотренном Положением о порядке приема и передачи дел и имущества при назначении на должность и освобождении от должности руководителей структурных подразделений Организации.")
            put(11, f"2. Назначить {val('representative')} представителем Организации при приеме и передаче дел и имущества.")
            control(12, 3)
            signature, notes_start = 13, 14
        elif tid == "local_act":
            put(7, f"Об утверждении и введении в действие документа «{val('act_name')}»")
            put(8, f"В целях {val('purpose')},")
            put(10, f"1. Утвердить документ «{val('act_name')}» согласно приложению к настоящему приказу.")
            put(11, f"2. Ввести указанный документ в действие с {val('effective_date')}.")
            put(12, f"3. {val('department')} обеспечить:")
            put(14, f"• размещение актуальной редакции документа {val('publication_place')}.")
            if _true(v.get("cancel_previous")):
                put(15, f"4. Признать утратившим силу {val('previous_act')}.")
            else:
                remove(15)
            control(16, 5 if _true(v.get("cancel_previous")) else 4)
            signature, notes_start = 17, 18
        elif tid == "audit":
            process = "процессов" if _true(v.get("multiple_processes")) else "процесса"
            put(7, f"О проведении внутреннего аудита {process} {val('process_name')}")
            put(8, f"В соответствии с {val('basis')}, в целях оценки результативности процессов и управления связанными с ними рисками,")
            put(10, f"1. Провести с {val('start_date')} по {val('end_date')} внутренний аудит {process} {val('process_name')}.")
            people(16)
            put(17, f"4. {val('owner')} обеспечить аудиторской группе доступ к документам, информационным системам и работникам в пределах программы аудита.")
            put(18, f"5. Руководителю аудиторской группы до {val('report_deadline')} представить {val('report_recipient')} отчет о результатах аудита.")
            put(19, f"6. {val('plan_owner')} до {val('plan_deadline')} подготовить план мероприятий по результатам аудита.")
            control(20, 7)
            signature, notes_start = 21, 22
        elif tid == "committee":
            plural = _true(v.get("multiple_projects"))
            project = "проектам" if plural else "проекту"
            heading = f"О создании проектного комитета по {project} {val('projects')}"
            cancel = _true(v.get("cancel_previous"))
            if cancel:
                heading += f" и об отмене распоряжения Организации от {val('previous_date')} № {val('previous_number')}"
            put(7, heading)
            reason = f"в связи с {val('basis')}, " if v.get("basis") else ""
            put(8, f"В целях {val('purpose')}, {reason}для обеспечения координации и контроля реализации {'проектов' if plural else 'проекта'} {val('projects')}, {'реализуемых' if plural else 'реализуемого'} {val('department')},")
            put(10, f"1. Создать Проектный комитет по {project} {val('projects')} (далее - Комитет) в составе:")
            people(11)
            if cancel:
                put(12, f"2. Отменить распоряжение Организации от {val('previous_date')} № {val('previous_number')} «{val('previous_title')}».")
            else:
                remove(12)
            control(13, 3 if cancel else 2)
            signature, notes_start = None, 15
        else:
            put(7, "О создании рабочей группы по " + val("topic"))
            put(8, f"В целях {val('purpose')},")
            put(10, f"1. Создать рабочую группу по {val('topic')} (далее - Рабочая группа) в составе:")
            people(11)
            put(12, f"2. Рабочей группе до {val('deadline')} {val('action')} и представить {val('deliverable')}.")
            put(15, f"5. Рабочая группа прекращает деятельность после {val('end_event')}.")
            control(16, 6)
            signature, notes_start = 17, 18
        if signature is not None:
            signature_row = blocks[signature].findall("w:tr", NS)[0]
            cells = signature_row.findall("w:tc", NS)
            _paragraph_text(cells[1].find("w:p", NS), "")
            _paragraph_text(cells[2].find("w:p", NS), val("signer"))
            # Keep the signer's job title, name and following EP zone together.
            # A split row can leave just "Организации" on the next page.
            trpr = signature_row.find('w:trPr', NS)
            if trpr is None:
                trpr = etree.Element(f'{{{W}}}trPr')
                signature_row.insert(0, trpr)
            if trpr.find('w:cantSplit', NS) is None:
                etree.SubElement(trpr, f'{{{W}}}cantSplit')
            preceding = blocks[signature - 1]
            keep_paragraphs = signature_row.findall('.//w:p', NS)
            if preceding.tag == f'{{{W}}}p' and preceding.getparent() is body:
                keep_paragraphs.append(preceding)
            for paragraph in keep_paragraphs:
                props = paragraph.find('w:pPr', NS)
                if props is None:
                    props = etree.Element(f'{{{W}}}pPr')
                    paragraph.insert(0, props)
                if props.find('w:keepNext', NS) is None:
                    etree.SubElement(props, f'{{{W}}}keepNext')
        for block in blocks[notes_start:]:
            if block.tag != f"{{{W}}}sectPr" and block.getparent() is body:
                body.remove(block)
        return package, root

    def _generic_tree(self, tid: str, v: dict) -> tuple[dict[str, bytes], etree._Element]:
        item = self._templates[tid]
        package = _read_package(Path(item["_path"]))
        root = _xml(package["word/document.xml"])
        for part_name in list(package):
            if part_name != "word/document.xml" and not re.fullmatch(r"word/(header|footer)\d+\.xml", part_name):
                continue
            part = root if part_name == "word/document.xml" else _xml(package[part_name])
            for p in part.findall(".//w:p", NS):
                text = _text(p)
                if TOKEN.search(text):
                    rendered = TOKEN.sub(lambda m: str(v.get(m.group(1)) or f"«НЕ ЗАПОЛНЕНО: {m.group(1)}»")[:10000], text)
                    _paragraph_text(p, rendered)
            if part_name != "word/document.xml":
                package[part_name] = etree.tostring(part, xml_declaration=True, encoding="UTF-8", standalone=True)
        return package, root

    def _tree(self, tid: str, v: dict):
        return self._builtin_tree(tid, v) if self._templates[tid].get("_builtin") else self._generic_tree(tid, v)

    def _preview(self, tid: str, v: dict) -> str:
        _, root = self._tree(tid, v)
        lines = []
        for block in root.find("w:body", NS):
            if block.tag == f"{{{W}}}tbl":
                for row in block.findall("w:tr", NS):
                    lines.append(" | ".join(_text(cell) for cell in row.findall("w:tc", NS)))
            elif block.tag == f"{{{W}}}p":
                text = _text(block)
                if text.strip():
                    lines.append(text)
        return "\n\n".join(lines)

    def export(self, template_id: str, values: dict, output_path: Path) -> Path:
        with self._lock:
            self.get_template(template_id)
            v = self._values(template_id, values)
            package, root = self._tree(template_id, v)
            if self._templates[template_id].get("_builtin"):
                _fit_tables(root)
                _instruction_footer(package, root)
            package["word/document.xml"] = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)
            # PAGE field is preserved; ask Word to refresh cached field results when opened.
            if "word/settings.xml" in package:
                settings = _xml(package["word/settings.xml"])
                update = settings.find("w:updateFields", NS)
                if update is None:
                    update = etree.SubElement(settings, f"{{{W}}}updateFields")
                update.set(f"{{{W}}}val", "true")
                package["word/settings.xml"] = etree.tostring(settings, xml_declaration=True, encoding="UTF-8", standalone=True)
            output_path = Path(output_path)
            if output_path.resolve() == Path(self._templates[template_id]["_path"]).resolve():
                raise ValueError("Исходный модуль нельзя перезаписывать.")
            output_path.parent.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as archive:
                for name, data in package.items():
                    archive.writestr(name, data)
            return output_path

    def register_template(self, path: Path, title: str, source_document_id: str, version: int | str = 1) -> dict:
        """Activate a reviewed uploaded template. Call only when its source is activated.

        New files must contain {{field_key}} tokens. Arbitrary underscore modules
        are deliberately rejected instead of silently guessing their semantics.
        """
        path = Path(path)
        if path.suffix.lower() in {".txt", ".md"}:
            from docx import Document
            from docx.shared import Mm, Pt
            source_text = path.read_text(encoding="utf-8-sig")
            if not TOKEN.search(source_text):
                raise ValueError("Для конструктора добавьте явные поля {{ключ_поля}} в текст шаблона.")
            document = Document()
            for section in document.sections:
                section.page_width, section.page_height = Mm(210), Mm(297)
                section.left_margin, section.right_margin = Mm(30), Mm(20)
                section.top_margin, section.bottom_margin = Mm(25), Mm(20)
            normal = document.styles["Normal"]
            normal.font.name, normal.font.size = "Times New Roman", Pt(13)
            normal.paragraph_format.line_spacing = 1.15
            for line in source_text.splitlines():
                document.add_paragraph(line)
            stream = io.BytesIO()
            document.save(stream)
            uploaded_bytes = stream.getvalue()
            with zipfile.ZipFile(io.BytesIO(uploaded_bytes)) as archive:
                package = {name: archive.read(name) for name in archive.namelist()}
        elif path.suffix.lower() == ".docx":
            package = _read_package(path)
            uploaded_bytes = path.read_bytes()
        else:
            raise ValueError("Для конструктора загрузите DOCX, TXT или MD с полями {{ключ_поля}}. PDF доступен как источник базы знаний.")
        if "word/document.xml" not in package:
            raise ValueError("Файл не содержит основного документа DOCX.")
        keys = []
        all_text = ""
        for name, data in package.items():
            if name != "word/document.xml" and not re.fullmatch(r"word/(header|footer)\d+\.xml", name):
                continue
            root = _xml(data)
            text = "\n".join(_text(p) for p in root.findall(".//w:p", NS))
            all_text += text
            keys.extend(TOKEN.findall(text))
        keys = list(dict.fromkeys(keys))
        if not keys:
            # A published baseline may be reactivated after a new tokenized
            # revision. Recognize only the exact retained bytes, never guess an
            # unknown underscore module's field mapping from its filename.
            baseline = next((t for t in self._builtins.values() if t.get("source_document_id") == source_document_id), None)
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            if baseline and digest == hashlib.sha256(Path(baseline["_path"]).read_bytes()).hexdigest():
                with self._lock:
                    restored = copy.deepcopy(baseline)
                    restored.update(version=version, title=title, _persist=True, _source_hash=digest)
                    self._templates[restored["id"]] = restored
                    self._persist_registry()
                    return self.get_template(restored["id"])
            raise ValueError("Автоматическая форма требует явных полей {{ключ_поля}}. Линии и номера примечаний не распознаются как безопасная схема нового модуля; добавьте маркеры в DOCX.")
        if len(keys) > 100:
            raise ValueError("В одном шаблоне поддерживается не более 100 разных полей.")
        with self._lock:
            existing = next((t for t in self._templates.values() if t.get("source_document_id") == source_document_id), None)
            tid = existing["id"] if existing else "uploaded_" + hashlib.sha256(source_document_id.encode()).hexdigest()[:14]
            version_safe = re.sub(r"[^\w.-]", "_", str(version))[:80]
            destination = self.template_dir / "uploaded" / tid / f"v{version_safe}.docx"
            destination.parent.mkdir(parents=True, exist_ok=True)
            source_hash = hashlib.sha256(path.read_bytes()).hexdigest()
            if destination.exists() and existing and str(existing.get("version")) == str(version) and existing.get("_source_hash") != source_hash:
                raise ValueError("Версия шаблона уже сохранена с другим содержимым; загрузите новую версию.")
            if not destination.exists():
                destination.write_bytes(uploaded_bytes)
            item = {"id": tid, "title": title, "description": "Пользовательский модуль: поля обнаружены по явным маркерам {{ключ}}.",
                    "document_type": "directive" if "РАСПОРЯЖЕНИЕ" in all_text else "order" if "ПРИКАЗ" in all_text else "other",
                    "version": version, "source_document_id": source_document_id, "source_filename": path.name,
                    "fields": [field(k, k.replace("_", " ").replace(".", " ").capitalize(), "text") for k in keys],
                    "tags": title.lower().split(), "hints": ["Поля извлечены автоматически. Куратор должен проверить обязательность, постоянный текст и правила согласования.", "Автоматическое распознавание условных пунктов и таблиц из произвольного модуля не выполняется."],
                    "_path": str(destination), "_builtin": False, "_source_hash": source_hash}
            self._templates[tid] = item
            self._persist_registry()
            return self.get_template(tid)
