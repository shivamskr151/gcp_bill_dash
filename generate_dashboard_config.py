#!/usr/bin/env python3
"""
Generate browser-consumable config for gcp_billing_dashboard.html from .env

Output: dashboard_config.js (gitignored)
"""

from __future__ import annotations

import json
from pathlib import Path

from config import DASHBOARD_PROMETHEUS_URL, PROJECT_DISPLAY_NAME, PROJECT_ID


def main() -> None:
    repo_root = Path(__file__).resolve().parent
    out_path = repo_root / "dashboard_config.js"

    cfg = {
        "prometheusUrl": DASHBOARD_PROMETHEUS_URL,
        "projects": [
            {
                "id": PROJECT_ID,
                "name": PROJECT_DISPLAY_NAME,
            }
        ],
    }

    out_path.write_text(
        "// Auto-generated. Do not edit by hand.\n"
        "window.DASHBOARD_CONFIG = "
        + json.dumps(cfg, indent=2)
        + ";\n",
        encoding="utf-8",
    )

    print(f"Wrote {out_path}")


if __name__ == "__main__":
    main()
