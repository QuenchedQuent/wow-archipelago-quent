"""
Game data: Northshire -> Elwynn Forest -> Westfall, for any Alliance race and any class but
the Death Knight. A slot's quests, spells and trainer checks depend on its race and class.

ID ENCODING (shared contract with server/lua_scripts/archipelago.lua -- keep in sync):
    location_id = LOC_BASE  + kind * KIND_SIZE + game_id
    item_id     = ITEM_BASE + kind * KIND_SIZE + payload

Quests, exploration, rares, spells and gear come from gen_data.py, which
tools/extract_data.py generates from a real AzerothCore world DB.
"""
from typing import Dict, List, NamedTuple, Optional, Tuple

from . import gen_data

GAME = "World of Warcraft"

LOC_BASE = 870_000_000
ITEM_BASE = 880_000_000
KIND_SIZE = 1_000_000


class LocKind:
    QUEST = 0
    EXPLORE = 1    # AreaTable id
    LEVEL = 2      # character level
    RARE = 3       # creature_template entry
    VENDOR = 4     # (later)
    TRAINER = 5    # spell id the check is sold in place of


class ItemKind:
    SPELL = 0      # payload = spell id
    ZONE = 1       # payload = zone (AreaTable) id
    GAME_ITEM = 2  # payload = item_template entry
    MONEY = 3      # payload = copper
    FLIGHT = 4     # (later)


def loc_id(kind: int, game_id: int) -> int:
    return LOC_BASE + kind * KIND_SIZE + game_id


def item_id(kind: int, payload: int) -> int:
    return ITEM_BASE + kind * KIND_SIZE + payload


# --- characters ----------------------------------------------------------------------------

RACES: Dict[int, str] = dict(gen_data.RACES)
CLASSES: Dict[int, str] = dict(gen_data.CLASSES)

# Race/class combinations that exist in 3.3.5 (Death Knights aside).
VALID_CLASSES: Dict[int, Tuple[int, ...]] = {
    1: (1, 2, 4, 5, 8, 9),       # Human: warrior, paladin, rogue, priest, mage, warlock
    3: (1, 2, 3, 4, 5),          # Dwarf: warrior, paladin, hunter, rogue, priest
    4: (1, 3, 4, 5, 11),         # Night Elf: warrior, hunter, rogue, priest, druid
    7: (1, 4, 8, 9),             # Gnome: warrior, rogue, mage, warlock
    11: (1, 2, 3, 5, 7, 8),      # Draenei: warrior, paladin, hunter, priest, shaman, mage
}


# --- regions -------------------------------------------------------------------------------

class RegionData(NamedTuple):
    name: str
    key_item: Optional[str]          # item required to enter from the previous region
    levels: range                    # level milestones attributed to this region
    explore: Dict[str, int]          # subzone name -> area id
    rares: Dict[str, int]            # creature name -> entry


# Linear chain; each region connects to the previous one.
REGIONS: List[RegionData] = [
    RegionData(
        name=r["name"],
        key_item=r["key"],
        levels=range(r["levels"][0], r["levels"][1] + 1),
        explore=r["explore"],
        rares=r["rares"],
    )
    for r in gen_data.REGIONS
]

MAX_LEVEL: int = gen_data.MAX_LEVEL


def level_region(level: int) -> int:
    """Index of the region that owns `level` (below the first region's levels: the first)."""
    for i, r in enumerate(REGIONS):
        if level <= r.levels[-1]:
            return i
    return len(REGIONS) - 1


# The last levels of a region are a grind with that region's mobs alone.
GRIND_LEVELS = 2


def is_grindy_level(region: RegionData, level: int) -> bool:
    return level > region.levels[-1] - GRIND_LEVELS


# --- quests --------------------------------------------------------------------------------

class QuestData(NamedTuple):
    name: str        # location name without the "Quest: " prefix
    id: int
    region: int
    races: int       # race mask, 0 = any
    classes: int     # class mask, 0 = any
    prev: int        # prerequisite quest id, 0 = none
    group: int       # exclusive group, 0 = none


QUESTS: List[QuestData] = [QuestData(**q) for q in gen_data.QUESTS]
QUESTS_BY_ID: Dict[int, QuestData] = {q.id: q for q in QUESTS}


def slot_quests(race: int, cls: int) -> List[Tuple[QuestData, int]]:
    """(quest, region) for each quest location of a character. A quest counts when its
    race/class masks allow the character and its prerequisite is available (any member of the
    prerequisite's exclusive group will do, as in the core). The members of an exclusive group
    the character can take are one location (lowest id) in the earliest member's region."""
    race_bit, class_bit = 1 << (race - 1), 1 << (cls - 1)
    fits = {q.id for q in QUESTS
            if (not q.races or q.races & race_bit) and (not q.classes or q.classes & class_bit)}
    members: Dict[int, List[int]] = {}
    for q in QUESTS:
        if q.group:
            members.setdefault(q.group, []).append(q.id)

    taken: set = set()
    changed = True
    while changed:
        changed = False
        for qid in sorted(fits - taken):
            prev = QUESTS_BY_ID[qid].prev
            prev_group = QUESTS_BY_ID[prev].group if prev in QUESTS_BY_ID else 0
            if not prev or prev in taken or (prev_group and any(m in taken for m in members[prev_group])):
                taken.add(qid)
                changed = True

    out: List[Tuple[QuestData, int]] = []
    done_groups = set()
    for qid in sorted(taken):
        q = QUESTS_BY_ID[qid]
        if q.group:
            if q.group in done_groups:
                continue
            done_groups.add(q.group)
            mine = [QUESTS_BY_ID[m] for m in members[q.group] if m in taken]
            out.append((min(mine, key=lambda m: m.id), min(m.region for m in mine)))
        else:
            out.append((q, q.region))
    return out


# --- items ---------------------------------------------------------------------------------

# Zone keys: item name -> zone id
ZONE_KEYS: Dict[str, int] = dict(gen_data.ZONE_KEYS)

# Spell items (every class): item name -> spell id
SPELLS: Dict[str, int] = dict(gen_data.SPELLS)
# A class's spell pool: class -> {item name: level}
CLASS_SPELLS: Dict[int, Dict[str, int]] = {c: dict(s) for c, s in gen_data.CLASS_SPELLS.items()}

# Trainer checks: class -> {location name: (spell id, level, copper)}; only classes with a
# trainer in our zones. TRAINER_REGION: the earliest region with one of its trainers.
TRAINER_CHECKS: Dict[int, Dict[str, Tuple[int, int, int]]] = {
    c: dict(t) for c, t in gen_data.TRAINER_CHECKS.items()}
TRAINER_REGION: Dict[int, int] = dict(gen_data.TRAINER_REGION)


def trainer_locations(cls: int) -> List[Tuple[str, int, int, int]]:
    """(location name, spell id, level, region) for a class's trainer checks: in the region
    owning the spell's level, or the first region with a trainer of that class if later."""
    if cls not in TRAINER_CHECKS:
        return []
    first = TRAINER_REGION[cls]
    return [(name, spell, level, max(level_region(level), first))
            for name, (spell, level, _cost) in TRAINER_CHECKS[cls].items()]


# Quest reward gear (taken off the quests, shuffled into the pool): name -> item entry, and
# quest -> (fixed gear, choose-one gear). A slot gets the fixed gear and one random choice of
# each quest that is one of its locations.
GEAR_ITEMS: Dict[str, int] = dict(gen_data.GEAR_ITEMS)
GEAR_BY_QUEST: Dict[int, Tuple[List[str], List[str]]] = {
    q: (list(fixed), list(choice)) for q, (fixed, choice) in gen_data.GEAR_BY_QUEST.items()}

# Filler: name -> item id
FILLER_ITEMS: Dict[str, int] = {
    "Item: Minor Healing Potion": item_id(ItemKind.GAME_ITEM, 118),
    "Item: Linen Bandage": item_id(ItemKind.GAME_ITEM, 1251),
    "Money: 25 Silver": item_id(ItemKind.MONEY, 2500),
    "Money: 1 Gold": item_id(ItemKind.MONEY, 10000),
}

# Random filler items: any obtainable item without a level requirement (gear capped at a low
# item level by the extractor). name -> item entry, minus anything already an item above.
_taken_entries = set(GEAR_ITEMS.values()) | {
    i - item_id(ItemKind.GAME_ITEM, 0) for i in FILLER_ITEMS.values()
    if ITEM_BASE + ItemKind.GAME_ITEM * KIND_SIZE <= i < ITEM_BASE + (ItemKind.GAME_ITEM + 1) * KIND_SIZE}
RANDOM_ITEMS: Dict[str, int] = {}
RANDOM_ITEMS_BY_CATEGORY: Dict[str, List[str]] = {}
for _name, (_entry, _category) in gen_data.RANDOM_ITEMS.items():
    if _entry in _taken_entries or _name in FILLER_ITEMS or _name in GEAR_ITEMS:
        continue
    RANDOM_ITEMS[_name] = _entry
    RANDOM_ITEMS_BY_CATEGORY.setdefault(_category, []).append(_name)

# How a random filler item is picked: a category by weight, then an item in it. Recipes are
# about half the pool, so a plain uniform pick would mostly hand out end-game recipes.
RANDOM_CATEGORY_WEIGHTS: Dict[str, int] = {
    "gear": 55, "consumable": 20, "trade goods": 10, "other": 10, "recipe": 5,
}

# End-game gear: one random piece per slot goes into each seed's pool. name -> item entry.
END_GAME_GEAR: Dict[str, int] = {}
END_GAME_BY_SLOT: Dict[str, List[str]] = {}
for _name, (_entry, _slot) in gen_data.END_GAME_GEAR.items():
    if _entry in _taken_entries or _name in FILLER_ITEMS or _name in GEAR_ITEMS or _name in RANDOM_ITEMS:
        continue
    END_GAME_GEAR[_name] = _entry
    END_GAME_BY_SLOT.setdefault(_slot, []).append(_name)


# --- names ---------------------------------------------------------------------------------

def location_name(kind: int, name: str) -> str:
    prefix = {
        LocKind.QUEST: "Quest",
        LocKind.EXPLORE: "Explore",
        LocKind.RARE: "Rare",
    }[kind]
    return f"{prefix}: {name}"


def level_location_name(level: int) -> str:
    return f"Reach Level {level}"
