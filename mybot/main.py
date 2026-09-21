import helper as unswbc
from helper import Direction, EdgeType
import random

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
            ct.get_length() >= 5
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

    ct.make_move(Direction.NORTH)

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
