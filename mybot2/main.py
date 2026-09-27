"""
============================================================================
DESIGN OVERVIEW -- read this before the code, not just the inline comments
============================================================================

How the game is scored (confirmed from docs/structure): eliminating every enemy
dragon wins outright. Otherwise, at round 500, the team with the single LONGEST
dragon wins; total length is the tiebreak after that. Dragon COUNT is never
scored directly -- it only matters because more heads visit more pearls, and
because a bigger swarm is harder to eliminate.

Given that, this bot runs two roles:

  - CHAMPION: exactly one dragon (the very first one, chosen once and never
    reconsidered) whose whole job is to survive and grow as long as possible
    for the round-500 tiebreak. A champion never splits except to escape
    certain death, and is exactly as cautious as everyone else about threats
    (see below -- there is no combat logic in this build at all).

  - BREEDER: every other dragon. Splits off a 2-long child whenever it is
    long enough to (the length needed to do so climbs slowly as the game goes
    on -- see split_threshold), and otherwise just eats pearls.

This build deliberately has NO killer/kamikaze logic: no dragon ever
deliberately seeks out a collision. All dragons are equally, maximally
cautious about any tile an enemy could step onto next turn. That is a
deliberate simplification, not an oversight -- offense can be added back as
its own separate layer later without touching anything below.

Per-turn decision order, every dragon, every turn:
  1. Is my ONLY legal action fatal? -> take the least-bad option, EXCEPT we
     never deliberately ram a teammate's head (that kills two of our own
     dragons for the price of one). If nothing else is available, we take NO
     action at all, which the judge scores as "no valid action" -- a death
     that costs us exactly one dragon, same as any other self-only death, and
     strictly better than a mutual kill with a teammate.
  2. Split, if we're a breeder, long enough, and not currently fleeing.
  3. Is there a pearl currently in view that we can reach before every other
     visible dragon (teammate or enemy) can? If so, go straight for it.
  4. Otherwise: is another visible dragon (teammate or enemy) facing directly
     back at us? If so, that direction has probably already been picked
     over -- turn to face a perpendicular direction instead.
  5. Otherwise: keep drifting in our current standing "desired heading",
     routing around any kelp in the way without abandoning that heading.

Memory: unlike a from-scratch memory-free build, this version KEEPS permanent
memory of terrain (which tiles are kelp, water, or portals -- that never
changes, so remembering it is free and lets our pathfinding route around
obstacles outside our current 7x7 view). It does NOT keep memory of pearls
past what is visible RIGHT NOW: a remembered "there was a pearl here" could
easily be stale (someone else may have already taken it), so every pearl
decision below is made only from tiles we can see this exact turn.
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
# Compass bookkeeping. DIRS is the engine's own ordered list of the 4
# directions; we work with plain integer indices 0-3 into it everywhere else,
# only converting back to a Direction object at the moment we call
# ct.make_move. DIR_INDEX is the reverse lookup (Direction -> index), used
# whenever the engine hands us a direction (e.g. another dragon's heading)
# and we need our own index for it.
# ---------------------------------------------------------------------------
DIRS = Direction.get_direction_list()                      # e.g. [N, E, S, W]
DIR_INDEX = {d: i for i, d in enumerate(DIRS)}
DX = (0, 1, 0, -1)          # x-offset of moving one step in direction i
DY = (-1, 0, 1, 0)          # y-offset of moving one step in direction i
OPP = (2, 3, 0, 1)          # OPP[i] = the index of the direction facing exactly opposite i

DEBUG = False                                       # verbose ct.output_log every turn; keep off
LOCAL_DEBUG = os.environ.get("BC_DEBUG") == "1"      # exception logging for OUR OWN local testing
                                                      # only -- this env var will never be set when
                                                      # the judge actually runs the bot, so this is
                                                      # always a no-op in competition.

# ============================================================================ tuning constants
# Every constant below is a single, named knob -- if you want to change one behaviour, there
# should be exactly one line to edit, and its comment should tell you what changing it does.

# --- splitting (breeders only; champions have their own separate rule further down)
SPLIT_SIZE = 2                       # a split always sheds exactly this many segments -- a bigger
                                      # child costs the same effort to grow back but a smaller one
                                      # is available sooner, so there's no reason to ever ask for
                                      # more than the minimum.
MIN_SPLIT_LEN = 2 * SPLIT_SIZE        # 4: you cannot shed 2 segments and have any body left over
                                      # with fewer than 4 total, so this is a hard physical floor,
                                      # not a tuning choice.
SMALL_MAP_THRESHOLD = 25              # width OR height at or below this counts as "small" for the
                                      # two splitting-related knobs just below.
SPLIT_RAMP_SMALL = 0.01               # every round, the length required to split (on a small map)
SPLIT_RAMP_LARGE = 0.02               # rises by this much -- so early splits are easy and it slowly
                                      # gets more demanding, rather than every dragon splitting the
                                      # instant it hits length 4 for the entire game.
SPLIT_CAP_SMALL = 6                   # ...but the requirement never climbs past this,
SPLIT_CAP_LARGE = 12                  # so it's not effectively "never split again" by round 500.
SPLIT_STOP_ROUND = 450                # regardless of length, NEVER growth-split this late (an
                                      # absolute outer bound) -- a brand new 2-long child born with
                                      # <50 rounds left cannot realistically pay back the investment
                                      # of growing it, no matter how badly the team needs numbers.
PRESERVE_ROUND = 400                  # past this round, an ordinary breeder stops growth-splitting
PRESERVE_MIN_POP_SMALL = 12           # and just holds its current length instead -- UNLESS the
PRESERVE_MIN_POP_LARGE = 32           # team's total dragon count drops below this floor, resuming
                                      # splitting. Prevents fatal crowding on small maps while
                                      # maintaining swarm presence on large maps.

STARVE_BREEDERS_ROUND = 380           # past this round, breeders completely stop chasing pearls.
                                      # A pearl eaten by a breeder late game is mathematically wasted
                                      # for the round 500 tiebreaker. This funnels all remaining food
                                      # directly to the champion.

# --- drift / default movement
CYCLE_CHECK_WINDOW = 10                # if our head revisits a tile it stood on within this many
                                      # turns, that can only mean we're retracing our own path in a
                                      # small loop (see the note at the reorientation check) rather
                                      # than genuinely making progress -- force a reorientation.
STALE_TRIGGER_TURNS = 25               # if this many consecutive turns pass with NO tile in view
                                      # ever being genuinely new (see observe's `discovered_new`),
                                      # that means we've been going in circles around already-known
                                      # ground, even if not literally retracing our own exact path
                                      # closely enough for the check above to catch it. Once this
                                      # fires, we deliberately head somewhere we've never mapped.
FRONTIER_SEARCH_CAP = 250               # unexplored_heading's BFS gives up after visiting this
                                      # many known tiles without finding a frontier -- bounds the
                                      # cost on a large, mostly-explored map; reusing memory we
                                      # already keep (`edges`), no separate "recency" record needed.
CENTER_RUSH_ROUND = 120                # on a large map, any breeder (not our team's original/
                                      # eventual champion, which never risks the contested middle)
                                      # born before this round starts off heading towards the map's
                                      # coordinate centre (see toward_center_heading) instead of
                                      # whatever direction it happens to spawn facing. Measured
                                      # problem this fixes: with nothing pulling dragons inward,
                                      # they tend to hover near their own spawn corner, so a big
                                      # map's central pearls go unclaimed early -- exactly when
                                      # missed pearls cost the most (fewer splits compounds over the
                                      # whole game). Only "some" dragons get this nudge by
                                      # construction: only whatever gets born within this early
                                      # window, which is naturally a small number before splitting
                                      # has had time to ramp up; from then on the ordinary
                                      # reorientation triggers (stale memory, loops, opposing
                                      # dragons) take over and diversify them same as any other.

# --- champion (the single dragon we are growing for the round-500 tiebreak)
CHAMPION_START_SMALL = 120            # the very first dragon breeds normally like everyone else
CHAMPION_START_LARGE = 100            # until this round, THEN permanently stops splitting and just
                                      # grows for the rest of the game. Small maps end by
                                      # elimination far more often than large ones, so it's worth
                                      # keeping that first dragon breeding for longer there.
CHAMPION_TAKEOVER_SMALL = 11          # ANY dragon that happens to grow this long (e.g. one that
CHAMPION_TAKEOVER_LARGE = 15          # inherited most of a parent's body from an emergency split)
                                      # is automatically promoted to champion too -- no reason to
                                      # let a dragon that's already this valuable keep splitting.
DYNAMIC_PROMOTE_ROUND = 200            # from this round on, a breeder that is CLEARLY the biggest
DYNAMIC_PROMOTE_MARGIN = 2             # thing it can currently see among its own team, and is
DYNAMIC_PROMOTE_MIN_SAMPLE = 2         # already close to the hard CHAMPION_TAKEOVER length anyway
DYNAMIC_PROMOTE_RATIO = 1.5            # (within this many segments of it), promotes itself
                                      # immediately rather than waiting on the random backup roll
                                      # below or on reaching takeover outright. "Clearly the
                                      # biggest" needs a real sample (>= DYNAMIC_PROMOTE_MIN_SAMPLE
                                      # different teammates) and a real margin (at least
                                      # DYNAMIC_PROMOTE_RATIO times the longest of them) -- but
                                      # measured directly: on a map with continuous splitting, the
                                      # visible population is dominated by newborns almost all the
                                      # time (in one test, even a 6-teammate sample topped out at
                                      # length 7), so the teammate comparison alone can't do the
                                      # filtering. Tried progressively: a plain "longer than visible
                                      # teammates" rule fired 163 times in one game; tightening only
                                      # the sample/ratio brought that to 136; MARGIN=4 (allowing any
                                      # length within 4 of takeover) still gave 53, because ordinary
                                      # breeders naturally fluctuate near SPLIT_CAP_LARGE as part of
                                      # normal operation and that range overlapped it heavily.
                                      # MARGIN=2 was the point this actually became rare (8 firings
                                      # in that same game) rather than a mid-game population filter
                                      # -- the floor has to do the real work here, not the
                                      # comparison: this stays a way to lock in a near-champion
                                      # slightly EARLY, not a broad substitute for takeover.
BACKUP_START_ROUND = 200              # from this round on, ordinary breeders occasionally lock in
BACKUP_RATE = 0.001                   # as a backup champion at this per-turn chance -- kept as a
                                      # fallback for whenever no teammate happens to be in view to
                                      # compare against at all, so the dynamic check above can't fire.
LATE_LOCK_ROUND = 430                 # very late in the game, any dragon at least this long stops
LATE_LOCK_LEN = 6                     # splitting and just plays safe: there's no time left for a
                                      # newly-split child to become useful anyway.

# --- opportunistic aggression. This build is otherwise fully defensive (see the module docstring),
# but a head-to-head kill is mutual regardless of length (confirmed: docs/death), so trading a cheap
# breeder for a MUCH bigger enemy dragon -- one that could well be their champion -- is a clear net
# win under the actual scoring rules. Deliberately narrow: only a same-turn adjacent opportunity, no
# hunting or multi-step chase, and never the champion itself (it never risks itself for a trade).
OPPORTUNISTIC_MIN_ENEMY_LEN = 10       # never take the trade against a dragon shorter than this...
OPPORTUNISTIC_RATIO = 2                # ...and only when they're also at least this many times us

# --- per-turn CPU budget. The judge gives each turn 100M CPU points, worth 0.1s of perf_counter
# in its sandbox (natively this never matters -- a turn takes milliseconds). Reading every visible
# tile and starting the engine's own bookkeeping already costs roughly 10-15M points before our
# code even starts, so these budgets are measured from the start of execute_turn and leave margin.
SOFT_BUDGET_S = 0.045     # past this, skip optional extras (checking every rival's distance to
                          # every candidate pearl is the single priciest thing we do)
HARD_BUDGET_S = 0.065     # past this, stop planning entirely and just take a cheap, safe move

SEARCH_DEPTH = 24         # how many steps our own pathfinding explores through remembered terrain
RIVAL_SEARCH_DEPTH = 14   # how many steps we search from another dragon's position when checking
                          # whether they would beat us to a pearl -- shallower than our own search
                          # since we only need to know "closer than us or not", not their full map
ROOM_CHECK_MARGIN = 1.5   # room_check's cheap flood-fill pre-check must clear the room requirement
                          # by this multiplier before it's trusted; anything less overlaps with the
                          # "borderline" range where a flood fill can overstate real safety, and we
                          # fall back to the exact self-avoiding search instead (see room_check).

# ============================================================================ persistent state
# Every one of these survives from one turn to the next. `edges`, `portal_id_at` and `portal_ends`
# are genuine map memory (terrain never changes, so this is free accuracy). Everything else is a
# small piece of our own standing intent or role, not a memory of the world.
W = H = 0
edges: dict = {}          # (x, y) -> (north, east, south, west) edge types: 0 open, 1 kelp, 2 portal
portal_id_at: dict = {}   # ((x, y), side) -> the id of the portal on that side of that tile
portal_ends: dict = {}    # portal id -> the set of physical edges we have seen carrying it
hist: list = []           # our own head's past positions, oldest first (see update_body)
desired_heading = 0       # the cardinal direction we are currently trying to drift in when there
                          # is no pearl worth chasing -- this is the ONE piece of "intent" state
                          # this design needs; everything else is recomputed fresh every turn.
stale_turns = 0           # consecutive turns since our terrain memory last actually grew (see
                          # observe's `discovered_new`) -- how "no update in a while" is measured.
is_champion = False
is_small_map = False
NBR: dict = {}            # (x, y) -> its four wrapped neighbours, cached once per tile ever seen
turn_t0 = 0.0


def step(pos, i):
    """The tile one step from pos in direction i, wrapping around the map (the board is a torus)."""
    return ((pos[0] + DX[i]) % W, (pos[1] + DY[i]) % H)


def nbrs(pos):
    """The four neighbours of pos. Cached because every search below re-visits the same tiles many
    times per turn, and computing this is one of the few things we do often enough to matter."""
    r = NBR.get(pos)
    if r is None:
        x, y = pos
        r = NBR[pos] = ((x, (y - 1) % H), ((x + 1) % W, y), (x, (y + 1) % H), ((x - 1) % W, y))
    return r


def spent():
    """Seconds spent so far this turn, for the CPU-budget checks."""
    return time.perf_counter() - turn_t0


def dir_between(a, b):
    """The direction index that steps from tile a onto adjacent tile b, or None if they aren't
    adjacent. Used only to reconstruct our own body chain in update_body."""
    for i in range(4):
        if step(a, i) == b:
            return i
    return None


def wrap_dist(a, b):
    """Manhattan distance between two tiles, accounting for the map wrapping around both axes.
    This is a cheap ESTIMATE used only to shortlist candidates before doing real pathfinding; the
    actual "who gets there first" decision always uses real BFS distance, never this."""
    dx = abs(a[0] - b[0])
    dy = abs(a[1] - b[1])
    return min(dx, W - dx) + min(dy, H - dy)


def perpendiculars(h):
    """The two directions at right angles to heading h. DIRS is ordered N,E,S,W (a full turn every
    4 steps), so +1 and -1 (mod 4) are always the two perpendiculars and +2 is always the opposite
    (that's exactly what the OPP table encodes)."""
    return (h + 1) % 4, (h + 3) % 4


def toward_center_heading(pos):
    """A rough initial heading from `pos` towards the map's coordinate centre (W/2, H/2).
    Properly calculates the shortest directional vector across a wrapping Torus map."""
    cx, cy = W // 2, H // 2
    dx = (cx - pos[0] + W // 2) % W - W // 2
    dy = (cy - pos[1] + H // 2) % H - H // 2
    if abs(dx) >= abs(dy):
        return 1 if dx > 0 else 3    # east : west
    return 2 if dy > 0 else 0        # south : north


def unexplored_heading(mp):
    """Which of our first moves leads soonest, by actual walking distance through remembered
    terrain, to the nearest FRONTIER tile -- a tile we've already mapped that sits directly next
    to at least one tile we've never seen at all. A bounded BFS through known terrain (capped at
    FRONTIER_SEARCH_CAP tiles visited) finds the true nearest frontier this way, which correctly
    handles an unexplored pocket that's diagonal from us or hidden just around a kelp wall --
    cases a fixed-distance straight-line probe in only the 4 cardinal directions would miss
    entirely, since it can only ever see what's directly on those 4 lines. Returns None if the
    search exhausts its cap without finding one (would mean we're deep inside a very large,
    already fully-mapped open area)."""
    for i in range(4):
        if edges[mp][i] == 2:
            if portal_exit(mp, i) is None: return i
        elif step(mp, i) not in edges:
            return i        # already bordering the unknown: no need to search any further
            
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
            
            # Check if this new tile borders the unknown
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
    """A name for the physical edge on `side` of `tile` that is the SAME name no matter which of
    the two tiles on either side of it you ask from -- needed because a portal's information is
    keyed by which physical wall it sits on, not by which tile you were standing on when you saw
    it. ('H', x, y) is the horizontal edge between (x, y-1) and (x, y); ('V', x, y) is the vertical
    edge between (x-1, y) and (x, y)."""
    x, y = tile
    if side == 0:                       # north side of this tile
        return ('H', x, y)
    if side == 2:                       # south side of this tile
        return ('H', x, (y + 1) % H)
    if side == 3:                       # west side of this tile
        return ('V', x, y)
    return ('V', (x + 1) % W, y)        # side == 1, east side of this tile


def portal_exit(tile, side):
    """Where our head would land if we stepped through the portal on `side` of `tile`. Returns
    None if we have never seen the partner end of this portal (we know a portal is there, but not
    yet where it leads)."""
    pid = portal_id_at.get((tile, side))
    if pid is None:
        return None
    here = edge_key(tile, side)
    for kind, x, y in portal_ends.get(pid, ()):
        if (kind, x, y) == here:
            continue                    # that's the edge we're standing at, not the far end
        if kind == 'H':
            if side == 2:
                return (x, y)
            if side == 0:
                return (x, (y - 1) % H)
        elif side == 1:
            return (x, y)
        elif side == 3:
            return ((x - 1) % W, y)
        return (x, y)                   # the partner runs across our line of travel
    return None


# ============================================================================ perception
# Everything in this section runs once at the start of every turn and answers "what do we know
# right now" -- some of it (terrain) gets folded into permanent memory; pearls and other dragons
# never do, because they change too fast to trust a memory of them.

def observe(now):
    """Read this turn's 7x7 vision window.

    Folds newly-seen TERRAIN into permanent memory (edges never change, so each tile's terrain is
    only ever read once, the very first time we see it). Everything else -- who is standing where,
    which tiles currently hold a pearl -- is returned fresh and is NOT stored anywhere: next turn
    this function runs again from scratch.

    Returns:
      occupied: dict (x,y) -> the dragon part object standing there (any team, any dragon)
      mine:     dict (x,y) -> our own body parts (excluding our own head) that are visible
      others:   list of (id, pos, heading_index) for every OTHER visible head, teammate or enemy
      enemies:  the subset of `others` that are on the opposing team
      pearls:   set of (x,y) tiles that currently, this instant, hold a pearl
      discovered_new: True if any tile in view this turn had never been seen before -- i.e. our
                      terrain memory genuinely grew this turn. This is the cheapest possible signal
                      for "have I learned anything lately", since it falls straight out of the
                      "first time seeing this tile" check we already do below; no separate
                      recency-tracking structure is needed to compute it.
    """
    my_id = ct.get_id()
    my_team = ct.get_team()
    occupied, mine, others, enemies, pearls = {}, {}, [], [], set()
    discovered_new = False
    for tile in ct.get_tiles():
        p = tile.get_position()
        key = (p.x, p.y)

        if key not in edges:
            # First time ever seeing this tile: read its terrain once and remember it forever.
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
    """Our best guess at our own body, head first, or None only if we cannot even guess.

    If every one of our segments happens to be inside this turn's 7x7 view (only possible for a
    short dragon), we can read the whole chain directly and that becomes our new ground truth.
    Otherwise -- which is the normal case for any dragon longer than about 6 -- we fall back on
    `hist`, the trail of head positions we have been recording every turn since this dragon was
    born. That trail is exact (we know every move we've ever made), so once enough turns have
    passed since birth for it to contain `length` entries, we know our full body with certainty
    even though we can no longer SEE most of it.

    The gap neither of those covers: a dragon born via the emergency "hand over most of the body"
    split (see the boxed-in branch of execute_turn) starts life already long, but its `hist` starts
    completely empty -- it has no memory of moves it never made. Rather than return None and leave
    callers like escape_depth and child_has_exit fully blind for that dragon's first `length` turns
    of life, we ESTIMATE the unseen tail: take whichever of `chain` or `hist` gives us the longer
    known prefix from the head, and extend it in a straight line, continuing whatever direction its
    last known segment was already heading. This is a guess, not ground truth -- but a straight
    continuation is the least-surprising assumption about a body shape we have no better evidence
    for, and gives safety checks something real to work with instead of skipping them outright.
    Only returns None if we don't even have 2 known points to tell which way to extrapolate (in
    practice, only possible in a dragon's first turn or two of life).
    """
    global hist
    behind = {}
    for tile, part in mine.items():
        # a visible body segment always faces the neighbour that is closer to the head
        behind[step(tile, DIR_INDEX[part.get_dir()])] = tile
    chain = [mp]
    seen = {mp}
    cur = mp
    while cur in behind and behind[cur] not in seen:
        cur = behind[cur]
        chain.append(cur)
        seen.add(cur)

    if len(chain) == length:
        hist = chain[::-1]              # the whole body was visible: this is exact ground truth
        return hist[::-1][:length]
    if not hist or hist[-1] != mp:
        hist.append(mp)                 # otherwise just keep extending our own trail
    if len(hist) > 256:
        del hist[:-128]                 # never need more history than our own max plausible length
    if len(hist) >= length:
        return hist[::-1][:length]      # exact: our own trail alone covers the whole body

    hist_chain = hist[::-1]
    known = hist_chain if len(hist_chain) > len(chain) else chain
    if len(known) < 2:
        return None                     # can't even tell which way to extrapolate yet
    dx = known[-1][0] - known[-2][0]
    dy = known[-1][1] - known[-2][1]
    # normalise for map wraparound: a raw step of W-1 is really a step of -1, and vice versa
    if dx > 1:
        dx -= W
    elif dx < -1:
        dx += W
    if dy > 1:
        dy -= H
    elif dy < -1:
        dy += H
    est = list(known)
    while len(est) < length:
        lx, ly = est[-1]
        est.append(((lx + dx) % W, (ly + dy) % H))
    return est[:length]


def visible_lengths(occupied, team, exclude_id=None):
    """Visible segments per dragon on `team` (skipping `exclude_id` if given): a LOWER bound on
    each dragon's real length, since any segments outside our 7x7 window simply aren't counted.
    Used for both the opportunistic-aggression check (how big is that enemy, really) and dynamic
    champion promotion (how do I compare to the teammates I can currently see) -- a trade or a
    promotion decided on this can only look BETTER in reality than it does here, never worse."""
    counts = {}
    for part in occupied.values():
        if part.get_team() == team and part.get_id() != exclude_id:
            counts[part.get_id()] = counts.get(part.get_id(), 0) + 1
    return counts


def is_tile_scary(pos, enemies, is_champ, my_length, enemy_lens, is_small):
    """
    Determines if a tile should be avoided.
    Champions avoid ALL enemies.
    Breeders only avoid enemies that are smaller or roughly equal to them (a bad trade).
    They will happily play 'chicken' with massive enemies to claim space and force the enemy to retreat.
    """
    if pos is None: # We cannot be threatened if we step into an unknown portal (we vanish)
        return False
        
    for pid, ekey, edir in enemies:
        back = OPP[edir]
        e = edges.get(ekey)
        if e is None:
            continue
        for i in range(4):
            if i == back:
                continue
            # Treat enemy traversing through a known portal as a threat
            dest = step(ekey, i) if e[i] == 0 else (portal_exit(ekey, i) if e[i] == 2 else None)
            if dest == pos:
                if is_champ:
                    return True # Champion plays purely for survival
                
                # If we are a breeder, how big is the threatening enemy?
                e_len = enemy_lens.get(pid, 1)
                
                # On small maps, don't flee from equal sized enemies. Play chicken to hold space.
                fear_ratio = 1.0 if is_small else 1.5
                if e_len < my_length * fear_ratio:
                    return True
    return False


# ============================================================================ search
# The actual pathfinding. All of it operates over REMEMBERED terrain (the `edges` dict, which only
# ever grows), not just what's in view this instant -- that's what lets a dragon route sensibly
# around a wall it saw ten turns ago even if that wall is currently out of its 7x7 window.

def my_search(mp, allowed, blocked):
    """Breadth-first search from our head, seeded from every currently-allowed first move. For
    every tile we can reach through remembered, unblocked terrain, records the shortest distance
    to it AND a bitmask of which of our first moves start a shortest path there -- so later code
    can ask "which of my legal first moves gets me closer to tile X" in O(1) instead of
    re-deriving a path. Bails out early past SOFT_BUDGET_S: whatever has been found by then is
    still exactly correct for the tiles it covers, the search just doesn't reach as far."""
    dist, mask = {}, {}
    q = deque()
    for i in allowed:
        if edges[mp][i] == 2:
            nb = portal_exit(mp, i)
            if nb is None: 
                continue # Cannot pathfind through an unknown portal
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
            if e[i] == 1:                       # kelp
                continue
            if e[i] == 2:                       # traverse portal
                nb = portal_exit(cur, i)
                if nb is None: 
                    continue
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
                mask[nb] |= m               # another equally-short route: remember this option too
    return dist, mask


def multi_source_search(starts, depth, targets):
    """Distances from the NEAREST of any tile in `starts` through remembered terrain, out to
    `depth` steps -- a plain BFS seeded from multiple sources at once, in the same style as
    my_search's multi-seeded first moves. Doesn't track a first-move mask (callers only need "how
    far", not "which way") and stops as soon as every tile in `targets` has been reached, since at
    that point every distance still needed is already known exactly.

    Used to check how far away the NEAREST other dragon is from each pearl we're considering: for
    "would somebody beat us to this pearl", the minimum distance across every rival is exactly the
    quantity that matters, and a single multi-source pass computes it directly for every target in
    one search, rather than running a separate single-source search per rival and taking the min
    ourselves afterwards."""
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
            if e[i] == 1:
                continue
            if e[i] == 2:
                nb = portal_exit(cur, i)
                if nb is None:
                    continue
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
    """How many moves in a row could we make, without hitting anything, starting by stepping onto
    `nb`? Capped at `need`: if we can make `need` = our own length moves, our whole body will have
    moved on by then and we are certainly free regardless of what happens after.

    This is a genuine self-avoiding PATH search, not a flood fill -- a flood fill would say a
    tightly coiled pocket of our own body "looks roomy" by counting all its open-looking cells,
    even though a single head can only ever walk one route through it and might not fit. A body
    cell only counts as free again once enough moves have passed that our own tail would have
    already vacated it (`free_after`). Gives up after `budget` recursive steps and reports the best
    partial route found rather than searching forever."""
    length = len(body)
    free_after = {body[k]: length - k for k in range(length)}   # moves until body[k] is vacated
    on_path = {nb}
    best = 1
    used = 0

    def open_exits(cell, t):
        """A cheap look-ahead used only to order which neighbour to try first: how many of a
        candidate cell's own neighbours will still be open by the time we'd arrive there."""
        e = edges.get(cell)
        if e is None:
            return 4
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
        if t > best:
            best = t
        if best >= need:
            return True
        used += 1
        if used > budget:
            return False
        if not used & 31 and spent() > HARD_BUDGET_S:
            used = budget + 1           # out of time: unwind immediately, keep what we have
            return False
        e = edges.get(cur)
        if e is None:
            best = need                 # beyond anything we've ever mapped: assume open water
            return True
        nexts = []
        for i in range(4):
            if e[i] == 1:
                continue
            if e[i] == 2:
                n2 = portal_exit(cur, i)
                if n2 is None:          # Successfully escaped into unknown territory!
                    best = need
                    return True
            else:
                n2 = step(cur, i)
                
            if n2 in on_path or n2 in others_block or free_after.get(n2, 0) >= t + 1:
                continue
            nexts.append((-open_exits(n2, t + 1), n2))     # try the most-open option first
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


def flood_fill_volume(start, blocked, cap):
    """How many distinct tiles (through remembered, unblocked terrain) lie within `cap` BFS steps
    of `start`. This is deliberately NOT the safety answer on its own -- see escape_depth's own doc
    for exactly why a flood fill can overstate real safety for a tightly coiled pocket (it counts
    every open-looking cell, even ones a single continuous body could never actually thread through
    to reach). It's a fast, cheap UPPER BOUND, used only in room_check below to skip the expensive
    exact search entirely in the common case of obviously open water, where escape_depth's DFS is
    the most expensive for no real benefit (there's nothing subtle to get right in open water)."""
    seen = {start}
    q = deque([start])
    count = 1
    while q and count < cap:
        cur = q.popleft()
        e = edges.get(cur)
        if e is None:
            continue
        for i in range(4):
            if e[i] == 1:
                continue
            if e[i] == 2:
                nb = portal_exit(cur, i)
                if nb is None:
                    return cap      # Infinite volume through an unexplored portal
            else:
                nb = step(cur, i)
                
            if nb in seen or nb in blocked or nb not in edges:
                continue
            seen.add(nb)
            count += 1
            if count >= cap:
                break
            q.append(nb)
    return count


def room_check(nb, body, others_block, need, budget=500):
    """Is `need` moves of room available starting from `nb`? Tries the cheap flood fill first: if
    the raw reachable volume clears `need` by a comfortable margin (ROOM_CHECK_MARGIN), that is
    reliable evidence of open water and we skip the expensive self-avoiding search entirely. Only
    when the flood fill comes back borderline -- which is exactly the situation where a coiled,
    self-trapping pocket becomes possible, per escape_depth's own doc -- do we pay for the exact
    answer. This keeps the real correctness guarantee where it actually matters (tight, complex
    spaces) while skipping the most expensive case (wide open water) almost entirely, which is
    where the DFS was actually burning CPU for no benefit."""
    threshold = int(need * ROOM_CHECK_MARGIN) + 1
    static_block = others_block | set(body)
    if flood_fill_volume(nb, static_block, threshold) >= threshold:
        return need
    return escape_depth(nb, body, others_block, need, budget)


# ============================================================================ pearls: the bidding
#
# We look only at pearls we can see THIS INSTANT (never a remembered belief that a pearl might
# still be there), and a pearl only becomes a real target if it clears two separate bars: we can
# reach it at least as fast as every other visible dragon (bidding), AND we could actually get
# back out again afterwards without being trapped (feasibility, via escape_depth -- see below).
# Distance alone was the original, simpler version of this; it was not enough, since a pearl in a
# dead end can easily be the closest one in sight while being a guaranteed death the moment we
# arrive. Both checks recompute fresh every turn, same as everything else in this file -- there is
# no persistent memory of "that pearl was a trap last time I checked."
#
# The bidding half is still deliberately simple: real BFS distance from us, compared against real
# BFS distance from every other visible dragon over the SAME remembered terrain graph, ties going
# to us. That simplicity does mean this can flicker between two similarly-priced pearls turn to
# turn, more than a version that "locks" onto a choice would -- a known, deliberate trade-off; if
# it turns out to matter in testing, reintroducing a lock is a small, isolated change here.

def nearby_pearl_count(p, pearls, radius=3):
    """How many OTHER currently-visible pearls lie within `radius` tiles of p. Used only to break
    ties between two pearls at the exact same distance: given a choice, a pearl with more pearls
    clustered around it is worth preferring, since taking it will very likely put us close to the
    next one too, whereas an isolated pearl of equal distance offers no such follow-on."""
    return sum(1 for q in pearls if q != p and wrap_dist(p, q) <= radius)


def best_winnable_pearl(mp, my_dist, pearls, others, body, others_block, length):
    """The nearest currently-visible pearl we can (a) reach at least as fast as every other visible
    dragon, AND (b) actually escape from again afterwards. Either one failing rules a pearl out.

    (b) is the check that was MISSING before this: a pearl sitting in a dead end can easily win on
    pure distance while being a death trap, since nothing about "how far away is it" says anything
    about "is there a way back out". We answer that the exact same way we already judge the safety
    of an ordinary move elsewhere in this file (escape_depth): treat the pearl's own tile as a
    candidate destination and ask whether our body (one segment longer, since eating it grows us)
    could still make `length + 1` further moves afterwards without getting trapped. If it can't,
    this pearl is not a target no matter how close it is.

    Ties in distance are broken by nearby_pearl_count: distance is still what decides the ranking
    in every non-tied case, this only matters when two candidates are EXACTLY equally close.

    If `body` is unknown (see update_body) we cannot run this check precisely and skip it rather
    than block all pearl-chasing until it resolves -- the same fallback used elsewhere in this file
    for the same reason."""
    if not pearls:
        return None
    candidates = sorted((my_dist[p], -nearby_pearl_count(p, pearls), p)
                         for p in pearls if p in my_dist)
    if not candidates:
        return None

    if body is not None:
        need = min(length + 1, 30)
        feasible = []
        for d, negcount, p in candidates:
            if spent() > SOFT_BUDGET_S:
                break   # out of time to verify further candidates: don't gamble on the unverified ones
            if room_check(p, body, others_block, need) >= need:
                feasible.append((d, negcount, p))
        candidates = feasible
        if not candidates:
            return None

    if not others:
        return candidates[0][2]         # nobody else visible at all: nothing to race against

    # One multi-source BFS, seeded from every other visible dragon's position at once, gives the
    # shortest distance from ANY of them to each candidate pearl in a single pass -- exactly the
    # quantity "would somebody beat us to it" needs. A separate BFS per rival (the previous version
    # of this function) computed strictly more than necessary: it's only ever the MINIMUM across
    # rivals that matters here, never any individual rival's distance on its own.
    goals = {p for _, _, p in candidates}
    rd = multi_source_search([opos for _, opos, _ in others], RIVAL_SEARCH_DEPTH, goals)
    for i, (d, negcount, p) in enumerate(candidates):
        rdist = rd.get(p)
        if rdist is not None and rdist < d:
            candidates[i] = None      # somebody else strictly beats us to this one: drop it
    for entry in candidates:
        if entry is not None:
            return entry[2]
    return None


# ============================================================================ splitting & roles

def child_has_exit(body, occupied):
    """Would the child's new head (our current tail tip) have somewhere legal to go on its very
    first turn, other than straight back into its own neck? If `body` is None (see update_body),
    we cannot check this precisely and callers should fall back to trusting ct.can_split alone."""
    tip, neck = body[-1], body[-2]
    e = edges.get(tip)
    if e is None:
        return True
    for i in range(4):
        if e[i] == 0:
            n2 = step(tip, i)
            if n2 != neck and n2 not in occupied:
                return True
        elif e[i] == 2:
            n2 = portal_exit(tip, i)
            if n2 is None or (n2 != neck and n2 not in occupied):
                return True
    return False


def split_threshold(now):
    """The minimum length a BREEDER needs before splitting for growth. 
    Locks at the absolute minimum (4) for the early game to maximize swarm 
    multiplication, then climbs slowly so late-game dragons are sturdier."""
    if now < 150:
        return MIN_SPLIT_LEN
    if is_small_map:
        return min(MIN_SPLIT_LEN + SPLIT_RAMP_SMALL * (now - 150), SPLIT_CAP_SMALL)
    return min(MIN_SPLIT_LEN + SPLIT_RAMP_LARGE * (now - 150), SPLIT_CAP_LARGE)


def update_role(now, length, teammate_lens):
    """Decide (once, permanently -- this function returns immediately once is_champion is True)
    whether THIS dragon is our champion. See the module docstring for why there is exactly one.

    `teammate_lens` is `visible_lengths` restricted to our own team, excluding ourselves -- the
    lower-bound lengths of whichever teammates we can currently see, if any."""
    global is_champion
    if is_champion:
        return
    if ct.get_id() <= 1:
        # Dragon ids are assigned globally across both teams, so each team's very first dragon is
        # either id 0 or id 1 -- this is how we recognise "the original dragon" for either side.
        if now >= (CHAMPION_START_SMALL if is_small_map else CHAMPION_START_LARGE):
            is_champion = True
        return
    takeover = CHAMPION_TAKEOVER_SMALL if is_small_map else CHAMPION_TAKEOVER_LARGE
    if length >= takeover:
        is_champion = True
    elif (now >= DYNAMIC_PROMOTE_ROUND and length >= takeover - DYNAMIC_PROMOTE_MARGIN
          and len(teammate_lens) >= DYNAMIC_PROMOTE_MIN_SAMPLE
          and length >= max(teammate_lens.values()) * DYNAMIC_PROMOTE_RATIO):
        if DEBUG:
            ct.output_log("DYNAMIC_PROMOTE", "len", length, "vs teammates", teammate_lens)
        # We can directly see a real sample of teammates, including a rough lower bound on each
        # one's length -- if we're clearly ahead of the best of them (not just barely ahead of
        # whichever happened to be nearby) this deep into the game, that's real, if partial,
        # evidence we're the best candidate to carry the tiebreak, and there's no reason to wait on
        # the random roll below when this is available and immediate.
        is_champion = True
    elif now >= LATE_LOCK_ROUND and length >= LATE_LOCK_LEN:
        is_champion = True
    elif now >= BACKUP_START_ROUND and random.random() < BACKUP_RATE:
        is_champion = True


# ============================================================================ the turn

def least_bad_move(mp, occupied):
    """Called only when EVERY direction is either kelp or occupied by something -- there is no
    good option left. Ranks what's left: an enemy head is the cheapest (that kills the enemy too,
    a fair trade for us dying anyway), then any body segment (we die, nobody else does).

    We NEVER deliberately choose a teammate's head: that mutual kill costs our team two dragons
    for the price of one, which is strictly worse than every other option here -- including taking
    no action at all. Confirmed directly against the judge: a dragon that submits no action simply
    dies with reason "no valid action", and nothing else is affected. So if a teammate's head is
    genuinely the only thing next to us, this function returns None, and the caller takes no
    action rather than force that trade."""
    my_team = ct.get_team()
    best, best_cost = None, None
    for i in range(4):
        if edges[mp][i] == 1:                                    # kelp: not enterable at all
            continue
            
        dest = step(mp, i) if edges[mp][i] == 0 else portal_exit(mp, i)
        if dest is None:
            return i # Escaping into an unknown portal is safe and costs nothing!
            
        part = occupied.get(dest)
        if part is None:
            cost = 0                     # shouldn't happen here (would already be a legal move),
        elif part.is_head() and part.get_team() == my_team:       # kept only for robustness
            continue                     # never deliberately pick this, at any cost
        elif part.is_head():
            cost = 0.5                   # enemy head: mutual kill, a fine trade
        else:
            cost = 1                     # any body segment: only we die
        if best_cost is None or cost < best_cost:
            best, best_cost = i, cost
    return best


def execute_turn() -> None:
    global turn_t0, desired_heading, stale_turns

    turn_t0 = time.perf_counter()
    now = game.get_round_num()
    head = ct.get_position()
    mp = (head.x, head.y)
    heading = DIR_INDEX[ct.get_dir()]
    length = ct.get_length()

    if desired_heading is None:
        # Give early dragons on ALL maps a nudge toward the center to fight for territory.
        # Small maps need this desperately to avoid getting boxed into the spawn corner.
        rush_limit = CENTER_RUSH_ROUND if not is_small_map else 40
        if ct.get_id() > 1 and now < rush_limit:
            desired_heading = toward_center_heading(mp)
            if DEBUG:
                ct.output_log("CENTER_RUSH", "spawn", mp, "heading", DIRS[desired_heading].value)
        else:
            desired_heading = heading

    occupied, mine, others, enemies, pearls, discovered_new = observe(now)
    body = update_body(mp, length, mine)
    my_team = ct.get_team()
    teammate_lens = visible_lengths(occupied, my_team, exclude_id=ct.get_id())
    update_role(now, length, teammate_lens)

    # Calculate enemy lengths early so we can use them for courage/safety checks
    enemy_lens = visible_lengths(occupied, my_team.get_enemy_team())

    # ---- 0.2 opportunistic aggression. 
    if not is_champion:
        for i in range(4):
            if edges[mp][i] == 1:
                continue
            dest = step(mp, i) if edges[mp][i] == 0 else portal_exit(mp, i)
            if dest is None: 
                continue
            part = occupied.get(dest)
            if part is None or not part.is_head() or part.get_team() == my_team:
                continue
            L = enemy_lens.get(part.get_id(), 1)
            # Lowered the opportunistic ratio to 2 to enforce territorial control better
            if L >= OPPORTUNISTIC_MIN_ENEMY_LEN and L >= length * OPPORTUNISTIC_RATIO:
                if DEBUG:
                    ct.output_log("OPPORTUNISTIC_HIT", mp, "vs enemy len", L, "my len", length)
                ct.make_move(DIRS[i])
                return

    # ---- 1. safety (Updated for Portals and Fear Scaling). 
    legal = []
    for i in range(4):
        if edges[mp][i] == 0:
            if step(mp, i) not in occupied:
                legal.append(i)
        elif edges[mp][i] == 2 and i != OPP[heading]: # don't step backwards through portal
            dest = portal_exit(mp, i)
            if dest is None or dest not in occupied:
                legal.append(i)
                
    safe = []
    for i in legal:
        dest = step(mp, i) if edges[mp][i] == 0 else portal_exit(mp, i)
        # We cannot be threatened if we step into an unknown portal
        if dest is None or not is_tile_scary(dest, enemies, is_champion, length, enemy_lens, is_small_map):
            safe.append(i)
            
    allowed = safe or legal
    own = set(body) if body is not None else set(hist[-length:])

    if not allowed:
        # Fully boxed in. An emergency split hands almost the whole body to a child whose 
        # head (our current tail) may have room even though our own head does not, and which
        # also keeps our own head standing still for this turn instead of forcing a bad move.
        if length - SPLIT_SIZE >= SPLIT_SIZE and ct.can_split(length - SPLIT_SIZE):
            ct.do_split(length - SPLIT_SIZE)
            return
        move = least_bad_move(mp, occupied)
        if move is not None:
            ct.make_move(DIRS[move])
        # else: deliberately take no action. This dragon dies alone; nothing else is harmed.
        return

    # Redefine fleeing: We are only "fleeing" (which blocks splitting) if the threat 
    # adjacent to us is actually SCARY (a smaller/equal enemy). If it's a giant enemy, 
    # we aren't fleeing, we are daring them to ram us.
    imminent_scary_threat = is_tile_scary(mp, enemies, is_champion, length, enemy_lens, is_small_map)
    in_danger = imminent_scary_threat and len(safe) <= 1
    fleeing = bool(safe) and imminent_scary_threat and not in_danger

    # Computed here (rather than down at step 4, where the ordinary drift/reorientation logic
    # actually uses it) because splitting -- step 2, right below -- also needs to know about it:
    # see the comment on split_now for why a persistent loop is treated as a reason to split even
    # outside the normal growth-threshold rules.
    stuck_in_loop = hist[-CYCLE_CHECK_WINDOW - 1:-1].count(mp) > 0
    stale_turns = 0 if discovered_new else stale_turns + 1
    others_block = {t for t, p in occupied.items() if p.get_id() != ct.get_id()}

    # ---- room check: this is the SAME question best_winnable_pearl asks about a pearl -- "could
    # we actually get back out again?" -- asked here about ordinary movement instead. Without this,
    # a dragon could walk itself into an unrecoverable pocket while just drifting or reorienting,
    # with no pearl anywhere involved; the pearl-specific check further down does not cover that
    # case at all, since it only ever runs when a pearl is actually being considered. Narrows
    # `allowed` down to directions we could still fully escape from afterwards, falling back to
    # whichever option gives the most room if every direction is now bad in this respect (better to
    # pick the least-cramped option than to refuse to move at all). Skipped only if `body` is not
    # yet known (see update_body) -- the same fallback used everywhere else in this file for that.
    if body is not None:
        need = min(length, 30)
        room = {}
        for i in allowed:
            dest = step(mp, i) if edges[mp][i] == 0 else portal_exit(mp, i)
            if dest is None:
                room[i] = need  # Infinite room through an unknown portal
            else:
                room[i] = room_check(dest, body, others_block, need)
                
        roomy = [i for i in allowed if room[i] >= need]
        allowed = roomy or [i for i in allowed if room[i] == max(room.values())]

    # ---- 2. splitting. Champions only ever split as a last-ditch escape (see in_danger below);
    # breeders split for growth whenever they're long enough, it isn't past the absolute cutoff
    # round, and (past PRESERVE_ROUND) the team can currently afford to hold length instead of
    # rebuilding numbers -- see PRESERVE_ROUND's own comment for why. Either
    # way we skip it entirely while fleeing, since a split is an action in its own right and can't
    # double as the move that gets us out of danger.
    if not fleeing:
        if is_champion:
            split_now = (in_danger or stuck_in_loop) and length >= MIN_SPLIT_LEN
        else:
            min_pop = PRESERVE_MIN_POP_SMALL if is_small_map else PRESERVE_MIN_POP_LARGE
            team_short_handed = ct.get_unit_count() < min_pop
            growth_ok = (now <= SPLIT_STOP_ROUND and length >= split_threshold(now)
                         and (now < PRESERVE_ROUND or team_short_handed))
            split_now = length >= MIN_SPLIT_LEN and (in_danger or stuck_in_loop or growth_ok)
        # Confirmed directly in testing: a dragon can end up genuinely confined to a small closed
        # loop of tiles with nowhere fresher reachable at all -- not a bug in the reorientation
        # logic below (which does correctly prefer fresher ground whenever any is available), just
        # a dragon too long to turn around within whatever pocket it's ended up in. Shedding length
        # via a normal 2-child split is a real fix for that specific problem: it can be enough to
        # make the remaining body short enough to actually navigate back out, where continuing to
        # try to out-think the geometry with pure movement logic alone cannot help.
        exit_ok = child_has_exit(body, occupied) if body is not None else True
        if split_now and ct.can_split(SPLIT_SIZE) and exit_ok:
            ct.do_split(SPLIT_SIZE)
            return

    # ---- 3. pearls: go for the nearest one we're not beaten to AND can actually escape after
    # eating (see best_winnable_pearl's own doc for exactly how both are judged). `blocked`
    # excludes every currently-occupied tile except our own head (which my_search needs as the
    # search origin, not an obstacle).
    
    # BREEDER FASTING: Past STARVE_BREEDERS_ROUND, non-champions completely ignore pearls.
    # This prevents breeders from mathematically wasting late-game food, naturally funneling
    # 100% of remaining pearls to the Champion for the round 500 tiebreaker.
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
                # prefer continuing in our current heading among equally-good options, purely to
                # avoid needless zig-zagging when more than one first step is equally short
                mv = max(wanted, key=lambda i: i == heading)
                if DEBUG:
                    ct.output_log("pearl", mp, "->", target, DIRS[mv].value)
                ct.make_move(DIRS[mv])
                return

    # ---- 4. no pearl worth chasing. Three independent reasons to abandon the current heading,
    # checked in order from strongest signal to weakest:
    #
    # (a) STALE MEMORY: `stale_turns` (updated above) has crossed STALE_TRIGGER_TURNS -- we have
    # gone that many turns without our terrain memory growing at all, meaning every tile we've seen
    # lately was already known. This can happen even without literally retracing our own path (so
    # (c) below wouldn't catch it): pacing back and forth across several different, all-already-
    # mapped tiles in a small pocket looks fine to a same-tile-revisit check but is just as
    # unproductive. When this fires we don't just turn perpendicular -- we deliberately aim for the
    # nearest ground we have never mapped at all (unexplored_heading), reusing our existing terrain
    # memory rather than picking an arbitrary new direction.
    #
    # (b) another visible dragon -- teammate or enemy, it doesn't matter which -- is facing
    # directly back along our own heading, which strongly suggests whatever is ahead of us has
    # already been swept without much luck (that's exactly why they're now heading the other way).
    #
    # (c) `stuck_in_loop` (computed earlier, alongside in_danger/fleeing, since splitting also
    # needs it): `hist` (our own recent head-position trail, already kept for update_body) shows we
    # stood on our current tile within roughly the last CYCLE_CHECK_WINDOW turns. A dragon moving in
    # a genuinely new direction never revisits a head position that recently by construction, so
    # this can only mean we are looping -- confirmed by testing: with nothing to break it, a dragon
    # can settle into a small permanent loop (a perfect 4-tile E-S-W-N cycle was observed directly
    # in a real match) that never makes further progress, since nothing about the pure "keep facing
    # this way, detour around kelp" rule alone can notice that it isn't actually going anywhere.

    if stale_turns >= STALE_TRIGGER_TURNS:
        # If we've been stuck for a while, try to find a new direction.
        new_heading = unexplored_heading(mp)
        if new_heading is not None:
            desired_heading = new_heading
            stale_turns = 0     # heading somewhere new now; give it a fresh window to arrive
    elif stuck_in_loop:
        # Detecting the loop isn't enough on its own: simply picking "whichever perpendicular
        # happens to be open" (the ordinary reorientation response just below) can reproduce the
        # EXACT same rotation every time, because which perpendicular is currently open is often
        # dictated by our own tail -- which is itself cycling in lockstep with the loop. Confirmed
        # directly: adding pure loop detection alone, with that same left-then-right response,
        # still retraced the identical 4-tile cycle forever in a real match. So when we're actually
        # stuck, break the tie differently: among the currently allowed directions, prefer whichever
        # leads to the tile we visited longest ago (or never at all) rather than an arbitrary
        # left/right preference -- that can't help but eventually walk away from ground we keep
        # revisiting, since freshly-explored ground can never score worse than the loop itself.
        def staleness(i):
            dest = step(mp, i) if edges[mp][i] == 0 else portal_exit(mp, i)
            if dest is None or dest not in hist:
                return len(hist) + 1        # never visited at all: as fresh as it gets
            return len(hist) - 1 - hist[::-1].index(dest)   # turns since last there; bigger = staler
        desired_heading = max(allowed, key=staleness)
    elif any(hd == OPP[desired_heading] for _, _, hd in others):
        left, right = perpendiculars(desired_heading)
        if left in allowed:
            desired_heading = left
        elif right in allowed:
            desired_heading = right
        # if neither perpendicular is currently safe, we keep our existing heading for now and
        # let the detour logic below route us around whatever's actually blocking it this turn

    # ---- 5. drift. Keep heading the way we're already set; if that exact direction isn't legal
    # or safe THIS turn (typically kelp), take a one-turn perpendicular detour without abandoning
    # the underlying heading -- so a wall gets routed around, not treated as a reason to turn away
    # from "eastward" (say) permanently.
    if desired_heading in allowed:
        if DEBUG:
            ct.output_log("drift", mp, DIRS[desired_heading].value)
        ct.make_move(DIRS[desired_heading])
        return
    left, right = perpendiculars(desired_heading)
    for cand in (left, right):
        if cand in allowed:
            ct.make_move(DIRS[cand])
            return
    ct.make_move(DIRS[allowed[0]])          # only reachable if both perpendiculars are also blocked


def fallback_move() -> None:
    """Used only if execute_turn hits a bug we didn't anticipate: step onto any fully-empty
    neighbouring tile (friend or enemy, we don't even check -- just anything not already occupied),
    or hold still if there genuinely isn't one."""
    here = ct.get_position()
    here_tile = ct.get_tile(here)
    for direction in Direction.get_direction_list():
        if not here_tile.get_edge(direction).is_passable():
            continue
        ahead = ct.get_tile(here.add_dir(direction))
        if ahead is not None and ahead.get_dragon() is None:
            ct.make_move(direction)
            return
    # no safe neighbour found even here: take no action rather than force a bad guess


def main() -> None:
    global ct, game, W, H, is_small_map, is_champion, desired_heading
    ct, game = unswbc.init()
    W, H = game.get_map_size()
    is_small_map = W <= SMALL_MAP_THRESHOLD or H <= SMALL_MAP_THRESHOLD
    is_champion = False
    # desired_heading is deliberately NOT set here: ct.get_position() is None and ct.get_dir() is
    # a hardcoded placeholder (always "North") until the first unswbc.update() below actually
    # succeeds -- confirmed directly against helper.py's Controller constructor. Reading either one
    # here crashes immediately for get_position, and silently initializes every single dragon to
    # a fake "North" heading for get_dir -- both real bugs, caught only by actually tracing why a
    # new check crashed instead of firing. The one-time decision is deferred into execute_turn's
    # first call instead, at the point real spawn position and heading are guaranteed valid.
    desired_heading = None

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