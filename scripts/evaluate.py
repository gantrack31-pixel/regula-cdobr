"""Reproducible retrieval checks and optional live local-model smoke evaluation.

No external calls or synthetic performance claims. See output/evaluation.json.
"""
import argparse
import json
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.app.knowledge import KnowledgeStore
from backend.app.llm import LLMClient

CASES = [
    ('incoming_registration', 'В какой срок регистрируется поступивший документ?', 'instruction', 'п. 5.6'),
    ('font', 'Какой шрифт и размер текста используются для документов?', 'instruction', 'п. 8.16'),
    ('review_order', 'Сколько рабочих дней рассматривают проект приказа?', 'instruction', 'п. 10.3'),
    ('review_directive', 'Сколько рабочих дней рассматривают проект распоряжения?', 'instruction', 'п. 10.3'),
    ('visas_sequence', 'В какой последовательности визируется проект документа?', 'instruction', 'п. 10.6'),
    ('mandatory_matrix', 'Кто визирует приказ о вводе информационной системы в эксплуатацию?', 'approval-matrix', None),
    ('modular', 'Какие правила согласования применяются при изменении модульного документа?', 'module-guidance', None),
]

def main():
    p = argparse.ArgumentParser()
    p.add_argument('--live', action='store_true', help='Also generate real answers via local Ollama')
    p.add_argument('--output', default=str(ROOT / 'output' / 'evaluation.json'))
    args = p.parse_args()
    llm = LLMClient()
    store = KnowledgeStore(ROOT / 'data', llm)
    store.seed(ROOT / 'data' / 'sources' / 'manifest.json')
    results = []
    for cid, question, document_id, section in CASES:
        start = time.monotonic()
        found = store.search(question, limit=6)
        match = any(c['document_id'] == document_id and (section is None or c['section'] == section) for c in found)
        result = {'id': cid, 'question': question, 'expected_document': document_id, 'expected_section': section,
                  'retrieval_hit_at_6': match, 'retrieval_ms': round((time.monotonic()-start)*1000),
                  'found': [{k: c.get(k) for k in ('document_id', 'section', 'page', 'retrieval')} for c in found]}
        if args.live:
            start = time.monotonic()
            answer = store.answer(question)
            result.update(answer=answer, generation_ms=round((time.monotonic()-start)*1000))
        results.append(result)
    if args.live:
        results.append({'id': 'unknown', 'question': 'Какая у меня зарплата за октябрь?',
                        'answer': store.answer('Какая у меня зарплата за октябрь?')})
    report = {'timestamp': time.strftime('%Y-%m-%dT%H:%M:%S%z'), 'models': llm.health(),
              'corpus': store.stats(), 'checks': len(CASES),
              'retrieval_hits_at_6': sum(r.get('retrieval_hit_at_6', False) for r in results),
              'note': 'Небольшая проверка поиска по известным вопросам; не оценка смысловой точности всех ответов.',
              'results': results}
    target = Path(args.output)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({k: v for k, v in report.items() if k != 'results'}, ensure_ascii=False))

if __name__ == '__main__':
    main()
