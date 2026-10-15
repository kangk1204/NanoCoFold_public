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
