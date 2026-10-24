"""Download official Apache-2.0 Qwen GGUFs and import into local Ollama.

No document content is sent. Downloads are verified against Hugging Face LFS SHA256.
Normally `ollama pull qwen3:8b` and `ollama pull qwen3-embedding:0.6b` suffice.
This path also works when the registry's CDN redirect is unavailable.
"""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import hashlib
import json
import os
import subprocess
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
MODELS = [
    ('Qwen/Qwen3-8B-GGUF', 'Qwen3-8B-Q4_K_M.gguf', 'qwen3:8b'),
    ('Qwen/Qwen3-Embedding-0.6B-GGUF', 'Qwen3-Embedding-0.6B-Q8_0.gguf', 'qwen3-embedding:0.6b'),
]


def prepare(item):
    repo, name, tag = item
    directory = ROOT / 'tmp' / 'models'
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / name
    with urllib.request.urlopen('https://huggingface.co/api/models/' + repo + '?blobs=true', timeout=60) as response:
        metadata = json.load(response)
    record = next(x for x in metadata['siblings'] if x['rfilename'] == name)
    expected = record.get('lfs', {}).get('sha256')
    expected_size = record.get('size') or record.get('lfs', {}).get('size')
    if not target.exists() or target.stat().st_size != expected_size:
        partial = target.with_suffix('.partial')
        url = 'https://huggingface.co/' + repo + '/resolve/main/' + name
        with urllib.request.urlopen(url, timeout=120) as response, partial.open('wb') as output:
            total, report = 0, 0
            while block := response.read(8 * 1024 * 1024):
                output.write(block)
                total += len(block)
                if total - report >= 256 * 1024 * 1024:
                    print(f'{tag}: {total // 1048576} MiB', flush=True)
                    report = total
        partial.replace(target)
    digest = hashlib.file_digest(target.open('rb'), 'sha256').hexdigest()
    if not expected or digest != expected:
        raise RuntimeError(f'Checksum verification failed: {name}')
    modelfile = directory / (tag.replace(':', '-') + '.Modelfile')
    extra = '\nPARAMETER num_ctx 8192\nPARAMETER temperature 0.2\n' if tag == 'qwen3:8b' else '\n'
    modelfile.write_text('FROM "' + target.as_posix() + '"' + extra, encoding='utf-8')
    print(f'{tag}: SHA256 verified, importing', flush=True)
    result = subprocess.run(['ollama', 'create', tag, '-f', str(modelfile)], capture_output=True, text=True, encoding='utf-8', errors='replace')
    if result.returncode:
        raise RuntimeError(result.stderr[-3000:])
    print(f'{tag}: ready', flush=True)


if __name__ == '__main__':
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(prepare, MODELS))
