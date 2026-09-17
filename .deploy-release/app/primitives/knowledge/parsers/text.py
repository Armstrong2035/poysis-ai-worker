from typing import List, Dict, Any

def parse_text(content: str, chunk_size: int = 1000) -> List[Dict[str, Any]]:
    """
    Splits plain text into manageable chunks.
    """
    # Simple chunking for now
    chunks = [content[i:i + chunk_size] for i in range(0, len(content), chunk_size)]
    
    return [
        {
            "text": chunk,
            "metadata": {"chunk_index": i, "total_chunks": len(chunks)}
        }
        for i, chunk in enumerate(chunks)
    ]
