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


def force_text(workbook):
    for sheet in workbook:
        for row in sheet:
            for cell in row:
                if isinstance(cell.value, str):
                    cell.data_type = "s"


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
