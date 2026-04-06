import os
import sys
import time
import asyncio
from dotenv import load_dotenv

load_dotenv()
sys.path.append(os.getcwd())

from app.primitives.knowledge.engine import KnowledgeEngine

async def run_test():
    file_path = os.path.join("app", "test-stuff", "test_data.csv")
    notebook_id = "nb_csv_test_001"
    
    print(f"\n{'='*60}")
    print(f"VECTOR INGESTION TEST: CSV")
    print(f"File: {file_path}")
    print(f"Target Notebook: {notebook_id}")
    print(f"{'='*60}")

    start_time = time.time()
    try:
        engine = KnowledgeEngine()
        
        print(f"\n[1] Starting ingestion of {os.path.basename(file_path)}...")
        count = await engine.ingest_file(notebook_id, file_path)
        print(f"SUCCESS: Indexed {count} vectors.")
        
        print(f"\n[2] Verifying retrieval...")
        query = "Who bought the Poysis AI Worker?"
        results = await engine.fetch_raw(notebook_id, query, top_k=2)
        print(f"Found {len(results)} matches.")
        for i, res in enumerate(results):
            print(f" Match {i+1}: [Score: {res['score']:.4f}] {str(res['metadata'].get('text'))[:150]}...")

    except Exception as e:
        print(f"\n!!! CSV TEST ERROR: {e}")
        import traceback
        traceback.print_exc()
    finally:
        print(f"\n{'='*60}")
        print(f"TOTAL TIME: {time.time() - start_time:.2f} seconds")
        print(f"{'='*60}\n")

if __name__ == "__main__":
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(run_test())
