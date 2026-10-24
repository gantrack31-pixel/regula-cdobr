"""Local account administration. Passwords are never command-line arguments."""
from __future__ import annotations

import argparse
import getpass
import os
from pathlib import Path
import sqlite3
import sys
import warnings

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.app.db import AppDB, ROLES, validate_password


def read_password() -> str:
    # getpass otherwise falls back to echoing stdin on an unsupported terminal.
    with warnings.catch_warnings():
        warnings.simplefilter('error', getpass.GetPassWarning)
        try:
            first = getpass.getpass('Новый пароль (минимум 12 символов): ')
            second = getpass.getpass('Повторите пароль: ')
        except getpass.GetPassWarning as exc:
            raise ValueError('Нужен интерактивный терминал со скрытым вводом пароля.') from exc
    if first != second:
        raise ValueError('Пароли не совпадают.')
    validate_password(first)
    return first


def parser() -> argparse.ArgumentParser:
    cli = argparse.ArgumentParser(description='Локальное управление учётными записями Регулы. Запускайте на сервере с доступом к SQLite.')
    default_db = Path(os.getenv('DATA_DIR', str(ROOT / 'data'))) / 'runtime' / 'app.sqlite3'
    cli.add_argument('--database', type=Path, default=default_db, help='Путь к app.sqlite3; по умолчанию DATA_DIR/runtime/app.sqlite3')
    commands = cli.add_subparsers(dest='command', required=True)
    create = commands.add_parser('create', help='Создать учётную запись')
    create.add_argument('username')
    create.add_argument('--name', required=True, help='Отображаемое имя')
    create.add_argument('--role', choices=sorted(ROLES), default='employee')
    commands.add_parser('list', help='Показать учётные записи без хешей паролей')
    for command, description in [('set-password', 'Сменить пароль и отозвать все сессии'),
                                  ('disable', 'Отключить вход и отозвать все сессии'),
                                  ('enable', 'Восстановить вход; старые сессии не восстановятся')]:
        sub = commands.add_parser(command, help=description)
        sub.add_argument('username')
    return cli


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    path = args.database.expanduser().resolve()
    try:
        bootstrap = not path.exists()
        if bootstrap and not (args.command == 'create' and args.username == 'admin' and args.role == 'admin'):
            raise ValueError('База ещё не создана. Сначала выполните create admin --role admin --name "Администратор".')
        password = read_password() if args.command in {'create', 'set-password'} else None
        db = AppDB(path, demo_mode=False, bootstrap_password=password if bootstrap else None)
        if args.command == 'create':
            if bootstrap:
                with db.connect() as connection:
                    connection.execute('UPDATE users SET name=? WHERE username=?', (args.name[:200], 'admin'))
                db.audit('local-admin', 'account.bootstrap', 'admin')
            else:
                db.create_user(args.username, args.name, args.role, password)
            print(f'Учётная запись {args.username} создана. Роль: {args.role}.')
        elif args.command == 'list':
            for user in db.list_users():
                status = 'отключена' if user['disabled'] else 'активна'
                print(f"{user['username']}\t{user['role']}\t{status}\t{user['name']}")
        elif args.command == 'set-password':
            db.set_password(args.username, password)
            print(f'Пароль {args.username} изменён. Все сессии отозваны.')
        else:
            db.set_disabled(args.username, args.command == 'disable')
            print(f"Учётная запись {args.username} {'отключена' if args.command == 'disable' else 'включена'}. Старые сессии отозваны.")
        return 0
    except (ValueError, RuntimeError, sqlite3.Error, OSError) as exc:
        print(f'Ошибка: {exc}', file=sys.stderr)
        return 1
    except (EOFError, KeyboardInterrupt):
        print('\nОперация отменена.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
