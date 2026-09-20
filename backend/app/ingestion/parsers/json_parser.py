"""
JSON parser.

WHY JSON NEEDS ITS OWN PARSER
-----------------------------
A JSON file has no paragraphs. It has *addressable leaves*. If you stringify it
and chunk the result, a citation can only say "somewhere in this 4 MB file". By
walking the structure and emitting one block per meaningful leaf, we can cite
`results[2].metrics.accuracy` - an address the user can actually verify.

Rendering rule:
    {"model": "ResNet", "accuracy": 0.94}
    ->  "model: ResNet | accuracy: 0.94"        at path "results[2]"

Scalars directly under a key become "key: value". Objects and arrays become their
own path segment so nested records stay independently retrievable.
"""

from __future__ import annotations

import json
from pathlib import Path

from app.ingestion.parsers import register
from app.ingestion.types import NormalizedDocument, clean_text

_MAX_LEAVES = 6000
_MAX_VALUE_CHARS = 800
_MAX_DEPTH = 24


@register(".json", ".jsonl", ".ndjson", label="JSON")
def parse_json(path: Path, document_id: int, document_name: str) -> NormalizedDocument:
    doc = NormalizedDocument(
        document_id=document_id, document_name=document_name, file_type="json"
    )

    text = _decode(path.read_bytes()).strip()
    if not text:
        return doc

    # JSON Lines support: one independent object per line.
    if path.suffix.lower() in (".jsonl", ".ndjson") or _looks_like_jsonl(text):
        count = 0
        for line_number, line in enumerate(text.splitlines(), start=1):
            line = line.strip()
            if not line:
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                doc.warnings.append(f"Line {line_number} is not valid JSON and was skipped.")
                continue
            _emit(doc, value, f"[{line_number}]", depth=0)
            count += 1
            if count >= _MAX_LEAVES:
                doc.warnings.append(f"Truncated at {_MAX_LEAVES} records.")
                break
        doc.doc_metadata = {"format": "jsonl", "records": count}
        return doc

    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON: line {exc.lineno}, column {exc.colno}.") from exc

    _emit(doc, data, "$", depth=0)
    doc.doc_metadata = {"format": "json"}
    return doc


def _decode(raw: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _looks_like_jsonl(text: str) -> bool:
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if len(lines) < 2:
        return False
    head = lines[:5]
    try:
        return all(isinstance(json.loads(ln), (dict, list)) for ln in head)
    except json.JSONDecodeError:
        return False


def _render_scalar(value: object) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    text = str(value)
    if len(text) > _MAX_VALUE_CHARS:
        text = text[:_MAX_VALUE_CHARS] + "..."
    return text


def _emit(doc: NormalizedDocument, node: object, path: str, depth: int) -> None:
    """Recursively walk the structure, emitting one block per record."""
    if depth > _MAX_DEPTH:
        doc.warnings.append(f"Maximum nesting depth ({_MAX_DEPTH}) reached; deeper values were skipped.")
        return
    if len(doc.blocks) >= _MAX_LEAVES:
        return

    if isinstance(node, dict):
        if not node:
            return

        # A "flat record" (mostly scalars) is emitted as a single labelled line,
        # which is what makes a row of JSON data self-describing.
        scalar_pairs: list[str] = []
        nested: list[tuple[str, object]] = []

        for key, value in node.items():
            if isinstance(value, (dict, list)) and value:
                nested.append((str(key), value))
            elif isinstance(value, list) and not value:
                scalar_pairs.append(f"{key}: []")
            elif isinstance(value, dict) and not value:
                scalar_pairs.append(f"{key}: {{}}")
            else:
                scalar_pairs.append(f"{key}: {_render_scalar(value)}")

        if scalar_pairs:
            section = _section_for_path(path)
            doc.add_block(
                clean_text(" | ".join(scalar_pairs)),
                block_type="json_leaf",
                json_path=path,
                section=section,
                depth=depth,
            )

        for key, value in nested:
            child_path = f"{path}.{key}" if path not in ("$", "") else key
            if isinstance(value, list):
                for index, item in enumerate(value):
                    if isinstance(item, (dict, list)):
                        _emit(doc, item, f"{child_path}[{index}]", depth + 1)
                    else:
                        doc.add_block(
                            clean_text(f"{key}[{index}]: {_render_scalar(item)}"),
                            block_type="json_leaf",
                            json_path=f"{child_path}[{index}]",
                            section=_section_for_path(path),
                            depth=depth + 1,
                        )
            else:
                _emit(doc, value, child_path, depth + 1)
        return

    if isinstance(node, list):
        for index, item in enumerate(node):
            _emit(doc, item, f"{path}[{index}]", depth + 1)
        return

    doc.add_block(
        clean_text(f"{path}: {_render_scalar(node)}"),
        block_type="json_leaf",
        json_path=path,
        section=_section_for_path(path),
        depth=depth,
    )


def _section_for_path(path: str) -> str:
    """Derive a human-readable section from a JSON path.

    "results[2].metrics" -> "results"   (the collection the record belongs to)
    """
    root = path.lstrip("$").lstrip(".")
    if not root:
        return "$ (root)"
    head = root.split("[")[0].split(".")[0]
    return head or "$ (root)"
