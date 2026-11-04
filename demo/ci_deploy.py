#!/usr/bin/env python3
"""CI/CD stand-in for the demo: merge the run's branch into main of one fake repo and run its tests.
Exit 0 = deployed. The Release order is enforced by the caller (scripts/wf_run.py), not here."""
import argparse
import os
import subprocess
import sys
from pathlib import Path

ap = argparse.ArgumentParser()
ap.add_argument("--workspace", required=True)
ap.add_argument("--ticket", required=True)
ap.add_argument("--repo", required=True)
a = ap.parse_args()
repo = Path(a.workspace) / "repos" / a.repo
branch = f"wf/{a.ticket}-run1"


def git(*args):
    return subprocess.run(["git", "-c", "user.name=demo-ci", "-c", "user.email=demo@example.com", *args],
                          cwd=repo, capture_output=True, text=True, check=True).stdout.strip()


git("checkout", "main")
git("merge", "--no-ff", branch, "-m", f"Merge {branch} ({a.ticket})")
res = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests"], cwd=repo,
                     env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}, capture_output=True, text=True)
if res.returncode != 0:
    print(res.stderr)
    sys.exit(1)
print(f"merged {branch} into main of {a.repo}; tests on main pass")
