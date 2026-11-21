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
    TRAINER = 5    # (later)


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


# Linear chain for the MVP; each region connects to the previous one.
REGIONS: List[RegionData] = [
    RegionData(
        name=r["name"],
        key_item=r["key"],
        levels=range(r["levels"][0], r["levels"][1] + 1),
        quests=r["quests"],
        explore=r["explore"],
        rares=r["rares"],
    )
    for r in gen_data.REGIONS
]

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


def location_name(kind: int, name: str) -> str:
    prefix = {
        LocKind.QUEST: "Quest",
        LocKind.EXPLORE: "Explore",
        LocKind.RARE: "Rare",
    }[kind]
    return f"{prefix}: {name}"


def level_location_name(level: int) -> str:
    return f"Reach Level {level}"
