"""Smoke-test embeddings and Qdrant with no API key required.

    python scripts/check_vectorstore.py

Downloads the embedding model on first run (~130 MB), then works offline.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import console  # noqa: E402

console.setup()

from app import vectorstore  # noqa: E402
from app.config import get_settings  # noqa: E402

SAMPLES = [
    {
        "text": "FST100-2006A soil sensor. Output signal: RS485 Modbus RTU. "
                "Supply voltage 5-30 VDC. Temperature range -40 to 85 C.",
        "mpn": "FST100-2006A",
        "kind": "text",
    },
    {
        "text": "[Figure, page 2] Specification table. Moisture accuracy plus or "
                "minus 3 percent. Conductivity range 0 to 20 mS/cm. IP68 sealed.",
        "mpn": "FST100-2006A",
        "kind": "image",
    },
    {
        "text": "Pressure transmitter PT500. Output 4-20 mA two-wire. "
                "Range 0-16 bar. Accuracy 0.5 percent full scale.",
        "mpn": "PT500",
        "kind": "text",
    },
]


def main() -> None:
    settings = get_settings()
    print(f"model      {settings.embedding_model} ({settings.embedding_dim} dims)")
    print(f"qdrant     {settings.qdrant_path}")

    print("\nloading embedder (first run downloads the model)...")
    vectors = vectorstore.embed(["dimension probe"])
    actual = len(vectors[0])
    print(f"embedded   ok, {actual} dims")
    if actual != settings.embedding_dim:
        print(f"  ! MISMATCH: config says {settings.embedding_dim}, model gives {actual}")
        sys.exit(1)

    if vectorstore.count() == 0:
        added = vectorstore.add_chunks(SAMPLES)
        print(f"indexed    {added} sample chunks")
    else:
        print(f"indexed    {vectorstore.count()} chunks already present")

    for query in [
        "what output signal does the soil sensor use",
        "which part measures pressure in bar",
        "ingress protection rating",
    ]:
        print(f"\nq: {query}")
        for hit in vectorstore.search(query, limit=2):
            snippet = hit["text"][:78].replace("\n", " ")
            print(f"   {hit['score']:.3f}  [{hit['mpn']}] {snippet}...")

    print("\nvector store ok")
    vectorstore.close()


if __name__ == "__main__":
    main()
