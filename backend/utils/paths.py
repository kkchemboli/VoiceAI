"""Safe filesystem helpers for user-supplied filenames (path traversal guards)."""

import os


class InvalidFilenameError(ValueError):
    """Raised when a user-supplied filename would escape the allowed directory."""


ALLOWED_KNOWLEDGE_EXTENSIONS = (".pdf", ".txt")


def safe_knowledge_path(base_dir: str, filename: str) -> str:
    """Return the absolute path of `filename` confined to `base_dir`.

    Rejects path separators, `..`, and absolute paths, then verifies the
    resolved path still lives inside `base_dir` as a belt-and-braces check.
    """
    if not filename:
        raise InvalidFilenameError("Empty filename")

    cleaned = os.path.basename(filename)
    if cleaned != filename or ".." in cleaned or os.path.isabs(filename):
        raise InvalidFilenameError(f"Invalid filename: {filename}")

    base_dir = os.path.abspath(base_dir)
    full_path = os.path.abspath(os.path.join(base_dir, cleaned))

    if os.path.commonpath([base_dir, full_path]) != base_dir:
        raise InvalidFilenameError(f"Filename escapes allowed directory: {filename}")

    return full_path