"""Create the Supabase/Postgres schema (run once)."""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from uk_jobops.config import load_config  # noqa: E402
from uk_jobops.db import Store  # noqa: E402

cfg = load_config()
store = Store(cfg.secrets.supabase_db_url)
store.init_schema()
store.close()
print("Schema ready.")
