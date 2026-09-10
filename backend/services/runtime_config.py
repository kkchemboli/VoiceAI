"""Runtime-editable configuration persisted outside of `.env`.

The `.env` file holds secrets and must never be mutated from an API endpoint.
Values that administrators may change at runtime (e.g. the Google Sheet URL)
are stored in a small JSON file that excludes secrets.
"""

import json
import logging
import os
import tempfile

logger = logging.getLogger("voice-agent")

_RUNTIME_CONFIG_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data",
    "runtime_config.json",
)


def _load() -> dict:
    try:
        with open(_RUNTIME_CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except (json.JSONDecodeError, OSError) as e:
        logger.error(f"Failed to read runtime config: {e}")
        return {}


def _save(data: dict) -> None:
    os.makedirs(os.path.dirname(_RUNTIME_CONFIG_PATH), exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        dir=os.path.dirname(_RUNTIME_CONFIG_PATH), suffix=".json"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(tmp_path, _RUNTIME_CONFIG_PATH)
    except Exception:
        logger.error(f"Failed to persist runtime config: {e}", exc_info=True)
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


def get_sheet_url() -> str:
    """Return the Google Sheet URL: in-memory env first, then persisted runtime config."""
    url = os.getenv("GOOGLE_SHEET_URL")
    if url:
        return url
    return _load().get("google_sheet_url", "")


def set_sheet_url(url: str) -> None:
    """Persist the Google Sheet URL and update the in-memory environment."""
    os.environ["GOOGLE_SHEET_URL"] = url
    data = _load()
    data["google_sheet_url"] = url
    _save(data)
    logger.info("Google Sheet URL updated in runtime config")