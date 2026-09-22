import helper as unswbc
from helper import Direction
import random
from collections import deque

ct: unswbc.Controller
game: unswbc.Game

# Compass indices follow Direction.get_direction_list(): 0=N, 1=E, 2=S, 3=W.
DIRS = Direction.get_direction_list()
DIR_INDEX = {d: i for i, d in enumerate(DIRS)}
DX = (0, 1, 0, -1)
DY = (-1, 0, 1, 0)
OPP = (2, 3, 0, 1)

SPLIT_SIZE = 2          # default split-off size for non-queens
REQUIRE_OPPOSITE_HEADS = True   # only split when parent and child would face opposite ways;
                                # False splits the moment a split is legal
SEARCH_DEPTH = 24       # how far (in steps) we plan through remembered tiles
RIVAL_DEPTH = 16        # how far we look when guessing where other dragons are heading
MAX_RIVALS = 5
MAX_TARGETS = 12
PLAN_DEPTH = 5          # how many moves ahead we look for a way to line up a split
SPAWN_HORIZON = 40      # only walk towards a not-yet-spawned pearl if it appears this soon
STALE_AFTER = 80        # rounds until a remembered tile counts as fully "unexplored" again
DEBUG = False

# --- role/queen tuning ---
QUEEN_BASE_RATE = 0.10        # base per-turn promotion chance for a non-queen
QUEEN_COMBAT_BONUS = 0.05     # extra promotion chance added per survived threat
QUEEN_MIN_SPLIT_LENGTH = 12   # queens only split once they're at least this long
QUEEN_SPLIT_FRACTION = 3      # queens split off roughly 1/this of their length

# --- map-size-aware splitting tuning ---
SMALL_MAP_THRESHOLD = 25            # width and/or height at or below this counts as "small"
LARGE_MAP_LENGTH_PHASE_ROUND = 50   # large maps: stop discretionary splitting after this round

# --- killer role tuning ---
KILLER_BASE_RATE = 0.08       # per-turn promotion chance for a non-queen, non-killer dragon
KILLER_BFS_NODE_CAP = 800     # safety cap on the hunt pathfind so a pathological case can't run away

W = H = 0
edges: dict = {}        # (x, y) -> (north, east, south, west) edge types as ints (0 empty, 1 kelp, 2 portal)
last_seen: dict = {}    # (x, y) -> round we last had it in view
pearls: set = set()     # tiles we believe hold a pearl right now
spawn_at: dict = {}     # (x, y) -> round a pearl is expected to appear
hist: list = []         # our head's positions, oldest first; the body is its tail end
prev_target = None      # the tile we were heading for last turn; sticking to it stops dithering
STICKY_BONUS = 3        # turns' worth of head start the current target gets over a fresh one

# --- role state ---
is_queen = False
is_killer = False
is_small_map = False
survived_threats = 0


def step(pos, i):
    return ((pos[0] + DX[i]) % W, (pos[1] + DY[i]) % H)


def dir_between(a, b):
    """Index of the direction that takes a to the adjacent tile b, or None."""
    for i in range(4):
        if step(a, i) == b:
            return i
    return None


def wrap_dist(a, b):
    dx = abs(a[0] - b[0])
    dy = abs(a[1] - b[1])
    return min(dx, W - dx) + min(dy, H - dy)


# --------------------------------------------------------------------------- perception

def observe(now):
    """Fold this turn's 7x7 window into memory.
    Returns (occupied, mine, friends, enemies): dragon parts by tile, our own body parts by tile,
    and the friendly / enemy heads in view."""
    my_id = ct.get_id()
    my_team = ct.get_team()
    occupied, mine, friends, enemies = {}, {}, [], []
    for tile in ct.get_tiles():
        p = tile.get_position()
        key = (p.x, p.y)
        edges[key] = tuple(tile.get_edge(d).get_edge_type().value for d in DIRS)
        last_seen[key] = now

        if tile.has_pearl():
            pearls.add(key)
            spawn_at.pop(key, None)
        else:
            pearls.discard(key)
            countdown = tile.get_pearl_time()
            if countdown >= 0:
                spawn_at[key] = now + countdown
            else:
                spawn_at.pop(key, None)

        part = tile.get_dragon()
        if part is None:
            continue
        occupied[key] = part
        if part.get_id() == my_id:
            if not part.is_head():
                mine[key] = part
        elif part.is_head():
            (friends if part.get_team() == my_team else enemies).append(part)
    return occupied, mine, friends, enemies


def update_body(mp, length, mine):
    """Best guess at our body, head first, or None if we cannot tell yet.
    Body parts in view are authoritative; when part of the tail is out of view we fall back on
    the trail of head positions we have recorded."""
    global hist
    behind = {}
    for tile, part in mine.items():
        # a body part faces the neighbour that is closer to the head
        behind[step(tile, DIR_INDEX[part.get_dir()])] = tile
    chain = [mp]
    seen = {mp}
    cur = mp
    while cur in behind and behind[cur] not in seen:
        cur = behind[cur]
        chain.append(cur)
        seen.add(cur)

    if len(chain) == length:
        hist = chain[::-1]
    elif not hist or hist[-1] != mp:
        hist.append(mp)
    if len(hist) > 256:
        del hist[:-128]
    if len(hist) >= length:
        return hist[::-1][:length]
    return None


def threatened(pos, enemies):
    """Could an enemy head step onto this tile next turn (which would kill us both)?"""
    for e in enemies:
        ep = e.get_position()
        ekey = (ep.x, ep.y)
        eedges = edges.get(ekey)
        if eedges is None:
            continue
        back = OPP[DIR_INDEX[e.get_dir()]]
        for i in range(4):
            if i != back and eedges[i] == 0 and step(ekey, i) == pos:
                return True
    return False


# --------------------------------------------------------------------------- search

def my_search(mp, allowed, blocked):
    """Breadth-first search from our head. For every reachable tile returns the distance and a
    bitmask of the first moves that start a shortest path to it."""
    dist, mask = {}, {}
    q = deque()
    for i in allowed:
        nb = step(mp, i)
        dist[nb] = 1
        mask[nb] = 1 << i
        q.append(nb)
    while q:
        cur = q.popleft()
        d = dist[cur]
        if d >= SEARCH_DEPTH:
            continue
        e = edges[cur]
        m = mask[cur]
        for i in range(4):
            if e[i]:
                continue
            nb = step(cur, i)
            if nb == mp or nb in blocked or nb not in edges:
                continue
            nd = dist.get(nb)
            if nd is None:
                dist[nb] = d + 1
                mask[nb] = m
                q.append(nb)
            elif nd == d + 1:
                mask[nb] |= m
    return dist, mask


def plain_search(start, depth):
    dist = {start: 0}
    q = deque([start])
    while q:
        cur = q.popleft()
        d = dist[cur]
        if d >= depth:
            continue
        e = edges.get(cur)
        if e is None:
            continue
        for i in range(4):
            if e[i]:
                continue
            nb = step(cur, i)
            if nb not in dist and nb in edges:
                dist[nb] = d + 1
                q.append(nb)
    return dist


def escape_depth(nb, body, others_block, need, budget=500):
    """How many moves in a row we could keep making, without hitting anything, after stepping onto
    nb (capped at `need`). If we can make `need` = our own length moves, the whole body has moved
    on and we are certainly free.

    This is one self-avoiding path, not a flood fill: a head can only walk one route, so a coil
    of our own body can look roomy to a flood fill and still be a trap. Body cells count as free
    once the tail has had time to leave them. Gives up after `budget` steps and reports the best path it found."""
    length = len(body)
    free_after = {body[k]: length - k for k in range(length)}   # moves until body[k] is vacated
    on_path = {nb}
    best = 1
    used = 0

    def open_exits(cell, t):
        e = edges.get(cell)
        if e is None:
            return 4
        n = 0
        for i in range(4):
            if not e[i]:
                c2 = step(cell, i)
                if c2 not in on_path and c2 not in others_block and free_after.get(c2, 0) < t + 2:
                    n += 1
        return n

    def walk(cur, t):
        nonlocal best, used
        if t > best:
            best = t
        if best >= need:
            return True
        used += 1
        if used > budget:
            return False                    # out of patience: report what we found so far
        e = edges.get(cur)
        if e is None:
            best = need                     # beyond what we have mapped: assume open water
            return True
        nexts = []
        for i in range(4):
            if e[i]:
                continue
            n2 = step(cur, i)
            if n2 in on_path or n2 in others_block or free_after.get(n2, 0) >= t + 1:
                continue
            nexts.append((-open_exits(n2, t + 1), n2))
        nexts.sort()
        for _, n2 in nexts:
            on_path.add(n2)
            if walk(n2, t + 1):
                return True
            on_path.discard(n2)
            if used > budget:
                return False
        return False

    walk(nb, 1)
    return best


# --------------------------------------------------------------------------- targets

def assign_target(now, my_dist, rivals):
    """Pick the pearl (or soon-to-spawn pearl) we should go for, without stealing one that a
    friendly dragon is better placed to take.

    Every visible dragon head, friend or foe, bids for the candidates with the number of turns it
    needs; bids are settled cheapest first, lowest dragon id winning ties (lower ids move first).
    Returns (tile, rival_distance_to_it) or (None, None) if nothing is ours."""
    cands = []
    for p in pearls:
        d = my_dist.get(p)
        if d:
            cands.append((d, p, 0, 0))
    for p, s in spawn_at.items():
        if p in pearls:
            continue
        d = my_dist.get(p)
        if not d:
            continue
        wait = s - now
        if wait > SPAWN_HORIZON:
            continue
        if wait <= 0:
            # overdue: a pearl has very likely appeared since we last looked
            cands.append((d + 4, p, 0, 4))
        else:
            cands.append((max(d, wait) + 3, p, wait, 3))
    if not cands:
        return None, None
    cands.sort(key=lambda c: c[0])
    kept = cands[:MAX_TARGETS]
    if prev_target is not None and all(c[1] != prev_target for c in kept):
        kept += [c for c in cands if c[1] == prev_target]
    cands = kept

    bids = [(c[0] - (STICKY_BONUS if c[1] == prev_target else 0), ct.get_id(), -1, ci)
            for ci, c in enumerate(cands)]
    rival_dist = [dict() for _ in rivals]
    for ri, (rid, rpos) in enumerate(rivals):
        rd = plain_search(rpos, RIVAL_DEPTH)
        for ci, (_, p, wait, pen) in enumerate(cands):
            dd = rd.get(p)
            if dd is not None:
                rival_dist[ri][ci] = dd
                bids.append((max(dd, wait) + pen, rid, ri, ci))

    bids.sort()
    taken_targets, taken_dragons = set(), set()
    for eff, did, ri, ci in bids:
        if ci in taken_targets or did in taken_dragons:
            continue
        taken_targets.add(ci)
        taken_dragons.add(did)
        if ri == -1:
            others = [rd[ci] for rd in rival_dist if ci in rd]
            return cands[ci][1], (min(others) if others else None)
    return None, None


def exploration_goal(mp, my_dist, my_mask, heading, friends, now):
    """With no pearl to chase, head for the part of the map we have seen least, away from friends."""
    best, best_cost = None, None
    friend_tiles = [(f.get_position().x, f.get_position().y) for f in friends]
    for t, d in my_dist.items():
        if t not in edges:
            continue
        gain = 0.0
        x, y = t
        for dx, dy in ((3, 0), (-3, 0), (0, 3), (0, -3)):
            probe = ((x + dx) % W, (y + dy) % H)
            seen_at = last_seen.get(probe)
            gain += 1.0 if seen_at is None else min(1.0, (now - seen_at) / STALE_AFTER)
        if gain < 0.3:
            continue
        cost = d - 3 * gain
        for f in friend_tiles:
            cost += max(0, 6 - wrap_dist(t, f))
        if my_mask[t] >> heading & 1:
            cost -= 1     # keep going the way we are facing when it is all the same
        if best_cost is None or cost < best_cost:
            best, best_cost = t, cost
    return best


# --------------------------------------------------------------------------- splitting

def split_ready(body, heading):
    """Would a size-N split of `body` leave the child's head facing opposite to ours?

    The child is the last N segments reversed, so its head is our tail tip and it faces away
    from our body, i.e. along the step from the second-last segment to the last one."""
    if body is None or len(body) < 4:
        return False
    if not REQUIRE_OPPOSITE_HEADS:
        return True
    child_dir = dir_between(body[-2], body[-1])
    return child_dir is not None and child_dir == OPP[heading]


def child_has_exit(body, occupied):
    """The child must have somewhere to go on its first turn: an open side other than its neck."""
    tip, neck = body[-1], body[-2]
    e = edges.get(tip)
    if e is None:
        return True
    for i in range(4):
        if not e[i]:
            n2 = step(tip, i)
            if n2 != neck and n2 not in occupied:
                return True
    return False


def steer_to_split(body, allowed, others_block):
    """The quickest way to get to a position where a size-2 split leaves opposite heads.

    Searches move sequences (at most PLAN_DEPTH long) over the body as it would be after each move,
    growing when a move eats a pearl. Returns (bitmask of first moves that get there soonest,
    number of moves needed), or (0, None) if it cannot be done within the horizon."""
    layer = {(tuple(body), None): 0}
    for depth in range(1, PLAN_DEPTH + 1):
        nxt = {}
        for (b, _), mask in layer.items():
            head = b[0]
            e = edges.get(head)
            if e is None:
                continue
            for i in range(4):
                if e[i] or (depth == 1 and i not in allowed):
                    continue
                nb = step(head, i)
                if nb in others_block or nb in b:
                    continue
                nbody = (nb,) + b if nb in pearls else (nb,) + b[:-1]
                key = (nbody, i)
                nxt[key] = nxt.get(key, 0) | ((1 << i) if depth == 1 else mask)
        layer = nxt
        found = 0
        for (b, i), mask in layer.items():
            if split_ready(b, i):
                found |= mask
        if found:
            return found, depth
    return 0, None


# --------------------------------------------------------------------------- killer role

def estimate_enemy_lengths(occupied, my_id):
    """Count visible segments per dragon id. This is a LOWER BOUND on true length -- a dragon
    extending outside our 7x7 vision window will be undercounted, never overcounted."""
    counts = {}
    for part in occupied.values():
        if part.get_id() == my_id:
            continue
        counts[part.get_id()] = counts.get(part.get_id(), 0) + 1
    return counts


def find_bigger_enemy_target(mp, my_length, my_team, enemies, enemy_lengths):
    """Among currently visible enemy heads whose estimated length exceeds ours, pick the
    nearest one to hunt for a deliberate head-on collision."""
    best, best_dist = None, None
    for e in enemies:
        if e.get_team() == my_team:
            continue
        seen_len = enemy_lengths.get(e.get_id(), 1)
        if seen_len <= my_length:
            continue
        ep = e.get_position()
        ekey = (ep.x, ep.y)
        d = wrap_dist(mp, ekey)
        if best_dist is None or d < best_dist:
            best, best_dist = ekey, d
    return best


def bfs_path_to(start, target, blocked, node_cap=KILLER_BFS_NODE_CAP):
    """Shortest known path from start to target, ignoring the usual safety filters -- kelp is
    still impassable, but any tile is fair game so long as it isn't itself blocked (occupied by
    something other than the target). Returns a list of direction indices, or None if the target
    is unreachable within what we have mapped."""
    if start == target:
        return []
    parent = {start: None}
    pdir = {}
    q = deque([start])
    visited = 0
    while q:
        cur = q.popleft()
        e = edges.get(cur)
        if e is None:
            continue
        for i in range(4):
            if e[i]:
                continue
            nb = step(cur, i)
            if nb in parent:
                continue
            if nb != target and (nb in blocked or nb not in edges):
                continue
            parent[nb] = cur
            pdir[nb] = i
            if nb == target:
                path = []
                node = target
                while parent[node] is not None:
                    path.append(pdir[node])
                    node = parent[node]
                path.reverse()
                return path
            q.append(nb)
            visited += 1
            if visited > node_cap:
                return None
    return None


# --------------------------------------------------------------------------- the turn

def least_bad_move(mp, heading, occupied):
    """Every neighbour is taken, so something is going to hurt. Cheapest first: ram an enemy head
    (it dies too), then any body segment (only we die), and a friend's head only as a last resort
    because that costs us two dragons."""
    my_team = ct.get_team()
    best, best_cost = heading, None
    for i in range(4):
        if edges[mp][i]:
            continue
        part = occupied.get(step(mp, i))
        if part is None:
            cost = 0
        elif part.is_head():
            cost = 0.5 if part.get_team() != my_team else 3
        else:
            cost = 1
        if best_cost is None or cost < best_cost:
            best, best_cost = i, cost
    return best


def execute_turn() -> None:
    global prev_target, is_queen, is_killer, survived_threats
    now = game.get_round_num()
    head = ct.get_position()
    mp = (head.x, head.y)
    heading = DIR_INDEX[ct.get_dir()]
    length = ct.get_length()

    occupied, mine, friends, enemies = observe(now)
    body = update_body(mp, length, mine)

    # ---- role promotion
    if enemies and threatened(mp, enemies):
        survived_threats += 1

    if not is_queen:
        promote_chance = min(QUEEN_BASE_RATE + QUEEN_COMBAT_BONUS * survived_threats, 0.9)
        if random.random() < promote_chance:
            is_queen = True
            is_killer = False
        elif not is_killer and random.random() < KILLER_BASE_RATE:
            is_killer = True

    # ---- killer role: hunt a confirmed-bigger enemy for a deliberate head-on trade.
    # All safety filtering is skipped here on purpose -- forcing the collision is the goal.
    # If no visible enemy is bigger than us (including "no killer" or "we're the bigger one"),
    # this falls straight through to the normal safe pipeline below.
    if is_killer and not is_queen and enemies:
        enemy_lengths = estimate_enemy_lengths(occupied, ct.get_id())
        bigger = find_bigger_enemy_target(mp, length, ct.get_team(), enemies, enemy_lengths)
        if bigger is not None:
            hunt_blocked = {t for t in occupied if t != bigger}
            path = bfs_path_to(mp, bigger, hunt_blocked)
            if path:
                # sprinting x steps costs x-1 segments; keep at least 2 segments alive
                # for every step but the last (the last one is meant to be fatal)
                affordable = max(1, length - 1)
                moves = path[:affordable]
                if len(moves) > 1:
                    ct.make_moves([DIRS[i] for i in moves])
                else:
                    ct.make_move(DIRS[moves[0]])
                if DEBUG:
                    ct.output_log("killer hunt", bigger, "steps", len(moves))
                return
            # unreachable via what we currently know of the map: fall through to
            # normal behaviour this turn and try again once we know more

    # ---- which moves are on the table
    legal = [i for i in range(4) if edges[mp][i] == 0 and step(mp, i) not in occupied]
    safe = [i for i in legal if not threatened(step(mp, i), enemies)]
    allowed = safe or legal
    if not allowed and body is not None and len(body) > 1:
        # boxed in: our own tail tip is about to move out of the way
        allowed = [i for i in range(4) if edges[mp][i] == 0 and step(mp, i) == body[-1]]
    if not allowed:
        if ct.can_split(SPLIT_SIZE):
            # boxed in: shedding the tail frees space and leaves a child that can still get out
            ct.do_split(SPLIT_SIZE)
            return
        ct.make_move(DIRS[least_bad_move(mp, heading, occupied)])
        return

    # ---- where to go
    blocked = set(occupied)
    my_dist, my_mask = my_search(mp, allowed, blocked)

    # only our own dragons bid against us for pearls; enemies are handled as a safety matter
    rivals = []
    for part in friends:
        pp = part.get_position()
        rivals.append((part.get_id(), (pp.x, pp.y)))
    rivals.sort(key=lambda r: wrap_dist(mp, r[1]))
    rivals = rivals[:MAX_RIVALS]

    target, rival_d = assign_target(now, my_dist, rivals)
    if target is None:
        target = exploration_goal(mp, my_dist, my_mask, heading, friends, now)
        rival_d = None
    prev_target = target
    bits = my_mask[target] if target is not None else None

    # A pearl that will make us long enough to split (non-queens use SPLIT_SIZE; queens
    # only care about this once they're near their own, larger split threshold).
    check_split_size = SPLIT_SIZE if not is_queen else max(SPLIT_SIZE, length // QUEEN_SPLIT_FRACTION)
    if (target in pearls and body is not None and length >= 2 * check_split_size - 1
            and ct.get_unit_count() < game.get_unit_limit()
            and (not is_queen or length + 1 >= QUEEN_MIN_SPLIT_LENGTH)):
        arrive = dir_between(body[-1], body[-2])
        dist_to_target = my_dist.get(target)
        if arrive is not None and dist_to_target and dist_to_target >= 2:
            before = step(target, OPP[arrive])
            if my_dist.get(before) == dist_to_target - 1 and edges[before][arrive] == 0:
                bits = my_mask[before]
    options = [i for i in allowed if bits is None or bits >> i & 1] or allowed

    # ---- split first if it is possible and the heads would part ways
    can_split_basic = body is not None

    # "In trouble" = every safe move option is exhausted, or we're actively threatened with
    # at most one safe way out. This is the survival override for the large-map length phase.
    in_trouble = (not safe) or (threatened(mp, enemies) and len(safe) <= 1)

    if is_queen:
        queen_split_size = max(SPLIT_SIZE, length // QUEEN_SPLIT_FRACTION)

        if is_small_map:
            # small map: maximise splitting permanently, bypass the opposite-heads
            # geometry check, keep only the cheap exit check
            can_split = can_split_basic and ct.can_split(SPLIT_SIZE)
            split_ok = can_split and child_has_exit(body, occupied)
            split_size = SPLIT_SIZE
        elif now <= LARGE_MAP_LENGTH_PHASE_ROUND:
            # large map, early phase: normal queen splitting behaviour
            can_split = (
                can_split_basic
                and length >= QUEEN_MIN_SPLIT_LENGTH
                and ct.can_split(queen_split_size)
            )
            split_ok = can_split and split_ready(body, heading) and child_has_exit(body, occupied)
            split_size = queen_split_size
        else:
            # large map, length phase: only split if forced to survive
            can_split = can_split_basic and ct.can_split(SPLIT_SIZE) and in_trouble
            split_ok = can_split and child_has_exit(body, occupied)
            split_size = SPLIT_SIZE

        if split_ok:
            fleeing = safe and threatened(mp, enemies) and not in_trouble
            if not fleeing:
                ct.do_split(split_size)
                return
    else:
        can_split = can_split_basic and ct.can_split(SPLIT_SIZE)
        if is_small_map:
            split_ok = can_split and child_has_exit(body, occupied)
        else:
            split_ok = can_split and (
                in_trouble or (split_ready(body, heading) and child_has_exit(body, occupied))
            )

        if split_ok:
            fleeing = safe and threatened(mp, enemies) and not in_trouble
            if not fleeing:
                ct.do_split(SPLIT_SIZE)
                return

    # ---- pick among the equally short moves, never into a pocket we could not get out of
    want_split = ct.get_unit_count() < game.get_unit_limit()
    my_id = ct.get_id()
    others_block = {t for t, p in occupied.items() if p.get_id() != my_id}
    trail = body if body is not None else [mp]
    if body is None:
        others_block |= set(mine) | {mp}   # unknown body: treat what we can see of it as solid
    need = min(length, 30)
    room = {i: escape_depth(step(mp, i), trail, others_block, need) for i in allowed}
    pool = [i for i in allowed if room[i] >= need]
    if not pool:
        pool = [i for i in allowed if room[i] == max(room.values())]

    def exits_from(nb):
        """(exits by terrain alone, exits once dragons are counted) for a tile we might step onto."""
        e = edges.get(nb)
        if e is None:
            return 4, 4
        static = dynamic = 0
        for j in range(4):
            if not e[j]:
                n2 = step(nb, j)
                if n2 == mp:
                    continue
                static += 1
                if n2 not in occupied:
                    dynamic += 1
        return static, dynamic

    def squeezed(i):
        """Open ground that dragons have narrowed to a single way out; whoever moves next can seal it."""
        static, dynamic = exits_from(step(mp, i))
        return static >= 2 and dynamic <= 1

    pool = [i for i in pool if not squeezed(i)] or pool

    # pearls decide between the safe moves ...
    options = [i for i in pool if bits is None or bits >> i & 1] or pool
    # ... unless a non-queen split is legal and only needs lining up, in which case that comes first
    if not is_queen and can_split_basic and ct.can_split(SPLIT_SIZE):
        steer, _ = steer_to_split(body, pool, others_block)
        options = [i for i in pool if steer >> i & 1] or options

    def rank(i):
        nb = step(mp, i)
        toward_target = bits is not None and bits >> i & 1 == 1
        aligned_next = False
        if want_split and body is not None and not is_queen:
            after = ([nb] + body) if nb in pearls else ([nb] + body[:-1])
            aligned_next = split_ready(after, i)
        return (min(exits_from(nb)[1], 2), toward_target, aligned_next, i == heading, random.random())

    move = max(options, key=rank)
    if DEBUG:
        role = "queen" if is_queen else ("killer" if is_killer else "scout")
        ct.output_log(role, "target", target, "move", DIRS[move].value)
    ct.make_move(DIRS[move])


def fallback_move() -> None:
    """Used only if execute_turn hits an unexpected error: step onto any open neighbouring tile."""
    here = ct.get_position()
    here_tile = ct.get_tile(here)
    for direction in Direction.get_direction_list():
        if not here_tile.get_edge(direction).is_passable():
            continue
        ahead = ct.get_tile(here.add_dir(direction))
        if ahead is not None and ahead.get_dragon() is None:
            ct.make_move(direction)
            return
    ct.make_move(ct.get_dir())


def main() -> None:
    global ct, game, W, H, is_queen, is_killer, is_small_map
    ct, game = unswbc.init()
    W, H = game.get_map_size()
    is_small_map = W <= SMALL_MAP_THRESHOLD or H <= SMALL_MAP_THRESHOLD
    random.seed(ct.get_id())
    is_queen = ct.get_id() == 0
    is_killer = False

    while unswbc.update(ct, game):
        try:
            execute_turn()
        except Exception:
            # a bug must not cost us the dragon; whatever was printed so far is superseded by this move
            fallback_move()
        unswbc.end_turn()

if __name__ == "__main__":
    main()