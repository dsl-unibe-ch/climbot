"""
Quick Qdrant inspection script.

Usage:
  python scripts/query_qdrant.py                  # list collections + scroll first 5 points each
  python scripts/query_qdrant.py --search "text"  # embed a query and run similarity search
  python scripts/query_qdrant.py --collection climate_images --limit 10
  python scripts/query_qdrant.py --host localhost --port 6333
"""

import argparse
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from qdrant_client import QdrantClient

_repo_root = Path(__file__).resolve().parents[1]
# Root .env only has VM_HOST (for Makefile); service vars are in backend/.env.dev
load_dotenv(_repo_root / "backend" / ".env.dev")

QDRANT_HOST = os.environ.get("QDRANT_HOST", "localhost")
QDRANT_PORT = int(os.environ.get("QDRANT_PORT", "6333"))
QDRANT_API_KEY = os.environ.get("QDRANT_API_KEY") or None
COLLECTION_TEXT = os.environ.get("QDRANT_COLLECTION_TEXT", "climate_docs")
COLLECTION_IMAGES = os.environ.get("QDRANT_COLLECTION_IMAGES", "climate_images")

client = QdrantClient(
    host=QDRANT_HOST,
    port=QDRANT_PORT,
    api_key=QDRANT_API_KEY,
)


def list_collections() -> None:
    cols = client.get_collections().collections
    print(f"\n{'='*60}")
    print(f"Qdrant  {QDRANT_HOST}:{QDRANT_PORT}")
    print(f"{'='*60}")
    if not cols:
        print("No collections found.")
        return
    for col in cols:
        info = client.get_collection(col.name)
        print(
            f"  {col.name:<35} vectors: {info.vectors_count}  "
            f"indexed: {info.indexed_vectors_count}"
        )
    print()


def scroll_points(collection: str, limit: int = 5) -> None:
    results, _next = client.scroll(
        collection_name=collection,
        limit=limit,
        with_payload=True,
        with_vectors=False,
    )
    print(f"\n--- {collection} (first {limit} points) ---")
    if not results:
        print("  (empty)")
        return
    for pt in results:
        payload = pt.payload or {}
        print(f"\n  id: {pt.id}")
        for k, v in payload.items():
            val = str(v)
            if len(val) > 120:
                val = val[:117] + "..."
            print(f"    {k}: {val}")


def search(collection: str, query: str, limit: int = 5) -> None:
    # Import here so the script can still list/scroll without needing the model
    sys.path.insert(0, str(_repo_root / "backend"))
    from qdrant_client.models import Fusion, FusionQuery, Prefetch, SparseVector

    from app.core.embeddings import embed_sparse, embed_texts

    print(f"\nEmbedding query: '{query}'")
    query_vector = embed_texts([query])[0]
    sparse_indices, sparse_values = embed_sparse([query])[0]

    results = client.query_points(
        collection_name=collection,
        prefetch=[
            Prefetch(query=query_vector, using="dense", limit=limit * 4),
            Prefetch(
                query=SparseVector(indices=sparse_indices, values=sparse_values),
                using="sparse",
                limit=limit * 4,
            ),
        ],
        query=FusionQuery(fusion=Fusion.RRF),
        limit=limit,
        with_payload=True,
    ).points
    print(f"\n--- Search results in '{collection}' (top {limit}) ---")
    if not results:
        print("  (no results)")
        return
    for pt in results:
        payload = pt.payload or {}
        print(f"\n  score: {pt.score:.4f}  id: {pt.id}")
        for k, v in payload.items():
            val = str(v)
            if len(val) > 120:
                val = val[:117] + "..."
            print(f"    {k}: {val}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Inspect Qdrant collections")
    parser.add_argument("--collection", default=None, help="Restrict to one collection")
    parser.add_argument("--limit", type=int, default=5, help="Points to show (default 5)")
    parser.add_argument("--search", default=None, metavar="QUERY", help="Run a similarity search")
    args = parser.parse_args()

    list_collections()

    collections_to_check = (
        [args.collection] if args.collection else [COLLECTION_TEXT, COLLECTION_IMAGES]
    )

    if args.search:
        for col in collections_to_check:
            search(col, args.search, args.limit)
    else:
        for col in collections_to_check:
            scroll_points(col, args.limit)


if __name__ == "__main__":
    main()
