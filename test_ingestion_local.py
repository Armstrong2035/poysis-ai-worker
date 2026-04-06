import os
import sys
import time
import asyncio
from dotenv import load_dotenv

# Load env vars from root .env
load_dotenv()

# Add current directory to path so we can import app
sys.path.append(os.getcwd())

from app.primitives.knowledge.engine import KnowledgeEngine
from app.primitives.knowledge.parsers import get_parser

async def test_file_ingestion(file_path, notebook_id):
    print(f"\n" + "="*70)
    print(f"TESTING FILE: {os.path.basename(file_path)}")
    print(f"NOTEBOOK ID : {notebook_id}")
    print("="*70)
    
    start_total = time.time()
    try:
        engine = KnowledgeEngine()
        
        # Step 1: Detect File
        print(f"\n[STEP 1] Validating file path...")
        if not os.path.exists(file_path):
            print(f"ERROR: File not found at {file_path}")
            return
        print(f"SUCCESS: File found. Size: {os.path.getsize(file_path) / 1024:.2f} KB")
            
        # Step 2: Parsing
        step_start = time.time()
        print(f"\n[STEP 2] Parsing file content...")
        parser = get_parser(file_path)
        docs = parser(file_path)
        parse_time = time.time() - step_start
        print(f"SUCCESS: Parsed {len(docs)} documents/chunks in {parse_time:.2f}s")
        
        # Step 3: Metadata Prep
        print(f"\n[STEP 3] Preparing stable IDs and metadata...")
        for doc in docs:
            if "source_id" not in doc:
                # Use a stable hash-like ID based on file name and content/index
                doc["source_id"] = f"{os.path.basename(file_path)}_{doc.get('metadata', {}).get('page') or doc.get('metadata', {}).get('row_index') or '0'}"
        
        # Step 4: Embedding and Upserting
        step_start = time.time()
        print(f"\n[STEP 4] Generating Embeddings and Upserting to Pinecone (Namespace: {notebook_id})...")
        print(f"Note: This step communicates with Gemini and Pinecone APIs.")
        
        count = await engine.upsert_documents(notebook_id, docs)
        upsert_time = time.time() - step_start
        print(f"SUCCESS: Indexed {count} vectors in {upsert_time:.2f}s")
        
        # Step 5: Verification Query
        step_start = time.time()
        test_query = "Summarize the key points of this file."
        print(f"\n[STEP 5] Verifying retrieval with test query: '{test_query}'...")
        results = await engine.fetch_raw(notebook_id, test_query, top_k=3)
        query_time = time.time() - step_start
        
        if results:
            print(f"SUCCESS: Found {len(results)} matches in {query_time:.2f}s")
            for i, res in enumerate(results):
                text_preview = str(res['metadata'].get('text', 'NO TEXT'))[:150].replace('\n', ' ')
                print(f"  Match {i+1}: [Score: {res['score']:.4f}] {text_preview}...")
        else:
            print(f"WARNING: No results found for the test query in namespace '{notebook_id}'.")

    except Exception as e:
        print(f"\n!!! ERROR IN TEST: {e}")
        import traceback
        traceback.print_exc()
    finally:
        total_time = time.time() - start_total
        print(f"\n" + "-"*70)
        print(f"TOTAL TEST TIME FOR {os.path.basename(file_path)}: {total_time:.2f}s")
        print("-"*70)

async def main():
    # Use actual relative paths as seen in list_dir
    files_to_test = [
        {
            "path": os.path.join("app", "test-stuff", "_OceanofPDF.com_The_ONE_Thing_-_Gary_Keller.pdf"),
            "notebook_id": "nb_garykr_001"
        },
        {
            "path": os.path.join("app", "test-stuff", "Arabian Ranches New  (1) (1) (1).xlsx"),
            "notebook_id": "nb_arbrch_001"
        }
    ]
    
    print("starting ingestion tests...")
    for test in files_to_test:
        await test_file_ingestion(test["path"], test["notebook_id"])

if __name__ == "__main__":
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
