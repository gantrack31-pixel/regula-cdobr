"""Deterministic document parsing. Quotes are slices of normalized source text.

PDF pages use physical (1-based) indexes. DOCX page numbers are unknown without a
layout renderer, so they deliberately remain null. Tables retain their cell order.
"""
from __future__ import annotations

import re
import unicodedata
import zipfile
from dataclasses import dataclass
from pathlib import Path

from docx import Document
from docx.table import Table
from docx.text.paragraph import Paragraph
from pypdf import PdfReader


WATERMARK = re.compile(
    r"УЧЕБНЫЙ\s+ПРИМЕР\s+ДЛЯ\s+ИТ[\s-]*СОРЕВНОВАНИЙ\.?\s*"
    r"НЕ\s+ЯВЛЯЕТСЯ\s+ДЕЙСТВУЮЩИМ\s+ДОКУМЕНТОМ\.?", re.I)
SECTION = re.compile(r"^\s*(\d{1,2}(?:\.\d{1,3}){1,4})\.?\s+", re.M)
HEADING = re.compile(r"^\s*(?:Глава\s+\d+\.?|Приложение\s*(?:№\s*)?\d+|#{1,4}\s+.+)", re.I)


@dataclass(frozen=True)
class ParsedChunk:
    section: str
    page: int | None
    text: str
    context: str = ""


def normalize_text(text: str) -> str:
    text = unicodedata.normalize("NFC", text).replace("\u00ad", "").replace("\xa0", " ")
    text = WATERMARK.sub("", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _docx_blocks(path: Path) -> list[str]:
    with zipfile.ZipFile(path) as archive:
        if sum(i.file_size for i in archive.infolist()) > 100 * 1024 * 1024:
            raise ValueError("Распакованный DOCX превышает 100 МБ")
    document = Document(path)
    blocks = []
    for child in document.element.body.iterchildren():
        tag = child.tag.rsplit("}", 1)[-1]
        if tag == "p":
            value = Paragraph(child, document).text.strip()
            if value:
                blocks.append(value)
        elif tag == "tbl":
            table = Table(child, document)
            rows = []
            for row in table.rows:
                cells, seen = [], set()
                for cell in row.cells:
                    if id(cell._tc) in seen:
                        continue
                    seen.add(id(cell._tc))
                    cells.append(" / ".join(p.text.strip() for p in cell.paragraphs if p.text.strip()))
                if any(cells):
                    rows.append(" | ".join(cells))
            if rows:
                blocks.append("\n".join(rows))
    return blocks


def extract_pages(path: Path) -> list[tuple[int | None, str]]:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        reader = PdfReader(path)
        if reader.is_encrypted:
            raise ValueError("Защищенный PDF нужно сначала расшифровать")
        if len(reader.pages) > 400:
            raise ValueError("Допустимо не более 400 страниц")
        return [(number, normalize_text(page.extract_text() or "")) for number, page in enumerate(reader.pages, 1)]
    if suffix == ".docx":
        return [(None, normalize_text("\n\n".join(_docx_blocks(path))))]
    if suffix in {".txt", ".md"}:
        raw = path.read_bytes()
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = raw.decode("cp1251")
        return [(None, normalize_text(text))]
    raise ValueError("Поддерживаются PDF, DOCX, TXT и MD")


def _split_long(text: str, maximum: int = 1200) -> list[str]:
    """No invented bridging text, and no cutting through a word."""
    parts = []
    while len(text) > maximum:
        candidates = [text.rfind(mark, maximum // 2, maximum) for mark in ("\n", ". ", "; ")]
        end = max(candidates)
        if end < maximum // 2:
            end = text.rfind(" ", maximum // 2, maximum)
        if end < 0:
            end = maximum
        elif text[end:end + 2] in {". ", "; "}:
            end += 1
        parts.append(text[:end].strip())
        text = text[end:].strip()
    if text:
        parts.append(text)
    return parts


def _docx_table_chunks(path: Path) -> list[ParsedChunk]:
    """Cell values stay verbatim; column headers are separate provenance context."""
    chunks = []
    for number, block in enumerate(_docx_blocks(path), 1):
        if " | " not in block or "\n" not in block:
            continue
        lines = [normalize_text(line) for line in block.splitlines() if line.strip()]
        header = lines[0]
        if header.count(" | ") < 2:
            continue
        group = "Таблица"
        for index, line in enumerate(lines[1:], 1):
            if " | " not in line:
                group = line
                continue
            match = re.match(r"(\d+)\.", line)
            section = f"{group} · строка {match.group(1) if match else index}"
            for part in _split_long(line):
                chunks.append(ParsedChunk(section, None, part, header))
    return chunks


def parse_document(path: Path) -> list[ParsedChunk]:
    chunks: list[ParsedChunk] = []
    current_section = "Общие положения"
    pages = extract_pages(path)
    if path.suffix.lower() == ".docx":
        # Structured tables get one row per chunk with separate header context.
        blocks = _docx_blocks(path)
        ordinary = [b for b in blocks if not ("\n" in b and b.splitlines()[0].count(" | ") >= 2)]
        pages = [(None, normalize_text("\n\n".join(ordinary)))]
    for page, text in pages:
        if not text:
            continue
        matches = list(SECTION.finditer(text))
        boundaries = [(m.start(), "п. " + m.group(1)) for m in matches]
        # Unnumbered documents retain headings and paragraph boundaries.
        if not boundaries:
            boundaries = [(m.start(), m.group().strip("# \n")) for m in re.finditer(HEADING, text)]
        if not boundaries or boundaries[0][0] > 0:
            boundaries.insert(0, (0, current_section))
        for index, (offset, section) in enumerate(boundaries):
            end = boundaries[index + 1][0] if index + 1 < len(boundaries) else len(text)
            block = text[offset:end].strip()
            current_section = section
            for part in _split_long(block):
                # A page-leading chapter title is navigation, not a continuation
                # of the previous numbered clause and must not inherit its ID.
                if re.match(r"^Глава\s+\d+", part, re.I) and len(part) < 200:
                    continue
                if len(part) >= 12:
                    chunks.append(ParsedChunk(section, page, part))
    if not chunks:
        raise ValueError("В файле не найден текст. Для сканированного PDF требуется OCR перед загрузкой.")
    if path.suffix.lower() == ".docx":
        table_chunks = _docx_table_chunks(path)
        if table_chunks:
            chunks.extend(table_chunks)
    return chunks


def extract_text(path: Path) -> str:
    return "\n\n".join(text for _, text in extract_pages(path))
