import os
import sys
import time
import asyncio
from dotenv import load_dotenv

load_dotenv()
sys.path.append(os.getcwd())

from app.primitives.knowledge.engine import KnowledgeEngine

async def run_fast_test():
    file_path = os.path.join("app", "test-stuff", "_OceanofPDF.com_The_ONE_Thing_-_Gary_Keller.pdf")
    notebook_id = "nb_llamaindex_fast_001"
    
    print(f"\n{'='*70}")
    print(f"🚀 FAST INGESTION TEST (LlamaIndex)")
    print(f"File: {file_path}")
    print(f"Target Notebook: {notebook_id}")
    print(f"{'='*70}")

    start_time = time.time()
    try:
        engine = KnowledgeEngine()
        
        print(f"\n[1] Starting LlamaIndex ingestion...")
        print(f"Note: This uses IngestionPipeline with SentenceSplitter and Gemini Batch Embeddings.")
        
        # This will now use the new LlamaIndex logic
        count = await engine.ingest_file(notebook_id, file_path)
        
        print(f"\nSUCCESS: Indexed {count} semantic nodes (chunks).")
        
        print(f"\n[2] Verifying retrieval quality...")
        query = "What is the ONE thing according to Gary Keller?"
        results = await engine.fetch_raw(notebook_id, query, top_k=3)
        
        print(f"Found {len(results)} matches.")
        for i, res in enumerate(results):
            # LlamaIndex stores text in 'text' key by default, but our fetch_raw looks in metadata
            text = res.get('metadata', {}).get('text') or "NO TEXT CONTENT"
            print(f"  Match {i+1}: [Score: {res['score']:.4f}] {str(text)[:200]}...")

    except Exception as e:
        print(f"\n!!! FAST TEST ERROR: {e}")
        import traceback
        traceback.print_exc()
    finally:
        end_time = time.time()
        print(f"\n{'='*70}")
        print(f"🏁 TOTAL TIME: {end_time - start_time:.2f} seconds")
        print(f"PREVIOUS MANUAL TIME: ~168.00 seconds")
        print(f"{'='*70}\n")

if __name__ == "__main__":
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(run_fast_test())
