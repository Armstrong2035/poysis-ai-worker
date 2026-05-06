import re
from typing import List

from app.primitives.consolidation.connectors.base import RawSourceItem
from app.primitives.consolidation.processors.base import BaseProcessor, ProcessedChunk

CHUNK_CHAR_LIMIT = 1600  # ~400 tokens


class DocumentProcessor(BaseProcessor):
    """
    Handles plain text content from Google Docs and Slides.
    Splits at paragraph boundaries to preserve natural reading units.
    """

    async def process(self, item: RawSourceItem, content: str) -> List[ProcessedChunk]:
        if not content.strip():
            return []

        chunks = []
        current = ""

        for paragraph in re.split(r"\n{2,}", content):
            paragraph = paragraph.strip()
            if not paragraph:
                continue

            if len(current) + len(paragraph) > CHUNK_CHAR_LIMIT and current:
                chunks.append(self._make_chunk(item, current.strip()))
                current = paragraph
            else:
                current += "\n\n" + paragraph if current else paragraph

        if current.strip():
            chunks.append(self._make_chunk(item, current.strip()))

        return chunks

    def _make_chunk(self, item: RawSourceItem, text: str) -> ProcessedChunk:
        return ProcessedChunk(
            text=text,
            source_id=item.source_id,
            source_type=item.source_type,
            title=item.title,
            url=item.url,
        )
