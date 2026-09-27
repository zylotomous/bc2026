"""Offline Elo ladder: rate every bot that appears in any results/*.jsonl, from games already played.

Fits a Bradley-Terry model (draws count half a win to each side; errors are ignored) and reports it
on the Elo scale, anchored so the mean rating is 1500, with 90% intervals from bootstrap resampling.

  python3 tools/ladder.py [results/*.jsonl ...]
"""
import glob, json, math, random, sys
from collections import defaultdict
from pathlib import Path
ARENA = Path(__file__).resolve().parent.parent

def load(paths):
    games = []
    for p in paths:
        for line in open(p):
            if not line.strip(): continue
            r = json.loads(line)
            if r.get("error") or r.get("winner") is None: continue
            s = 0.5 if r["winner"] == "draw" else (1.0 if r["winner"] == "A" else 0.0)
            games.append((r["a"], r["b"], s))
    # the same game can appear in several result files (e.g. a verdict and a round robin)
    return games

def fit(games, iters=200):
    bots = sorted({g[0] for g in games} | {g[1] for g in games})
    strength = {b: 1.0 for b in bots}
    wins = defaultdict(float); pair_n = defaultdict(float)
    for a, b, s in games:
        wins[a] += s; wins[b] += 1 - s
        pair_n[(a, b)] += 1; pair_n[(b, a)] += 1
    for _ in range(iters):   # minorisation-maximisation update (Hunter 2004), with a tiny prior
        new = {}
        for i in bots:
            denom = sum(n / (strength[i] + strength[j]) for (x, j), n in pair_n.items() if x == i)
            new[i] = (wins[i] + 0.5) / (denom + 1.0 / (strength[i] + 1.0))
        g = math.exp(sum(math.log(v) for v in new.values()) / len(new))
        strength = {b: v / g for b, v in new.items()}
    return {b: 1500 + 400 * math.log10(v) for b, v in strength.items()}

def main():
    paths = sys.argv[1:] or glob.glob(str(ARENA / "results" / "*.jsonl"))
    games = load(paths)
    if not games:
        print("no finished games found"); return
    elo = fit(games)
    rng = random.Random(0); boots = defaultdict(list)
    for _ in range(100):
        sample = [games[rng.randrange(len(games))] for _ in games]
        for b, v in fit(sample, 80).items(): boots[b].append(v)
    n = defaultdict(int)
    for a, b, _ in games: n[a] += 1; n[b] += 1
    print(f"{'bot':28s} {'elo':>6s}   90% interval   games")
    for b in sorted(elo, key=lambda b: -elo[b]):
        bs = sorted(boots[b]); lo, hi = bs[int(0.05 * len(bs))], bs[int(0.95 * len(bs)) - 1]
        print(f"{b:28s} {elo[b]:6.0f}   [{lo:5.0f}, {hi:5.0f}]   {n[b]}")

if __name__ == "__main__":
    main()
