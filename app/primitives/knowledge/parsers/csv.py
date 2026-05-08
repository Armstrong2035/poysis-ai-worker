import pandas as pd
from typing import List, Dict, Any
import os

MAX_ROWS = 5000


def parse_spreadsheet(file_path: str) -> List[Dict[str, Any]]:
    """
    Parses CSV or Excel files and converts rows into semantic text blocks.
    Cleans the data before chunking: drops empty rows, deduplicates, caps at MAX_ROWS.
    """
    ext = os.path.splitext(file_path)[1].lower()

    if ext == ".csv":
        df = pd.read_csv(file_path)
    else:
        df = pd.read_excel(file_path)

    # Clean: drop rows that are entirely empty, then drop exact duplicates
    df = df.dropna(how="all")
    df = df.drop_duplicates()
    df = df.reset_index(drop=True)

    if len(df) > MAX_ROWS:
        print(f"[SpreadsheetParser] {os.path.basename(file_path)}: {len(df)} rows, capping at {MAX_ROWS}")
        df = df.iloc[:MAX_ROWS]

    documents = []
    for index, row in df.iterrows():
        row_text = " | ".join([f"{col}: {val}" for col, val in row.items() if pd.notna(val)])
        if row_text.strip():
            row_metadata = {k: v for k, v in row.to_dict().items() if pd.notna(v)}
            documents.append({
                "text": row_text,
                "metadata": {
                    "source": file_path,
                    "row_index": index,
                    **row_metadata
                }
            })

    return documents
