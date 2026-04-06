import pandas as pd
from typing import List, Dict, Any
import os

def parse_spreadsheet(file_path: str) -> List[Dict[str, Any]]:
    """
    Parses CSV or Excel files and converts rows into semantic text blocks.
    """
    ext = os.path.splitext(file_path)[1].lower()
    
    if ext == ".csv":
        df = pd.read_csv(file_path)
    else:
        df = pd.read_excel(file_path)
        
    documents = []
    # Convert each row to a string representation for embedding
    for index, row in df.iterrows():
        # Build a semantic string: "Column1: Value | Column2: Value"
        row_text = " | ".join([f"{col}: {val}" for col, val in row.items() if pd.notna(val)])
        
        if row_text.strip():
            documents.append({
                "text": row_text,
                "metadata": {
                    "source": file_path,
                    "row_index": index,
                    **row.to_dict() # Keep original data as metadata
                }
            })
            
    return documents
