from typing import Dict, List, Tuple

from BaseClasses import Location

from .data import REGIONS, LocKind, RegionData, level_location_name, loc_id, location_name


class WoWLocation(Location):
    game = "World of Warcraft"


def region_locations(region: RegionData, include_explore: bool, include_rares: bool) -> List[Tuple[str, int]]:
    """(name, id) pairs for the locations in a region, honoring the options."""
    out: List[Tuple[str, int]] = []
    for title, qid in region.quests.items():
        out.append((location_name(LocKind.QUEST, title), loc_id(LocKind.QUEST, qid)))
    for lvl in region.levels:
        out.append((level_location_name(lvl), loc_id(LocKind.LEVEL, lvl)))
    if include_explore:
        for area, aid in region.explore.items():
            out.append((location_name(LocKind.EXPLORE, area), loc_id(LocKind.EXPLORE, aid)))
    if include_rares:
        for mob, entry in region.rares.items():
            out.append((location_name(LocKind.RARE, mob), loc_id(LocKind.RARE, entry)))
    return out


# Full table (all options on) for the datapackage.
LOCATION_TABLE: Dict[str, int] = {
    name: lid for region in REGIONS for name, lid in region_locations(region, True, True)
}

assert len(set(LOCATION_TABLE.values())) == len(LOCATION_TABLE), "duplicate location id"
assert len(LOCATION_TABLE) == sum(len(region_locations(r, True, True)) for r in REGIONS), \
    "duplicate location name"
