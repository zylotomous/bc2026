"""Random map generator for unswbc, in the .map format the game reads directly
(https://game.battlecode.au/docs/map-files).

Architecture (deliberately simple, three independent passes over the grid):

  1. Terrain: start fully open, then carve `kelp` (wall) edges as a maze of rooms and corridors,
     using randomised depth-first carving so the result is guaranteed connected before any pruning.
     A symmetry (x, y or xy) is chosen and every wall is mirrored, so the map is fair.
  2. Portals: a few pairs of edges are turned into matching portal pairs, away from dragon spawns,
     mirrored the same way so both teams have equal access to them.
  3. Pearls: every open tile gets a spawn range (minGap, maxGap); a handful of tiles are marked
     never-spawn to give some texture. Dragon spawns are placed on open tiles, symmetric across teams,
     each with clear room to move on turn one.

Every generated map is validated before being written: connectivity (every open tile reachable from
every other, ignoring kelp), no team's spawn is boxed in, and the file round-trips through the same
parser genmap.py uses to read hand-made maps (so a generated map is provably in the same format the
judge accepts). Invalid attempts are discarded and retried with a new random draw.

Usage:
  python3 tools/mapgen.py --count 20 --out maps/generated --seed 1
  python3 tools/mapgen.py --count 5 --sizes 24x24 40x24 --portals 0 2 --seed 7
"""
from __future__ import annotations

import argparse
import random
from pathlib import Path

ARENA = Path(__file__).resolve().parent.parent


def mirror(x, y, w, h, sym):
    pts = {(x, y)}
    if "x" in sym:
        pts |= {(px, h - 1 - py) for px, py in pts}
    if "y" in sym:
        pts |= {(w - 1 - px, py) for px, py in pts}
    return pts


def _mirror_edge_x(t, side, w, h):
    """Flip top-bottom (mirror y -> h-1-y). A tile's north edge sits at row y; after the flip the
    edge that was tile (x,y)'s north edge becomes the north edge of tile (x, h-1-y+1 mod h) [the row
    below the mirrored tile], i.e. edge row 2y -> edge row 2*(h-y). A west edge just moves to the
    mirrored row: edge row 2y+1 -> edge row 2*(h-1-y)+1."""
    x, y = t % w, t // w
    if side == 0:
        my = (h - y) % h
    else:
        my = h - 1 - y
    return (my * w + x, side)


def _mirror_edge_y(t, side, w, h):
    """Flip left-right (mirror x -> w-1-x). A west edge at column x becomes the west edge of the
    mirrored tile shifted one column right (column w-x); a north edge just moves to column w-1-x."""
    x, y = t % w, t // w
    if side == 1:
        mx = (w - x) % w
    else:
        mx = w - 1 - x
    return (y * w + mx, side)


def mirror_edge(t, side, w, h, sym):
    """All edges an edge maps to under the map's symmetry group (itself included)."""
    out = {(t, side)}
    if "x" in sym:
        out |= {_mirror_edge_x(tt, s, w, h) for tt, s in out}
    if "y" in sym:
        out |= {_mirror_edge_y(tt, s, w, h) for tt, s in out}
    return out


class Grid:
    """W x H toroidal grid of open/kelp edges, matching the engine's edge indexing exactly."""

    def __init__(self, w, h, sym):
        self.w, self.h, self.sym = w, h, sym
        self.T = w * h
        # kind[t][0]=north edge, kind[t][1]=west edge; 0 open, 1 kelp, 2 portal
        self.kind = [[0, 0] for _ in range(self.T)]
        self.portal = [[-1, -1] for _ in range(self.T)]

    def tile(self, x, y):
        return (y % self.h) * self.w + (x % self.w)

    def set_kelp(self, t, side, wall=True):
        for tt, s in mirror_edge(t, side, self.w, self.h, self.sym):
            self.kind[tt][s] = 1 if wall else 0

    def set_portal(self, t1, s1, t2, s2, pid):
        for tt, s in ((t1, s1), (t2, s2)):
            self.kind[tt][s] = 2
            self.portal[tt][s] = pid

    def neighbours_open(self, t):
        x, y = t % self.w, t // self.w
        out = []
        if self.kind[t][0] == 0:
            out.append(self.tile(x, y - 1))
        if self.kind[self.tile(x, y + 1)][0] == 0:
            out.append(self.tile(x, y + 1))
        if self.kind[t][1] == 0:
            out.append(self.tile(x - 1, y))
        if self.kind[self.tile(x + 1, y)][1] == 0:
            out.append(self.tile(x + 1, y))
        return out

    def connected(self):
        seen = {0}
        stack = [0]
        while stack:
            u = stack.pop()
            for v in self.neighbours_open(u):
                if v not in seen:
                    seen.add(v)
                    stack.append(v)
        return len(seen) == self.T


def carve(rng, w, h, sym, wall_density):
    """Start with every internal edge as kelp, then carve a random spanning structure (randomised DFS)
    so the result is connected by construction, then knock out a further fraction of the remaining
    walls at random to open up loops (a maze on its own is too corridor-heavy to be fun)."""
    g = Grid(w, h, sym)
    for t in range(g.T):
        g.kind[t][0] = 1
        g.kind[t][1] = 1

    def edge_between(a, b):
        ax, ay = a % w, a // w
        bx, by = b % w, b // w
        if (bx - ax) % w == 0:
            return (a, 0) if (ay - by) % h == 1 else (b, 0)
        return (a, 1) if (ax - bx) % w == 1 else (b, 1)

    start = g.tile(rng.randrange(w), rng.randrange(h))
    seen = {start}
    stack = [start]
    while stack:
        u = stack[-1]
        x, y = u % w, u // w
        nbs = [g.tile(x, y - 1), g.tile(x, y + 1), g.tile(x - 1, y), g.tile(x + 1, y)]
        rng.shuffle(nbs)
        nxt = next((v for v in nbs if v not in seen), None)
        if nxt is None:
            stack.pop()
            continue
        t, s = edge_between(u, nxt)
        g.set_kelp(t, s, wall=False)
        seen.add(nxt)
        stack.append(nxt)
    # open extra loops: knock out walls with probability (1 - wall_density), skipping the border of a
    # spanning tree edge we already opened
    for t in range(g.T):
        for s in (0, 1):
            if g.kind[t][s] == 1 and rng.random() > wall_density:
                g.set_kelp(t, s, wall=False)
    return g


def place_portals(rng, g, n_pairs, avoid):
    """Turn n_pairs of currently-open edges into mirrored portal pairs, well clear of spawns."""
    if n_pairs <= 0:
        return
    w, h = g.w, g.h
    candidates = [(t, s) for t in range(g.T) for s in (0, 1)
                  if g.kind[t][s] == 0 and t not in avoid]
    rng.shuffle(candidates)
    pid = 0
    used = set()
    for t, s in candidates:
        if pid >= n_pairs:
            break
        if (t, s) in used:
            continue
        mirrored = mirror_edge(t, s, w, h, g.sym) - {(t, s)}
        mirrored = [(tt, ss) for tt, ss in mirrored if (tt, ss) not in used and g.kind[tt][ss] == 0]
        if not mirrored:
            continue
        t2, s2 = mirrored[0]
        g.set_portal(t, s, t2, s2, pid)
        used |= {(t, s), (t2, s2)}
        pid += 1


def place_dragons(rng, g, n_per_team, seg_len=3):
    """Symmetric dragon spawns on open tiles with room to move, well apart from each other."""
    w, h, sym = g.w, g.h, g.sym
    open_tiles = [t for t in range(g.T) if sum(1 for _ in g.neighbours_open(t)) >= 2]
    rng.shuffle(open_tiles)

    def line_from(head):
        # PORT NOTE: the game itself wraps a toroidal map, but a DRAGON line in a map *file* must be
        # adjacent in raw (unwrapped) coordinates -- the engine rejects a spawn body that only
        # connects by wrapping round the edge. So this stays entirely inside [0,w)x[0,h) with no mod.
        x, y = head % w, head // w
        for dx, dy in ((1, 0), (0, 1), (-1, 0), (0, -1)):
            xs = [x + i * dx for i in range(seg_len)]
            ys = [y + i * dy for i in range(seg_len)]
            if not all(0 <= xx < w for xx in xs) or not all(0 <= yy < h for yy in ys):
                continue
            body = [g.tile(xx, yy) for xx, yy in zip(xs, ys)]
            if len(set(body)) != seg_len:
                continue
            if all(b in g.neighbours_open(a) for a, b in zip(body, body[1:])):
                return body
        return None

    dragons = []
    taken = set()
    for head in open_tiles:
        if len(dragons) >= 2 * n_per_team:
            break
        if head in taken:
            continue
        mirrors = mirror(head % w, head // w, w, h, sym)
        if len(mirrors) < 2:
            continue   # a fixed point of the symmetry: no fair opposite spawn
        body = line_from(head)
        if body is None or any(b in taken for b in body):
            continue
        mx, my = next(iter(mirrors - {(head % w, head // w)}))
        mhead = g.tile(mx, my)
        mbody = line_from(mhead)
        if mbody is None or any(b in taken for b in mbody) or set(mbody) & set(body):
            continue
        too_close = any(min(abs((b % w) - (a % w)), w - abs((b % w) - (a % w))) +
                         min(abs((b // w) - (a // w)), h - abs((b // w) - (a // w))) < 4
                         for a in body for b in mbody)
        if too_close and n_per_team == 1:
            continue
        dragons.append((0, body))
        dragons.append((1, mbody))
        taken |= set(body) | set(mbody)
    return dragons


def assign_pearls(rng, g, dragons, never_frac=0.15):
    """Every open, unoccupied tile gets a spawn range; a fraction never spawn, for texture. Spawn
    tiles are the same on both members of a mirrored pair, matching the game's own symmetric maps."""
    w, h, sym = g.w, g.h, g.sym
    occupied = {t for _, body in dragons for t in body}
    gap = [None] * g.T
    done = set()
    tiles = list(range(g.T))
    rng.shuffle(tiles)
    for t in tiles:
        if t in done:
            continue
        mirrors = {g.tile(x, y) for x, y in mirror(t % w, t // w, w, h, sym)}
        if t in occupied or any(m in occupied for m in mirrors):
            for m in mirrors:
                gap[m] = (0, 0)
            done |= mirrors
            continue
        if rng.random() < never_frac:
            g_ = (0, 0)
        else:
            lo = rng.choice([1, 1, 1, 5, 10])
            hi = lo + rng.choice([20, 50, 100, 250, 500])
            g_ = (lo, hi)
        for m in mirrors:
            gap[m] = g_
        done |= mirrors
    return gap


def render(name, w, h, sym, g, gap, dragons):
    lines = [f"MAP {w} {h}", f"SYMMETRY {sym}"]
    if name:
        lines.append(f"MAP_NAME {name}")
    lines.append(f"TILE_COUNT {g.T}")
    for t in range(g.T):
        lines.append(f"TILE {t % w} {t // w} {gap[t][0]} {gap[t][1]}")
    # PORT NOTE: edge indices are row*(w+1)+col, with row 2y = tile row y's north edges and row 2y+1
    # its west edges, columns 0..w-1 (column w and the final row 2h are the format's toroidal-wrap
    # padding and are never referenced by any real map file, confirmed against maps/default.map).
    R = w + 1
    edges = []
    for y in range(h):
        for x in range(w):
            t = g.tile(x, y)
            edges.append((2 * y * R + x, t, 0))
        for x in range(w):
            t = g.tile(x, y)
            edges.append(((2 * y + 1) * R + x, t, 1))
    edges.sort()
    lines.append(f"EDGE_COUNT {len(edges)}")
    for idx, t, s in edges:
        lines.append(f"EDGE {idx} {g.kind[t][s]} {g.portal[t][s]}")
    lines.append(f"DRAGON_COUNT {len(dragons)}")
    for team, body in dragons:
        lines.append(f"DRAGON {team} {len(body)} " + " ".join(f"{t % w} {t // w}" for t in body))
    return "\n".join(lines) + "\n"


def validate(text):
    """Round-trip the rendered text through the same parser genmap.py uses on hand-made maps (so a
    generated map is provably in the format the judge accepts), then re-check connectivity, dragon
    placement and portal pairing from that parsed result -- independent of the generator's own
    bookkeeping."""
    import sys
    sys.path.insert(0, str(ARENA))
    import genmap
    path = ARENA / "_mapgen_tmp.map"
    path.write_text(text)
    try:
        name, w, h, sym, tiles, E, drag = genmap.parse(str(path))
    finally:
        path.unlink(missing_ok=True)
    if len(drag) < 2:
        return False, "fewer than 2 dragons"
    seen_tiles = set()
    for d in drag:
        team, ln = d[0], d[1]
        coords = d[2:]
        pts = [(coords[2 * i], coords[2 * i + 1]) for i in range(ln)]
        if len(set(pts)) != ln:
            return False, "dragon overlaps itself"
        if seen_tiles & set(pts):
            return False, "dragons overlap"
        seen_tiles |= set(pts)
        for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
            # unwrapped adjacency only: the engine rejects a spawn body that only connects by
            # wrapping round the toroidal edge, even though movement itself wraps during play
            if abs(x1 - x2) + abs(y1 - y2) != 1:
                return False, "dragon segments not adjacent"
    R = w + 1

    def openN(t):
        x, y = t % w, t // w
        k, _ = E.get(2 * y * R + x, (0, -1))
        return k != 1

    def openW(t):
        x, y = t % w, t // w
        k, _ = E.get((2 * y + 1) * R + x, (0, -1))
        return k != 1

    seen = {0}
    stack = [0]
    while stack:
        u = stack.pop()
        x, y = u % w, u // w
        cands = []
        if openN(u):
            cands.append(((y - 1) % h) * w + x)
        if openN(((y + 1) % h) * w + x):
            cands.append(((y + 1) % h) * w + x)
        if openW(u):
            cands.append(y * w + (x - 1) % w)
        if openW(y * w + (x + 1) % w):
            cands.append(y * w + (x + 1) % w)
        for v in cands:
            if v not in seen:
                seen.add(v)
                stack.append(v)
    if len(seen) != w * h:
        return False, f"map not connected ({len(seen)}/{w*h} tiles reachable)"
    portal_ends = {}
    for t, side, kind, pid in [(t, s, k, p) for t in range(w * h) for s, (k, p) in
                                enumerate([(E.get(2*(t//w)*R+(t%w), (0,-1))), (E.get((2*(t//w)+1)*R+(t%w), (0,-1)))])
                                if k == 2]:
        portal_ends.setdefault(pid, []).append((t, side))
    for pid, ends in portal_ends.items():
        if len(ends) != 2:
            return False, f"portal {pid} does not have exactly 2 ends"
        if ends[0][1] != ends[1][1]:
            return False, f"portal {pid} mismatched orientation"
    return True, ""


def generate_one(rng, w, h, sym, n_portals, n_dragons, wall_density, name):
    g = carve(rng, w, h, sym, wall_density)
    if not g.connected():
        return None
    dragons = place_dragons(rng, g, n_dragons)
    if len(dragons) < 2 * n_dragons:
        return None
    place_portals(rng, g, n_portals, avoid={t for _, body in dragons for t in body})
    gap = assign_pearls(rng, g, dragons)
    text = render(name, w, h, sym, g, gap, dragons)
    ok, why = validate(text)
    if not ok:
        return None
    return text


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--count", type=int, default=10)
    ap.add_argument("--sizes", nargs="+", default=["16x16", "24x24", "32x32", "32x16", "48x24"],
                     help="WxH choices, one picked per map")
    ap.add_argument("--symmetries", nargs="+", default=["x", "y", "xy"])
    ap.add_argument("--dragons", nargs="+", type=int, default=[1, 2, 3, 4],
                     help="dragons per team, one picked per map")
    ap.add_argument("--portals", nargs="+", type=int, default=[0, 0, 1, 2])
    ap.add_argument("--wall-density", nargs="+", type=float, default=[0.15, 0.3, 0.45])
    ap.add_argument("--out", default="maps/generated")
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--prefix", default="gen")
    args = ap.parse_args()

    rng = random.Random(args.seed)
    out_dir = ARENA / args.out
    out_dir.mkdir(parents=True, exist_ok=True)
    made = 0
    attempts = 0
    while made < args.count and attempts < args.count * 40:
        attempts += 1
        w, h = (int(v) for v in rng.choice(args.sizes).split("x"))
        sym = rng.choice(args.symmetries)
        nd = rng.choice(args.dragons)
        npo = rng.choice(args.portals)
        wd = rng.choice(args.wall_density)
        name = f"{args.prefix}-{made:03d}"
        text = generate_one(rng, w, h, sym, npo, nd, wd, name)
        if text is None:
            continue
        path = out_dir / f"{name}.map"
        path.write_text(text)
        made += 1
        print(f"wrote {path.relative_to(ARENA)}  ({w}x{h} sym={sym} dragons/team={nd} portals={npo})")
    if made < args.count:
        print(f"stopped after {attempts} attempts, only {made}/{args.count} validated maps generated",
              file=__import__("sys").stderr)


if __name__ == "__main__":
    main()
