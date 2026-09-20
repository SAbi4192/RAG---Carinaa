"""
Parser registry.

Each parser converts ONE file format into the shared `NormalizedDocument`.
Adding support for a new format means writing one module and registering it
here - no other file in the codebase changes.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Protocol

from app.core.errors import ParseFailure, UnsupportedFileType
from app.ingestion.types import NormalizedDocument


class Parser(Protocol):
    """Signature every format parser implements."""

    extensions: tuple[str, ...]
    label: str

    def __call__(self, path: Path, document_id: int, document_name: str) -> NormalizedDocument: ...


# Populated at import time by the decorator below.
_REGISTRY: dict[str, Callable[..., NormalizedDocument]] = {}
_LABELS: dict[str, str] = {}


def register(*extensions: str, label: str = "") -> Callable:
    """Decorator: `@register(".pdf", ".PDF", label="PDF")`."""

    def decorator(func: Callable[..., NormalizedDocument]) -> Callable[..., NormalizedDocument]:
        for ext in extensions:
            _REGISTRY[ext.lower()] = func
            _LABELS[ext.lower()] = label or ext.lstrip(".").upper()
        return func

    return decorator


def supported_extensions() -> list[str]:
    """Sorted, deduplicated list of extensions the UI may advertise."""
    return sorted({e for e in _REGISTRY})


def parser_label(extension: str) -> str:
    return _LABELS.get(extension.lower(), extension.lstrip(".").upper())


def is_supported(filename: str) -> bool:
    return Path(filename).suffix.lower() in _REGISTRY


def get_parser(filename: str) -> Callable[..., NormalizedDocument]:
    ext = Path(filename).suffix.lower()
    parser = _REGISTRY.get(ext)
    if parser is None:
        raise UnsupportedFileType(
            f"'{ext or filename}' is not a supported file type.",
            detail={"supported": supported_extensions()},
        )
    return parser


def parse_file(path: Path, document_id: int, document_name: str) -> NormalizedDocument:
    """Dispatch to the right parser and sanity-check the result."""
    parser = get_parser(document_name)
    try:
        doc = parser(path, document_id, document_name)
    except (UnsupportedFileType, ParseFailure):
        raise
    except Exception as exc:
        raise ParseFailure(
            f"We could not read '{document_name}'. The file may be corrupted or "
            f"password-protected.",
            detail={"reason": exc.__class__.__name__},
        ) from exc

    if doc.is_empty:
        raise ParseFailure(
            f"No extractable text was found in '{document_name}'. If it is a scanned "
            f"document, it would need OCR first - Carinaa does not perform OCR.",
            detail={"hint": "scanned_or_image_only"},
        )
    return doc


# Import parsers so their @register decorators run.
from app.ingestion.parsers import (  # noqa: E402,F401
    csv_parser,
    docx_parser,
    json_parser,
    pdf_parser,
    pptx_parser,
    text_parser,
    xlsx_parser,
)

__all__ = [
    "Parser",
    "get_parser",
    "is_supported",
    "parse_file",
    "parser_label",
    "register",
    "supported_extensions",
]
