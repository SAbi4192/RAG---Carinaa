"""
Download the local embedding model.

WHY THIS SCRIPT EXISTS
----------------------
`fastembed` normally pulls models through the HuggingFace hub client. In some
locked-down, proxied or sandboxed environments that client silently produces
zero-byte files, which then fail to load with a confusing
"ModelProto does not have a graph" error.

This script downloads the same files over plain HTTPS instead, verifies every one
of them, and installs them into both:

  * `data/models/fastembed/`              - used directly via `specific_model_path`
  * the fastembed HF cache snapshot       - the fallback path

Run it once, before first use:

    .venv/Scripts/python scripts/fetch_models.py

After it completes, embedding and retrieval work entirely offline.

WHAT IT DOWNLOADS
-----------------
    sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2  (ONNX, quantized)
      ~235 MB, 384 dimensions, 50+ languages

    Mirrored at the `qdrant/paraphrase-multilingual-MiniLM-L12-v2-onnx-Q` repo,
    which is the ONNX build fastembed expects.
"""

from __future__ import annotations

import json
import shutil
import sys
import urllib.error
import urllib.request
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "backend"))

from app.core.config import settings  # noqa: E402

REPO = "qdrant/paraphrase-multilingual-MiniLM-L12-v2-onnx-Q"
BASE_URL = f"https://huggingface.co/{REPO}/resolve/main"

# Everything fastembed needs. `model_optimized.onnx` is the actual network.
FILES: tuple[str, ...] = (
    "model_optimized.onnx",
    "config.json",
    "tokenizer.json",
    "tokenizer_config.json",
    "special_tokens_map.json",
    "ort_config.json",
    "unigram.json",
)

# A file is considered broken if it is missing, empty, or (for JSON) unparseable.
_MIN_SIZES: dict[str, int] = {
    "model_optimized.onnx": 10 * 1024 * 1024,
    "tokenizer.json": 100 * 1024,
    "unigram.json": 100 * 1024,
    "config.json": 100,
    "tokenizer_config.json": 100,
    "special_tokens_map.json": 50,
    "ort_config.json": 50,
}


def download(url: str, destination: Path) -> int:
    """Stream a URL to disk. Returns bytes written."""
    request = urllib.request.Request(url, headers={"User-Agent": "carinaa-setup/1.0"})
    with urllib.request.urlopen(request, timeout=300) as response:
        total = int(response.headers.get("Content-Length") or 0)
        written = 0
        with destination.open("wb") as handle:
            while True:
                block = response.read(1024 * 256)
                if not block:
                    break
                handle.write(block)
                written += len(block)
        return written if written else total


def is_healthy(path: Path, name: str) -> bool:
    if not path.is_file():
        return False
    size = path.stat().st_size
    if size < _MIN_SIZES.get(name, 1):
        return False
    if name.endswith(".json"):
        try:
            json.loads(path.read_text(encoding="utf-8"))
        except (ValueError, UnicodeDecodeError):
            return False
    return True


def find_cache_snapshot() -> Path | None:
    """Locate the fastembed HF cache snapshot directory, if it exists."""
    cache = Path(settings.embedding_cache_dir)
    pattern = f"models--{REPO.replace('/', '--')}"
    root = cache / pattern / "snapshots"
    if not root.is_dir():
        return None
    snapshots = [p for p in root.iterdir() if p.is_dir()]
    return snapshots[0] if snapshots else None


def main() -> int:
    target = Path(settings.embedding_local_dir)
    target.mkdir(parents=True, exist_ok=True)

    print(f"Model      : {settings.embedding_model}")
    print(f"Downloading: {REPO}")
    print(f"Into       : {target}")
    print("-" * 68)

    failures: list[str] = []

    for name in FILES:
        destination = target / name

        if is_healthy(destination, name):
            size_mb = destination.stat().st_size / (1024 * 1024)
            print(f"  cached   {name:28} {size_mb:8.2f} MB")
            continue

        print(f"  fetching {name:28} ...", end="", flush=True)
        try:
            written = download(f"{BASE_URL}/{name}", destination)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            print(f" FAILED ({exc.__class__.__name__})")
            failures.append(f"{name}: {exc}")
            continue

        if not is_healthy(destination, name):
            print(" INVALID after download")
            failures.append(f"{name}: downloaded file failed validation")
            continue

        print(f" {written / (1024 * 1024):8.2f} MB")

    if failures:
        print("-" * 68)
        print("FAILED. The model cannot be used until these are resolved:")
        for failure in failures:
            print(f"  - {failure}")
        print("\nCheck your network connection or proxy settings and re-run.")
        return 1

    print("-" * 68)
    print("Verifying the model loads...")

    try:
        from fastembed import TextEmbedding

        model = TextEmbedding(
            model_name=settings.embedding_model, specific_model_path=str(target)
        )
        vector = next(iter(model.embed(["Carinaa embedding self-test"])))
        dimension = len(vector)
        print(f"OK - model loads and embeds. Dimension = {dimension}")
    except Exception as exc:
        print(f"FAILED to load: {exc.__class__.__name__}: {exc}")
        return 1

    # ---- repair the fastembed cache as well --------------------------------
    snapshot = find_cache_snapshot()
    if snapshot is not None:
        print(f"\nRepairing fastembed cache snapshot:\n  {snapshot}")
        for name in FILES:
            source = target / name
            destination = snapshot / name
            if is_healthy(destination, name):
                continue
            try:
                shutil.copyfile(source, destination)
                print(f"  repaired {name}")
            except OSError as exc:
                print(f"  could not repair {name}: {exc}")
    else:
        print(
            "\nNo fastembed cache snapshot found. That is fine - the model will be "
            "loaded directly from the local directory."
        )

    print("\nDone. Embeddings will now run fully offline.")
    print(f"To use a different model, change EMBEDDING_MODEL in .env, run this script")
    print(f"with the matching repo, and re-index your documents from the Knowledge Base.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
