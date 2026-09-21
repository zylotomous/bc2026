import helper as unswbc
from helper import Direction, EdgeType
import random
from collections import deque

ct: unswbc.Controller
game: unswbc.Game

is_queen = False
pearls_eaten = 0
previous_length = 0
next_queen_split = 5
tracked_enemy_id = None
drift_direction = None

# Seed so we get the same random generator every time.
random.seed(0)

def _bfs_next_step(target):
    """Find the first step of a shortest path to target within vision,
    treating kelp and other dragons' bodies as obstacles. Returns a
    Direction, or None if target is unreachable within the visible
    window."""
    here = ct.get_position()
    if target is None or here == target:
        return None

    visited = {here}
    queue = deque()

    here_tile = ct.get_tile(here)
    for direction in Direction.get_direction_list():
        if here_tile.get_edge(direction).get_edge_type() == EdgeType.KELP:
            continue
        nxt = here.add_dir(direction)
        nxt_tile = ct.get_tile(nxt)
        if nxt_tile is None:
            continue
        occupant = nxt_tile.get_dragon()
        if occupant is not None and nxt != target:
            continue
        if nxt in visited:
            continue
        visited.add(nxt)
        if nxt == target:
            return direction
        queue.append((nxt, direction))

    while queue:
        pos, first_dir = queue.popleft()
        tile = ct.get_tile(pos)
        if tile is None:
            continue
        for direction in Direction.get_direction_list():
            if tile.get_edge(direction).get_edge_type() == EdgeType.KELP:
                continue
            nxt = pos.add_dir(direction)
            if nxt in visited:
                continue
            nxt_tile = ct.get_tile(nxt)
            if nxt_tile is None:
                continue
            occupant = nxt_tile.get_dragon()
            if occupant is not None and nxt != target:
                continue
            visited.add(nxt)
            if nxt == target:
                return first_dir
            queue.append((nxt, first_dir))

    return None

def _safest_fallback():
    """When no target-driven candidate exists, pick any direction that
    isn't kelp and isn't occupied. This is the last resort and must
    never skip safety checks."""
    here = ct.get_position()
    here_tile = ct.get_tile(here)
    for direction in Direction.get_direction_list():
        if here_tile.get_edge(direction).get_edge_type() == EdgeType.KELP:
            continue
        ahead = ct.get_tile(here.add_dir(direction))
        if ahead is not None and ahead.get_dragon() is not None:
            continue
        return direction
    return Direction.NORTH  # truly no safe option; death is unavoidable


def _is_trapped(here, here_tile):
    """True if every direction is blocked by kelp or an occupied tile."""
    for direction in Direction.get_direction_list():
        if here_tile.get_edge(direction).get_edge_type() == EdgeType.KELP:
            continue
        ahead = ct.get_tile(here.add_dir(direction))
        if ahead is not None and ahead.get_dragon() is None:
            return False
    return True

def _distance(first: unswbc.Position, second: unswbc.Position) -> int:
    """Return the shortest wrapped distance between two positions."""
    horizontal = abs(first.x - second.x)
    vertical = abs(first.y - second.y)
    horizontal = min(horizontal, game.width - horizontal)
    vertical = min(vertical, game.height - vertical)
    return horizontal + vertical


def _visible_target(target_type: str, dragon_id: int):
    """Find the nearest pearl or enemy dragon currently in vision."""
    here = ct.get_position()
    target = None
    target_distance = None

    for tile in ct.get_tiles():
        candidate = tile.get_dragon()
        if target_type == "pearl":
            if not tile.has_pearl():
                continue
            position = tile.get_position()
        else:
            if candidate is None or candidate.get_id() == dragon_id:
                continue
            if candidate.get_team() == ct.get_team():
                continue
            if not candidate.is_head():
                continue
            position = candidate.get_position()

        distance = _distance(here, position)
        if target_distance is None or distance < target_distance:
            target = position
            target_distance = distance

    return target


def _visible_enemy(dragon_id: int, enemy_id: int | None):
    """Find a visible part of a tracked enemy, or the nearest enemy part."""
    here = ct.get_position()
    target = None
    target_distance = None
    found_id = enemy_id

    for tile in ct.get_tiles():
        candidate = tile.get_dragon()
        if candidate is None or candidate.get_id() == dragon_id:
            continue
        if candidate.get_team() == ct.get_team():
            continue
        if enemy_id is not None and candidate.get_id() != enemy_id:
            continue

        position = candidate.get_position()
        distance = _distance(here, position)
        if target_distance is None or distance < target_distance:
            target = position
            target_distance = distance
            found_id = candidate.get_id()

    return found_id, target

def execute_turn() -> None:
    """Apply the dragon's role rules, then seek its current target."""
    global is_queen, pearls_eaten, previous_length, next_queen_split
    global tracked_enemy_id, drift_direction

    if ct.get_length() > previous_length:
        pearls_eaten += ct.get_length() - previous_length
    previous_length = ct.get_length()

    if not is_queen and random.random() < 0.10:
        is_queen = True

    if is_queen:
        can_split = (
            ct.get_length() >= 4
            and pearls_eaten >= next_queen_split
            and ct.can_split(2)
        )
    else:
        can_split = ct.can_split(2)

    if can_split:
        ct.do_split(2)
        if is_queen:
            next_queen_split = pearls_eaten + 2
        return

    dragon_id = ct.get_id()
    here = ct.get_position()
    here_tile = ct.get_tile(here)

    target_type = "pearl"
    target = _visible_target(target_type, dragon_id)
    if not is_queen:
        tracked_enemy_id, enemy_target = _visible_enemy(dragon_id, tracked_enemy_id)
        if enemy_target is not None:
            target_type = "dragon"
            target = enemy_target
        else:
            tracked_enemy_id = None

    # Try BFS pathfinding to the target first.
    if target is not None:
        step = _bfs_next_step(target)
        if step is not None:
            # For an enemy-head target, make sure the step is actually
            # legal (BFS treats the target tile as passable even though
            # it holds a dragon, which is fine only when it's the head
            # we're deliberately colliding with).
            if target_type == "dragon":
                ahead = ct.get_tile(here.add_dir(step))
                ahead_dragon = ahead.get_dragon() if ahead else None
                is_target_head = (
                    ahead_dragon is not None
                    and ahead.get_position() == target
                    and ahead_dragon.get_team() != ct.get_team()
                    and ahead_dragon.get_id() == tracked_enemy_id
                    and ahead_dragon.is_head()
                )
                if ahead_dragon is not None and not is_target_head:
                    step = None
            if step is not None:
                ct.make_move(step)
                return

    # BFS found nothing usable (no target, target out of vision, or
    # unreachable). Fall back to the old greedy scan, which also
    # handles the "drift in a straight line" wandering behaviour.
    directions = Direction.get_direction_list()
    random.shuffle(directions)
    candidates = []

    for direction in directions:
        edge = here_tile.get_edge(direction).get_edge_type()
        if edge == EdgeType.KELP:
            continue

        ahead = ct.get_tile(here.add_dir(direction))
        if ahead is None:
            continue
        ahead_dragon = ahead.get_dragon()
        if ahead_dragon is not None:
            is_target_head = (
                target_type == "dragon"
                and target is not None
                and ahead.get_position() == target
                and ahead_dragon.get_team() != ct.get_team()
                and ahead_dragon.get_id() == tracked_enemy_id
                and ahead_dragon.is_head()
            )
            if not is_target_head:
                continue

        distance = _distance(ahead.get_position(), target) if target is not None else 0
        candidates.append((distance, direction))

    if candidates:
        if target is not None:
            _, direction = min(candidates, key=lambda candidate: candidate[0])
        else:
            available_directions = {candidate[1] for candidate in candidates}
            if drift_direction not in available_directions:
                _, drift_direction = candidates[0]
            direction = drift_direction
        ct.make_move(direction)
        return

    if _is_trapped(here, here_tile):
        max_child = ct.get_length() - 2
        if max_child >= 2 and ct.can_split(max_child):
            ct.do_split(max_child)
            return

    ct.make_move(_safest_fallback())

def main() -> None:
    global ct, game, is_queen, previous_length, drift_direction
    ct, game = unswbc.init()
    is_queen = ct.get_id() == 0
    previous_length = ct.get_length()
    drift_direction = random.choice(Direction.get_direction_list())

    while unswbc.update(ct, game):
        execute_turn()
        unswbc.end_turn()

if __name__ == "__main__":
    main()
