"""
Game data for the MVP (Human Warrior, Northshire -> Elwynn Forest -> Westfall).

ID ENCODING (shared contract with server/lua_scripts/archipelago.lua -- keep in sync):
    location_id = LOC_BASE  + kind * KIND_SIZE + game_id
    item_id     = ITEM_BASE + kind * KIND_SIZE + payload

Quests, exploration, rares and spells come from gen_data.py, which
tools/extract_data.py generates from a real AzerothCore world DB.
"""
from typing import Dict, List, NamedTuple, Optional

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


class RegionData(NamedTuple):
    name: str
    key_item: Optional[str]          # item required to enter from the previous region
    levels: range                    # level milestones attributed to this region
    quests: Dict[str, int]           # quest title -> quest id
    explore: Dict[str, int]          # subzone name -> area id
    rares: Dict[str, int]            # creature name -> entry
    trainer: Dict[str, int]          # "Trainer: <spell>" -> spell id, bought at a class trainer
    trainer_levels: Dict[str, int]   # "Trainer: <spell>" -> level it needs


# Linear chain for the MVP; each region connects to the previous one.
REGIONS: List[RegionData] = [
    RegionData(
        name=r["name"],
        key_item=r["key"],
        levels=range(r["levels"][0], r["levels"][1] + 1),
        quests=r["quests"],
        explore=r["explore"],
        rares=r["rares"],
        # A trainer check needs the spell's level: it goes in the region that owns that level.
        trainer={name: spell for name, (spell, level, _cost) in gen_data.TRAINER_CHECKS.items()
                 if r["levels"][0] <= max(level, gen_data.REGIONS[0]["levels"][0]) <= r["levels"][1]},
        trainer_levels={name: level for name, (_spell, level, _cost) in gen_data.TRAINER_CHECKS.items()},
    )
    for r in gen_data.REGIONS
]
assert sum(len(r.trainer) for r in REGIONS) == len(gen_data.TRAINER_CHECKS), "trainer check outside regions"


MAX_LEVEL: int = gen_data.MAX_LEVEL

# Zone keys: item name -> zone id
ZONE_KEYS: Dict[str, int] = dict(gen_data.ZONE_KEYS)

# Spells in the pool: item name -> spell id
SPELLS: Dict[str, int] = {name: spell for name, (spell, _level) in gen_data.SPELLS.items()}

# Quest reward gear (taken off the quests, shuffled into the pool): name -> (item entry, copies)
GEAR: Dict[str, tuple] = {name: (entry, copies) for name, (entry, copies, _req) in gen_data.GEAR.items()}

# Filler: name -> item id
FILLER_ITEMS: Dict[str, int] = {
    "Item: Minor Healing Potion": item_id(ItemKind.GAME_ITEM, 118),
    "Item: Linen Bandage": item_id(ItemKind.GAME_ITEM, 1251),
    "Money: 25 Silver": item_id(ItemKind.MONEY, 2500),
    "Money: 1 Gold": item_id(ItemKind.MONEY, 10000),
}


# Random filler items: any obtainable item without a level requirement (gear capped at a low
# item level by the extractor). name -> item entry, minus anything already an item above.
_taken_entries = {entry for entry, _ in GEAR.values()} | {
    i - item_id(ItemKind.GAME_ITEM, 0) for i in FILLER_ITEMS.values()
    if ITEM_BASE + ItemKind.GAME_ITEM * KIND_SIZE <= i < ITEM_BASE + (ItemKind.GAME_ITEM + 1) * KIND_SIZE}
RANDOM_ITEMS: Dict[str, int] = {}
RANDOM_ITEMS_BY_CATEGORY: Dict[str, List[str]] = {}
for _name, (_entry, _category) in gen_data.RANDOM_ITEMS.items():
    if _entry in _taken_entries or _name in FILLER_ITEMS or _name in GEAR:
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
    if _entry in _taken_entries or _name in FILLER_ITEMS or _name in GEAR or _name in RANDOM_ITEMS:
        continue
    END_GAME_GEAR[_name] = _entry
    END_GAME_BY_SLOT.setdefault(_slot, []).append(_name)


def location_name(kind: int, name: str) -> str:
    prefix = {
        LocKind.QUEST: "Quest",
        LocKind.EXPLORE: "Explore",
        LocKind.RARE: "Rare",
    }[kind]
    return f"{prefix}: {name}"


# The last levels of a region are a grind with that region's mobs alone.
GRIND_LEVELS = 2


def is_grindy_level(region: RegionData, level: int) -> bool:
    return level > region.levels[-1] - GRIND_LEVELS


def level_location_name(level: int) -> str:
    return f"Reach Level {level}"
