"""Минимальная запись таблиц Excel (.xlsx) без сторонних библиотек: листы с текстом и числами.

В отличие от CSV, .xlsx открывается одинаково в Excel, Google Таблицах, Numbers и в просмотре
файлов на телефоне: не нужно угадывать разделитель, а «7,75» не разваливается на две колонки.
"""

from __future__ import annotations

import io
import re
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from xml.sax.saxutils import escape, quoteattr

Cell = str | int | float | None

_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_PKG_REL = "http://schemas.openxmlformats.org/package/2006/relationships"
_HEAD = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
_BAD_XML = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f￾￿]")
_BAD_SHEET = re.compile(r"[\[\]:*?/\\]")

# Стили (индексы в cellXfs): обычный, жирный, число 0,00, жирное число 0,00.
_PLAIN, _BOLD, _DECIMAL, _BOLD_DECIMAL = 0, 1, 2, 3
_STYLES = (
    f'{_HEAD}<styleSheet xmlns="{_NS}">'
    '<fonts count="2"><font><sz val="11"/><name val="Calibri"/></font>'
    '<font><b/><sz val="11"/><name val="Calibri"/></font></fonts>'
    '<fills count="2"><fill><patternFill patternType="none"/></fill>'
    '<fill><patternFill patternType="gray125"/></fill></fills>'
    '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
    '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
    '<cellXfs count="4">'
    '<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
    '<xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0" applyFont="1"/>'
    '<xf numFmtId="2" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/>'
    '<xf numFmtId="2" fontId="1" fillId="0" borderId="0" xfId="0" applyFont="1" applyNumberFormat="1"/>'
    "</cellXfs>"
    '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>'
    "</styleSheet>"
)


@dataclass
class Sheet:
    """Лист: строки ячеек; float показывается как 0,00, int — как есть, None и "" — пустые ячейки."""

    name: str
    widths: list[float] = field(default_factory=list)
    rows: list[tuple[list[Cell], bool]] = field(default_factory=list)

    def add(self, *cells: Cell, bold: bool = False) -> None:
        self.rows.append((list(cells), bold))


def column(index: int) -> str:
    """0 → A, 25 → Z, 26 → AA."""
    name = ""
    index += 1
    while index:
        index, rest = divmod(index - 1, 26)
        name = chr(65 + rest) + name
    return name


def _text(value: str) -> str:
    return escape(_BAD_XML.sub("", value))


class _Strings:
    """Общая таблица строк (sharedStrings) — так пишет сам Excel, понимают все программы."""

    def __init__(self) -> None:
        self.index: dict[str, int] = {}
        self.count = 0

    def ref(self, value: str) -> int:
        self.count += 1
        return self.index.setdefault(value, len(self.index))

    def xml(self) -> str:
        items = "".join(f'<si><t xml:space="preserve">{_text(value)}</t></si>' for value in self.index)
        return f'{_HEAD}<sst xmlns="{_NS}" count="{self.count}" uniqueCount="{len(self.index)}">{items}</sst>'


def _sheet_xml(sheet: Sheet, strings: _Strings) -> str:
    out = [f'{_HEAD}<worksheet xmlns="{_NS}">']
    if sheet.widths:
        cols = "".join(
            f'<col min="{i}" max="{i}" width="{width}" customWidth="1"/>' for i, width in enumerate(sheet.widths, 1)
        )
        out.append(f"<cols>{cols}</cols>")
    out.append("<sheetData>")
    for r, (cells, bold) in enumerate(sheet.rows, 1):
        parts = []
        for c, value in enumerate(cells):
            if value is None or value == "":
                continue
            ref = f"{column(c)}{r}"
            if isinstance(value, bool):
                value = "да" if value else "нет"
            if isinstance(value, (int, float)):
                style = (
                    (_BOLD_DECIMAL if bold else _DECIMAL) if isinstance(value, float) else (_BOLD if bold else _PLAIN)
                )
                attr = f' s="{style}"' if style else ""
                parts.append(f'<c r="{ref}"{attr}><v>{value!r}</v></c>')
            else:
                attr = f' s="{_BOLD}"' if bold else ""
                parts.append(f'<c r="{ref}" t="s"{attr}><v>{strings.ref(str(value))}</v></c>')
        out.append(f'<row r="{r}">{"".join(parts)}</row>')
    out.append("</sheetData></worksheet>")
    return "".join(out)


def _sheet_names(sheets: list[Sheet]) -> list[str]:
    names: list[str] = []
    for i, sheet in enumerate(sheets, 1):
        name = _BAD_SHEET.sub(" ", _BAD_XML.sub("", sheet.name)).strip(" '")[:31] or f"Лист{i}"
        while name in names:
            name = f"{name[:28]}~{i}"
        names.append(name)
    return names


def build(sheets: list[Sheet], title: str = "", created: datetime | None = None) -> bytes:
    """Собрать файл .xlsx из листов."""
    strings = _Strings()
    bodies = [_sheet_xml(sheet, strings) for sheet in sheets]
    names = _sheet_names(sheets)
    when = (created or datetime.now(timezone.utc)).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    sheet_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"

    content_types = (
        f'{_HEAD}<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        '<Override PartName="/xl/styles.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
        '<Override PartName="/xl/sharedStrings.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml"/>'
        + "".join(
            f'<Override PartName="/xl/worksheets/sheet{i}.xml" ContentType="{sheet_type}"/>'
            for i in range(1, len(sheets) + 1)
        )
        + '<Override PartName="/docProps/core.xml" '
        'ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>'
        "</Types>"
    )
    root_rels = (
        f'{_HEAD}<Relationships xmlns="{_PKG_REL}">'
        f'<Relationship Id="rId1" Type="{_REL}/officeDocument" Target="xl/workbook.xml"/>'
        '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/'
        'core-properties" Target="docProps/core.xml"/>'
        "</Relationships>"
    )
    core = (
        f"{_HEAD}<cp:coreProperties "
        'xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
        'xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" '
        'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
        f"<dc:title>{_text(title)}</dc:title><dc:creator>Бот «Мои оценки»</dc:creator>"
        f'<dcterms:created xsi:type="dcterms:W3CDTF">{when}</dcterms:created>'
        f'<dcterms:modified xsi:type="dcterms:W3CDTF">{when}</dcterms:modified>'
        "</cp:coreProperties>"
    )
    workbook = (
        f'{_HEAD}<workbook xmlns="{_NS}" xmlns:r="{_REL}">'
        '<bookViews><workbookView activeTab="0"/></bookViews><sheets>'
        + "".join(f'<sheet name={quoteattr(name)} sheetId="{i}" r:id="rId{i}"/>' for i, name in enumerate(names, 1))
        + "</sheets></workbook>"
    )
    count = len(sheets)
    workbook_rels = (
        f'{_HEAD}<Relationships xmlns="{_PKG_REL}">'
        + "".join(
            f'<Relationship Id="rId{i}" Type="{_REL}/worksheet" Target="worksheets/sheet{i}.xml"/>'
            for i in range(1, count + 1)
        )
        + f'<Relationship Id="rId{count + 1}" Type="{_REL}/styles" Target="styles.xml"/>'
        f'<Relationship Id="rId{count + 2}" Type="{_REL}/sharedStrings" Target="sharedStrings.xml"/>'
        "</Relationships>"
    )

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("_rels/.rels", root_rels)
        archive.writestr("docProps/core.xml", core)
        archive.writestr("xl/workbook.xml", workbook)
        archive.writestr("xl/_rels/workbook.xml.rels", workbook_rels)
        archive.writestr("xl/styles.xml", _STYLES)
        archive.writestr("xl/sharedStrings.xml", strings.xml())
        for i, body in enumerate(bodies, 1):
            archive.writestr(f"xl/worksheets/sheet{i}.xml", body)
    return buffer.getvalue()
