"""Save a numbered snapshot of a bot folder into snapshots/, keeping the last N (default 5).

  python3 tools/snapshot.py cppimp            -> snapshots/003-cppimp
  python3 tools/verdict.py NEW cppimp --snapshots 3   (new candidates must beat recent versions too)
"""
import shutil, sys
from pathlib import Path
ARENA = Path(__file__).resolve().parent.parent

def main():
    bot = sys.argv[1]; keep = int(sys.argv[2]) if len(sys.argv) > 2 else 5
    snap = ARENA / "snapshots"; snap.mkdir(exist_ok=True)
    existing = sorted(p for p in snap.iterdir() if p.is_dir())
    n = int(existing[-1].name.split("-")[0]) + 1 if existing else 1
    dest = snap / f"{n:03d}-{Path(bot).name}"
    shutil.copytree(ARENA / bot, dest, ignore=shutil.ignore_patterns("__pycache__", "*.replay", "build"))
    print(f"saved {dest.relative_to(ARENA)}")
    existing = sorted(p for p in snap.iterdir() if p.is_dir())
    for old in existing[:-keep]:
        shutil.rmtree(old); print(f"pruned {old.name}")

if __name__ == "__main__":
    main()
