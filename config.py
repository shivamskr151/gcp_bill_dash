from __future__ import annotations

import os
from pathlib import Path
from typing import Optional


def _load_dotenv() -> None:
    """
    Load .env once, if python-dotenv is installed.
    We intentionally *don't* error if it's missing so the code
    still works with normal OS environment variables.
    """
    repo_root = Path(__file__).resolve().parent
    env_path = repo_root / ".env"
    if not env_path.exists():
        return

    # 1) Prefer python-dotenv if available
    try:
        from dotenv import load_dotenv  # type: ignore
    except Exception:
        load_dotenv = None  # type: ignore

    if load_dotenv is not None:
        load_dotenv(dotenv_path=env_path)
        return

    # 2) Minimal fallback parser (KEY=VALUE, supports quotes, ignores comments)
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            continue
        if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
            value = value[1:-1]
        # Do not override real environment variables
        os.environ.setdefault(key, value)


_load_dotenv()


def env_str(name: str, default: Optional[str] = None, *, required: bool = False) -> str:
    value = os.getenv(name, default)
    if required and (value is None or value.strip() == ""):
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value or ""


def env_int(name: str, default: int, *, required: bool = False) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        if required:
            raise RuntimeError(f"Missing required environment variable: {name}")
        return default
    try:
        return int(raw)
    except ValueError as e:
        raise RuntimeError(f"Invalid integer for {name}: {raw!r}") from e


# ---- Exporter config ----
PROJECT_ID = env_str("GCP_PROJECT_ID", required=True)
BIGQUERY_DATASET = env_str("BQ_BILLING_DATASET", required=True)
BILLING_ACCOUNT_ID = env_str("GCP_BILLING_ACCOUNT_ID", default="")

# Prefer GOOGLE_SERVICE_ACCOUNT_FILE, fallback to GOOGLE_APPLICATION_CREDENTIALS
SERVICE_ACCOUNT_FILE = env_str(
    "GOOGLE_SERVICE_ACCOUNT_FILE",
    default=os.getenv("GOOGLE_APPLICATION_CREDENTIALS", ""),
    required=True,
)

EXPORTER_HOST = env_str("EXPORTER_HOST", default="0.0.0.0")
EXPORTER_PORT = env_int("EXPORTER_PORT", default=9091)
METRICS_ENDPOINT = env_str("EXPORTER_METRICS_ENDPOINT", default="/metrics")


# ---- Dashboard / Prometheus helper config ----
DASHBOARD_PROMETHEUS_URL = env_str("DASHBOARD_PROMETHEUS_URL", default="")
PROJECT_DISPLAY_NAME = env_str("DASHBOARD_PROJECT_NAME", default=PROJECT_ID)
PROMETHEUS_SCRAPE_TARGET = env_str("PROMETHEUS_SCRAPE_TARGET", default="")
PROMETHEUS_SCRAPE_INTERVAL = env_str("PROMETHEUS_SCRAPE_INTERVAL", default="60s")
PROMETHEUS_GLOBAL_SCRAPE_INTERVAL = env_str("PROMETHEUS_GLOBAL_SCRAPE_INTERVAL", default="30s")
PROMETHEUS_GLOBAL_EVALUATION_INTERVAL = env_str("PROMETHEUS_GLOBAL_EVALUATION_INTERVAL", default="30s")
PROMETHEUS_METRICS_PATH = env_str("PROMETHEUS_METRICS_PATH", default="/metrics")
