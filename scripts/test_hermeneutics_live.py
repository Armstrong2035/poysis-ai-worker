"""Explicit live DeepSeek smoke test with synthetic evidence, no customer data."""

import asyncio
import json
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))

from dotenv import load_dotenv
from hermeneutics import HermeneuticsEngine, SQLiteRepository
from hermeneutics.deepseek import DeepSeekModel
from test_hermeneutics_engine import FixtureAdapter, SCOPE, request


async def main():
    load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=False)
    with tempfile.TemporaryDirectory() as directory:
        model = DeepSeekModel()
        engine = HermeneuticsEngine(SQLiteRepository(Path(directory) / "live.sqlite"),
                                    lambda scope: FixtureAdapter(), model, model_timeout=190)
        await engine.submit(SCOPE, request(), "synthetic-live-test")
        result = await engine.run_next()
        print(json.dumps({"status": result["status"], "error_code": result["error_code"],
                          "model": model.model_version,
                          "outcome": result["interpretation"]["synthesis"]["outcome"] if result["interpretation"] else None}))
        if result["status"] != "completed":
            raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
