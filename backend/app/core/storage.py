"""
Stored-file housekeeping.

WHY THIS IS ITS OWN MODULE
--------------------------
Deleting an uploaded file looks like a one-liner, and that is exactly why it keeps
causing trouble. Three separate places needed to do it (delete a document, delete a
workspace, clean up a half-written upload), and each one had its own slightly
different, slightly wrong error handling.

THE RULE
--------
**File removal is never allowed to fail the operation that triggered it.**

By the time we remove a file, the meaningful state change has already happened: the
database row is gone and the vectors are gone. The stored file is only a convenience
copy - the text lives in the chunks and the numbers live in the index. So if the file
cannot be removed, the correct outcome is:

    the operation SUCCEEDS, and we log a warning

not "raise a 500 and let the caller believe nothing happened". A failed cleanup that
reports failure is worse than a stray file on disk, because the user retries a delete
that already worked.

WHAT WE CATCH, AND WHY IT IS BROADER THAN OSError
-------------------------------------------------
`OSError` covers the ordinary cases (permissions, locked file, path too long). But it
is not the only thing that can come out of a filesystem call:

  * Some environments install their own delete interceptor that raises
    `SystemExit` rather than an `OSError`. `SystemExit` derives from
    `BaseException`, so `except Exception` does not catch it - it would tear
    through the request handler and, in a worker thread, take the process with it.
  * A misbehaving antivirus or indexer can raise nearly anything.

We therefore catch `(Exception, SystemExit)` explicitly. `SystemExit` is named
separately rather than catching `BaseException` wholesale so that
`KeyboardInterrupt` still propagates and Ctrl-C keeps working.
"""

from __future__ import annotations

from pathlib import Path

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

# Anything a filesystem delete can realistically throw at us. `SystemExit` is
# listed by name so that KeyboardInterrupt is deliberately NOT swallowed.
_CLEANUP_ERRORS = (Exception, SystemExit)


def remove_stored_file(stored_filename: str, *, context: str = "") -> bool:
    """Best-effort removal of one uploaded file.

    Returns True if the file is gone (or was never there), False if it could not be
    removed. Never raises - callers do not need to guard this.
    """
    if not stored_filename:
        return True

    path: Path = settings.upload_dir / stored_filename
    try:
        path.unlink(missing_ok=True)
        return True
    except _CLEANUP_ERRORS as exc:
        logger.warning(
            "Could not remove the stored file '%s'%s (%s: %s). The operation itself "
            "succeeded; only the file remains on disk.",
            stored_filename,
            f" for {context}" if context else "",
            exc.__class__.__name__,
            exc,
        )
        return False


def remove_stored_files(stored_filenames: list[str], *, context: str = "") -> int:
    """Best-effort removal of several files. Returns how many were removed."""
    removed = 0
    for filename in stored_filenames:
        if remove_stored_file(filename, context=context):
            removed += 1
    return removed
