"""Did a change make the bot better? Answers: BETTER, WORSE or UNDECIDED.

Method (after over-yonder.tech's "Better, worse or undecided"):

  Paired games. Every game the candidate plays is played a second time with the BASELINE sitting in
  the candidate's seat: same opponent, same map, same side, same seed. If the change never came into
  play, both games are identical and the pair cancels, so the change is judged only on games it could
  actually affect. (This is exact when games are deterministic: always under --sandbox, where the
  clock advances with points spent; natively, bots that budget with a real clock add some noise, and
  the report says how many pairs came out identical so you can see it.)

  Behaviour weighting (optional). Bots write an INDICATOR each turn naming the behaviour that chose
  the move. With --behaviour NAME, each changed pair counts in proportion to the share of the
  candidate's turns whose indicator contains NAME.

  Sign-flip test. If the change did nothing, each pair's change is as likely to have gone the other
  way. p_better = P(random signs on the same changes sum to at least the observed total); p_worse
  likewise at most. With every weight 1 this equals counting wins against losses. Cut-off 5%.

  Weak bots. Candidate and baseline also play the starter bots, paired the same way. A decent bot
  should never lose to them, so a candidate that loses more of those games than the baseline is never
  called better, one that loses significantly more is called worse, and every weak-bot game the
  candidate lost but the baseline won is listed: each is a bug to look at.

  Snapshots. With --snapshots K the last K saved versions (see snapshot.py) join the opponent pool,
  so a change can't pass by going round a rock-paper-scissors circle.

  Game length. In pairs both versions won: median rounds to win for each, how often the candidate won
  faster, and unusually short wins flagged as possible flukes.

Usage:
  python3 tools/verdict.py CANDIDATE BASELINE [--behaviour NAME] [--seeds 2] [--maps ...]
                           [--weak starter-py starter-c] [--snapshots 3] [--workers N] [--sandbox]
"""
from __future__ import annotations

import argparse
import itertools
import os
import random
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness import ARENA, DEFAULT_MAPS, Game, run_many, seed_for  # noqa: E402

ALPHA = 0.05


def sign_flip_p_values(changes: list[float], trials: int = 200_000) -> tuple[float, float]:
    """(p_at_least, p_at_most): probability that random signs on each change sum to at least /
    at most the observed total. Exact enumeration for up to 20 non-zero changes, Monte Carlo above."""
    changes = [c for c in changes if abs(c) > 1e-12]
    if not changes:
        return 1.0, 1.0
    observed = sum(changes)
    mags = [abs(c) for c in changes]
    eps = 1e-9
    if len(mags) <= 20:
        ge = le = 0
        total_cases = 1 << len(mags)
        for mask in range(total_cases):
            s = 0.0
            for i, m in enumerate(mags):
                s += m if mask >> i & 1 else -m
            ge += s >= observed - eps
            le += s <= observed + eps
        return ge / total_cases, le / total_cases
    rng = random.Random(0)
    ge = le = 0
    for _ in range(trials):
        s = sum(m if rng.random() < 0.5 else -m for m in mags)
        ge += s >= observed - eps
        le += s <= observed + eps
    return ge / trials, le / trials


def score(result: dict, seat: str) -> float | None:
    if result["error"]:
        return None
    if result["winner"] == "draw":
        return 0.5
    return 1.0 if result["winner"] == seat else 0.0


def behaviour_share(result: dict, seat: str, behaviour: str) -> float:
    turns = result["turns"].get(seat, 0)
    if not turns:
        return 0.0
    hits = sum(n for text, n in result["indicators"].get(seat, {}).items() if behaviour in text)
    return hits / turns


def latest_snapshots(k: int, exclude: set[str]) -> list[str]:
    snap_dir = ARENA / "snapshots"
    if k <= 0 or not snap_dir.exists():
        return []
    snaps = sorted(p for p in snap_dir.iterdir() if p.is_dir())
    names = [f"snapshots/{p.name}" for p in snaps]
    names = [n for n in names if Path(n).name.split("-", 1)[-1] not in exclude and n not in exclude]
    return names[-k:]


def build_pairs(cand: str, base: str, opponents: list[str], maps: list[str], seeds: int, base_seed: str):
    """For each opponent/map/side/seed: (candidate game, baseline game) with the baseline in the
    candidate's seat. The seed depends on the opponent, map and repeat only, so both games of a pair
    (and both sides) share it."""
    pairs = []
    for opp in opponents:
        for m in maps:
            for rep in range(seeds):
                seed = seed_for(base_seed, "SEAT", opp, m, rep)
                for seat in ("A", "B"):
                    if seat == "A":
                        gc, gb = Game(cand, opp, m, seed), Game(base, opp, m, seed)
                    else:
                        gc, gb = Game(opp, cand, m, seed), Game(opp, base, m, seed)
                    pairs.append((opp, m, seat, seed, gc, gb))
    return pairs


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("candidate")
    ap.add_argument("baseline")
    ap.add_argument("--behaviour", help="weight each changed pair by how often this indicator ran")
    ap.add_argument("--maps", nargs="+", default=DEFAULT_MAPS)
    ap.add_argument("--seeds", type=int, default=2)
    ap.add_argument("--weak", nargs="*", default=["starter-py", "starter-c"])
    ap.add_argument("--snapshots", type=int, default=0)
    ap.add_argument("--workers", type=int, default=os.cpu_count() or 1)
    ap.add_argument("--sandbox", action="store_true")
    ap.add_argument("--timeout", type=float, default=900)
    ap.add_argument("--base-seed", default="verdict")
    ap.add_argument("--out", default=None, help="results JSONL (default results/verdict_<cand>_<base>.jsonl)")
    args = ap.parse_args()

    cand, base = args.candidate, args.baseline
    strong = [base] + latest_snapshots(args.snapshots, {cand, base})
    weak = list(args.weak or [])
    pairs = build_pairs(cand, base, strong + weak, args.maps, args.seeds, args.base_seed)

    # a bot playing itself (baseline vs baseline) is fine; unswbc gives each side its own processes
    games, seen = [], set()
    for *_, gc, gb in pairs:
        for g in (gc, gb):
            if g.key() not in seen:
                seen.add(g.key())
                games.append(g)
    out = ARENA / (args.out or f"results/verdict_{Path(cand).name}_{Path(base).name}.jsonl")
    results = {f"{r['a']}|{r['b']}|{r['map']}|{r['seed']}": r
               for r in run_many(games, out, args.workers, args.sandbox, args.timeout)}

    def report_group(label, group):
        changes, weighted, identical, errors = [], [], 0, []
        gained, dropped = [], []
        for opp, m, seat, seed, gc, gb in group:
            rc, rb = results[gc.key()], results[gb.key()]
            sc, sb = score(rc, seat), score(rb, seat)
            if sc is None or sb is None:
                errors.append((opp, m, seat, seed, rc["error"] or rb["error"],
                               "candidate" if sc is None else "baseline"))
                continue
            if (rc["winner"], rc["rounds"], rc["turns"]) == (rb["winner"], rb["rounds"], rb["turns"]):
                identical += 1
            d = sc - sb
            w = behaviour_share(rc, seat, args.behaviour) if args.behaviour else 1.0
            changes.append(d)
            weighted.append(d * w)
            if d > 0:
                gained.append((opp, m, seat, seed, rc["log"]))
            elif d < 0:
                dropped.append((opp, m, seat, seed, rc["log"]))
        n = len(changes)
        same = sum(1 for d in changes if d == 0)
        p_hi, p_lo = sign_flip_p_values(weighted)
        print(f"\n== {label}: {n} valid pairs ({identical} identical, {same} same result), "
              f"candidate gained {len(gained)}, dropped {len(dropped)}"
              + (f", weighted by '{args.behaviour}'" if args.behaviour else ""))
        print(f"   random signs do at least this well {p_hi:.1%} of the time, at most this well {p_lo:.1%}")
        if errors:
            print(f"   {len(errors)} pair(s) excluded for errors (bugs, not losses):")
            for e in errors[:8]:
                print(f"     {e[5]} error: opp={e[0]} map={e[1]} seat={e[2]} seed={e[3]}: {e[4]}")
        return dict(n=n, gained=gained, dropped=dropped, p_hi=p_hi, p_lo=p_lo, errors=errors)

    strong_pairs = [p for p in pairs if p[0] in strong]
    weak_pairs = [p for p in pairs if p[0] in weak]
    print(f"candidate {cand} vs baseline {base}; opponents: {', '.join(strong + weak)}; "
          f"{len(args.maps)} maps x {args.seeds} seeds x 2 sides; {'sandbox' if args.sandbox else 'native'}")
    hs = report_group("head-to-head pool (" + ", ".join(strong) + ")", strong_pairs)
    wk = report_group("weak bots (" + ", ".join(weak) + ")", weak_pairs) if weak else None

    if wk:
        cand_weak_losses = sum(1 for opp, m, seat, seed, gc, gb in weak_pairs if score(results[gc.key()], seat) == 0.0)
        base_weak_losses = sum(1 for opp, m, seat, seed, gc, gb in weak_pairs if score(results[gb.key()], seat) == 0.0)
        print(f"   weak-bot losses: candidate {cand_weak_losses}, baseline {base_weak_losses}")
        for opp, m, seat, seed, log in wk["dropped"][:10]:
            print(f"     NEW LOSS to {opp} on {m} (seat {seat}, seed {seed}) -> {log}")
    # game length: pairs where both won
    cw, bw, faster, both = [], [], 0, 0
    for opp, m, seat, seed, gc, gb in pairs:
        rc, rb = results[gc.key()], results[gb.key()]
        if score(rc, seat) == 1.0:
            cw.append(rc["rounds"])
        if score(rb, seat) == 1.0:
            bw.append(rb["rounds"])
        if score(rc, seat) == 1.0 and score(rb, seat) == 1.0:
            both += 1
            faster += rc["rounds"] < rb["rounds"]
    if cw and bw:
        mc = statistics.median(cw)
        print(f"\n== game length: median rounds to win: candidate {mc:.0f}, baseline {statistics.median(bw):.0f}; "
              f"in {both} pairs both won, candidate faster in {faster}")
        short = [(p[1], p[2], p[3], results[p[4].key()]['rounds']) for p in pairs
                 if score(results[p[4].key()], p[2]) == 1.0 and results[p[4].key()]["rounds"] < 0.35 * mc]
        for m, seat, seed, r in short[:5]:
            print(f"   unusually short candidate win: {m} seat {seat} seed {seed}: {r} rounds (check for a fluke)")

    verdict = "UNDECIDED"
    weak_worse = wk is not None and wk["p_lo"] < ALPHA and len(wk["dropped"]) > len(wk["gained"])
    weak_blocks = wk is not None and len(wk["dropped"]) > len(wk["gained"])
    if weak_worse:
        verdict = "WORSE (loses significantly more to weak bots)"
    elif hs["p_hi"] < ALPHA and not weak_blocks:
        verdict = "BETTER"
    elif hs["p_hi"] < ALPHA and weak_blocks:
        verdict = "UNDECIDED (better head-to-head, but loses more games to weak bots)"
    elif hs["p_lo"] < ALPHA:
        verdict = "WORSE"
    print(f"\nVERDICT: {verdict}")


if __name__ == "__main__":
    main()
