"""Delete office.db and re-seed it from scratch. Destructive -- dev use only.

Usage:
    python -m scripts.reset_db
"""
import asyncio
from pathlib import Path

from backend.config import config
from backend.state import store


async def main() -> None:
    db_path = Path(config.db_path)
    if db_path.exists():
        db_path.unlink()
        print(f"Deleted {db_path}")

    await store.init_db()
    await store.seed_if_empty()
    print("Re-seeded office.db with 3 rooms and 15 devices.")


if __name__ == "__main__":
    asyncio.run(main())
