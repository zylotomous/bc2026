"""Evaluation harness for unswbc bots.

Plays games with the official `unswbc run`, so every result is exactly what the organisers' tools
report; this file only automates the tedious parts:

  * Seeds are derived deterministically from the pairing, map and repeat number, so rerunning a
    schedule replays the same games, and both side orders of a pairing share a seed.
  * Games run in parallel, each in its own process group, so a hung game can be killed together
    with every dragon process it spawned.
  * Anything that isn't a clean win, loss or draw (timeout, unswbc crash, a dragon's execution error
    such as exceeding the CPU limit, or a log with no result) is recorded as an ERROR, never a loss.
  * Every game's full log is kept, and results are appended to a JSONL file as they finish, so a long
    run can be interrupted and resumed without replaying finished games.

Usage:
  python3 tools/harness.py round-robin BOT BOT [BOT ...] [--maps m1 m2 ...] [--seeds 2]
                           [--workers N] [--sandbox] [--out results/rr.jsonl]
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import dataclasses
import hashlib
import itertools
import json
import os
import re
import signal
import subprocess
import sys
import time
from collections import defaultdict
from pathlib import Path

ARENA = Path(__file__).resolve().parent.parent
DEFAULT_MAPS = ["arena", "default_small", "Colosseum", "trophy", "queen_of_spades", "default", "dilemma",
                "devil", "stronghold", "trauma", "autarky", "schooltime", "big_empty"]

RESULT_RE = re.compile(r"^team ([AB]) wins after (\d+) rounds \(([^)]*)\)")
DRAW_RE = re.compile(r"^draw after (\d+) rounds \(([^)]*)\)")
BOT_LINE_RE = re.compile(r"^round (\d+): bot (\d+) \(team ([AB])\) (.*)$")
INDICATOR_RE = re.compile(r"^INDICATOR (.*)$")


@dataclasses.dataclass
class Game:
    a: str            # bot folder playing team A
    b: str            # bot folder playing team B
    map: str          # map name (maps/<name>.map)
    seed: str
    tag: str = ""     # free-form label (e.g. which verdict pairing this game belongs to)

    def key(self) -> str:
        return f"{self.a}|{self.b}|{self.map}|{self.seed}"


def seed_for(base: str, bot1: str, bot2: str, map_name: str, repeat: int) -> str:
    """Same seed for both side orders of a pairing: the bot names are sorted before hashing."""
    pairing = "/".join(sorted((bot1, bot2)))
    digest = hashlib.sha256(f"{base}/{pairing}/{map_name}/{repeat}".encode()).digest()
    return str(int.from_bytes(digest[:4], "big"))


def run_game(game: Game, log_dir: Path, sandbox: bool = False, timeout: float = 900.0) -> dict:
    """Play one game and return a result record. Never raises for a bad game: problems are recorded
    in the 'error' field so they can't be mistaken for losses."""
    log_dir.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", game.key())
    log_path = log_dir / f"{safe}.log"
    cmd = ["unswbc", "run", "-v", "--no-replay", "--seed", game.seed,
           str(ARENA / "maps" / f"{game.map}.map"), game.a, game.b]
    if sandbox:
        cmd.insert(2, "--sandbox")
    timed_out = False
    t0 = time.time()
    with log_path.open("w") as log:
        # A new session puts unswbc and every dragon process it starts into one process group,
        # so a hung game can be killed as a whole instead of leaking dragon processes.
        proc = subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT, cwd=ARENA,
                                env={**os.environ, "NO_COLOR": "1"}, start_new_session=True)
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
        finally:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            proc.wait()
    return parse_log(game, log_path, timed_out, proc.returncode, time.time() - t0, timeout)


def parse_log(game: Game, log_path: Path, timed_out: bool, returncode: int, seconds: float,
              timeout: float) -> dict:
    winner, rounds, reason, draw = None, None, None, False
    exec_error = None
    turns = {"A": 0, "B": 0}
    indicators = {"A": defaultdict(int), "B": defaultdict(int)}
    deaths = {"A": defaultdict(int), "B": defaultdict(int)}
    current_team = None
    for line in log_path.read_text(errors="replace").splitlines():
        m = BOT_LINE_RE.match(line)
        if m:
            team, rest = m.group(3), m.group(4)
            current_team = None
            if rest.startswith("stdout:"):
                current_team = team
                turns[team] += 1
            elif rest.startswith("died: "):
                deaths[team][rest[6:]] += 1
            elif rest.startswith(("stderr:", "points ")):
                pass
            elif exec_error is None:
                exec_error = {"team": team, "round": int(m.group(1)), "error": rest}
            continue
        if current_team is not None:
            mi = INDICATOR_RE.match(line)
            if mi:
                indicators[current_team][mi.group(1).strip()] += 1
        m = RESULT_RE.match(line)
        if m:
            winner, rounds, reason = m.group(1), int(m.group(2)), m.group(3)
            continue
        m = DRAW_RE.match(line)
        if m:
            draw, rounds, reason = True, int(m.group(1)), m.group(2)
    error = None
    if timed_out:
        error = f"timed out after {timeout:.0f}s"
    elif returncode:
        error = f"unswbc exited with code {returncode}"
    elif exec_error:
        error = f"team {exec_error['team']} round {exec_error['round']}: {exec_error['error']}"
    elif winner is None and not draw:
        error = "no result in the log"
    return {**dataclasses.asdict(game), "winner": None if error else ("draw" if draw else winner),
            "rounds": rounds, "reason": reason, "error": error, "seconds": round(seconds, 1),
            "turns": turns, "indicators": {t: dict(v) for t, v in indicators.items()},
            "deaths": {t: dict(v) for t, v in deaths.items()}, "log": str(log_path)}


def run_many(games: list[Game], out: Path, workers: int = 1, sandbox: bool = False,
             timeout: float = 900.0, quiet: bool = False) -> list[dict]:
    """Run games (in parallel if workers > 1), appending each result to `out` as it finishes.
    Games already present in `out` (same bots, map, seed and sandbox mode) are not replayed."""
    out.parent.mkdir(parents=True, exist_ok=True)
    done = {}
    if out.exists():
        for line in out.read_text().splitlines():
            if line.strip():
                r = json.loads(line)
                if r.get("sandbox", False) == sandbox:
                    done[f"{r['a']}|{r['b']}|{r['map']}|{r['seed']}"] = r
    todo = [g for g in games if g.key() not in done]
    results = [done[g.key()] for g in games if g.key() in done]
    log_dir = out.parent / (out.stem + "_logs")
    if not quiet and games:
        print(f"{len(games)} games ({len(games) - len(todo)} cached), {workers} worker(s)", file=sys.stderr)
    with cf.ThreadPoolExecutor(max_workers=max(1, workers)) as pool, out.open("a") as fh:
        futures = {pool.submit(run_game, g, log_dir, sandbox, timeout): g for g in todo}
        for i, fut in enumerate(cf.as_completed(futures), 1):
            g = futures[fut]
            r = fut.result()
            r["tag"] = g.tag
            r["sandbox"] = sandbox
            fh.write(json.dumps(r) + "\n")
            fh.flush()
            results.append(r)
            if not quiet:
                res = r["error"] and f"ERROR {r['error']}" or (r["winner"] == "draw" and "draw" or f"{r['winner']} wins")
                print(f"  [{i}/{len(todo)}] {g.map:16s} {g.a} vs {g.b} seed {g.seed}: {res} ({r['rounds']} rounds)",
                      file=sys.stderr)
    return results


def schedule_round_robin(bots, maps, seeds, base_seed="rr"):
    games = []
    for b1, b2 in itertools.combinations(bots, 2):
        for a, b in ((b1, b2), (b2, b1)):
            for m in maps:
                for rep in range(seeds):
                    games.append(Game(a, b, m, seed_for(base_seed, a, b, m, rep)))
    return games


def table(results, bots):
    stats = {b: defaultdict(int) for b in bots}
    for r in results:
        for bot, side in ((r["a"], "A"), (r["b"], "B")):
            if bot not in stats:
                continue
            if r["error"]:
                stats[bot]["errors"] += 1
            elif r["winner"] == "draw":
                stats[bot]["draws"] += 1
            elif r["winner"] == side:
                stats[bot]["wins"] += 1
            else:
                stats[bot]["losses"] += 1
    print(f"{'bot':20s} {'wins':>5s} {'draws':>5s} {'losses':>6s} {'errors':>6s}")
    for b in sorted(bots, key=lambda b: -stats[b]["wins"]):
        s = stats[b]
        print(f"{b:20s} {s['wins']:5d} {s['draws']:5d} {s['losses']:6d} {s['errors']:6d}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    rr = sub.add_parser("round-robin")
    rr.add_argument("bots", nargs="+")
    rr.add_argument("--maps", nargs="+", default=DEFAULT_MAPS)
    rr.add_argument("--seeds", type=int, default=2)
    rr.add_argument("--workers", type=int, default=os.cpu_count() or 1)
    rr.add_argument("--sandbox", action="store_true", help="judge-priced and deterministic, but slower")
    rr.add_argument("--timeout", type=float, default=900)
    rr.add_argument("--base-seed", default="rr")
    rr.add_argument("--out", default="results/round_robin.jsonl")
    args = ap.parse_args()
    games = schedule_round_robin(args.bots, args.maps, args.seeds, args.base_seed)
    results = run_many(games, ARENA / args.out, args.workers, args.sandbox, args.timeout)
    table(results, args.bots)


if __name__ == "__main__":
    main()
