from abc import ABC, abstractmethod
from typing import List, Optional
from pydantic import BaseModel

from app.primitives.consolidation.connectors.base import RawSourceItem


class ProcessedChunk(BaseModel):
    text: str
    source_id: str
    source_type: str
    title: str
    url: str
    timestamp_start_ms: Optional[int] = None
    timestamp_end_ms: Optional[int] = None
    extra_metadata: dict = {}


class BaseProcessor(ABC):
    @abstractmethod
    async def process(self, item: RawSourceItem, content: str) -> List[ProcessedChunk]:
        """Transform raw content into embeddable chunks."""
        ...
