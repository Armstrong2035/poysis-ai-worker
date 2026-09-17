from typing import List

from app.primitives.consolidation.connectors.base import RawSourceItem
from app.primitives.consolidation.processors.base import BaseProcessor, ProcessedChunk
from app.primitives.knowledge.parsers.csv import parse_spreadsheet


class SpreadsheetProcessor(BaseProcessor):
    """
    Wraps the existing row-by-row spreadsheet parser.
    Each row becomes one chunk — no further splitting needed.
    Works for Google Sheets (exported as CSV) and binary Excel/CSV files.
    """

    async def process(self, item: RawSourceItem, file_path: str) -> List[ProcessedChunk]:
        rows = parse_spreadsheet(file_path)

        return [
            ProcessedChunk(
                text=row["text"],
                source_id=item.source_id,
                source_type=item.source_type,
                title=item.title,
                url=item.url,
                extra_metadata=row.get("metadata", {}),
            )
            for row in rows if row.get("text")
        ]
