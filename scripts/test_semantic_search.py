import asyncio
import os
from dotenv import load_dotenv
from app.primitives.knowledge.engine import KnowledgeEngine

# Load environment variables (GEMINI_API_KEY, PINE_CONE_API_KEY)
load_dotenv()

async def run_test():
    workspace_id = "test-workspace-unified"
    engine = KnowledgeEngine()
    
    print(f"--- Phase 1: Testing Unified Knowledge Ingestion ---")
    sample_docs = [
        {
            "source_id": "doc_001",
            "text": "The solar system consists of the Sun and the objects that orbit it, including eight planets.",
            "category": "science"
        },
        {
            "source_id": "doc_002",
            "text": "Deep learning is a subset of machine learning that is based on artificial neural networks.",
            "category": "ai"
        }
    ]
    
    count = await engine.upsert_documents(workspace_id, sample_docs)
    print(f"Successfully indexed {count} JSON documents.\n")
    
    # Create a temporary CSV to test multi-format ingestion
    print(f"--- Phase 2: Testing Multi-Format Ingestion (CSV) ---")
    csv_content = "title,description\nMars,The fourth planet from the Sun\nPython,A versatile programming language"
    csv_path = "/tmp/test_data.csv"
    os.makedirs("/tmp", exist_ok=True)
    with open(csv_path, "w") as f:
        f.write(csv_content)
    
    file_count = await engine.ingest_file(workspace_id, csv_path)
    print(f"Successfully ingested {file_count} documents from CSV.\n")
    
    print(f"--- Phase 3: Testing Semantic Retrieval ---")
    queries = [
        "Tell me about planets",
        "How does machine learning work?",
        "Programming languages"
    ]
    
    for query in queries:
        print(f"\nQuery: '{query}'")
        raw_results = await engine.fetch_raw(workspace_id, query, top_k=4)
        
        # Apply block-level policy (min score)
        results = [r for r in raw_results if r.get("score", 0) >= 0.5]
        
        if not results:
            print("No results found.")
        for i, res in enumerate(results):
            metadata = res.get("metadata", {}) if isinstance(res, dict) else {}
            text = metadata.get("text", "N/A")
            print(f"  [{i+1}] Score: {res['score']:.4f} | Text: {text[:100]}...")

if __name__ == "__main__":
    asyncio.run(run_test())
