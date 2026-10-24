"""Windows: temporary authenticated HTTPS access through Cloudflare Quick Tunnel."""
from pathlib import Path
import hashlib
import json
import os
import re
import secrets
import socket
import subprocess
import sys
import time

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.app.db import AppDB

WORK = ROOT / 'tmp' / 'remote-access'
PRIVATE = ROOT / 'data' / 'runtime'
PORT = 8767


def main():
    if os.name != 'nt':
        raise SystemExit('This launcher is intended for Windows.')
    with socket.socket() as probe:
        if probe.connect_ex(('127.0.0.1', PORT)) == 0:
            raise SystemExit('Port 8767 is already occupied. Stop the previous remote instance first.')
    WORK.mkdir(parents=True, exist_ok=True)
    PRIVATE.mkdir(parents=True, exist_ok=True)
    executable = WORK / 'cloudflared.exe'
    metadata = WORK / 'release.json'
    with httpx.Client(follow_redirects=True, timeout=90) as client:
        if not metadata.exists():
            response = client.get('https://api.github.com/repos/cloudflare/cloudflared/releases/latest')
            response.raise_for_status()
            asset = next(a for a in response.json()['assets'] if a['name'] == 'cloudflared-windows-amd64.exe')
            metadata.write_text(json.dumps(asset), encoding='utf-8')
        asset = json.loads(metadata.read_text(encoding='utf-8'))
        expected = asset.get('digest', '')
        if not re.fullmatch(r'sha256:[0-9a-f]{64}', expected):
            raise RuntimeError('Official release has no SHA-256 digest.')
        if not executable.exists() or 'sha256:' + hashlib.sha256(executable.read_bytes()).hexdigest() != expected:
            response = client.get(asset['browser_download_url'])
            response.raise_for_status()
            if 'sha256:' + hashlib.sha256(response.content).hexdigest() != expected:
                raise RuntimeError('Download checksum mismatch.')
            executable.write_bytes(response.content)
    db = AppDB(PRIVATE / 'app.sqlite3', demo_mode=False)
    credentials_file = PRIVATE / 'remote-access-credentials.json'
    if credentials_file.exists():
        credentials = json.loads(credentials_file.read_text(encoding='utf-8'))
        if not db.authenticate(credentials['username'], credentials['password']):
            raise RuntimeError('Saved remote credentials are no longer valid; use the account CLI.')
    else:
        credentials = {'username': 'remote-owner', 'password': secrets.token_urlsafe(24)}
        db.create_user(credentials['username'], 'Владелец проекта', 'admin', credentials['password'])
        credentials_file.write_text(json.dumps(credentials, ensure_ascii=False), encoding='utf-8')
    tunnel_log = WORK / 'tunnel.log'
    tunnel_output = tunnel_log.open('w', encoding='utf-8')
    tunnel = subprocess.Popen([str(executable), 'tunnel', '--no-autoupdate', '--protocol', 'http2',
                               '--url', f'http://127.0.0.1:{PORT}', '--metrics', '127.0.0.1:0'],
                              cwd=ROOT, stdout=tunnel_output, stderr=subprocess.STDOUT,
                              creationflags=subprocess.CREATE_NO_WINDOW)
    server = None
    try:
        url = None
        for _ in range(90):
            if tunnel.poll() is not None:
                raise RuntimeError('Tunnel stopped. See tmp/remote-access/tunnel.log.')
            match = re.search(r'https://[a-z0-9-]+\.trycloudflare\.com', tunnel_log.read_text(encoding='utf-8', errors='replace'))
            if match:
                url = match.group(0)
                break
            time.sleep(1)
        if not url:
            raise RuntimeError('Cloudflare did not return a URL within 90 seconds.')
        for _ in range(35):
            log = tunnel_log.read_text(encoding='utf-8', errors='replace')
            if 'Registered tunnel connection' in log:
                break
            if tunnel.poll() is not None or 'hard_fail=true' in log:
                raise RuntimeError('Cloudflare edge is unreachable from this network. No working public link was created.')
            time.sleep(1)
        else:
            raise RuntimeError('Cloudflare connection was not established. No working public link was created.')
        host = url.split('://', 1)[1]
        env = {**os.environ, 'DEMO_MODE': '0', 'COOKIE_SECURE': '1',
               'ALLOWED_HOSTS': f'127.0.0.1,localhost,{host}', 'ALLOWED_ORIGINS': host,
               'DATA_DIR': str(ROOT / 'data')}
        server_output = (WORK / 'server.log').open('w', encoding='utf-8')
        server = subprocess.Popen([sys.executable, '-X', 'utf8', '-m', 'uvicorn', 'backend.app.main:app',
                                   '--host', '127.0.0.1', '--port', str(PORT), '--no-access-log'],
                                  cwd=ROOT, env=env, stdout=server_output, stderr=subprocess.STDOUT,
                                  creationflags=subprocess.CREATE_NO_WINDOW)
        state = {'url': url, 'tunnel_pid': tunnel.pid, 'server_pid': server.pid, 'port': PORT,
                 'credentials_file': str(credentials_file), 'verified': False}
        state_file = WORK / 'state.json'
        state_file.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding='utf-8')
        with httpx.Client(trust_env=False, timeout=5) as client:
            for _ in range(45):
                try:
                    response = client.get(f'http://127.0.0.1:{PORT}/api/health')
                    if response.status_code == 200 and response.json().get('demo_mode') is False:
                        break
                except httpx.HTTPError:
                    pass
                time.sleep(1)
            else:
                raise RuntimeError('Protected server did not become ready.')
        with httpx.Client(base_url=url, timeout=20, follow_redirects=True) as client:
            response = client.get('/api/health')
            response.raise_for_status()
            if response.json().get('demo_mode') is not False:
                raise RuntimeError('Remote instance must disable demonstration login.')
            if client.get('/api/sources').status_code != 401:
                raise RuntimeError('Anonymous data access must be denied.')
            if client.post('/api/auth/demo', json={'role': 'admin'}, headers={'Origin': url}).status_code != 403:
                raise RuntimeError('Demonstration login is not disabled.')
            response = client.post('/api/auth/login', json=credentials, headers={'Origin': url})
            response.raise_for_status()
            cookie = response.headers.get('set-cookie', '').lower()
            if not all(flag in cookie for flag in ('secure', 'httponly', 'samesite=lax')):
                raise RuntimeError('Session cookie protections are missing.')
            client.get('/api/auth/me').raise_for_status()
            client.post('/api/auth/logout', headers={'Origin': url}).raise_for_status()
        state['verified'] = True
        state_file.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps(state, ensure_ascii=False), flush=True)
    except BaseException:
        tunnel.terminate()
        if server:
            subprocess.run(['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass',
                            '-File', str(ROOT / 'scripts' / 'stop_remote.ps1')],
                           cwd=ROOT, creationflags=subprocess.CREATE_NO_WINDOW, check=False)
        raise


if __name__ == '__main__':
    main()
