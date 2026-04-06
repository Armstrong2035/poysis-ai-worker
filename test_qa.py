import os
import sys
import time
import asyncio
from dotenv import load_dotenv

load_dotenv()
sys.path.append(os.getcwd())

from app.primitives.knowledge.engine import KnowledgeEngine

async def run_qa_test():
    # We assume 'nb_llamaindex_fast_001' was already ingested in the previous step
    notebook_id = "nb_llamaindex_fast_001"
    
    print(f"\n{'='*70}")
    print(f"🧠 RAG TEST: DIRECT QUESTION ANSWERING")
    print(f"Notebook: {notebook_id}")
    print(f"{'='*70}")

    try:
        engine = KnowledgeEngine()
        
        # Test 1: Direct Question
        question = "What is the 'Focusing Question' described in the book?"
        print(f"\n[1] ASKING: {question}")
        
        start_time = time.time()
        result = await engine.answer_question(notebook_id, question)
        end_time = time.time()
        
        print(f"\n💡 ANSWER:\n{result['answer']}")
        
        print(f"\n📚 SOURCES:")
        for i, source in enumerate(result['sources']):
            print(f"  Source {i+1}: [{source['file']}] (Score: {source['score']:.4f})")
            print(f"  Snippet: {source['snippet']}\n")
            
        print(f"Time taken: {end_time - start_time:.2f} seconds")

        # Test 2: Search Verification (Is the "NO TEXT CONTENT" bug fixed?)
        print(f"\n[2] VERIFYING SEARCH SNIPPETS...")
        search_query = "Gary Keller success strategy"
        search_results = await engine.fetch_raw(notebook_id, search_query, top_k=2)
        
        for i, res in enumerate(search_results):
            text = res.get('text') or "STILL NO TEXT CONTENT"
            print(f"  Result {i+1}: [Score: {res['score']:.4f}] {text[:150]}...")

    except Exception as e:
        print(f"\n!!! QA TEST ERROR: {e}")
        import traceback
        traceback.print_exc()
    finally:
        print(f"\n{'='*70}\n")

if __name__ == "__main__":
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(run_qa_test())
