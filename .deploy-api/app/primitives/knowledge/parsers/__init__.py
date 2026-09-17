from .text import parse_text
from .pdf import parse_pdf
from .csv import parse_spreadsheet

def get_parser(file_path: str):
    import os
    ext = os.path.splitext(file_path)[1].lower()
    if ext == ".pdf":
        return parse_pdf
    elif ext in [".csv", ".xlsx", ".xls"]:
        return parse_spreadsheet
    else:
        return lambda p: parse_text(open(p, 'r', encoding='utf-8').read())
