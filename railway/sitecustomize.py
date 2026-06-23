"""Local launcher convenience for running the Railway app from ``railway/``.

The app modules use app-local imports such as ``import bigquery_service``.
Railway starts the service with ``railway/app`` on the import path, but a local
command like ``uvicorn app.main:app`` from ``railway/`` does not. Python imports
``sitecustomize`` automatically at startup when it is present on ``sys.path``,
so this keeps local launches aligned with the deployed process layout.
"""

from __future__ import annotations

import sys
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent / "app"
if APP_DIR.is_dir() and str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))
