"""Run the simulator standalone, without FastAPI, so you can watch it produce
realistic-looking toggles before the API layer (Phase A3) exists.

Usage:
    python -m scripts.run_sim_only

Demo-speed example (crosses into "after hours" and a 2h all-on window fast):
    SIM_START_TIME=2026-07-03T20:45:00 SIM_TIME_SCALE=60 python -m scripts.run_sim_only

Ctrl+C to stop.
"""
import asyncio

from backend.config import config
from backend.sim.simulator import Simulator
from backend.state import store


async def main() -> None:
    await store.init_db()
    await store.seed_if_empty()

    simulator = Simulator()
    print(
        f"Simulator starting. tick_ms={simulator.tick_ms}  "
        f"SIM_START_TIME={config.sim_start_time or '(unset -> real time)'}  "
        f"SIM_TIME_SCALE={config.sim_time_scale}"
    )
    print("Press Ctrl+C to stop.\n")

    try:
        while True:
            summary = await simulator.tick()
            snapshot = await store.get_snapshot()
            device_line = "  ".join(
                f"{d.id}={'ON ' if d.state == 'on' else 'off'}" for d in snapshot.devices
            )
            print(f"[{summary['server_time']}] total={summary['total_watts']:>4}W  {device_line}")
            for description in summary["fired_prelude_events"]:
                print(f"          >>> PRELUDE: {description}")
            await asyncio.sleep(simulator.tick_ms / 1000)
    except (KeyboardInterrupt, asyncio.CancelledError):
        print("\nStopped.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nStopped.")
