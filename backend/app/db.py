from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
from datetime import datetime, timezone, timedelta
from contextlib import contextmanager


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def uid() -> str:
    return secrets.token_hex(12)


def password_hash(password: str, salt: str | None = None) -> str:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(salt), 310000).hex()
    return salt + ':' + digest


ROLES = frozenset({'employee', 'curator', 'admin'})


def validate_password(password: str):
    if not isinstance(password, str) or not 12 <= len(password) <= 500 or not password.strip():
        raise ValueError('Пароль должен содержать от 12 до 500 символов и не состоять только из пробелов.')


class AppDB:
    def __init__(self, path: Path, demo_mode: bool, bootstrap_password: str | None = None):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS users(id TEXT PRIMARY KEY, username TEXT UNIQUE NOT NULL, name TEXT NOT NULL, role TEXT NOT NULL, password TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS sessions(token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL, expires_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS conversations(id TEXT PRIMARY KEY, user_id TEXT NOT NULL, title TEXT NOT NULL, updated_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS messages(id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL, role TEXT NOT NULL, content TEXT NOT NULL, response TEXT, created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS drafts(id TEXT PRIMARY KEY, user_id TEXT NOT NULL, template_id TEXT NOT NULL, title TEXT NOT NULL, data TEXT NOT NULL, updated_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS audit(id TEXT PRIMARY KEY, created_at TEXT NOT NULL, actor TEXT NOT NULL, action TEXT NOT NULL, target TEXT, details TEXT);
                CREATE INDEX IF NOT EXISTS idx_conversations_user ON conversations(user_id,updated_at);
                CREATE INDEX IF NOT EXISTS idx_messages_conversation ON messages(conversation_id,created_at);
                CREATE INDEX IF NOT EXISTS idx_drafts_user ON drafts(user_id,updated_at);
            ''')
            columns = {row[1] for row in db.execute('PRAGMA table_info(users)')}
            if 'disabled' not in columns:
                db.execute('ALTER TABLE users ADD COLUMN disabled INTEGER NOT NULL DEFAULT 0')
            if not db.execute("SELECT 1 FROM users WHERE username='admin'").fetchone():
                password = bootstrap_password if bootstrap_password is not None else os.getenv('ADMIN_PASSWORD')
                if not password:
                    if not demo_mode:
                        raise RuntimeError('Set ADMIN_PASSWORD (at least 12 characters) for production first start.')
                    password = secrets.token_urlsafe(24)
                    (path.parent / 'admin-password.txt').write_text(password, encoding='utf-8')
                validate_password(password)
                db.execute('INSERT INTO users(id,username,name,role,password) VALUES(?,?,?,?,?)', ('admin', 'admin', 'Администратор', 'admin', password_hash(password)))
            if demo_mode:
                for role, name in [('employee', 'Анна Морозова'), ('curator', 'Михаил Орлов'), ('admin', 'Администратор демо')]:
                    db.execute('INSERT OR IGNORE INTO users(id,username,name,role,password) VALUES(?,?,?,?,?)', ('demo-' + role, 'demo-' + role, name, role, password_hash(secrets.token_urlsafe(32))))

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA journal_mode=WAL')
        db.execute('PRAGMA foreign_keys=ON')
        try:
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def user(self, user_id: str):
        with self.connect() as db:
            row = db.execute('SELECT id,username,name,role FROM users WHERE id=?', (user_id,)).fetchone()
            return dict(row) if row else None

    def authenticate(self, username: str, password: str):
        with self.connect() as db:
            row = db.execute('SELECT * FROM users WHERE username=?', (username,)).fetchone()
        # Use the same derivation even for unknown users.
        stored = row['password'] if row else '0' * 32 + ':' + '0' * 64
        calculated = password_hash(password, stored.split(':')[0])
        if row and not row['disabled'] and secrets.compare_digest(stored, calculated):
            return {key: row[key] for key in ('id', 'username', 'name', 'role')}
        return None

    def new_session(self, user_id: str):
        token = secrets.token_urlsafe(32)
        expires = (datetime.now(timezone.utc) + timedelta(hours=8)).isoformat()
        with self.connect() as db:
            if not db.execute('SELECT 1 FROM users WHERE id=? AND disabled=0', (user_id,)).fetchone():
                raise ValueError('Учётная запись не найдена или отключена.')
            db.execute('DELETE FROM sessions WHERE expires_at<?', (now(),))
            db.execute('INSERT INTO sessions VALUES(?,?,?)', (hashlib.sha256(token.encode()).hexdigest(), user_id, expires))
        return token

    def session_user(self, token: str | None):
        if not token:
            return None
        with self.connect() as db:
            row = db.execute('SELECT u.id,u.username,u.name,u.role FROM sessions s JOIN users u ON u.id=s.user_id '
                             'WHERE s.token_hash=? AND s.expires_at>? AND u.disabled=0',
                             (hashlib.sha256(token.encode()).hexdigest(), now())).fetchone()
        return dict(row) if row else None

    def list_users(self):
        with self.connect() as db:
            rows = db.execute('SELECT id,username,name,role,disabled FROM users ORDER BY username').fetchall()
        return [{**dict(row), 'disabled': bool(row['disabled'])} for row in rows]

    def create_user(self, username: str, name: str, role: str, password: str):
        if not isinstance(username, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.@-]{2,99}', username):
            raise ValueError('Логин: от 3 до 100 латинских букв, цифр и символов _.@-, начиная с буквы или цифры.')
        if username.startswith('demo-'):
            raise ValueError('Префикс demo- зарезервирован для демонстрационных учётных записей.')
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 200:
            raise ValueError('Укажите имя длиной от 1 до 200 символов.')
        if role not in ROLES:
            raise ValueError('Допустимые роли: employee, curator, admin.')
        validate_password(password)
        hashed = password_hash(password)
        user_id = uid()
        try:
            with self.connect() as db:
                db.execute('INSERT INTO users(id,username,name,role,password,disabled) VALUES(?,?,?,?,?,0)',
                           (user_id, username, name.strip(), role, hashed))
        except sqlite3.IntegrityError as exc:
            raise ValueError('Учётная запись с таким логином уже существует.') from exc
        self.audit('local-admin', 'account.create', user_id, {'username': username, 'role': role})
        return self.user(user_id)

    def set_password(self, username: str, password: str):
        validate_password(password)
        hashed = password_hash(password)
        with self.connect() as db:
            row = db.execute('SELECT id FROM users WHERE username=?', (username,)).fetchone()
            if not row:
                raise ValueError('Учётная запись не найдена.')
            db.execute('UPDATE users SET password=? WHERE id=?', (hashed, row['id']))
            db.execute('DELETE FROM sessions WHERE user_id=?', (row['id'],))
        self.audit('local-admin', 'account.password-reset', row['id'])

    def set_disabled(self, username: str, disabled: bool = True):
        with self.connect() as db:
            row = db.execute('SELECT id FROM users WHERE username=?', (username,)).fetchone()
            if not row:
                raise ValueError('Учётная запись не найдена.')
            db.execute('UPDATE users SET disabled=? WHERE id=?', (int(bool(disabled)), row['id']))
            db.execute('DELETE FROM sessions WHERE user_id=?', (row['id'],))
        self.audit('local-admin', 'account.disable' if disabled else 'account.enable', row['id'])

    def disable_user(self, username: str):
        self.set_disabled(username, True)

    def logout(self, token: str | None):
        if token:
            with self.connect() as db:
                db.execute('DELETE FROM sessions WHERE token_hash=?', (hashlib.sha256(token.encode()).hexdigest(),))

    def audit(self, actor: str, action: str, target: str = '', details=None):
        with self.connect() as db:
            db.execute('INSERT INTO audit VALUES(?,?,?,?,?,?)', (uid(), now(), actor, action, target, json.dumps(details or {}, ensure_ascii=False)))

    def audit_events(self):
        with self.connect() as db:
            rows = db.execute('SELECT * FROM audit ORDER BY created_at DESC LIMIT 250').fetchall()
        return [{**dict(r), 'details': json.loads(r['details'])} for r in rows]

    def conversation(self, conversation_id, user_id):
        with self.connect() as db:
            row = db.execute('SELECT * FROM conversations WHERE id=? AND user_id=?', (conversation_id, user_id)).fetchone()
            if not row:
                return None
            messages = db.execute('SELECT * FROM messages WHERE conversation_id=? ORDER BY created_at,rowid', (conversation_id,)).fetchall()
        return {**dict(row), 'messages': [{**dict(m), 'response': json.loads(m['response']) if m['response'] else None} for m in messages]}

    def conversations(self, user_id):
        with self.connect() as db:
            return [dict(r) for r in db.execute('SELECT id,title,updated_at FROM conversations WHERE user_id=? ORDER BY updated_at DESC', (user_id,))]

    def create_conversation(self, user_id, title):
        cid = uid()
        with self.connect() as db:
            db.execute('INSERT INTO conversations VALUES(?,?,?,?)', (cid, user_id, title[:100], now()))
        return cid

    def add_message(self, conversation_id, role, content, response=None):
        with self.connect() as db:
            db.execute('INSERT INTO messages VALUES(?,?,?,?,?,?)', (uid(), conversation_id, role, content, json.dumps(response, ensure_ascii=False) if response else None, now()))
            db.execute('UPDATE conversations SET updated_at=? WHERE id=?', (now(), conversation_id))

    def delete_conversation(self, conversation_id, user_id):
        with self.connect() as db:
            exists = db.execute('SELECT 1 FROM conversations WHERE id=? AND user_id=?', (conversation_id, user_id)).fetchone()
            if not exists:
                return False
            db.execute('DELETE FROM messages WHERE conversation_id=?', (conversation_id,))
            db.execute('DELETE FROM conversations WHERE id=?', (conversation_id,))
        return True

    def save_draft(self, draft, user_id):
        with self.connect() as db:
            db.execute('INSERT INTO drafts VALUES(?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET title=excluded.title,data=excluded.data,updated_at=excluded.updated_at',
                       (draft['id'], user_id, draft['template_id'], draft['title'], json.dumps(draft, ensure_ascii=False), now()))

    def draft(self, draft_id, user_id):
        with self.connect() as db:
            row = db.execute('SELECT data FROM drafts WHERE id=? AND user_id=?', (draft_id, user_id)).fetchone()
            return json.loads(row['data']) if row else None

    def drafts(self, user_id):
        with self.connect() as db:
            return [json.loads(r['data']) for r in db.execute('SELECT data FROM drafts WHERE user_id=? ORDER BY updated_at DESC', (user_id,))]

    def metrics(self, user_id):
        with self.connect() as db:
            return {'drafts': db.execute('SELECT count(*) FROM drafts WHERE user_id=?', (user_id,)).fetchone()[0],
                    'conversations': db.execute('SELECT count(*) FROM conversations WHERE user_id=?', (user_id,)).fetchone()[0],
                    'events': db.execute('SELECT count(*) FROM audit').fetchone()[0]}
