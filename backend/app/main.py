from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime
import hashlib
import json
import logging
import os
from pathlib import Path
import re
from threading import Thread, BoundedSemaphore, RLock
import time
from typing import Any, Literal
from urllib.parse import urlparse

from fastapi import FastAPI, Depends, HTTPException, Request, Response, UploadFile, File, Form, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, Field, field_validator
from starlette.concurrency import run_in_threadpool

from .db import AppDB, now, uid
from .security import MAX_UPLOAD, RateLimiter, validate_upload, BodyLimitMiddleware, request_body_limit
from .llm import LLMClient, explicit_field_updates, validate_field_updates
from .knowledge import KnowledgeStore
from .templates import TemplateService
from .rules import build_route, review_document, review_text

ROOT = Path(__file__).resolve().parents[2]
# A small .env reader: literal values only, no shell evaluation or interpolation.
if (ROOT / '.env').exists():
    for line in (ROOT / '.env').read_text(encoding='utf-8-sig').splitlines():
        if '=' in line and not line.lstrip().startswith('#'):
            key, value = line.split('=', 1)
            if re.fullmatch(r'[A-Z][A-Z0-9_]*', key.strip()):
                os.environ.setdefault(key.strip(), value.strip().strip('"\''))
DATA = Path(os.getenv('DATA_DIR', str(ROOT / 'data'))).resolve()
RUNTIME = DATA / 'runtime'
DEMO_MODE = os.getenv('DEMO_MODE', '1').lower() in {'1', 'true', 'yes'}
logger = logging.getLogger('regula')
rate = RateLimiter()
model_slots = BoundedSemaphore(2)
source_mutations = RLock()


@asynccontextmanager
async def lifespan(app: FastAPI):
    RUNTIME.mkdir(parents=True, exist_ok=True)
    for directory in ('uploads', 'exports'):
        (RUNTIME / directory).mkdir(exist_ok=True)
    app.state.db = AppDB(RUNTIME / 'app.sqlite3', DEMO_MODE)
    app.state.llm = LLMClient()
    app.state.knowledge = KnowledgeStore(DATA, app.state.llm)
    app.state.knowledge.seed(DATA / 'sources' / 'manifest.json')
    app.state.templates = TemplateService(DATA)
    app.state.index_status = {'running': False, 'message': ''}
    yield


app = FastAPI(title='Регула API', description='Локальный помощник по документам и регламентам', version='1.0.0', lifespan=lifespan)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=os.getenv('ALLOWED_HOSTS', 'localhost,127.0.0.1,testserver').split(','))
app.add_middleware(BodyLimitMiddleware)


@app.middleware('http')
async def boundaries(request: Request, call_next):
    if request.method in {'POST', 'PUT', 'PATCH', 'DELETE'}:
        origin = request.headers.get('origin')
        allowed = {request.headers.get('host', ''), 'localhost:5173', '127.0.0.1:5173'}
        allowed.update(x.strip() for x in os.getenv('ALLOWED_ORIGINS', '').split(',') if x.strip())
        if (origin and urlparse(origin).netloc not in allowed) or request.headers.get('sec-fetch-site') == 'cross-site':
            return JSONResponse({'detail': 'Запрос с этого сайта не разрешён.'}, status_code=403)
        length = request.headers.get('content-length')
        if length and (not length.isdigit() or int(length) > request_body_limit(request.url.path)):
            return JSONResponse({'detail': 'Превышен допустимый размер запроса.'}, status_code=413)
    response = await call_next(request)
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Referrer-Policy'] = 'same-origin'
    response.headers['X-Frame-Options'] = 'SAMEORIGIN'
    response.headers['Permissions-Policy'] = 'camera=(), microphone=(), geolocation=()'
    response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; font-src 'self' data:; connect-src 'self' ws://localhost:5173 ws://127.0.0.1:5173; object-src 'none'; base-uri 'self'; frame-ancestors 'self'"
    if request.url.path.startswith('/api'):
        response.headers['Cache-Control'] = 'no-store'
    return response


@app.exception_handler(ValueError)
def invalid_value(request, exc):
    return JSONResponse({'detail': str(exc)[:600]}, status_code=422)


@app.exception_handler(RequestValidationError)
def invalid_request(request, exc):
    # Do not echo potentially large or confidential input (including passwords).
    errors = [{'field': '.'.join(str(x) for x in e['loc'])[:200],
               'message': e['msg'][:300], 'type': e['type']} for e in exc.errors()[:15]]
    return JSONResponse({'detail': 'Проверьте формат и допустимую длину полей запроса.', 'errors': errors}, status_code=422)


@app.exception_handler(KeyError)
def not_found(request, exc):
    return JSONResponse({'detail': 'Объект не найден или недоступен.'}, status_code=404)


@app.exception_handler(PermissionError)
def forbidden(request, exc):
    return JSONResponse({'detail': 'Недостаточно прав для доступа к этому источнику.'}, status_code=403)


def current_user(request: Request):
    user = request.app.state.db.session_user(request.cookies.get('regula_session'))
    if not user or (not DEMO_MODE and user['username'].startswith('demo-')):
        raise HTTPException(401, 'Войдите в рабочее пространство.')
    return {**user, 'demo_mode': DEMO_MODE}


def curator(user=Depends(current_user)):
    if user['role'] not in {'curator', 'admin'}:
        raise HTTPException(403, 'Изменять базу знаний может куратор или администратор.')
    return user


def admin(user=Depends(current_user)):
    if user['role'] != 'admin':
        raise HTTPException(403, 'Раздел доступен только администратору.')
    return user


def session_response(request, response, user):
    token = request.app.state.db.new_session(user['id'])
    response.set_cookie('regula_session', token, max_age=8 * 3600, httponly=True,
                        secure=request.url.scheme == 'https' or os.getenv('COOKIE_SECURE') == '1', samesite='lax', path='/')
    request.app.state.db.audit(user['username'], 'auth.login', user['id'])
    return {**user, 'demo_mode': DEMO_MODE}


class Login(BaseModel):
    username: str = Field(max_length=100)
    password: str = Field(max_length=500)


class DemoLogin(BaseModel):
    role: Literal['employee', 'curator', 'admin'] = 'employee'


@app.get('/api/health')
def health(request: Request):
    h = request.app.state.llm.health()
    stats = request.app.state.knowledge.stats()
    return {'status': 'ok', **h, 'source_count': stats.get('sources', 0), 'chunk_count': stats.get('chunks', 0),
            'mode': 'llm' if h.get('model_available') else 'extractive', 'demo_mode': DEMO_MODE,
            'indexing': request.app.state.index_status}


@app.post('/api/auth/demo')
def demo_login(data: DemoLogin, request: Request, response: Response):
    if not DEMO_MODE:
        raise HTTPException(403, 'Демонстрационный вход отключён.')
    rate.check('login:' + (request.client.host if request.client else 'local'), 30)
    return session_response(request, response, request.app.state.db.user('demo-' + data.role))


@app.post('/api/auth/login')
def login(data: Login, request: Request, response: Response):
    rate.check('login:' + (request.client.host if request.client else 'local'), 10)
    user = request.app.state.db.authenticate(data.username, data.password)
    if not user:
        raise HTTPException(401, 'Неверный логин или пароль.')
    return session_response(request, response, user)


@app.get('/api/auth/me')
def me(user=Depends(current_user)):
    return user


@app.post('/api/auth/logout')
def logout(request: Request, response: Response):
    request.app.state.db.logout(request.cookies.get('regula_session'))
    response.delete_cookie('regula_session', path='/')
    return {'ok': True}


class ChatInput(BaseModel):
    message: str = Field(min_length=1, max_length=6000)
    conversation_id: str | None = Field(default=None, max_length=100)
    as_of: str | None = None

    @field_validator('message')
    @classmethod
    def nonempty(cls, value):
        if not value.strip():
            raise ValueError('Введите сообщение.')
        return value.strip()

    @field_validator('as_of')
    @classmethod
    def date_valid(cls, value):
        if value:
            datetime.strptime(value, '%Y-%m-%d')
        return value


@app.post('/api/chat')
def chat(data: ChatInput, request: Request, user=Depends(current_user)):
    rate.check('chat:' + user['id'], 20)
    db, knowledge = request.app.state.db, request.app.state.knowledge
    history = []
    if data.conversation_id:
        conversation = db.conversation(data.conversation_id, user['id'])
        if not conversation:
            raise HTTPException(404, 'Диалог не найден.')
        history = [{'role': m['role'], 'content': m['content']} for m in conversation['messages'][-12:]]
        cid = data.conversation_id
    else:
        cid = None
    start = time.monotonic()
    if not model_slots.acquire(blocking=False):
        raise HTTPException(429, 'Модель обрабатывает другие запросы. Повторите через несколько секунд.')
    try:
        result = knowledge.answer(data.message, history=history, role=user['role'], as_of=data.as_of)
    finally:
        model_slots.release()
    if cid is None:
        cid = db.create_conversation(user['id'], data.message)
    result['conversation_id'] = cid
    result['elapsed_ms'] = round((time.monotonic() - start) * 1000)
    allowed_template_ids = {t['id'] for t in visible_templates(request, user)}
    suggestions = [tid for tid in request.app.state.templates.suggest(data.message) if tid in allowed_template_ids]
    result['template_suggestions'] = suggestions
    result.setdefault('citations', [])
    result.setdefault('suggested_questions', [])
    result.setdefault('mode', 'extractive')
    result.setdefault('status', 'grounded')
    result.setdefault('trace', {})
    if suggestions and re.search(r'созда|подготов|состав|заполн|сдела|выпуст', data.message, re.I):
        template = request.app.state.templates.get_template(suggestions[0])
        required = [f['label'] for f in template['fields'] if f.get('required')][:4]
        result['suggested_questions'] = [f'Открыть модуль «{template["title"]}»', *required[:2]]
    db.add_message(cid, 'user', data.message)
    db.add_message(cid, 'assistant', result['answer'], result)
    db.audit(user['username'], 'chat.answer', cid, {'status': result['status'], 'mode': result['mode'], 'citations': len(result['citations']), 'elapsed_ms': result['elapsed_ms']})
    return result


@app.get('/api/conversations')
def conversations(request: Request, user=Depends(current_user)):
    return request.app.state.db.conversations(user['id'])


@app.get('/api/conversations/{cid}')
def conversation(cid: str, request: Request, user=Depends(current_user)):
    result = request.app.state.db.conversation(cid, user['id'])
    if not result:
        raise HTTPException(404, 'Диалог не найден.')
    return result


@app.delete('/api/conversations/{cid}')
def delete_conversation(cid: str, request: Request, user=Depends(current_user)):
    if not request.app.state.db.delete_conversation(cid, user['id']):
        raise HTTPException(404, 'Диалог не найден.')
    request.app.state.db.audit(user['username'], 'conversation.delete', cid)
    return {'ok': True}


@app.get('/api/search')
def search(request: Request, q: str = Query(min_length=1, max_length=2000), user=Depends(current_user)):
    rate.check('search:' + user['id'], 40)
    if not model_slots.acquire(blocking=False):
        raise HTTPException(429, 'Поиск занят. Повторите через несколько секунд.')
    try:
        return {'results': request.app.state.knowledge.search(q, role=user['role'])}
    finally:
        model_slots.release()


@app.get('/api/sources')
def sources(request: Request, user=Depends(current_user)):
    return {'sources': request.app.state.knowledge.list_sources(user['role'])}


async def save_upload(file: UploadFile) -> Path:
    filename = Path(file.filename or '').name
    suffix = Path(filename).suffix.lower()
    path = RUNTIME / 'uploads' / (uid() + suffix)
    size = 0
    try:
        with path.open('wb') as output:
            while block := await file.read(1024 * 1024):
                size += len(block)
                if size > MAX_UPLOAD:
                    raise HTTPException(413, 'Максимальный размер файла — 25 МБ.')
                output.write(block)
        validate_upload(path)
        return path
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    finally:
        await file.close()


@app.post('/api/sources/upload')
async def upload_source(request: Request, file: UploadFile = File(...), title: str = Form(...),
                        kind: str = Form('other'), access: str = Form('all'), document_id: str | None = Form(None),
                        effective_from: str | None = Form(None), user=Depends(curator)):
    rate.check('upload:' + user['id'], 15)
    if kind not in {'instruction', 'matrix', 'guidance', 'template', 'other'} or access not in {'all', 'curator', 'admin'}:
        raise HTTPException(422, 'Некорректный вид документа или уровень доступа.')
    if not title.strip() or len(title) > 300:
        raise HTTPException(422, 'Укажите название до 300 символов.')
    if effective_from:
        datetime.strptime(effective_from, '%Y-%m-%d')
    if access == 'admin' and user['role'] != 'admin':
        raise HTTPException(403, 'Этот уровень доступа назначает администратор.')
    if document_id:
        request.app.state.knowledge.get_source(document_id, user['role'])
    path = await save_upload(file)
    try:
        def ingest_locked():
            with source_mutations:
                return request.app.state.knowledge.ingest(path, title.strip(), kind, access, document_id or None, effective_from or None)
        result = await run_in_threadpool(ingest_locked)
    finally:
        path.unlink(missing_ok=True)
    request.app.state.db.audit(user['username'], 'source.upload', result['document_id'], {'version': result['version'], 'kind': kind, 'access': access})
    return result


@app.get('/api/sources/{sid}')
def source(sid: str, request: Request, user=Depends(current_user)):
    return request.app.state.knowledge.get_source(sid, user['role'])


@app.get('/api/sources/{sid}/file')
def source_file(sid: str, request: Request, version_id: str | None = None, user=Depends(current_user)):
    path = Path(request.app.state.knowledge.file_path(sid, version_id, user['role']))
    source = request.app.state.knowledge.get_source(sid, user['role'])
    return FileResponse(path, filename=source['title'][:150] + path.suffix,
                        content_disposition_type='inline' if path.suffix.lower() == '.pdf' else 'attachment')


class ActivateInput(BaseModel):
    version_id: str = Field(max_length=100)


@app.post('/api/sources/{sid}/activate')
def activate(sid: str, data: ActivateInput, request: Request, user=Depends(curator)):
    with source_mutations:
        return activate_locked(sid, data, request, user)


def activate_locked(sid, data, request, user):
    store = request.app.state.knowledge
    source = store.get_source(sid, user['role'])
    version = next((v for v in source['versions'] if v['id'] == data.version_id), None)
    if not version:
        raise HTTPException(404, 'Редакция не найдена.')
    if source['kind'] == 'template':
        path = store.file_path(sid, data.version_id, user['role'])
        request.app.state.templates.register_template(Path(path), version.get('title', source['title']), sid, str(version['version']))
    result = store.activate(sid, data.version_id)
    request.app.state.db.audit(user['username'], 'source.activate', sid, {'version_id': data.version_id})
    return result


@app.get('/api/sources/{sid}/diff')
def source_diff(sid: str, request: Request, from_version: str, to_version: str, user=Depends(curator)):
    request.app.state.knowledge.get_source(sid, user['role'])
    return request.app.state.knowledge.diff(sid, from_version, to_version)


@app.post('/api/sources/index')
def index_sources(request: Request, user=Depends(curator)):
    if request.app.state.index_status['running']:
        return request.app.state.index_status
    request.app.state.index_status = {'running': True, 'message': 'Индексируются активные редакции'}
    def work():
        try:
            with model_slots:
                result = request.app.state.knowledge.index_embeddings()
            request.app.state.index_status = {'running': False, 'message': 'Индексация завершена', 'result': result}
        except Exception:
            logger.exception('Indexing failed')
            request.app.state.index_status = {'running': False, 'message': 'Модель эмбеддингов недоступна. Поиск по словам продолжает работать.'}
    Thread(target=work, daemon=True).start()
    request.app.state.db.audit(user['username'], 'source.index')
    return request.app.state.index_status


def visible_templates(request, user):
    templates = request.app.state.templates.list_templates()
    sources = {s['id']: s for s in request.app.state.knowledge.list_sources(user['role'])}
    # Fail closed if an interrupted activation left registry and source versions out of sync.
    return [t for t in templates if not t.get('source_document_id') or
            (t['source_document_id'] in sources and sources[t['source_document_id']].get('active_version_id') and
             str(t.get('version')) == str(sources[t['source_document_id']].get('version')))]


def authorized_template(tid, request, user):
    template = next((t for t in visible_templates(request, user) if t['id'] == tid), None)
    if not template:
        raise HTTPException(404, 'Шаблон не найден или недоступен.')
    return request.app.state.templates.get_template(tid)


@app.get('/api/templates')
def templates(request: Request, user=Depends(current_user)):
    return {'templates': visible_templates(request, user)}


@app.get('/api/templates/{tid}')
def template(tid: str, request: Request, user=Depends(current_user)):
    return authorized_template(tid, request, user)


class ValuesInput(BaseModel):
    values: dict[str, Any] = Field(default_factory=dict)

    @field_validator('values')
    @classmethod
    def safe_size(cls, value):
        if len(json.dumps(value, ensure_ascii=False)) > 100000:
            raise ValueError('Слишком большой проект документа.')
        return value


class DraftInput(ValuesInput):
    template_id: str = Field(max_length=100)


def assemble_draft(tid, values, request, user, draft_id=None):
    template = authorized_template(tid, request, user)
    valid_keys = {f['key'] for f in template['fields']}
    values = {k: v for k, v in values.items() if k in valid_keys}
    for field in template['fields']:
        if field['key'] not in values and field.get('default') is not None:
            values[field['key']] = field['default']
        if field['key'] not in values:
            continue
        value = values[field['key']]
        kind = field['type']
        texts = list(value.values()) if isinstance(value, dict) else [v for row in value if isinstance(row, dict) for v in row.values()] if isinstance(value, list) else [value]
        if any(isinstance(v, str) and re.search(r'[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff]', v) for v in texts):
            raise ValueError('В значениях есть недопустимые управляющие символы. Вставьте обычный текст.')
        if value is None:
            values[field['key']] = [] if kind == 'people' else False if kind == 'boolean' else ''
        elif kind == 'boolean' and not isinstance(value, bool):
            raise ValueError(f"Поле «{field['label']}» должно быть логическим значением.")
        elif kind in {'text', 'textarea', 'date'} and (not isinstance(value, str) or len(value) > 10000):
            raise ValueError(f"Поле «{field['label']}» должно содержать текст до 10 000 символов.")
        elif kind == 'people':
            if not isinstance(value, list) or len(value) > 100:
                raise ValueError('В составе допускается до 100 участников.')
            for person in value:
                if not isinstance(person, dict) or any(k not in {'role', 'department', 'position', 'name'} or not isinstance(v, str) or len(v) > 1000 for k, v in person.items()):
                    raise ValueError('Для участника укажите текстовые поля: роль, подразделение, должность, ФИО.')
    result = request.app.state.templates.validate(tid, values)
    result['route'] = build_route({'document_type': template['document_type'], 'topic': template['title'],
                                  'modular': True, 'changed': False, 'it_project': values.get('it_project'),
                                  'access_change': values.get('access_change'),
                                  'temporary_group': True if tid in {'workgroup', 'committee'} else None})
    draft = {'id': draft_id or uid(), 'template_id': tid, 'title': template['title'], 'values': values,
             **result, 'updated_at': now(), 'template_version': template.get('version', '1')}
    request.app.state.db.save_draft(draft, user['id'])
    return draft


@app.get('/api/drafts')
def drafts(request: Request, user=Depends(current_user)):
    allowed_template_ids = {t['id'] for t in visible_templates(request, user)}
    return {'drafts': [d for d in request.app.state.db.drafts(user['id']) if d['template_id'] in allowed_template_ids]}


@app.post('/api/drafts')
def create_draft(data: DraftInput, request: Request, user=Depends(current_user)):
    result = assemble_draft(data.template_id, data.values, request, user)
    request.app.state.db.audit(user['username'], 'draft.create', result['id'], {'template_id': data.template_id})
    return result


def owned_draft(did, request, user):
    result = request.app.state.db.draft(did, user['id'])
    if not result:
        raise HTTPException(404, 'Черновик не найден.')
    authorized_template(result['template_id'], request, user)
    return result


@app.get('/api/drafts/{did}')
def get_draft(did: str, request: Request, user=Depends(current_user)):
    return owned_draft(did, request, user)


@app.patch('/api/drafts/{did}')
def update_draft(did: str, data: ValuesInput, request: Request, user=Depends(current_user)):
    previous = owned_draft(did, request, user)
    return assemble_draft(previous['template_id'], {**previous['values'], **data.values}, request, user, did)


class AssistInput(BaseModel):
    message: str = Field(min_length=1, max_length=6000)


@app.post('/api/drafts/{did}/assist')
def assist_draft(did: str, data: AssistInput, request: Request, user=Depends(current_user)):
    rate.check('assist:' + user['id'], 20)
    previous = owned_draft(did, request, user)
    template = authorized_template(previous['template_id'], request, user)
    updates = {}
    if not model_slots.acquire(blocking=False):
        raise HTTPException(429, 'Модель обрабатывает другие запросы. Повторите через несколько секунд.')
    try:
        updates = request.app.state.llm.extract_fields(data.message, template['fields'], previous['values']) or {}
    except Exception:
        logger.info('Field extraction unavailable; trying explicit field=value input')
    finally:
        model_slots.release()
    # Independently verify source support before saving; do not repair malformed
    # model JSON or persist serialized fields as ordinary text.
    updates = validate_field_updates(data.message, template['fields'], updates)
    if not updates:
        updates = explicit_field_updates(data.message, template['fields'])
    fields = {f['key']: f for f in template['fields']}
    draft = assemble_draft(previous['template_id'], {**previous['values'], **updates}, request, user, did)
    missing_labels = [fields[k]['label'] for k in draft['missing_fields'] if k in fields]
    answer = ('Заполнил: ' + ', '.join(fields[k]['label'] for k in updates) + '. Проверьте внесённые значения. ') if updates else 'Не удалось однозначно извлечь поля. Заполните форму или укажите «название поля: значение». '
    answer += ('Ещё нужно уточнить: ' + '; '.join(missing_labels[:3]) + '.') if missing_labels else 'Обязательные поля заполнены. Проверьте предупреждения и предпросмотр перед скачиванием.'
    request.app.state.db.audit(user['username'], 'draft.assist', did, {'updated_fields': list(updates)})
    return {'draft': draft, 'answer': answer, 'updated_fields': list(updates)}


@app.post('/api/drafts/{did}/export')
def export_draft(did: str, request: Request, user=Depends(current_user)):
    draft = owned_draft(did, request, user)
    template = authorized_template(draft['template_id'], request, user)
    if str(draft.get('template_version', '1')) != str(template.get('version', '1')):
        raise HTTPException(409, 'Шаблон обновлён. Сохраните черновик, чтобы проверить его по актуальной редакции.')
    path = RUNTIME / 'exports' / (uid() + '.docx')
    request.app.state.templates.export(draft['template_id'], draft['values'], path)
    incomplete = bool(draft['missing_fields']) or any(v.get('severity') == 'error' for v in draft.get('validation', []))
    request.app.state.db.audit(user['username'], 'draft.export', did, {'incomplete': incomplete, 'template_version': draft['template_version']})
    return FileResponse(path, media_type='application/vnd.openxmlformats-officedocument.wordprocessingml.document',
                        filename='Проект — ' + draft['title'][:100] + '.docx', headers={'X-Draft-Incomplete': str(incomplete).lower()})


class RouteInput(BaseModel):
    document_type: Literal['order', 'directive', 'letter']
    topic: str = Field(default='', max_length=3000)
    it_project: bool | None = None
    access_change: bool | None = None
    recipient_type: Literal['government', 'foreign', 'russian_legal', 'internal'] = 'internal'
    modular: bool = False
    changed: bool = False
    temporary_group: bool | None = None
    personnel: bool = False
    additional_departments: list[str] = Field(default_factory=list, max_length=30)


@app.post('/api/route')
def route(data: RouteInput, request: Request, user=Depends(current_user)):
    return build_route(data.model_dump())


class ReviewInput(BaseModel):
    text: str = Field(min_length=1, max_length=150000)
    document_type: str | None = None


@app.post('/api/review/text')
def text_review(data: ReviewInput, request: Request, user=Depends(current_user)):
    result = review_text(data.text, data.document_type)
    result.setdefault('id', uid())
    result.setdefault('filename', 'Текст документа')
    request.app.state.db.audit(user['username'], 'review.text', result['id'], {'findings': len(result.get('findings', []))})
    return result


@app.post('/api/review')
async def file_review(request: Request, file: UploadFile = File(...), user=Depends(current_user)):
    rate.check('review:' + user['id'], 15)
    name = Path(file.filename or 'Документ').name
    path = await save_upload(file)
    try:
        result = await run_in_threadpool(review_document, path)
        result['filename'] = name
        result.setdefault('id', uid())
        request.app.state.db.audit(user['username'], 'review.file', result['id'], {'findings': len(result.get('findings', []))})
        return result
    finally:
        path.unlink(missing_ok=True)


@app.get('/api/audit')
def audit(request: Request, user=Depends(admin)):
    return {'events': request.app.state.db.audit_events()}


@app.get('/api/metrics')
def metrics(request: Request, user=Depends(current_user)):
    stats = request.app.state.knowledge.stats()
    h = request.app.state.llm.health()
    return {**stats, **request.app.state.db.metrics(user['id']), 'model': h.get('model'), 'mode': 'llm' if h.get('model_available') else 'extractive'}


@app.get('/api/demo/scenarios')
def scenarios(user=Depends(current_user)):
    return {'scenarios': [
        {'id': 'answer', 'title': 'Ответ с доказательством', 'description': 'Найдите норму и откройте её точный пункт.', 'type': 'chat', 'prompt': 'Сколько рабочих дней рассматривают проект распоряжения и нужен ли новый круг визирования после изменения исполнителя?'},
        {'id': 'draft', 'title': 'От задачи к документу', 'description': 'Соберите рабочую группу, получите заполненный DOCX.', 'type': 'template', 'template_id': 'workgroup'},
        {'id': 'route', 'title': 'Объяснимое визирование', 'description': 'Ввод ИС: покажите обязательные роли и основания.', 'type': 'route'},
        {'id': 'review', 'title': 'Найдите ошибку до согласования', 'description': 'Загрузите проект или вставьте текст для проверки.', 'type': 'review'},
        {'id': 'version', 'title': 'Новые правила без переобучения', 'description': 'Загрузите демонстрационный шаблон, активируйте и заполните.', 'type': 'knowledge'},
        {'id': 'unknown', 'title': 'Честное отсутствие ответа', 'description': 'Помощник не должен выдумывать сведения вне базы.', 'type': 'chat', 'prompt': 'Какой у меня остаток отпуска и зарплата за октябрь?'},
    ]}


DIST = ROOT / 'frontend' / 'dist'


@app.get('/{path:path}', include_in_schema=False)
def spa(path: str):
    if path.startswith('api/'):
        raise HTTPException(404, 'API не найден.')
    target = (DIST / path).resolve()
    if not target.is_relative_to(DIST.resolve()):
        raise HTTPException(404)
    if target.is_file():
        return FileResponse(target)
    if (DIST / 'index.html').exists():
        return FileResponse(DIST / 'index.html')
    return JSONResponse({'message': 'Регула API работает. Запустите frontend: npm run dev, либо npm run build.'})
