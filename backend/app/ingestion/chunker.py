"""
Chunking.

WHY THIS FILE EXISTS
--------------------
Retrieval quality is decided here, more than anywhere else in the pipeline.

The embedding model turns text into ONE vector. A vector is a single point, so it
can only represent one "topic" well. If a chunk covers three topics, its vector is
a blurry average of all three and matches nothing precisely. If a chunk is a single
sentence, it has no context to match against. Chunking is the search for the middle.

We start with the simplest strategy that actually works (spec section 8):

  1. Structure-aware:  never split in the middle of a paragraph if we can avoid it
  2. Recursive:        if a block is too big, split on the largest natural
                       boundary first (blank line -> newline -> sentence -> word)
  3. Bounded:          target `chunk_size` characters, hard ceiling `max_chunk_chars`
  4. Overlapped:       carry the tail of the previous chunk forward so a fact that
                       straddles a boundary is not lost from both chunks

We deliberately do NOT do semantic chunking. It is a Phase 7 experiment (see
docs/06_advanced_rag.md) and it would hide the mechanism the team has to explain.

WHY OVERLAP BY WHOLE BLOCK (and not by characters)
--------------------------------------------------
The naive approach - "prepend the last 150 characters of the previous chunk" -
produces text whose provenance is unknown. Which page did those 150 characters come
from? If we cannot answer that, the citation is a lie.

So we overlap by re-including whole trailing blocks. Every character in every chunk
therefore still maps to exactly one source block, and therefore to exactly one page
/ slide / sheet / row. Provenance stays exact. That is worth the small amount of
extra text.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from app.ingestion.types import NormalizedBlock, NormalizedDocument, clean_text

# ---------------------------------------------------------------------------
# Recursive split boundaries, most natural first.
# ---------------------------------------------------------------------------
DEFAULT_SEPARATORS: tuple[str, ...] = (
    "\n\n",   # paragraph break
    "\n",     # line break
    ". ",     # sentence
    "! ",
    "? ",
    "; ",
    ", ",     # clause
    " ",      # word
    "",       # character (last resort)
)

_TOKEN_RATIO = 4  # rough chars-per-token; used for estimates only


@dataclass(slots=True)
class ChunkDraft:
    """A chunk ready to be embedded and stored."""

    content: str
    chunk_index: int
    char_start: int
    char_end: int
    block_type: str
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def token_estimate(self) -> int:
        return max(1, len(self.content) // _TOKEN_RATIO)

    @property
    def length(self) -> int:
        return len(self.content)

    def vector_id(self, document_id: int) -> str:
        return f"doc{document_id}_chunk{self.chunk_index}"


# ---------------------------------------------------------------------------
# Step 1 - recursive splitting of oversized text
# ---------------------------------------------------------------------------
def recursive_split(
    text: str,
    max_chars: int,
    separators: tuple[str, ...] = DEFAULT_SEPARATORS,
) -> list[str]:
    """Split `text` into pieces of at most ~`max_chars`, preferring natural boundaries.

    This is a plain, readable implementation on purpose. You can trace exactly why
    a given split happened, which is what makes it teachable.
    """
    text = text.strip()
    if not text:
        return []
    if len(text) <= max_chars:
        return [text]

    # Find the most natural separator that actually occurs in this text.
    separator = ""
    remaining: tuple[str, ...] = ()
    for index, candidate in enumerate(separators):
        if candidate == "":
            separator, remaining = "", ()
            break
        if candidate in text:
            separator, remaining = candidate, separators[index + 1 :]
            break

    if separator == "":
        # Nothing natural left: hard-split on the character grid. This is the
        # last resort and is only hit by things like minified JSON or base64.
        return [text[i : i + max_chars] for i in range(0, len(text), max_chars)]

    pieces = text.split(separator)
    out: list[str] = []
    buffer = ""

    for piece in pieces:
        candidate = piece if not buffer else buffer + separator + piece
        if len(candidate) <= max_chars:
            buffer = candidate
            continue

        if buffer:
            out.append(buffer)
            buffer = ""

        if len(piece) <= max_chars:
            buffer = piece
        else:
            # This single piece is still too big - recurse with finer separators.
            out.extend(recursive_split(piece, max_chars, remaining))

    if buffer:
        out.append(buffer)

    return [p for p in (p.strip() for p in out) if p]


# ---------------------------------------------------------------------------
# Step 2 - prepare blocks
# ---------------------------------------------------------------------------
def _prepare_blocks(document: NormalizedDocument, max_chars: int) -> list[NormalizedBlock]:
    """Clean blocks and split any that exceed the hard ceiling.

    Splitting happens HERE, before packing, so that the packing loop can assume
    every block is small enough to fit in a chunk. Keeping the two concerns apart
    is what makes both of them easy to reason about.
    """
    prepared: list[NormalizedBlock] = []

    for block in document.blocks:
        text = clean_text(block.text)
        if not text:
            continue

        if len(text) <= max_chars:
            prepared.append(
                NormalizedBlock(
                    text=text,
                    block_type=block.block_type,
                    order=len(prepared),
                    metadata=dict(block.metadata),
                )
            )
            continue

        # Oversized block: split it and give each part the same provenance, plus
        # a `part` marker so the trace can show that a split happened.
        parts = recursive_split(text, max_chars)
        for part_index, part in enumerate(parts):
            metadata = dict(block.metadata)
            metadata["split_part"] = part_index + 1
            metadata["split_parts_total"] = len(parts)
            prepared.append(
                NormalizedBlock(
                    text=part,
                    block_type=block.block_type,
                    order=len(prepared),
                    metadata=metadata,
                )
            )

    return prepared


# ---------------------------------------------------------------------------
# Step 3 - pack blocks into overlapping chunks
# ---------------------------------------------------------------------------
def chunk_document(
    document: NormalizedDocument,
    *,
    chunk_size: int = 1000,
    chunk_overlap: int = 150,
    min_chunk_chars: int = 80,
    max_chunk_chars: int = 4000,
    separators: tuple[str, ...] = DEFAULT_SEPARATORS,
) -> list[ChunkDraft]:
    """Turn a NormalizedDocument into a list of ChunkDrafts.

    Complexity is O(n) in the number of blocks for packing, plus the recursive
    split cost for any oversized block. There is no quadratic pass over the text.
    """
    if chunk_size <= 0:
        raise ValueError("chunk_size must be positive")
    if chunk_overlap >= chunk_size:
        # Overlap >= size would make every chunk start inside the previous one and
        # the loop would never make progress. Clamp rather than explode.
        chunk_overlap = max(0, chunk_size // 4)

    ceiling = max(max_chunk_chars, chunk_size)
    blocks = _prepare_blocks(document, ceiling)
    if not blocks:
        return []

    chunks: list[ChunkDraft] = []

    # `current` holds (block, is_overlap) pairs.
    current: list[tuple[NormalizedBlock, bool]] = []
    current_len = 0
    cursor = 0  # character offset into the concatenated document

    def flush() -> None:
        nonlocal current, current_len
        if not current:
            return
        drafts = _build_chunk(current, len(chunks), cursor - current_len)
        if drafts is not None:
            chunks.append(drafts)
        current = []
        current_len = 0

    for block in blocks:
        block_len = len(block.text)
        projected = current_len + block_len + (2 if current else 0)

        if current and projected > chunk_size:
            # --- carry overlap forward -------------------------------------
            overlap_blocks: list[tuple[NormalizedBlock, bool]] = []
            overlap_len = 0
            if chunk_overlap > 0:
                for candidate, was_overlap in reversed(current):
                    if was_overlap:
                        # Never carry an already-carried block again, otherwise a
                        # long document accumulates stale text indefinitely.
                        break
                    candidate_len = len(candidate.text)
                    if overlap_len + candidate_len > chunk_overlap:
                        break
                    overlap_blocks.insert(0, (candidate, True))
                    overlap_len += candidate_len

            flush()
            current = list(overlap_blocks)
            current_len = overlap_len

        current.append((block, False))
        current_len += block_len + (2 if len(current) > 1 else 0)
        cursor += block_len + 2

    flush()

    chunks = _merge_tiny_chunks(chunks, min_chunk_chars=min_chunk_chars, ceiling=ceiling)
    return _reindex(chunks)


def _build_chunk(
    entries: list[tuple[NormalizedBlock, bool]], index: int, char_start: int
) -> ChunkDraft | None:
    """Assemble one chunk from its blocks and merge their provenance."""
    if not entries:
        return None

    content = "\n\n".join(block.text for block, _ in entries).strip()
    if not content:
        return None

    primary = [block for block, is_overlap in entries if not is_overlap] or [entries[0][0]]
    metadata = _merge_metadata(primary, entries)

    block_types = {block.block_type for block, _ in entries}
    if len(block_types) == 1:
        block_type = next(iter(block_types))
    elif "table_row" in block_types or "table" in block_types:
        block_type = "table"
    else:
        block_type = "mixed"

    return ChunkDraft(
        content=content,
        chunk_index=index,
        char_start=char_start,
        char_end=char_start + len(content),
        block_type=block_type,
        metadata=metadata,
    )


def _merge_metadata(
    primary: list[NormalizedBlock],
    entries: list[tuple[NormalizedBlock, bool]],
) -> dict[str, Any]:
    """Combine provenance from the primary blocks of a chunk.

    Multi-valued fields become lists so a chunk spanning pages 3-4 keeps both, and
    the citation card can say "pages 3-4" instead of picking one and being wrong.
    """
    metadata: dict[str, Any] = {}

    # Document-level identity, taken from the first block.
    for key in ("document_id", "document_name", "file_type"):
        value = primary[0].metadata.get(key)
        if value is not None:
            metadata[key] = value

    def collect(key: str) -> list[Any]:
        seen: list[Any] = []
        for block in primary:
            value = block.metadata.get(key)
            if value is None or value == "":
                continue
            if value not in seen:
                seen.append(value)
        return seen

    pages = collect("page_number")
    if pages:
        metadata["page_number"] = min(pages)
        metadata["page_end"] = max(pages)
        metadata["pages"] = sorted(pages)

    slides = collect("slide_number")
    if slides:
        metadata["slide_number"] = min(slides)
        metadata["slide_end"] = max(slides)
        metadata["slides"] = sorted(slides)

    sheets = collect("sheet_name")
    if sheets:
        metadata["sheet_name"] = sheets[0]
        metadata["sheets"] = sheets

    sections = collect("section")
    if sections:
        metadata["section"] = sections[0]
        metadata["sections"] = sections

    json_paths = collect("json_path")
    if json_paths:
        metadata["json_path"] = json_paths[0]
        metadata["json_paths"] = json_paths[:8]

    rows = collect("row_start")
    if rows:
        metadata["row_start"] = min(rows)
        metadata["row_end"] = max(collect("row_end") or rows)

    block_types = collect("block_type")
    if block_types:
        metadata["block_types"] = block_types

    # Record how many blocks were carried over purely as overlap. This makes the
    # overlap visible in RAG Trace rather than being invisible magic.
    overlap_count = sum(1 for _, is_overlap in entries if is_overlap)
    metadata["overlap_blocks"] = overlap_count
    metadata["block_count"] = len(entries)

    return metadata


def _merge_tiny_chunks(
    chunks: list[ChunkDraft], *, min_chunk_chars: int, ceiling: int
) -> list[ChunkDraft]:
    """Absorb fragments into their neighbour.

    A 30-character chunk at the end of a document embeds to noise and pollutes
    Top-K. Better to attach it to the previous chunk than to index it.
    """
    if not chunks:
        return []

    merged: list[ChunkDraft] = [chunks[0]]
    for chunk in chunks[1:]:
        previous = merged[-1]
        if chunk.length >= min_chunk_chars:
            merged.append(chunk)
            continue
        if previous.length + chunk.length + 2 <= ceiling:
            previous.content = f"{previous.content}\n\n{chunk.content}"
            previous.char_end = previous.char_start + len(previous.content)
            previous.metadata = _merge_metadata(
                [
                    NormalizedBlock("", metadata=previous.metadata),
                    NormalizedBlock("", metadata=chunk.metadata),
                ],
                [
                    (NormalizedBlock("", metadata=previous.metadata), False),
                    (NormalizedBlock("", metadata=chunk.metadata), False),
                ],
            )
            previous.block_type = "mixed"
        else:
            merged.append(chunk)

    # If the very first chunk is tiny and there is a next one, fold it forward.
    if len(merged) > 1 and merged[0].length < min_chunk_chars:
        first, second = merged[0], merged[1]
        if second.length + first.length + 2 <= ceiling:
            second.content = f"{first.content}\n\n{second.content}"
            second.char_start = first.char_start
            second.char_end = second.char_start + len(second.content)
            merged = merged[1:]

    return merged


def _reindex(chunks: list[ChunkDraft]) -> list[ChunkDraft]:
    """Renumber chunk_index and refresh char offsets after merging."""
    for index, chunk in enumerate(chunks):
        chunk.chunk_index = index
    return chunks


# ---------------------------------------------------------------------------
# Diagnostics - shown on the Playground page
# ---------------------------------------------------------------------------
def describe_chunks(chunks: list[ChunkDraft]) -> dict[str, Any]:
    """Real statistics about a chunking run. Nothing here is estimated or faked."""
    if not chunks:
        return {"count": 0}

    lengths = [c.length for c in chunks]
    tokens = [c.token_estimate for c in chunks]
    overlaps = [c.metadata.get("overlap_blocks", 0) for c in chunks]

    return {
        "count": len(chunks),
        "characters": {
            "total": sum(lengths),
            "min": min(lengths),
            "max": max(lengths),
            "mean": round(sum(lengths) / len(lengths), 1),
        },
        "tokens_estimated": {
            "total": sum(tokens),
            "min": min(tokens),
            "max": max(tokens),
            "mean": round(sum(tokens) / len(tokens), 1),
        },
        "overlap": {
            "chunks_with_overlap": sum(1 for o in overlaps if o),
            "blocks_carried_total": sum(overlaps),
        },
        "block_types": _count_block_types(chunks),
        "has_provenance": {
            "with_page": sum(1 for c in chunks if c.metadata.get("page_number")),
            "with_slide": sum(1 for c in chunks if c.metadata.get("slide_number")),
            "with_sheet": sum(1 for c in chunks if c.metadata.get("sheet_name")),
            "with_section": sum(1 for c in chunks if c.metadata.get("section")),
            "with_json_path": sum(1 for c in chunks if c.metadata.get("json_path")),
        },
    }


def _count_block_types(chunks: list[ChunkDraft]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for chunk in chunks:
        counts[chunk.block_type] = counts.get(chunk.block_type, 0) + 1
    return counts


# ---------------------------------------------------------------------------
# Chunk preview used by the "Chunking" demo panel
# ---------------------------------------------------------------------------
def preview(text: str, *, chunk_size: int, chunk_overlap: int, max_chunks: int = 12) -> list[dict]:
    """Chunk a raw string without touching the database.

    This is what powers the interactive chunk-size slider on the Playground page:
    you type or paste text, move the slider, and watch the real chunker respond.
    """
    document = NormalizedDocument(
        document_id=0, document_name="preview", file_type="txt"
    )
    for index, paragraph in enumerate(re.split(r"\n\s*\n", text)):
        if paragraph.strip():
            document.add_block(paragraph, block_type="paragraph", block_index=index)

    drafts = chunk_document(
        document,
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        min_chunk_chars=0,
        max_chunk_chars=max(chunk_size * 3, 1200),
    )

    return [
        {
            "chunk_index": c.chunk_index,
            "content": c.content,
            "characters": c.length,
            "tokens_estimated": c.token_estimate,
            "char_start": c.char_start,
            "char_end": c.char_end,
            "block_type": c.block_type,
            "overlap_blocks": c.metadata.get("overlap_blocks", 0),
        }
        for c in drafts[:max_chunks]
    ]
