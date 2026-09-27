"""
============================================================================
DESIGN OVERVIEW
============================================================================

How the game is scored: eliminating every enemy dragon wins outright. 
Otherwise, at round 500, the team with the single LONGEST dragon wins; 
total length is the tiebreak after that. 

This build integrates advanced C++ bot concepts into our Python architecture:
  - DYNAMIC KING PROMOTION: Tracks the absolute longest dragon via Sonar,
    allowing the Champion role to dynamically shift if a breeder outgrows
    the current King.
  - ACTIVE FEEDER PATHING: During stalemate consolidation, breeders actively
    path to the Champion's coordinates before sacrificing themselves, ensuring
    pearls are delivered directly to the King.
  - RELAXED FRIENDLY-FIRE: Allies are no longer treated as static obstacles 
    in safety checks, preventing pessimistic self-trapping.
============================================================================
"""

import helper as unswbc
from helper import Direction
import os
import random
import time
from collections import deque

ct: unswbc.Controller
game: unswbc.Game

# ---------------------------------------------------------------------------
# Compass bookkeeping
# ---------------------------------------------------------------------------
DIRS = Direction.get_direction_list()
DIR_INDEX = {d: i for i, d in enumerate(DIRS)}
DX = (0, 1, 0, -1)
DY = (-1, 0, 1, 0)
OPP = (2, 3, 0, 1)

DEBUG = False
LOCAL_DEBUG = os.environ.get("BC_DEBUG") == "1"

# ============================================================================ 
# Tuning Constants
# ============================================================================

SPLIT_SIZE = 2
MIN_SPLIT_LEN = 2 * SPLIT_SIZE
SMALL_MAP_THRESHOLD = 25
SPLIT_RAMP_SMALL = 0.01
SPLIT_RAMP_LARGE = 0.02
SPLIT_CAP_SMALL = 6
SPLIT_CAP_LARGE = 12
SPLIT_STOP_ROUND = 450

PRESERVE_ROUND = 400
PRESERVE_MIN_POP_SMALL = 12
PRESERVE_MIN_POP_LARGE = 32

STARVE_BREEDERS_ROUND = 380

# --- Sonar Protocol Constants
SONAR_FEED_CHAMPION = 1
SONAR_ENEMY_ALERT = 2
MSG_KING_PREFIX = 3
STALEMATE_ROUNDS = 100

CYCLE_CHECK_WINDOW = 10
STALE_TRIGGER_TURNS = 25
FRONTIER_SEARCH_CAP = 250
CENTER_RUSH_ROUND = 120

CHAMPION_START_SMALL = 120
CHAMPION_START_LARGE = 100
DYNAMIC_PROMOTE_MARGIN = 2

OPPORTUNISTIC_MIN_ENEMY_LEN = 10
OPPORTUNISTIC_RATIO = 2

SOFT_BUDGET_S = 0.045
HARD_BUDGET_S = 0.065
SEARCH_DEPTH = 24
RIVAL_SEARCH_DEPTH = 14
ROOM_CHECK_MARGIN = 1.5

# ============================================================================ 
# Persistent State
# ============================================================================
W = H = 0
edges: dict = {}
portal_id_at: dict = {}
portal_ends: dict = {}
hist: list = []
desired_heading = 0
stale_turns = 0
is_champion = False
is_small_map = False
NBR: dict = {}
turn_t0 = 0.0

# Per-Dragon Sonar State
last_enemy_seen_round = 0
feed_protocol_active = False
last_alert_sent = -10
last_feed_sent = -10

# Global King Tracking
global_king_id = -1
global_king_len = -1
global_king_pos = None


def encode_king(d_id, d_len, x, y):
    """Encodes the Champion's ID, length, and coordinates into a 64-bit int."""
    return (MSG_KING_PREFIX << 60) | (int(d_id) << 32) | (int(d_len) << 16) | (int(x) << 8) | int(y)


def decode_king(msg):
    """Decodes a 64-bit int into Champion info if the prefix matches."""
    if (msg >> 60) == MSG_KING_PREFIX:
        return (msg >> 32) & 0xFFF, (msg >> 16) & 0xFFFF, (msg >> 8) & 0xFF, msg & 0xFF
    return None


def step(pos, i):
    return ((pos[0] + DX[i]) % W, (pos[1] + DY[i]) % H)


def nbrs(pos):
    r = NBR.get(pos)
    if r is None:
        x, y = pos
        r = NBR[pos] = ((x, (y - 1) % H), ((x + 1) % W, y), (x, (y + 1) % H), ((x - 1) % W, y))
    return r


def spent():
    return time.perf_counter() - turn_t0


def wrap_dist(a, b):
    dx = abs(a[0] - b[0])
    dy = abs(a[1] - b[1])
    return min(dx, W - dx) + min(dy, H - dy)


def perpendiculars(h):
    return (h + 1) % 4, (h + 3) % 4


def toward_center_heading(pos):
    cx, cy = W // 2, H // 2
    dx = (cx - pos[0] + W // 2) % W - W // 2
    dy = (cy - pos[1] + H // 2) % H - H // 2
    if abs(dx) >= abs(dy):
        return 1 if dx > 0 else 3
    return 2 if dy > 0 else 0


def unexplored_heading(mp):
    for i in range(4):
        if edges[mp][i] == 2:
            if portal_exit(mp, i) is None: return i
        elif step(mp, i) not in edges:
            return i
            
    dist, mask = {mp: 0}, {}
    q = deque([mp])
    visited = 0
    while q and visited < FRONTIER_SEARCH_CAP:
        cur = q.popleft()
        visited += 1
        d = dist[cur]
        e = edges.get(cur)
        if e is None:
            continue
        m = mask.get(cur, 0)
        for i in range(4):
            if e[i] == 1:
                continue
            if e[i] == 2:
                nb = portal_exit(cur, i)
                if nb is None:
                    return next(b for b in range(4) if (m if cur != mp else (1 << i)) >> b & 1)
            else:
                nb = step(cur, i)
                
            if nb in dist or nb not in edges:
                continue
            first_move = (1 << i) if cur == mp else m
            dist[nb] = d + 1
            mask[nb] = first_move
            
            nb_edges = edges.get(nb)
            if nb_edges is None:
                return next(b for b in range(4) if first_move >> b & 1)
            for j in range(4):
                if nb_edges[j] == 2:
                    if portal_exit(nb, j) is None:
                        return next(b for b in range(4) if first_move >> b & 1)
                elif step(nb, j) not in edges:
                    return next(b for b in range(4) if first_move >> b & 1)
            q.append(nb)
    return None


def edge_key(tile, side):
    x, y = tile
    if side == 0: return ('H', x, y)
    if side == 2: return ('H', x, (y + 1) % H)
    if side == 3: return ('V', x, y)
    return ('V', (x + 1) % W, y)


def portal_exit(tile, side):
    pid = portal_id_at.get((tile, side))
    if pid is None:
        return None
    here = edge_key(tile, side)
    for kind, x, y in portal_ends.get(pid, ()):
        if (kind, x, y) == here: continue
        if kind == 'H':
            if side == 2: return (x, y)
            if side == 0: return (x, (y - 1) % H)
        elif side == 1:
            return (x, y)
        elif side == 3:
            return ((x - 1) % W, y)
        return (x, y)
    return None


def observe(now):
    my_id = ct.get_id()
    my_team = ct.get_team()
    occupied, mine, others, enemies, pearls = {}, {}, [], [], set()
    discovered_new = False
    for tile in ct.get_tiles():
        p = tile.get_position()
        key = (p.x, p.y)

        if key not in edges:
            discovered_new = True
            sides = [tile.get_edge(d) for d in DIRS]
            edges[key] = tuple(s.get_edge_type().value for s in sides)
            for i, s in enumerate(sides):
                if s.is_portal():
                    portal_id_at[key, i] = s.get_portal_id()
                    portal_ends.setdefault(s.get_portal_id(), set()).add(edge_key(key, i))

        if tile.has_pearl():
            pearls.add(key)

        part = tile.get_dragon()
        if part is None:
            continue
        occupied[key] = part
        pid = part.get_id()
        if pid == my_id:
            if not part.is_head():
                mine[key] = part
        elif part.is_head():
            hd = DIR_INDEX[part.get_dir()]
            others.append((pid, key, hd))
            if part.get_team() != my_team:
                enemies.append((pid, key, hd))
    return occupied, mine, others, enemies, pearls, discovered_new


def update_body(mp, length, mine):
    global hist
    behind = {}
    for tile, part in mine.items():
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
        return hist[::-1][:length]
    if not hist or hist[-1] != mp:
        hist.append(mp)
    if len(hist) > 256:
        del hist[:-128]
    if len(hist) >= length:
        return hist[::-1][:length]

    hist_chain = hist[::-1]
    known = hist_chain if len(hist_chain) > len(chain) else chain
    if len(known) < 2:
        return None
    dx = known[-1][0] - known[-2][0]
    dy = known[-1][1] - known[-2][1]
    if dx > 1: dx -= W
    elif dx < -1: dx += W
    if dy > 1: dy -= H
    elif dy < -1: dy += H
    est = list(known)
    while len(est) < length:
        lx, ly = est[-1]
        est.append(((lx + dx) % W, (ly + dy) % H))
    return est[:length]


def visible_lengths(occupied, team, exclude_id=None):
    counts = {}
    for part in occupied.values():
        if part.get_team() == team and part.get_id() != exclude_id:
            counts[part.get_id()] = counts.get(part.get_id(), 0) + 1
    return counts


def is_tile_scary(pos, enemies, is_champ, my_length, enemy_lens, is_small):
    if pos is None: 
        return False
        
    for pid, ekey, edir in enemies:
        back = OPP[edir]
        e = edges.get(ekey)
        if e is None:
            continue
        for i in range(4):
            if i == back:
                continue
            dest = step(ekey, i) if e[i] == 0 else (portal_exit(ekey, i) if e[i] == 2 else None)
            if dest == pos:
                if is_champ:
                    return True
                e_len = enemy_lens.get(pid, 1)
                fear_ratio = 1.0 if is_small else 1.5
                if e_len < my_length * fear_ratio:
                    return True
    return False


def my_search(mp, allowed, blocked):
    dist, mask = {}, {}
    q = deque()
    for i in allowed:
        if edges[mp][i] == 2:
            nb = portal_exit(mp, i)
            if nb is None: continue 
        else:
            nb = step(mp, i)
        dist[nb] = 1
        mask[nb] = 1 << i
        q.append(nb)
    popped = 0
    while q:
        popped += 1
        if not popped & 127 and spent() > SOFT_BUDGET_S:
            break
        cur = q.popleft()
        d = dist[cur]
        if d >= SEARCH_DEPTH:
            continue
        e = edges.get(cur)
        if e is None:
            continue
        m = mask[cur]
        around = nbrs(cur)
        for i in range(4):
            if e[i] == 1: continue
            if e[i] == 2:
                nb = portal_exit(cur, i)
                if nb is None: continue
            else:
                nb = around[i]
                
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


def multi_source_search(starts, depth, targets):
    dist = {s: 0 for s in starts if s in edges}
    remaining = set(targets) - set(dist)
    q = deque(dist.keys())
    if not remaining:
        return dist
    popped = 0
    while q:
        popped += 1
        if not popped & 127 and spent() > SOFT_BUDGET_S:
            break
        cur = q.popleft()
        d = dist[cur]
        if d >= depth:
            continue
        e = edges.get(cur)
        if e is None:
            continue
        around = nbrs(cur)
        for i in range(4):
            if e[i] == 1: continue
            if e[i] == 2:
                nb = portal_exit(cur, i)
                if nb is None: continue
            else:
                nb = around[i]
                
            if nb not in dist and nb in edges:
                dist[nb] = d + 1
                remaining.discard(nb)
                if not remaining:
                    return dist
                q.append(nb)
    return dist


def escape_depth(nb, body, others_block, need, budget=500):
    length = len(body)
    free_after = {body[k]: length - k for k in range(length)}
    on_path = {nb}
    best = 1
    used = 0

    def open_exits(cell, t):
        e = edges.get(cell)
        if e is None: return 4
        n = 0
        for i in range(4):
            if e[i] == 0:
                c2 = step(cell, i)
                if c2 not in on_path and c2 not in others_block and free_after.get(c2, 0) < t + 2:
                    n += 1
            elif e[i] == 2:
                c2 = portal_exit(cell, i)
                if c2 is None or (c2 not in on_path and c2 not in others_block and free_after.get(c2, 0) < t + 2):
                    n += 1
        return n

    def walk(cur, t):
        nonlocal best, used
        if t > best: best = t
        if best >= need: return True
        used += 1
        if used > budget: return False
        if not used & 31 and spent() > HARD_BUDGET_S:
            used = budget + 1
            return False
        e = edges.get(cur)
        if e is None:
            best = need
            return True
        nexts = []
        for i in range(4):
            if e[i] == 1: continue
            if e[i] == 2:
                n2 = portal_exit(cur, i)
                if n2 is None:
                    best = need
                    return True
            else:
                n2 = step(cur, i)
                
            if n2 in on_path or n2 in others_block or free_after.get(n2, 0) >= t + 1:
                continue
            nexts.append((-open_exits(n2, t + 1), n2))
        nexts.sort()
        for _, n2 in nexts:
            on_path.add(n2)
            if walk(n2, t + 1): return True
            on_path.discard(n2)
            if used > budget: return False
        return False

    walk(nb, 1)
    return best


def flood_fill_volume(start, blocked, cap):
    seen = {start}
    q = deque([start])
    count = 1
    while q and count < cap:
        cur = q.popleft()
        e = edges.get(cur)
        if e is None:
            continue
        for i in range(4):
            if e[i] == 1: continue
            if e[i] == 2:
                nb = portal_exit(cur, i)
                if nb is None: return cap
            else:
                nb = step(cur, i)
                
            if nb in seen or nb in blocked or nb not in edges:
                continue
            seen.add(nb)
            count += 1
            if count >= cap: break
            q.append(nb)
    return count


def room_check(nb, body, others_block, need, budget=500):
    threshold = int(need * ROOM_CHECK_MARGIN) + 1
    static_block = others_block | set(body)
    if flood_fill_volume(nb, static_block, threshold) >= threshold:
        return need
    return escape_depth(nb, body, others_block, need, budget)


def nearby_pearl_count(p, pearls, radius=3):
    return sum(1 for q in pearls if q != p and wrap_dist(p, q) <= radius)


def best_winnable_pearl(mp, my_dist, pearls, others, body, others_block, length):
    if not pearls: return None
    candidates = sorted((my_dist[p], -nearby_pearl_count(p, pearls), p) for p in pearls if p in my_dist)
    if not candidates: return None

    if body is not None:
        need = min(length + 1, 30)
        feasible = []
        for d, negcount, p in candidates:
            if spent() > SOFT_BUDGET_S: break
            if room_check(p, body, others_block, need) >= need:
                feasible.append((d, negcount, p))
        candidates = feasible
        if not candidates: return None

    if not others:
        return candidates[0][2]

    goals = {p for _, _, p in candidates}
    rd = multi_source_search([opos for _, opos, _ in others], RIVAL_SEARCH_DEPTH, goals)
    for i, (d, negcount, p) in enumerate(candidates):
        rdist = rd.get(p)
        if rdist is not None and rdist < d:
            candidates[i] = None
    for entry in candidates:
        if entry is not None:
            return entry[2]
    return None


def child_has_exit(body, occupied):
    tip, neck = body[-1], body[-2]
    e = edges.get(tip)
    if e is None: return True
    for i in range(4):
        if e[i] == 0:
            n2 = step(tip, i)
            if n2 != neck and n2 not in occupied: return True
        elif e[i] == 2:
            n2 = portal_exit(tip, i)
            if n2 is None or (n2 != neck and n2 not in occupied): return True
    return False


def split_threshold(now):
    if now < 150: return MIN_SPLIT_LEN
    if is_small_map:
        return min(MIN_SPLIT_LEN + SPLIT_RAMP_SMALL * (now - 150), SPLIT_CAP_SMALL)
    return min(MIN_SPLIT_LEN + SPLIT_RAMP_LARGE * (now - 150), SPLIT_CAP_LARGE)


def least_bad_move(mp, occupied):
    my_team = ct.get_team()
    best, best_cost = None, None
    for i in range(4):
        if edges[mp][i] == 1: continue
            
        dest = step(mp, i) if edges[mp][i] == 0 else portal_exit(mp, i)
        if dest is None: return i
            
        part = occupied.get(dest)
        if part is None:
            cost = 0
        elif part.is_head() and part.get_team() == my_team:
            continue
        elif part.is_head():
            cost = 0.5
        else:
            cost = 1
            
        if best_cost is None or cost < best_cost:
            best, best_cost = i, cost
    return best


def execute_turn() -> None:
    global turn_t0, desired_heading, stale_turns, is_champion
    global last_enemy_seen_round, feed_protocol_active, last_alert_sent, last_feed_sent
    global global_king_id, global_king_len, global_king_pos
    
    turn_t0 = time.perf_counter()
    now = game.get_round_num()
    head = ct.get_position()
    mp = (head.x, head.y)
    heading = DIR_INDEX[ct.get_dir()]
    length = ct.get_length()

    if desired_heading is None:
        rush_limit = CENTER_RUSH_ROUND if not is_small_map else 40
        if ct.get_id() > 1 and now < rush_limit:
            desired_heading = toward_center_heading(mp)
        else:
            desired_heading = heading

    occupied, mine, others, enemies, pearls, discovered_new = observe(now)
    body = update_body(mp, length, mine)
    my_team = ct.get_team()
    
    # Track the global King directly using Sonar rather than local vision
    saw_enemy_directly = len(enemies) > 0
    if saw_enemy_directly:
        last_enemy_seen_round = now
        feed_protocol_active = False
        if now - last_alert_sent > 5:
            try:
                for d in DIRS: ct.send_sonar(d, SONAR_ENEMY_ALERT)
            except Exception: pass
            last_alert_sent = now

    received_alert = False
    received_feed = False
    try:
        for msg in ct.get_sonar_messages():
            if msg == SONAR_ENEMY_ALERT: received_alert = True
            elif msg == SONAR_FEED_CHAMPION: received_feed = True
            else:
                kinfo = decode_king(msg)
                if kinfo:
                    kid, klen, kx, ky = kinfo
                    if klen > global_king_len or (klen == global_king_len and kid < global_king_id):
                        global_king_id = kid
                        global_king_len = klen
                        global_king_pos = (kx, ky)
    except Exception:
        pass

    if received_alert and not saw_enemy_directly:
        last_enemy_seen_round = now
        feed_protocol_active = False
        if now - last_alert_sent > 5:
            try:
                for d in DIRS: ct.send_sonar(d, SONAR_ENEMY_ALERT)
            except Exception: pass
            last_alert_sent = now

    # Dynamic King Promotion
    if length > global_king_len or (length == global_king_len and ct.get_id() <= global_king_id):
        global_king_id = ct.get_id()
        global_king_len = length
        global_king_pos = mp
        is_champion = True
    elif is_champion and global_king_id != ct.get_id() and global_king_len >= length + DYNAMIC_PROMOTE_MARGIN:
        is_champion = False

    if is_champion and now % 3 == 0:
        try:
            msg = encode_king(ct.get_id(), length, mp[0], mp[1])
            for d in DIRS: ct.send_sonar(d, msg)
        except Exception: pass

    is_stalemate = now > STALEMATE_ROUNDS and (now - last_enemy_seen_round > STALEMATE_ROUNDS)

    if is_champion and is_stalemate:
        feed_protocol_active = True
        if now - last_feed_sent > 5:
            try:
                for d in DIRS: ct.send_sonar(d, SONAR_FEED_CHAMPION)
            except Exception: pass
            last_feed_sent = now

    if received_feed and is_stalemate and not feed_protocol_active:
        feed_protocol_active = True
        if now - last_feed_sent > 5:
            try:
                for d in DIRS: ct.send_sonar(d, SONAR_FEED_CHAMPION)
            except Exception: pass
            last_feed_sent = now

    enemy_lens = visible_lengths(occupied, my_team.get_enemy_team())

    # Active Feeder Pathing
    if not is_champion and feed_protocol_active:
        if global_king_pos is not None and mp != global_king_pos:
            blocked = set(occupied) - {mp}
            my_dist, my_mask = my_search(mp, [i for i in range(4) if edges[mp][i] != 1], blocked)
            if global_king_pos in my_mask:
                bits = my_mask[global_king_pos]
                wanted = [i for i in range(4) if bits >> i & 1]
                if wanted:
                    mv = max(wanted, key=lambda i: i == heading)
                    ct.make_move(DIRS[mv])
                    return
        return  # Die to drop pearls

    if not is_champion:
        for i in range(4):
            if edges[mp][i] == 1: continue
            dest = step(mp, i) if edges[mp][i] == 0 else portal_exit(mp, i)
            if dest is None: continue
            part = occupied.get(dest)
            if part is None or not part.is_head() or part.get_team() == my_team: continue
            L = enemy_lens.get(part.get_id(), 1)
            if L >= OPPORTUNISTIC_MIN_ENEMY_LEN and L >= length * OPPORTUNISTIC_RATIO:
                ct.make_move(DIRS[i])
                return

    legal = []
    for i in range(4):
        if edges[mp][i] == 0:
            if step(mp, i) not in occupied: legal.append(i)
        elif edges[mp][i] == 2 and i != OPP[heading]:
            dest = portal_exit(mp, i)
            if dest is None or dest not in occupied: legal.append(i)
                
    safe = []
    for i in legal:
        dest = step(mp, i) if edges[mp][i] == 0 else portal_exit(mp, i)
        if dest is None or not is_tile_scary(dest, enemies, is_champion, length, enemy_lens, is_small_map):
            safe.append(i)
            
    allowed = safe or legal
    own = set(body) if body is not None else set(hist[-length:])

    if not allowed:
        if length - SPLIT_SIZE >= SPLIT_SIZE and ct.can_split(length - SPLIT_SIZE):
            ct.do_split(length - SPLIT_SIZE)
            return
        move = least_bad_move(mp, occupied)
        if move is not None: ct.make_move(DIRS[move])
        return

    imminent_scary_threat = is_tile_scary(mp, enemies, is_champion, length, enemy_lens, is_small_map)
    in_danger = imminent_scary_threat and len(safe) <= 1
    fleeing = bool(safe) and imminent_scary_threat and not in_danger

    stuck_in_loop = hist[-CYCLE_CHECK_WINDOW - 1:-1].count(mp) > 0
    stale_turns = 0 if discovered_new else stale_turns + 1
    
    # Remove friendly-fire penalties by excluding allies from blocking logic
    others_block = {t for t, p in occupied.items() if p.get_id() != ct.get_id() and p.get_team() != my_team}

    if body is not None:
        need = min(length, 30)
        room = {}
        for i in allowed:
            dest = step(mp, i) if edges[mp][i] == 0 else portal_exit(mp, i)
            if dest is None: room[i] = need
            else: room[i] = room_check(dest, body, others_block, need)
                
        roomy = [i for i in allowed if room[i] >= need]
        allowed = roomy or [i for i in allowed if room[i] == max(room.values())]

    if not fleeing:
        if is_champion:
            split_now = (in_danger or stuck_in_loop) and length >= MIN_SPLIT_LEN
        else:
            min_pop = PRESERVE_MIN_POP_SMALL if is_small_map else PRESERVE_MIN_POP_LARGE
            team_short_handed = ct.get_unit_count() < min_pop
            growth_ok = (now <= SPLIT_STOP_ROUND and length >= split_threshold(now)
                         and (now < PRESERVE_ROUND or team_short_handed))
            split_now = length >= MIN_SPLIT_LEN and (in_danger or stuck_in_loop or growth_ok)
        
        exit_ok = child_has_exit(body, occupied) if body is not None else True
        if split_now and ct.can_split(SPLIT_SIZE) and exit_ok:
            ct.do_split(SPLIT_SIZE)
            return

    if not is_champion and now >= STARVE_BREEDERS_ROUND:
        pearls = set()
        
    blocked = set(occupied) - {mp}
    my_dist, my_mask = my_search(mp, allowed, blocked)
    target = best_winnable_pearl(mp, my_dist, pearls, others, body, others_block, length)
    if target is not None:
        bits = my_mask.get(target)
        if bits:
            wanted = [i for i in allowed if bits >> i & 1]
            if wanted:
                mv = max(wanted, key=lambda i: i == heading)
                ct.make_move(DIRS[mv])
                return

    if stale_turns >= STALE_TRIGGER_TURNS:
        new_heading = unexplored_heading(mp)
        if new_heading is not None:
            desired_heading = new_heading
            stale_turns = 0
    elif stuck_in_loop:
        def staleness(i):
            dest = step(mp, i) if edges[mp][i] == 0 else portal_exit(mp, i)
            if dest is None or dest not in hist: return len(hist) + 1
            return len(hist) - 1 - hist[::-1].index(dest)
        desired_heading = max(allowed, key=staleness)
    elif any(hd == OPP[desired_heading] for _, _, hd in others):
        left, right = perpendiculars(desired_heading)
        if left in allowed: desired_heading = left
        elif right in allowed: desired_heading = right

    if desired_heading in allowed:
        ct.make_move(DIRS[desired_heading])
        return
    left, right = perpendiculars(desired_heading)
    for cand in (left, right):
        if cand in allowed:
            ct.make_move(DIRS[cand])
            return
    ct.make_move(DIRS[allowed[0]])


def fallback_move() -> None:
    here = ct.get_position()
    here_tile = ct.get_tile(here)
    for direction in Direction.get_direction_list():
        if not here_tile.get_edge(direction).is_passable(): continue
        ahead = ct.get_tile(here.add_dir(direction))
        if ahead is not None and ahead.get_dragon() is None:
            ct.make_move(direction)
            return


def main() -> None:
    global ct, game, W, H, is_small_map, is_champion, desired_heading
    global last_enemy_seen_round, feed_protocol_active, last_alert_sent, last_feed_sent, stale_turns
    global global_king_id, global_king_len, global_king_pos
    global edges, portal_id_at, portal_ends, hist, NBR
    
    ct, game = unswbc.init()
    W, H = game.get_map_size()
    is_small_map = W <= SMALL_MAP_THRESHOLD or H <= SMALL_MAP_THRESHOLD
    
    is_champion = False
    desired_heading = None
    last_enemy_seen_round = 0
    feed_protocol_active = False
    last_alert_sent = -10
    last_feed_sent = -10
    global_king_id = -1
    global_king_len = -1
    global_king_pos = None
    stale_turns = 0
    edges.clear()
    portal_id_at.clear()
    portal_ends.clear()
    hist.clear()
    NBR.clear()

    while unswbc.update(ct, game):
        try:
            execute_turn()
        except Exception:
            if LOCAL_DEBUG:
                import traceback
                ct.output_log("EXC " + traceback.format_exc().replace("\n", " | "))
            fallback_move()
        unswbc.end_turn()


if __name__ == "__main__":
    main()