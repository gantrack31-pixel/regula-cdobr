"""Account migrations, disabled access, password resets and local CLI."""
import sqlite3
from pathlib import Path

import pytest

from backend.app.db import AppDB, password_hash
from scripts import manage_users


PASSWORD = 'correct-horse-battery-123'


@pytest.fixture
def db(tmp_path):
    return AppDB(tmp_path / 'app.sqlite3', demo_mode=False, bootstrap_password=PASSWORD)


def test_disabled_account_loses_login_and_all_sessions(db):
    user = db.create_user('employee1', 'Сотрудник', 'employee', PASSWORD)
    token1, token2 = db.new_session(user['id']), db.new_session(user['id'])
    assert db.session_user(token1)['id'] == user['id']
    db.disable_user('employee1')
    assert db.authenticate('employee1', PASSWORD) is None
    assert db.session_user(token1) is None
    assert db.session_user(token2) is None
    with pytest.raises(ValueError, match='отключена'):
        db.new_session(user['id'])
    db.set_disabled('employee1', False)
    assert db.authenticate('employee1', PASSWORD)['id'] == user['id']
    assert db.session_user(token1) is None


def test_password_reset_revokes_sessions_and_never_logs_password(db):
    user = db.create_user('curator1', 'Куратор', 'curator', PASSWORD)
    token = db.new_session(user['id'])
    new_password = 'new-safe-password-987'
    db.set_password('curator1', new_password)
    assert db.authenticate('curator1', PASSWORD) is None
    assert db.authenticate('curator1', new_password)['role'] == 'curator'
    assert db.session_user(token) is None
    assert new_password not in str(db.audit_events())
    assert PASSWORD not in str(db.audit_events())
    assert all('password' not in user for user in db.list_users())


def test_role_and_password_constraints(db):
    with pytest.raises(ValueError, match='роли'):
        db.create_user('invalidrole', 'Тест', 'root', PASSWORD)
    with pytest.raises(ValueError, match='Пароль'):
        db.create_user('shortpass', 'Тест', 'employee', '123')
    with pytest.raises(ValueError, match='зарезервирован'):
        db.create_user('demo-admin', 'Тест', 'admin', PASSWORD)
    with pytest.raises(ValueError, match='существует'):
        db.create_user('admin', 'Дубль', 'admin', PASSWORD)
    with pytest.raises(ValueError, match='не найдена'):
        db.set_password('absent', PASSWORD)


def test_old_database_migrates_without_changing_admin_password(tmp_path, monkeypatch):
    path = tmp_path / 'legacy.sqlite3'
    with sqlite3.connect(path) as connection:
        connection.execute('CREATE TABLE users(id TEXT PRIMARY KEY,username TEXT UNIQUE NOT NULL,name TEXT NOT NULL,role TEXT NOT NULL,password TEXT NOT NULL)')
        connection.execute('INSERT INTO users VALUES(?,?,?,?,?)', ('admin', 'admin', 'Администратор', 'admin', password_hash(PASSWORD)))
    monkeypatch.setenv('ADMIN_PASSWORD', 'different-env-password-456')
    db = AppDB(path, demo_mode=False)
    assert db.authenticate('admin', PASSWORD)
    assert db.authenticate('admin', 'different-env-password-456') is None
    assert db.list_users()[0]['disabled'] is False
    db.set_password('admin', 'different-env-password-456')
    assert db.authenticate('admin', 'different-env-password-456')


def test_cli_bootstrap_create_disable_and_safe_output(tmp_path, monkeypatch, capsys):
    path = tmp_path / 'app.sqlite3'
    monkeypatch.setattr(manage_users, 'read_password', lambda: PASSWORD)
    prefix = ['--database', str(path)]
    assert manage_users.main(prefix + ['create', 'admin', '--name', 'Главный администратор', '--role', 'admin']) == 0
    assert manage_users.main(prefix + ['create', 'staff', '--name', 'Сотрудник', '--role', 'employee']) == 0
    assert manage_users.main(prefix + ['disable', 'staff']) == 0
    assert manage_users.main(prefix + ['list']) == 0
    captured = capsys.readouterr()
    assert PASSWORD not in captured.out + captured.err
    assert 'staff\temployee\tотключена' in captured.out
    db = AppDB(path, demo_mode=False)
    assert db.authenticate('staff', PASSWORD) is None
    assert manage_users.main(prefix + ['enable', 'staff']) == 0
    assert db.authenticate('staff', PASSWORD)


def test_cli_has_no_password_argument_and_rejects_mismatched_prompt(monkeypatch):
    with pytest.raises(SystemExit):
        manage_users.parser().parse_args(['create', 'staff', '--name', 'Тест', '--password', PASSWORD])
    values = iter([PASSWORD, PASSWORD + '!'])
    monkeypatch.setattr(manage_users.getpass, 'getpass', lambda _: next(values))
    with pytest.raises(ValueError, match='не совпадают'):
        manage_users.read_password()
