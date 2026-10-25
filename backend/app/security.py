from collections import defaultdict, deque
from pathlib import Path
from threading import Lock
import re
import time
import zipfile
from xml.etree import ElementTree as ET

from fastapi import HTTPException
from starlette.responses import JSONResponse

MAX_UPLOAD = 25 * 1024 * 1024
ALLOWED_EXTENSIONS = {'.pdf', '.docx', '.txt', '.md'}


def request_body_limit(path):
    return MAX_UPLOAD + 1024 * 1024 if path in {'/api/sources/upload', '/api/review'} else 1024 * 1024


class BodyLimitMiddleware:
    """Bound bytes while ASGI receives them, including requests with chunked encoding."""
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            return await self.app(scope, receive, send)
        total, started = 0, False
        limit = request_body_limit(scope.get('path', ''))

        async def bounded_receive():
            nonlocal total
            message = await receive()
            if message['type'] == 'http.request':
                total += len(message.get('body', b''))
                if total > limit:
                    raise HTTPException(413, 'Превышен допустимый размер запроса.')
            return message

        async def tracked_send(message):
            nonlocal started
            if message['type'] == 'http.response.start':
                started = True
            await send(message)

        try:
            await self.app(scope, bounded_receive, tracked_send)
        except HTTPException as exc:
            if exc.status_code != 413 or started:
                raise
            await JSONResponse({'detail': exc.detail}, status_code=413,
                               headers={'X-Content-Type-Options': 'nosniff'})(scope, receive, send)


def validate_docx(archive):
    infos = archive.infolist()
    if len(infos) > 3000 or sum(i.file_size for i in infos) > 80 * 1024 * 1024:
        raise HTTPException(413, 'Слишком большой распакованный DOCX.')
    names = [i.filename.replace('\\', '/') for i in infos]
    if len(set(n.lower() for n in names)) != len(names):
        raise HTTPException(400, 'В DOCX есть повторяющиеся имена частей.')
    if any(n.startswith('/') or '..' in n.split('/') or ':' in n for n in names):
        raise HTTPException(400, 'Некорректная структура DOCX.')
    if any(i.flag_bits & 1 for i in infos):
        raise HTTPException(415, 'Документы с паролем не поддерживаются.')
    if 'word/document.xml' not in names:
        raise HTTPException(400, 'Файл не содержит документ Word.')
    blocked_parts = ('vbaproject', '/embeddings/', '/activex/', 'customui/')
    if any(any(part in n.lower() for part in blocked_parts) for n in names):
        raise HTTPException(415, 'Макросы, встроенные объекты и элементы управления не поддерживаются.')
    field_codes = []
    for info in infos:
        if not info.filename.endswith(('.xml', '.rels')):
            continue
        raw = archive.read(info)
        if re.search(br'<!\s*(?:DOCTYPE|ENTITY)\b', raw.replace(b'\x00', b''), re.I):
            raise HTTPException(415, 'Объявления DTD и внешних сущностей в DOCX запрещены.')
        try:
            root = ET.fromstring(raw)
        except ET.ParseError:
            raise HTTPException(400, 'В DOCX есть поврежденная XML-часть.')
        field_codes.append('\n')
        for element in root.iter():
            local = element.tag.rsplit('}', 1)[-1]
            if local == 'Relationship':
                rel = element.get('Type', '').rsplit('/', 1)[-1].lower()
                if element.get('TargetMode', '').lower() == 'external' or rel in {'attachedtemplate', 'oleobject', 'package', 'afchunk', 'control'}:
                    raise HTTPException(415, 'Внешние связи, подключенные шаблоны и активные объекты в DOCX запрещены. Удалите связи и загрузите копию.')
            elif local == 'instrText':
                field_codes.append(element.text or '')
            elif local == 'fldChar':
                field_codes.append('\n')
            elif local == 'fldSimple':
                field_codes.extend('\n' + value + '\n' for key, value in element.attrib.items() if key.rsplit('}', 1)[-1] == 'instr')
    # A field instruction can be split between several Word runs.
    if re.search(r'\b(?:DDEAUTO|DDE|INCLUDETEXT|INCLUDEPICTURE|LINK|DATABASE|MACROBUTTON)\b', ''.join(field_codes), re.I):
        raise HTTPException(415, 'DOCX содержит поля, запускающие команды или загружающие внешнее содержимое.')


def validate_pdf(path):
    from pypdf import PdfReader
    from pypdf.generic import IndirectObject, DictionaryObject, ArrayObject
    try:
        reader = PdfReader(path, strict=False)
        if reader.is_encrypted:
            raise HTTPException(415, 'PDF с шифрованием или паролем не поддерживается.')
        if len(reader.pages) > 400:
            raise HTTPException(413, 'Поддерживаются PDF до 400 страниц.')
        seen, stack, count = set(), [reader.trailer], 0
        while stack:
            obj = stack.pop()
            if isinstance(obj, IndirectObject):
                key = (obj.idnum, obj.generation)
                if key in seen:
                    continue
                seen.add(key)
                obj = obj.get_object()
            count += 1
            if count > 100000:
                raise HTTPException(413, 'Слишком сложная структура PDF.')
            if isinstance(obj, DictionaryObject):
                if any(key in obj for key in ('/JS', '/JavaScript', '/OpenAction', '/AA', '/EmbeddedFiles', '/RichMedia', '/XFA')):
                    raise HTTPException(415, 'PDF с активными действиями, скриптами или вложениями не поддерживается.')
                if str(obj.get('/S', '')) in {'/JavaScript', '/Launch', '/SubmitForm', '/ImportData', '/GoToR', '/Rendition'}:
                    raise HTTPException(415, 'PDF содержит небезопасное действие.')
                stack.extend(obj.values())
            elif isinstance(obj, (ArrayObject, list)):
                stack.extend(obj)
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(400, 'Не удалось прочитать структуру PDF.')


def validate_upload(path: Path):
    if path.suffix.lower() not in ALLOWED_EXTENSIONS:
        raise HTTPException(415, 'Поддерживаются только PDF, DOCX, TXT и MD.')
    if not path.stat().st_size or path.stat().st_size > MAX_UPLOAD:
        raise HTTPException(413, 'Файл должен быть непустым и не превышать 25 МБ.')
    if path.suffix.lower() == '.docx':
        try:
            with zipfile.ZipFile(path) as archive:
                validate_docx(archive)
        except (zipfile.BadZipFile, RuntimeError, OSError):
            raise HTTPException(400, 'Поврежденный DOCX.')
    if path.suffix.lower() == '.pdf':
        if not path.read_bytes()[:1024].lstrip().startswith(b'%PDF-'):
            raise HTTPException(400, 'Файл не является PDF.')
        validate_pdf(path)


class RateLimiter:
    def __init__(self):
        self.entries = defaultdict(deque)
        self.lock = Lock()

    def check(self, key: str, limit=30, window=60):
        current = time.monotonic()
        with self.lock:
            entries = self.entries[key]
            while entries and entries[0] < current - window:
                entries.popleft()
            if len(entries) >= limit:
                retry = max(1, int(window - (current - entries[0])) + 1)
                raise HTTPException(429, 'Слишком много запросов. Подождите минуту.', headers={'Retry-After': str(retry)})
            entries.append(current)
            if len(self.entries) > 10000:
                for old_key in list(self.entries):
                    if not self.entries[old_key] or self.entries[old_key][-1] < current - window:
                        del self.entries[old_key]
