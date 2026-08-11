from __future__ import annotations

import os


# Unit tests are intentionally lightweight and isolated on temporary SQLite
# databases. PostgreSQL coverage lives in test_postgres_integration.py and is
# enabled with an explicit RUN_POSTGRES_TESTS=1 environment.
os.environ.setdefault("STORAGE_BACKEND", "sqlite")
