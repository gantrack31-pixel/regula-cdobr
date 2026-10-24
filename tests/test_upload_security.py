"""Files must remain passive when later opened by an employee."""
from pathlib import Path
import zipfile

import pytest
from fastapi import HTTPException
from pypdf import PdfWriter

from backend.app.security import validate_upload

ROOT = Path(__file__).resolve().parents[1]
W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'


def write_docx(path, content='', extra=None):
    with zipfile.ZipFile(path, 'w') as package:
        package.writestr('word/document.xml', f'<w:document xmlns:w="{W}"><w:body>{content}</w:body></w:document>')
        for name, data in (extra or {}).items():
            package.writestr(name, data)


def test_supplied_corpus_is_accepted():
    for path in (ROOT / 'data' / 'sources').iterdir():
        if path.suffix in {'.pdf', '.docx'}:
            validate_upload(path)


@pytest.mark.parametrize('kind', ['attachedTemplate', 'image', 'oleObject'])
def test_external_docx_relationship_rejected(tmp_path, kind):
    path = tmp_path / 'external.docx'
    write_docx(path, extra={'word/_rels/document.xml.rels':
        f'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="r1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/{kind}" Target="https://example.invalid/external" TargetMode="External"/></Relationships>'})
    with pytest.raises(HTTPException) as error:
        validate_upload(path)
    assert error.value.status_code == 415


def test_split_dde_instruction_is_rejected(tmp_path):
    path = tmp_path / 'dde.docx'
    write_docx(path, '<w:p><w:r><w:fldChar w:fldCharType="begin"/></w:r><w:r><w:instrText>PAGE</w:instrText></w:r><w:r><w:fldChar w:fldCharType="end"/></w:r><w:r><w:fldChar w:fldCharType="begin"/></w:r><w:r><w:instrText>DDE</w:instrText></w:r><w:r><w:instrText>AUTO example</w:instrText></w:r></w:p>')
    with pytest.raises(HTTPException) as error:
        validate_upload(path)
    assert error.value.status_code == 415


def test_docx_embedded_object_and_entities_are_rejected(tmp_path):
    path = tmp_path / 'embedded.docx'
    write_docx(path, extra={'word/embeddings/oleObject1.bin': b'passive test bytes'})
    with pytest.raises(HTTPException):
        validate_upload(path)
    with zipfile.ZipFile(path, 'w') as package:
        xml = '<!DOCTYPE x [<!ENTITY demo "text">]><x>&demo;</x>'
        package.writestr('word/document.xml', xml.encode('utf-16'))
    with pytest.raises(HTTPException) as error:
        validate_upload(path)
    assert error.value.status_code == 415


def test_pdf_script_is_rejected_and_plain_pdf_accepted(tmp_path):
    path = tmp_path / 'example.pdf'
    writer = PdfWriter()
    writer.add_blank_page(595, 842)
    writer.write(path)
    validate_upload(path)
    writer.add_js('app.alert("test only");')
    writer.write(path)
    with pytest.raises(HTTPException) as error:
        validate_upload(path)
    assert error.value.status_code == 415
