"""Minimal reader for the .xlsx result tables Quiz Please publishes.

Only what a results sheet needs: shared strings, inline strings and numbers.
Formatting, dates and formulas are ignored (the files contain none).
"""

import re
import xml.etree.ElementTree as ET
import zipfile

__all__ = ["read_first_sheet"]

_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_CELL_REF = re.compile(r"([A-Z]+)(\d+)")


def _column_index(ref):
    letters = _CELL_REF.match(ref).group(1)
    index = 0
    for char in letters:
        index = index * 26 + (ord(char) - 64)
    return index - 1


def _text_of(element):
    return "".join(node.text or "" for node in element.iter(_NS + "t"))


def read_first_sheet(source):
    """Return the first worksheet of ``source`` as a list of row lists.

    ``source`` is anything :class:`zipfile.ZipFile` accepts (path or file-like).
    Ragged rows are padded so every row has the same width.
    """
    with zipfile.ZipFile(source) as archive:
        shared = []
        if "xl/sharedStrings.xml" in archive.namelist():
            root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
            shared = [_text_of(item) for item in root]

        names = [n for n in archive.namelist() if n.startswith("xl/worksheets/sheet")]
        if not names:
            raise ValueError("workbook contains no worksheets")
        sheet = ET.fromstring(archive.read(sorted(names)[0]))

    rows = []
    for row_element in sheet.iter(_NS + "row"):
        row = []
        for cell in row_element.iter(_NS + "c"):
            index = _column_index(cell.get("r")) if cell.get("r") else len(row)
            while len(row) < index:
                row.append(None)
            cell_type = cell.get("t")
            value_element = cell.find(_NS + "v")
            if cell_type == "inlineStr":
                inline = cell.find(_NS + "is")
                value = _text_of(inline) if inline is not None else None
            elif value_element is None or value_element.text is None:
                value = None
            elif cell_type == "s":
                value = shared[int(value_element.text)]
            elif cell_type == "b":
                value = value_element.text == "1"
            else:
                value = value_element.text
            row.append(value)
        rows.append(row)

    width = max((len(row) for row in rows), default=0)
    for row in rows:
        row.extend([None] * (width - len(row)))
    return rows
