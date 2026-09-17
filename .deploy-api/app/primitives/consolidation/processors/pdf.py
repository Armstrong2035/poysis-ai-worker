from typing import List

from app.primitives.consolidation.connectors.base import RawSourceItem
from app.primitives.consolidation.processors.base import BaseProcessor, ProcessedChunk
from app.primitives.knowledge.parsers.pdf import parse_pdf


class PDFProcessor(BaseProcessor):
    """
    Wraps the existing PDF parser.
    Each page becomes one chunk with page number metadata.
    """

    async def process(self, item: RawSourceItem, file_path: str) -> List[ProcessedChunk]:
        pages = parse_pdf(file_path)

        return [
            ProcessedChunk(
                text=page["text"],
                source_id=item.source_id,
                source_type=item.source_type,
                title=item.title,
                url=item.url,
                extra_metadata={
                    "page": page["metadata"].get("page"),
                    "total_pages": page["metadata"].get("total_pages"),
                },
            )
            for page in pages if page.get("text")
        ]
