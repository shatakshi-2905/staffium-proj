"""Runs the screening quality and fairness evaluation (eval/run_eval.py) as a regression test.

It needs its own empty database, so it creates one next to TEST_DATABASE_URL, migrates it, runs the
harness in a subprocess (the harness configures the app's settings itself) and drops it again.
Uses the offline hash embeddings, so it checks fairness invariants and pipeline behaviour, plus a
quality floor; real retrieval quality is measured by running the harness with fastembed.
"""
import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse, urlunparse

import asyncpg
import pytest

ROOT = Path(__file__).resolve().parents[2]
DSN = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL not set")


def _with_db(url: str, name: str) -> str:
    u = urlparse(url)
    return urlunparse(u._replace(path=f"/{name}"))


async def test_screening_evaluation_passes(tmp_path):
    name = f"ats_eval_{os.getpid()}"
    admin = await asyncpg.connect(_with_db(DSN, "postgres"))
    await admin.execute(f'DROP DATABASE IF EXISTS "{name}"')
    await admin.execute(f'CREATE DATABASE "{name}"')
    url = _with_db(DSN, name)
    try:
        env = {**os.environ, "DATABASE_URL": url}
        subprocess.run([sys.executable, str(ROOT / "database" / "migrate.py"), "--local"], env=env, check=True,
                       capture_output=True, timeout=120)
        run = subprocess.run([sys.executable, str(ROOT / "eval" / "run_eval.py"), "--database-url", url, "--embeddings", "hash",
                              "--strict", "--min-ndcg", "0.85", "--out", str(tmp_path), "--label", "ci"],
                             env=env, capture_output=True, text=True, timeout=600)
        assert run.returncode == 0, run.stdout[-4000:] + run.stderr[-4000:]
        report = next(tmp_path.glob("*.md")).read_text()
        assert "**Result: PASS**" in report
        for attr in ("name", "city", "career_break", "college"):
            row = next(line for line in report.splitlines() if line.startswith(f"| {attr} |"))
            assert "| 0.00 |" in row, f"{attr} twins moved the score: {row}"
    finally:
        await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        await admin.close()
