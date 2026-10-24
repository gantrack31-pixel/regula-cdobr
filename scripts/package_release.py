"""Package source, provided educational inputs and deliverables; omit runtime data/secrets."""
from pathlib import Path
import hashlib
import json
import os
import zipfile

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / 'output'
EXCLUDED = {'.git', '.venv', 'node_modules', '__pycache__', '.pytest_cache', 'tmp', 'runtime'}
ROOT_FILES = {'.dockerignore', '.env.example', '.gitignore', 'Dockerfile', 'LICENSE', 'README.md',
              'compose.yaml', 'compose.ollama.yaml', 'requirements.txt', 'requirements-dev.txt'}
DIRECTORIES = {'backend', 'frontend', 'data', 'demo', 'docs', 'scripts', 'tests'}
ARTIFACT_SUFFIXES = {'.pptx', '.pdf', '.mp4', '.docx', '.json', '.xml'}


def main():
    OUTPUT.mkdir(exist_ok=True)
    files = []
    for directory, children, names in os.walk(ROOT):
        children[:] = [name for name in children if name not in EXCLUDED]
        for name in names:
            file = Path(directory) / name
            relative = file.relative_to(ROOT)
            if file.name == '.env' or file.suffix in {'.pyc', '.log', '.tsbuildinfo'}:
                continue
            if (len(relative.parts) == 1 and file.name in ROOT_FILES
                    or relative.parts[0] in DIRECTORIES
                    or relative.parts[0] == 'output' and file.suffix in ARTIFACT_SUFFIXES and file.name != 'release-manifest.json'):
                files.append(file)
    manifest = [{'path': str(f.relative_to(ROOT)).replace('\\', '/'), 'bytes': f.stat().st_size,
                 'sha256': hashlib.sha256(f.read_bytes()).hexdigest()} for f in sorted(files)]
    archive = OUTPUT / 'Regula-hackathon.zip'
    with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED, compresslevel=7) as z:
        for file in files:
            z.write(file, 'Regula/' + str(file.relative_to(ROOT)).replace('\\', '/'))
        z.writestr('Regula/release-manifest.json', json.dumps(manifest, ensure_ascii=False, indent=2))
    with zipfile.ZipFile(archive) as z:
        assert z.testzip() is None
        assert not any('/runtime/' in n or '/node_modules/' in n or n.endswith('/.env') for n in z.namelist())
        for entry in manifest:
            payload = z.read('Regula/' + entry['path'])
            assert len(payload) == entry['bytes']
            assert hashlib.sha256(payload).hexdigest() == entry['sha256']
    receipt = {'archive': archive.name, 'files': len(files), 'bytes': archive.stat().st_size,
               'sha256': hashlib.sha256(archive.read_bytes()).hexdigest(), 'all_file_hashes_verified': True}
    (OUTPUT / 'release-manifest.json').write_text(json.dumps(receipt, indent=2), encoding='utf-8')
    print(json.dumps(receipt))


if __name__ == '__main__':
    main()
