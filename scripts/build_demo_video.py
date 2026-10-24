"""Build a silent, explicitly labelled overview from real application screenshots.

The interface is contained without cropping or fabricated UI. Only title cards,
captions and short crossfades are generated. Requires Pillow and PyAV/libx264.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]

SCENES = [
    ('home', 'Одно рабочее пространство',
     'Ответы по регламентам, подготовка проектов, проверка и база знаний.'),
    ('answer', 'Ответ можно проверить',
     'Норма о регистрации: пункт 5.6, страница 16, дословная цитата и исходник.'),
    ('followup', 'Диалог сохраняет контекст',
     'Уточнение продолжается в том же диалоге. Ответ опирается на доступные материалы.'),
    ('library', 'Семь модулей организаторов',
     'Выбор проекта с готовой структурой и полями для конкретной задачи.'),
    ('documents', 'От данных к проекту DOCX',
     'Обязательные поля, предупреждения и предпросмотр. Номер и подпись не выдумываются.'),
    ('route', 'Обязательные визы с основаниями',
     'Маршрут учитывает тип документа, содержание, ИТ и изменение прав доступа.'),
    ('review', 'Ошибки видны до согласования',
     'В этом проекте — 2 ошибки и 2 вопроса для проверки. Замечания связаны с нормами.'),
    ('upload', 'Куратор добавляет новый источник',
     'PDF, DOCX, TXT и MD. Уровень доступа и черновая редакция до активации.'),
    ('knowledge', 'База знаний под контролем',
     'Исходники, версии и доступ. Активная учебная редакция обозначена явно.'),
    ('versions', 'Изменения можно сравнить',
     'До активации видны добавленные и удалённые фрагменты. История сохраняется.'),
    ('newmodule', 'Новый модуль становится формой',
     'Шаблон с именованными полями становится конструктором без переобучения модели.'),
    ('audit', 'Значимые действия остаются в журнале',
     'Кто загрузил источник, активировал редакцию или создал проект. Доступ администратора.'),
]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--screens', type=Path, default=ROOT / 'tmp' / 'demo' / 'screens')
    parser.add_argument('--output', type=Path, default=ROOT / 'output' / 'Регула — демонстрация.mp4')
    parser.add_argument('--font', type=Path, default=Path('C:/Windows/Fonts/arial.ttf'))
    parser.add_argument('--bold-font', type=Path, default=Path('C:/Windows/Fonts/arialbd.ttf'))
    parser.add_argument('--av-path', type=Path, default=ROOT / 'tmp' / 'study' / 'video_deps')
    parser.add_argument('--fps', type=int, default=24)
    parser.add_argument('--scene-seconds', type=float, default=9)
    args = parser.parse_args()
    if not 10 <= args.fps <= 60 or not 7.6 <= args.scene_seconds <= 11.5:
        parser.error('Используйте 10–60 fps и 7,6–11,5 секунды на экран (общая длительность 100–150 секунд).')
    try:
        import av
    except ImportError:
        if args.av_path.is_dir():
            sys.path.insert(0, str(args.av_path.resolve()))
        import av
    from PIL import Image, ImageDraw, ImageFont, ImageOps

    missing = [str(args.screens / f'{name}.png') for name, _, _ in SCENES if not (args.screens / f'{name}.png').is_file()]
    if missing:
        raise FileNotFoundError('Не хватает реальных экранов: ' + ', '.join(missing))
    if not args.font.is_file() or not args.bold_font.is_file():
        raise FileNotFoundError('Укажите кириллические TrueType-шрифты через --font и --bold-font.')

    width, screen_height, height = 1280, 720, 840
    background, accent, white, muted = '#101E38', '#4268FF', '#FFFFFF', '#B9C7E0'
    fonts = {
        'brand': ImageFont.truetype(str(args.bold_font), 90),
        'hero': ImageFont.truetype(str(args.bold_font), 47),
        'title': ImageFont.truetype(str(args.bold_font), 29),
        'body': ImageFont.truetype(str(args.font), 21),
        'intro': ImageFont.truetype(str(args.font), 28),
        'step': ImageFont.truetype(str(args.bold_font), 36),
        'small': ImageFont.truetype(str(args.font), 16),
    }

    def wrap(draw, text, font, max_width):
        lines, line = [], ''
        for word in text.split():
            proposed = (line + ' ' + word).strip()
            if line and draw.textlength(proposed, font=font) > max_width:
                lines.append(line)
                line = word
            else:
                line = proposed
        return lines + ([line] if line else [])

    def card(final=False):
        image = Image.new('RGB', (width, height), background)
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle((72, 74, 90, 226), radius=9, fill=accent)
        draw.text((116, 73), 'Регула', font=fonts['brand'], fill=white)
        draw.text((120, 190), 'Локальный помощник по документам', font=fonts['intro'], fill=muted)
        if not final:
            draw.text((80, 348), 'Обзор по реальным экранам', font=fonts['hero'], fill=white)
            draw.text((80, 407), 'приложения', font=fonts['hero'], fill=white)
            draw.text((80, 502), 'Ответ → основание → проект → проверка', font=fonts['intro'], fill=muted)
            draw.text((80, 736), 'Учебный кейс ЦДОБР  •  12 шагов  •  Без звуковой дорожки', font=fonts['body'], fill=muted)
        else:
            for number, line in enumerate(('Проверяемое основание.', 'Готовый проект.', 'Контроль изменений.')):
                draw.text((80, 330 + number * 66), line, font=fonts['hero'], fill=white)
            draw.text((80, 655), 'Учебные материалы. Реальные функции приложения.', font=fonts['intro'], fill=muted)
            draw.text((80, 736), 'Обзор экранов, а не запись непрерывного сеанса.', font=fonts['body'], fill=muted)
        return image

    panels, sources = [], []
    for index, (name, title, subtitle) in enumerate(SCENES, 1):
        path = args.screens / f'{name}.png'
        with Image.open(path) as source:
            original_size = source.size
            screenshot = ImageOps.contain(source.convert('RGB'), (width, screen_height), Image.Resampling.LANCZOS)
        panel = Image.new('RGB', (width, height), '#F5F7FC')
        panel.paste(screenshot, ((width - screenshot.width) // 2, (screen_height - screenshot.height) // 2))
        draw = ImageDraw.Draw(panel)
        draw.rectangle((0, screen_height, width, height), fill=background)
        draw.rounded_rectangle((24, 738, 110, 816), radius=14, fill=accent)
        draw.text((45, 741), f'{index:02}', font=fonts['step'], fill=white)
        draw.text((48, 788), 'из 12', font=fonts['small'], fill=white)
        draw.text((136, 732), title, font=fonts['title'], fill=white)
        lines = wrap(draw, subtitle, fonts['body'], width - 164)
        if len(lines) > 2:
            raise ValueError('Подпись не помещается: ' + subtitle)
        for line_index, line in enumerate(lines):
            draw.text((136, 776 + line_index * 26), line, font=fonts['body'], fill=muted)
        draw.rectangle((0, height - 4, round(width * index / len(SCENES)), height), fill=accent)
        panels.append(panel)
        sources.append({'filename': path.name, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                        'original_size': original_size, 'title': title, 'caption': subtitle})

    sequence = [(card(), 4.0, 'intro')] + [(panel, args.scene_seconds, item[0]) for panel, item in zip(panels, SCENES)] + [(card(True), 5.0, 'outro')]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    output = av.open(str(args.output), 'w', format='mp4', options={'movflags': '+faststart'})
    stream = output.add_stream('libx264', rate=args.fps)
    stream.width, stream.height, stream.pix_fmt = width, height, 'yuv420p'
    stream.options = {'crf': '20', 'preset': 'veryfast', 'tune': 'stillimage'}
    stream.codec_context.gop_size = args.fps * 2
    output.metadata['title'] = 'Регула — обзор по реальным экранам приложения'
    output.metadata['comment'] = 'Silent screenshot overview. UI screenshots are contained without cropping. Not a live screen recording.'
    previous, frame_count, timeline = None, 0, []
    transition = max(1, round(args.fps * .34))
    for panel, duration, name in sequence:
        count = round(args.fps * duration)
        timeline.append({'name': name, 'start_seconds': frame_count / args.fps, 'duration_seconds': count / args.fps})
        for frame_number in range(count):
            if previous is not None and frame_number < transition:
                fraction = (frame_number + 1) / transition
                fraction = fraction * fraction * (3 - 2 * fraction)
                frame_image = Image.blend(previous, panel, fraction)
            else:
                frame_image = panel
            frame = av.VideoFrame.from_image(frame_image)
            frame.pts = frame_count
            for packet in stream.encode(frame):
                output.mux(packet)
            frame_count += 1
        previous = panel
        print(f'Encoded {name}: {frame_count / args.fps:.1f}s', flush=True)
    for packet in stream.encode():
        output.mux(packet)
    output.close()

    # Decode the entire product, not merely its metadata. Save representative frames.
    qa_dir = ROOT / 'tmp' / 'demo' / 'video_qa'
    qa_dir.mkdir(parents=True, exist_ok=True)
    sample_seconds = [1, 16, 44, 62, 89, 106, frame_count / args.fps - 2]
    samples = {round(second * args.fps): second for second in sample_seconds}
    decoded = 0
    with av.open(str(args.output)) as video:
        duration = video.duration / av.time_base
        audio_streams = len(video.streams.audio)
        for frame in video.decode(video=0):
            if decoded in samples:
                frame.to_image().save(qa_dir / f'frame-{samples[decoded]:06.2f}.png')
            decoded += 1
    if decoded != frame_count or abs(duration - frame_count / args.fps) > .1 or audio_streams:
        raise RuntimeError(f'Video verification failed: {decoded}/{frame_count} frames, duration={duration}, audio={audio_streams}')
    manifest = {'created_at': datetime.now(timezone.utc).isoformat(), 'output': str(args.output.resolve()),
                'size_bytes': args.output.stat().st_size, 'width': width, 'height': height, 'fps': args.fps,
                'duration_seconds': duration, 'frames_decoded': decoded, 'audio_streams': audio_streams,
                'description': 'Обзор по реальным экранам приложения; не непрерывная видеозапись.',
                'sources': sources, 'timeline': timeline, 'qa_frames': str(qa_dir.resolve())}
    manifest_path = args.output.with_suffix('.manifest.json')
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({key: manifest[key] for key in ('output', 'size_bytes', 'duration_seconds', 'frames_decoded')}, ensure_ascii=False), flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
