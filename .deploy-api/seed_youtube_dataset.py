"""
seed_youtube_dataset.py

Index pre-fetched YouTube transcripts from a local JSON dataset into Poysis.
Use this when you have a transcript dataset file from an external extractor
instead of fetching live from the YouTube API.

Usage:
    python seed_youtube_dataset.py <workspace_id> [path-to-dataset.json]

Defaults to the most recent dataset file in the project root if no path given.

Requires .env with: OPENAI_API_KEY, SUPABASE_PRODUCT_URL, SUPABASE_SERVICE_ROLE_KEY,
                     SUPABASE_DIRECT_CONNECTION_STRING
"""
import asyncio
import json
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import List

from dotenv import load_dotenv
load_dotenv()

def parse_xml_captions(xml_string: str) -> List[dict]:
    """YouTube XML transcript → [{start, duration, text}, ...]."""
    try:
        root = ET.fromstring(xml_string)
    except ET.ParseError:
        return []
    segments = []
    for el in root.findall("text"):
        text = (el.text or "").strip()
        if text:
            segments.append({
                "start": float(el.get("start", 0)),
                "duration": float(el.get("dur", 0)),
                "text": text,
            })
    return segments


async def main():
    if len(sys.argv) < 2:
        print("Usage: python seed_youtube_dataset.py <workspace_id> [path-to-dataset.json]")
        sys.exit(1)

    workspace_id = sys.argv[1]
    dataset_path = sys.argv[2] if len(sys.argv) > 2 else _default_dataset()

    print(f"Loading dataset: {dataset_path}")
    with open(dataset_path, encoding="utf-8") as f:
        dataset = json.load(f)

    total = len(dataset)
    print(f"Found {total} videos in dataset\n")

    from app.primitives.database import DatabaseService
    from app.primitives.consolidation.connectors.base import RawSourceItem
    from app.primitives.consolidation.processors.transcript import TranscriptProcessor
    from app.primitives.knowledge.engine import KnowledgeEngine

    db = DatabaseService()
    processor = TranscriptProcessor()
    knowledge = KnowledgeEngine()
    namespace = f"youtube_{workspace_id}"

    indexed_files = await db.get_indexed_files(workspace_id)
    print(f"Already indexed: {len(indexed_files)} videos (will be skipped)\n")

    docs_processed = 0
    docs_skipped = 0
    total_vectors = 0
    errors = []

    for i, entry in enumerate(dataset):
        video_id = entry.get("videoId", "")
        title = entry.get("title", "Untitled")
        captions_xml = entry.get("captions", "")
        status = entry.get("status", "")

        prefix = f"[{i+1}/{total}]"

        if status != "OK" or not captions_xml:
            print(f"{prefix} SKIP '{title}' — status={status or 'no captions'}")
            docs_skipped += 1
            continue

        if video_id in indexed_files:
            print(f"{prefix} SKIP '{title}' — already indexed")
            docs_skipped += 1
            continue

        print(f"{prefix} '{title}'")

        try:
            segments = parse_xml_captions(captions_xml)
            if not segments:
                raise ValueError("XML parsed but no segments found")

            item = RawSourceItem(
                source_id=video_id,
                source_type="youtube",
                title=title,
                url=f"https://www.youtube.com/watch?v={video_id}",
                etag=video_id,
                last_modified=datetime.now(timezone.utc),
                content_type="document",
                size_bytes=0,
            )

            chunks = await processor.process(item, segments)
            print(f"  {len(segments)} segments → {len(chunks)} pre-chunks")

            vectors = await knowledge.embed_and_store(namespace, chunks)
            total_vectors += vectors
            docs_processed += 1

            await db.mark_files_indexed(workspace_id, [
                {"source_id": video_id, "etag": video_id, "source_type": "youtube"}
            ])
            print(f"  ✓ {vectors} vectors stored (running total: {total_vectors})\n")

        except Exception as e:
            print(f"  ERROR: {e}\n")
            errors.append(f"[{video_id}] {title}: {e}")

    print("--- Done ---")
    print(f"Docs processed : {docs_processed}")
    print(f"Docs skipped   : {docs_skipped}")
    print(f"Vectors indexed: {total_vectors}")
    if errors:
        print(f"\nErrors ({len(errors)}):")
        for err in errors[:20]:
            print(f"  {err}")
        if len(errors) > 20:
            print(f"  ... and {len(errors) - 20} more")


def _default_dataset() -> str:
    """Pick the most recently modified dataset JSON in the project root."""
    import glob
    import os
    matches = sorted(
        glob.glob("dataset_youtube-*.json"),
        key=os.path.getmtime,
        reverse=True,
    )
    if not matches:
        print("No dataset file found. Pass the path as an argument.")
        sys.exit(1)
    return matches[0]


if __name__ == "__main__":
    asyncio.run(main())
