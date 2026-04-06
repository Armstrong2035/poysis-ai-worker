import os
import sys
import time
import asyncio
from dotenv import load_dotenv

load_dotenv()
sys.path.append(os.getcwd())

from app.primitives.knowledge.engine import KnowledgeEngine

async def run_stream_test():
    notebook_id = "nb_llamaindex_fast_001"
    query = "What is the 'Focusing Question' described in the book?"
    
    print(f"\n{'='*70}")
    print(f"🌊 STREAMING RAG TEST")
    print(f"Notebook: {notebook_id}")
    print(f"Question: {query}")
    print(f"{'='*70}")
    print(f"\n💡 ANSWER (streaming):\n")

    start_time = time.time()
    first_token_time = None

    try:
        engine = KnowledgeEngine()
        
        sources = []
        async for token in engine.stream_answer(notebook_id, query):
            # Detect end-of-stream sources marker
            if token.startswith("\n\n__SOURCES__"):
                import json
                sources = json.loads(token.replace("\n\n__SOURCES__", ""))
                break
            
            # Capture time to first token
            if first_token_time is None:
                first_token_time = time.time()
                print(f"[First token in {first_token_time - start_time:.2f}s]\n")
            
            # Print each token immediately as it arrives
            print(token, end="", flush=True)

        print(f"\n\n📚 SOURCES:")
        for i, source in enumerate(sources):
            print(f"  Source {i+1}: [{source['file']}] (Score: {source['score']:.4f})")
            print(f"  Snippet: {source['snippet'][:120]}...\n")

    except Exception as e:
        print(f"\n!!! STREAM TEST ERROR: {e}")
        import traceback
        traceback.print_exc()
    finally:
        total_time = time.time() - start_time
        print(f"\n{'='*70}")
        print(f"⏱  Time to first token: {(first_token_time or total_time) - start_time:.2f}s")
        print(f"⏱  Total time: {total_time:.2f}s")
        print(f"{'='*70}\n")

if __name__ == "__main__":
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(run_stream_test())
