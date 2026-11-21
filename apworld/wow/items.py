from typing import Dict

from BaseClasses import Item, ItemClassification

from .data import FILLER_ITEMS, SPELLS, ZONE_KEYS, ItemKind, item_id


class WoWItem(Item):
    game = "World of Warcraft"


ITEM_TABLE: Dict[str, int] = {}
ITEM_CLASSIFICATION: Dict[str, ItemClassification] = {}

for _name, _zone in ZONE_KEYS.items():
    ITEM_TABLE[_name] = item_id(ItemKind.ZONE, _zone)
    ITEM_CLASSIFICATION[_name] = ItemClassification.progression

for _name, _spell in SPELLS.items():
    ITEM_TABLE[_name] = item_id(ItemKind.SPELL, _spell)
    ITEM_CLASSIFICATION[_name] = ItemClassification.useful

for _name, _id in FILLER_ITEMS.items():
    ITEM_TABLE[_name] = _id
    ITEM_CLASSIFICATION[_name] = ItemClassification.filler

ITEM_GROUPS = {
    "Zones": set(ZONE_KEYS),
    "Spells": set(SPELLS),
}
