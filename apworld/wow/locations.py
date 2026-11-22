from typing import Dict, List, Tuple

from BaseClasses import Location

from .data import (QUESTS, REGIONS, TRAINER_CHECKS, LocKind, RegionData, level_location_name, loc_id,
                   location_name, slot_quests, trainer_locations)


class WoWLocation(Location):
    game = "World of Warcraft"


def region_locations(region: RegionData, include_explore: bool, include_rares: bool) -> List[Tuple[str, int]]:
    """(name, id) pairs for a region's locations that don't depend on race or class."""
    out: List[Tuple[str, int]] = [(level_location_name(lvl), loc_id(LocKind.LEVEL, lvl)) for lvl in region.levels]
    if include_explore:
        for area, aid in region.explore.items():
            out.append((location_name(LocKind.EXPLORE, area), loc_id(LocKind.EXPLORE, aid)))
    if include_rares:
        for mob, entry in region.rares.items():
            out.append((location_name(LocKind.RARE, mob), loc_id(LocKind.RARE, entry)))
    return out


def slot_locations(race: int, cls: int, include_explore: bool, include_rares: bool,
                   include_trainer: bool) -> List[List[Tuple[str, int]]]:
    """Per region, the (name, id) locations of a character, honoring the options."""
    out = [region_locations(r, include_explore, include_rares) for r in REGIONS]
    for q, region in slot_quests(race, cls):
        out[region].append((location_name(LocKind.QUEST, q.name), loc_id(LocKind.QUEST, q.id)))
    if include_trainer:
        for name, spell, _level, region in trainer_locations(cls):
            out[region].append((name, loc_id(LocKind.TRAINER, spell)))
    return out


# Full table (every race, class and option) for the datapackage.
LOCATION_TABLE: Dict[str, int] = {}


def _add(name: str, lid: int) -> None:
    assert LOCATION_TABLE.get(name, lid) == lid, f"location name {name} has two ids"
    LOCATION_TABLE[name] = lid


for _r in REGIONS:
    for _n, _i in region_locations(_r, True, True):
        _add(_n, _i)
for _q in QUESTS:
    _add(location_name(LocKind.QUEST, _q.name), loc_id(LocKind.QUEST, _q.id))
for _checks in TRAINER_CHECKS.values():
    for _n, (_spell, _level, _cost) in _checks.items():
        _add(_n, loc_id(LocKind.TRAINER, _spell))

assert len(set(LOCATION_TABLE.values())) == len(LOCATION_TABLE), "location id with two names"
