"""
Static game data for the MVP (Human Warrior, Northshire -> Elwynn Forest -> Westfall).

ID ENCODING (shared contract with server/lua_scripts/archipelago.lua -- keep in sync):
    location_id = LOC_BASE  + kind * KIND_SIZE + game_id
    item_id     = ITEM_BASE + kind * KIND_SIZE + payload

Hand-curated for the loop proof. All IDs below are from memory of the 3.3.5 DB and
MUST be verified against the AzerothCore world DB; tools/extract_data.py will
eventually generate this data instead.
"""
from typing import Dict, List, NamedTuple, Optional

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
        name="Northshire Valley",
        key_item=None,
        levels=range(2, 7),
        quests={
            "A Threat Within": 783,
            "Kobold Camp Cleanup": 7,
            "Eagan Peltskinner": 5261,
            "Wolves Across the Border": 33,
            "Investigate Echo Ridge": 15,
            "Skirmish at Echo Ridge": 21,
            "Brotherhood of Thieves": 18,
            "Milly Osworth": 3903,
            "Milly's Harvest": 3904,
            "Grape Manifest": 3905,
            "Bounty on Garrick Padfoot": 6,
            "Report to Goldshire": 54,
            "Simple Letter": 3100,
        },
        explore={
            "Echo Ridge Mine": 34,
            "Northshire Vineyards": 59,
        },
        rares={},
    ),
    RegionData(
        name="Elwynn Forest",
        key_item="Zone: Elwynn Forest",
        levels=range(7, 13),
        quests={
            "The Fargodeep Mine": 62,
            "Gold Dust Exchange": 47,
            "A Fishy Peril": 40,
            "Further Concerns": 35,
            "Find the Lost Guards": 37,
            "Protect the Frontier": 52,
            "Kobold Candles": 60,
            "Shipment to Stormwind": 61,
            "Young Lovers": 106,
            "Speak with Gramma": 111,
            "Note to William": 107,
            "Lost Necklace": 85,
            "Pie for Billy": 86,
            "Back to Billy": 84,
            "Goldtooth": 87,
            "Princess Must Die!": 88,
            "The Jasperlode Mine": 76,
            "Westbrook Garrison Needs Help!": 239,
            "Riverpaw Gnoll Bounty": 11,
            "Wanted: \"Hogger\"": 176,
            "Rest and Relaxation": 2158,
            "Bounty on Murlocs": 46,
            "Red Linen Goods": 83,
            "Report to Gryan Stoutmantle": 109,
        },
        explore={
            "Goldshire": 87,
            "Fargodeep Mine": 57,
            "Jasperlode Mine": 54,
            "Eastvale Logging Camp": 86,
        },
        rares={
            "Mother Fang": 471,
            "Gruff Swiftbite": 100,
            "Fedfennel": 472,
            "Morgaine the Sly": 99,
        },
    ),
    RegionData(
        name="Westfall",
        key_item="Zone: Westfall",
        levels=range(13, 21),
        quests={
            "Westfall Stew": 36,
            "Goretusk Liver Pie": 22,
            "The Killing Fields": 9,
            "The People's Militia": 12,
            "The Forgotten Heirloom": 64,
            "Poor Old Blanchy": 151,
            "Red Leather Bandanas": 153,
            "Patrolling Westfall": 102,
            "Furlbrow's Deed": 184,
        },
        explore={
            "Sentinel Hill": 108,
            "Moonbrook": 20,
            "Jangolode Mine": 111,
            "Gold Coast Quarry": 113,
        },
        rares={},
    ),
]

# Zone keys: item name -> zone id (zone ids: Elwynn 12, Westfall 40)
ZONE_KEYS: Dict[str, int] = {
    "Zone: Elwynn Forest": 12,
    "Zone: Westfall": 40,
}

# Warrior spells in the pool: name -> spell id (3.3.5)
SPELLS: Dict[str, int] = {
    "Spell: Charge": 100,
    "Spell: Rend": 772,
    "Spell: Thunder Clap": 6343,
    "Spell: Hamstring": 1715,
    "Spell: Bloodrage": 2687,
    "Spell: Overpower": 7384,
    "Spell: Battle Shout": 6673,
    "Spell: Heroic Strike (Rank 2)": 284,
    "Spell: Sunder Armor": 7386,
    "Spell: Defensive Stance": 71,
    "Spell: Taunt": 355,
}

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
