"""Initialize office.db: create the schema (if needed) and seed 3 rooms + 15
devices (if the device table is empty). Safe to run repeatedly -- it will not
duplicate rows on a second run.

Usage:
    python -m scripts.seed_db
"""
import asyncio

from backend.state import store


async def main() -> None:
    await store.init_db()
    seeded = await store.seed_if_empty()
    if seeded:
        print("Seeded office.db with 3 rooms and 15 devices.")
    else:
        print("office.db already has devices -- left untouched.")


if __name__ == "__main__":
    asyncio.run(main())
