# Контракт приложения Регула

Русскоязычное локальное приложение: React + TypeScript + Vite, FastAPI, SQLite, Ollama. Все API начинаются `/api`, JSON UTF-8, ошибки `{detail: string}`. Аутентификация cookie HttpOnly, fetch credentials same-origin. Vite proxy `/api` → `http://127.0.0.1:8765`. Production FastAPI serves frontend/dist. Время ISO8601 UTC.

## Пользователь

`GET /health` → `{status, model, model_available, embedding_model, embedding_available, mode, source_count, chunk_count, demo_mode}`.
`GET /auth/me` → `{id, username, name, role: employee|curator|admin, demo_mode}`; 401 без сессии.
`POST /auth/demo {role: employee|curator|admin}` → пользователь (только при DEMO_MODE=1; очевидный учебный режим).
`POST /auth/login {username,password}`, `POST /auth/logout`.

## Диалог и источники

`POST /chat {message, conversation_id?:string, as_of?:string}` → `{conversation_id, answer, status: grounded|insufficient|clarification|error, mode: llm|extractive, citations: Citation[], suggested_questions:string[], template_suggestions:string[], route?:object, elapsed_ms, trace:{retrieval,model,version_policy}}`.
`Citation = {id, document_id, version_id, title, section, page:number|null, quote, score:number, source_url}`. Score — относительный поисковый балл, не вероятность правильности.
`GET /conversations` → `[{id,title,updated_at}]`; `GET /conversations/{id}` → `{id,title,messages:[{id,role,content,response?:ChatResponse,created_at}]}`; `DELETE /conversations/{id}`.
`GET /search?q=...` → `{results: Citation[]}`.
`GET /sources` → `{sources:[{id,title,kind,access,active_version_id,version,chunk_count,updated_at,synthetic,versions:[{id,version,status,created_at,effective_from,filename,chunk_count}]}]}`.
`GET /sources/{id}` → source object plus `chunks:[{id,section,page,text,version_id}]`.
`GET /sources/{id}/file?version_id=...` → authenticated original file.
`POST /sources/upload` multipart `file,title,kind:instruction|matrix|guidance|template|other,access:all|curator|admin,document_id?:string,effective_from?:YYYY-MM-DD` → `{document_id,version_id,version,chunk_count,status:'draft'}`; curator/admin only.
`POST /sources/{id}/activate {version_id}` → source; curator/admin only. Versions immutable, one active.
`GET /sources/{id}/diff?from_version=...&to_version=...` → `{added:string[],removed:string[],unchanged_count}`.

## Шаблоны и проекты

`GET /templates` → `{templates: Template[]}`; `GET /templates/{id}` → Template.
`Template = {id,title,description,document_type,version,source_document_id?,fields:Field[],hints:string[],tags:string[]}`.
`Field = {key,label,type:text|textarea|date|boolean|people,required,placeholder?,help?,default?}`; people value `[{role,department,position,name}]`.
`POST /drafts {template_id,values?:object}` → Draft.
`Draft = {id,template_id,title,values,missing_fields:string[],validation:[{field,severity: error|warning,message,rule?}],preview:string,readiness:number,revision:number,confirmed_fields:string[],unconfirmed_fields:string[],field_evidence:object,updated_at,route?:object}`. `readiness` — заполненность, не разрешение на подпись.
`GET /drafts` → `{drafts:Draft[]}`; `GET /drafts/{id}` → Draft; `PATCH /drafts/{id} {values:object,expected_revision?:number,confirm_fields?:string[]}` → Draft. Сервер атомарно проверяет ревизию при записи; несовпадение даёт 409, клиент должен загрузить свежий проект. Ручные изменения подтверждаются; неизменённые поля можно явно подтвердить через `confirm_fields`.
`POST /drafts/{id}/suggest {message}` → `{values,evidence,conflicts,base_revision,mode}`. Не меняет проект. Для применения выбранных значений используется PATCH с `expected_revision: base_revision`. Цитаты оснований берутся из сообщения пользователя. `mode: explicit|hybrid` различает программное извлечение и участие LLM.
`POST /drafts/{id}/assist {message}` → `{draft:Draft,answer:string,updated_fields:string[]}`. Совместимый API автоматического заполнения: изменения сохраняются, но остаются неподтверждёнными. Основной интерфейс использует `/suggest`.
`POST /drafts/{id}/export` → DOCX file; may export incomplete draft with explicit field placeholders, response headers X-Draft-Incomplete; never fabricate signature/date/registration number.

`X-Draft-Incomplete: true` возвращается также для заполненного проекта с неподтверждёнными сведениями. При ограничении частоты сервер возвращает 429 и `Retry-After`; занятые слоты модели не должны порождать неограниченную очередь запросов.

Ответ `/chat` дополнительно содержит `answer_kind` и `answer_label`: интерфейс различает точную норму, выбранные моделью проверенные цитаты и поисковые выдержки. Идентификаторы ссылок присваивает сервер. Статус `grounded` означает наличие проверенных оснований в текущем ответе, а не юридическую экспертизу.

## Визирование и проверка

`POST /route {document_type: order|directive|letter,topic?:string,it_project?:boolean,access_change?:boolean,recipient_type?: government|foreign|russian_legal|internal,modular?:boolean,changed?:boolean,temporary_group?:boolean,personnel?:boolean,additional_departments?:string[]}` → `{title,steps:[{order,name,reason,source}],departments:[{name,reason,source,conditional?:boolean}],notes:string[],missing_questions:string[],full_approval:boolean}`.
`POST /review` multipart file OR `POST /review/text {text,document_type?:string}` → `{id,filename,document_type,summary,score,findings:[{id,severity:error|warning|info,title,detail,source,location?,suggestion?}],route:object,extracted_text,checks:[{name,status}],limitations:string[]}`. Checks never claim full legal/semantic validation.

## Аудит и демо

`GET /audit` → `{events:[{id,created_at,actor,action,target,details}]}`; admin only.
`GET /metrics` → `{sources,versions,chunks,drafts,conversations,events,model,mode}`.
`GET /demo/scenarios` → `{scenarios:[{id,title,description,prompt?,template_id?,type}]}`.
