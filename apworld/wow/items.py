from typing import Dict

from BaseClasses import Item, ItemClassification

from .data import END_GAME_GEAR, FILLER_ITEMS, GEAR_ITEMS, RANDOM_ITEMS, SPELLS, ZONE_KEYS, ItemKind, item_id


class WoWItem(Item):
    game = "World of Warcraft"


# Every item any slot can have (all classes' spells, all gear) for the datapackage.
ITEM_TABLE: Dict[str, int] = {}
ITEM_CLASSIFICATION: Dict[str, ItemClassification] = {}


def _add(name: str, iid: int, classification: ItemClassification) -> None:
    assert name not in ITEM_TABLE, f"item name {name} used twice"
    ITEM_TABLE[name] = iid
    ITEM_CLASSIFICATION[name] = classification


for _name, _zone in ZONE_KEYS.items():
    _add(_name, item_id(ItemKind.ZONE, _zone), ItemClassification.progression)
for _name, _spell in SPELLS.items():
    _add(_name, item_id(ItemKind.SPELL, _spell), ItemClassification.useful)
for _name, _entry in GEAR_ITEMS.items():
    _add(_name, item_id(ItemKind.GAME_ITEM, _entry), ItemClassification.useful)
for _name, _id in FILLER_ITEMS.items():
    _add(_name, _id, ItemClassification.filler)
for _name, _entry in RANDOM_ITEMS.items():
    _add(_name, item_id(ItemKind.GAME_ITEM, _entry), ItemClassification.filler)
for _name, _entry in END_GAME_GEAR.items():
    _add(_name, item_id(ItemKind.GAME_ITEM, _entry), ItemClassification.useful)

assert len(set(ITEM_TABLE.values())) == len(ITEM_TABLE), "duplicate item id"

ITEM_GROUPS = {
    "Zones": set(ZONE_KEYS),
    "Spells": set(SPELLS),
    "Gear": set(GEAR_ITEMS),
    "Random Items": set(RANDOM_ITEMS),
    "End-Game Gear": set(END_GAME_GEAR),
}
