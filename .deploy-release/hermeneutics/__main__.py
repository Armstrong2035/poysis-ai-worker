"""Dedicated worker process. The host factory supplies authorized integrations."""

import argparse
import asyncio
import importlib


async def worker(engine, once=False, poll_seconds=1):
    while True:
        result = await engine.run_next()
        if once:
            return
        if result is None:
            await asyncio.sleep(poll_seconds)


def main():
    parser = argparse.ArgumentParser(description="Hermeneutics interpretation worker")
    parser.add_argument("--factory", required=True, help="Trusted host module:function returning a configured engine")
    parser.add_argument("--once", action="store_true", help="Process at most one job and exit")
    args = parser.parse_args()
    module, separator, name = args.factory.partition(":")
    if not separator or not name:
        parser.error("factory must be module:function")
    factory = getattr(importlib.import_module(module), name)
    asyncio.run(worker(factory(), once=args.once))


if __name__ == "__main__":
    main()
