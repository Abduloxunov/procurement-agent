"""Datasheet ingestion: PDF -> text chunks + captioned images -> Qdrant.

The image half is not decoration. Datasheet specifications live in tables and
diagrams that are frequently images inside the PDF, so without captioning them
roughly half the specifications are invisible to retrieval (design doc S08).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import fitz  # PyMuPDF

from app import vectorstore
from app.llm import describe_image

CAPTION_PROMPT = (
    "This image is from an industrial component datasheet. Transcribe it as "
    "searchable text. If it is a specification table, list every parameter and "
    "its value including units. If it is a wiring or dimensional diagram, "
    "describe the connections, pin labels and measurements. Be exhaustive and "
    "literal -- do not summarise or interpret."
)

# Small images are logos, icons and rules. Captioning them wastes calls.
MIN_IMAGE_WIDTH = 180
MIN_IMAGE_HEIGHT = 120

CHUNK_SIZE = 1000
CHUNK_OVERLAP = 150


def chunk_text(text: str, size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    """Overlapping character chunks, split on paragraph edges where possible."""
    text = text.strip()
    if not text:
        return []
    if len(text) <= size:
        return [text]

    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = start + size
        if end < len(text):
            # Prefer a paragraph or sentence boundary in the last 200 chars.
            window = text[start:end]
            for marker in ("\n\n", "\n", ". "):
                cut = window.rfind(marker)
                if cut > size - 200:
                    end = start + cut + len(marker)
                    break
        chunks.append(text[start:end].strip())
        start = max(end - overlap, start + 1)
    return [chunk for chunk in chunks if chunk]


def extract_page_images(page: "fitz.Page", doc: "fitz.Document") -> list[bytes]:
    """PNG bytes for every image on the page that is large enough to matter."""
    images: list[bytes] = []
    for xref, *_ in page.get_images(full=True):
        try:
            pixmap = fitz.Pixmap(doc, xref)
            if pixmap.width < MIN_IMAGE_WIDTH or pixmap.height < MIN_IMAGE_HEIGHT:
                continue
            if pixmap.n - pixmap.alpha >= 4:  # CMYK -> RGB
                pixmap = fitz.Pixmap(fitz.csRGB, pixmap)
            images.append(pixmap.tobytes("png"))
        except Exception:
            # A single unreadable image must not abort the whole datasheet.
            continue
    return images


def ingest_pdf(
    path: str | Path,
    *,
    mpn: str,
    manufacturer: str | None = None,
    caption_images: bool = True,
) -> dict[str, Any]:
    """Ingest one datasheet. Returns a summary of what went in."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Datasheet not found: {path}")

    doc = fitz.open(path)
    chunks: list[dict[str, Any]] = []
    captioned = 0

    for page_number, page in enumerate(doc, start=1):
        base_payload = {
            "mpn": mpn,
            "manufacturer": manufacturer,
            "source_file": path.name,
            "page": page_number,
        }

        for chunk in chunk_text(page.get_text()):
            chunks.append({**base_payload, "text": chunk, "kind": "text"})

        if not caption_images:
            continue

        for index, image_bytes in enumerate(extract_page_images(page, doc)):
            try:
                caption = describe_image(image_bytes, CAPTION_PROMPT)
            except Exception as exc:  # noqa: BLE001 - report and keep going
                print(f"  ! caption failed p{page_number} img{index}: {exc}")
                continue
            if not caption.strip():
                continue
            chunks.append(
                {
                    **base_payload,
                    "text": f"[Figure, page {page_number}] {caption}",
                    "kind": "image",
                    "image_index": index,
                }
            )
            captioned += 1

    page_count = doc.page_count
    doc.close()
    added = vectorstore.add_chunks(chunks)

    return {
        "file": path.name,
        "mpn": mpn,
        "pages": page_count,
        "text_chunks": sum(1 for c in chunks if c["kind"] == "text"),
        "image_captions": captioned,
        "total_indexed": added,
    }
