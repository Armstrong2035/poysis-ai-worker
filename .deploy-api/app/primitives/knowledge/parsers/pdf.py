from pypdf import PdfReader
from typing import List, Dict, Any

def parse_pdf(file_path: str) -> List[Dict[str, Any]]:
    """
    Extracts text from a PDF and returns page-by-page docs.
    """
    reader = PdfReader(file_path)
    documents = []
    
    for i, page in enumerate(reader.pages):
        text = page.extract_text()
        if text.strip():
            documents.append({
                "text": text,
                "metadata": {
                    "source": file_path,
                    "page": i + 1,
                    "total_pages": len(reader.pages)
                }
            })
            
    return documents
