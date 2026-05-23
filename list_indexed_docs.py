from dotenv import load_dotenv

load_dotenv(override=True)

from app.primitives.knowledge.vector_store import VectorService

WORKSPACE_ID = "test123"


def main():
    vs = VectorService()
    namespace = f"consolidation_{WORKSPACE_ID}"
    docs = vs.list_documents(namespace)

    print(f"{len(docs)} documents indexed in '{namespace}'\n")
    for i, d in enumerate(docs, 1):
        print(f"{i:3}. [{d['chunks']:3d} chunks]  {d['title'] or d['source_id']}")
        if d["url"]:
            print(f"          {d['url']}")


main()
