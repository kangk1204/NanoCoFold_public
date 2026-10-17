"""Keep untrusted text as data in spreadsheet exports."""


def csv_cell(value):
    # A lone hyphen is the persisted WT/no-mutation marker, not a formula.
    # Preserve it for CSV resume round trips; any nonempty expression or name
    # after a formula prefix still requires text escaping.
    if isinstance(value, str) and value == "-":
        return value
    if isinstance(value, str) and value.lstrip(" \t\r\n").startswith(("=", "+", "-", "@")):
        return "'" + value
    return value


def csv_headers(columns):
    """Fail closed if formula escaping would merge distinct column names."""
    headers = [csv_cell(value) for value in columns]
    # csv.writer renders None as empty text and all other headers as strings.
    emitted = ["" if value is None else str(value) for value in headers]
    if len(set(emitted)) != len(emitted):
        raise ValueError("CSV 헤더를 안전한 텍스트로 변환하면 열 이름이 중복됩니다. 원본 표의 열 이름을 구분해 주세요.")
    return headers


def force_text(workbook):
    for sheet in workbook:
        for row in sheet:
            for cell in row:
                if isinstance(cell.value, str):
                    cell.data_type = "s"


def deterministic_xlsx(workbook):
    """Serialize a saved-data export without wall-clock ZIP/core timestamps.

    1980-01-01 is an explicit packaging sentinel, not an execution timestamp.
    The caller's provenance sheets retain the actual recorded run times. This
    changes no cells, scientific values, source files or workbook properties.
    """
    import copy
    import io
    import re
    import zipfile
    stream = io.BytesIO()
    modified = workbook.properties.modified
    try:
        workbook.save(stream)
    finally:
        # openpyxl itself assigns the current modified time while saving.
        workbook.properties.modified = modified
    output = io.BytesIO()
    with zipfile.ZipFile(stream) as source, zipfile.ZipFile(output, "w") as target:
        for original in sorted(source.infolist(), key=lambda entry: entry.filename):
            data = source.read(original.filename)
            if original.filename == "docProps/core.xml":
                data = re.sub(
                    rb"(<dcterms:(?:created|modified)\b[^>]*>)[^<]*(</dcterms:(?:created|modified)>)",
                    rb"\g<1>1980-01-01T00:00:00Z\g<2>", data,
                )
            info = copy.copy(original)
            info.date_time = (1980, 1, 1, 0, 0, 0)
            target.writestr(info, data)
    return output.getvalue()


def excel_text_chunks(text, limit=30000):
    """Split losslessly by Excel's UTF-16 units, including supplementary glyphs."""
    chunks, start, units = [], 0, 0
    for index, char in enumerate(text):
        width = 2 if ord(char) > 0xFFFF else 1
        if units + width > limit:
            chunks.append(text[start:index])
            start, units = index, 0
        units += width
    chunks.append(text[start:])
    return chunks
