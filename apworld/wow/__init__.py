from typing import Any, ClassVar, Dict

import settings
from BaseClasses import ItemClassification, LocationProgressType, Region, Tutorial
from worlds.AutoWorld import WebWorld, World
from worlds.LauncherComponents import Component, Type, components, launch_subprocess

try:
    from Options import OptionError
except ImportError:  # older Archipelago
    OptionError = Exception

from .data import (CLASS_SPELLS, CLASSES, END_GAME_BY_SLOT, FILLER_ITEMS, GAME, GEAR_BY_QUEST, RACES,
                   RANDOM_CATEGORY_WEIGHTS, RANDOM_ITEMS_BY_CATEGORY, REGIONS, VALID_CLASSES, ZONE_KEYS,
                   is_grindy_level, level_location_name, slot_quests, trainer_locations)
from .items import ITEM_CLASSIFICATION, ITEM_GROUPS, ITEM_TABLE, WoWItem
from .locations import LOCATION_TABLE, WoWLocation, slot_locations
from .options import WoWOptions, ZoneKeyPlacement


def launch_client(*args: str) -> None:
    from .client import launch
    launch_subprocess(launch, name="WoWClient", args=args)


components.append(Component(
    "World of Warcraft Client", func=launch_client, component_type=Type.CLIENT, game_name=GAME,
    description="Links an Archipelago slot to your local WoW server.",
))


class WoWSettings(settings.Group):
    class DatabaseHost(str):
        """MySQL host of your WoW server."""

    class DatabasePort(int):
        """MySQL port. Windows server package: 3307. Docker setup: 3306."""

    class DatabaseUser(str):
        """MySQL user. Windows server package: acore. Docker setup: root."""

    class DatabasePassword(str):
        """MySQL password. Windows server package: acore. Docker setup: password."""

    class CharactersDatabase(str):
        """Name of the AzerothCore characters database."""

    class CharacterName(str):
        """Optional WoW character to link. Leave empty to auto-link the fresh character you are logged in with."""

    db_host: DatabaseHost = DatabaseHost("127.0.0.1")
    db_port: DatabasePort | int = 3307
    db_user: DatabaseUser = DatabaseUser("acore")
    db_password: DatabasePassword = DatabasePassword("acore")
    characters_db: CharactersDatabase = CharactersDatabase("acore_characters")
    character_name: CharacterName = CharacterName("")


class WoWWeb(WebWorld):
    theme = "grass"
    tutorials = [Tutorial(
        "Setup Guide",
        "Setting up the WoW private server, bridge and addon.",
        "English",
        "setup_en.md",
        "setup/en",
        ["you"],
    )]


class WoWWorld(World):
    """
    World of Warcraft on a 3.3.5 AzerothCore private server, for any Alliance race and class.
    Quests, levels, exploration, rare kills and class trainer purchases are checks; your
    class's spells, zone access and gear are items.
    """
    game = GAME
    web = WoWWeb()
    settings_key: ClassVar[str] = "wow_options"
    settings: ClassVar[WoWSettings]
    options_dataclass = WoWOptions
    options: WoWOptions
    topology_present = True

    item_name_to_id = ITEM_TABLE
    location_name_to_id = LOCATION_TABLE
    item_name_groups = ITEM_GROUPS

    race: int
    cls: int

    def generate_early(self) -> None:
        self.race = self.options.character_race.value
        self.cls = self.options.character_class.value
        if self.cls not in VALID_CLASSES[self.race]:
            raise OptionError(f"{self.player_name}: a {RACES[self.race]} can't be a {CLASSES[self.cls]}. "
                              f"Pick one of: {', '.join(CLASSES[c] for c in VALID_CLASSES[self.race])}.")

    def create_item(self, name: str) -> WoWItem:
        return WoWItem(name, ITEM_CLASSIFICATION[name], self.item_name_to_id[name], self.player)

    def get_filler_item_name(self) -> str:
        if self.random.randrange(100) < self.options.random_item_filler.value:
            categories = [c for c in RANDOM_CATEGORY_WEIGHTS if RANDOM_ITEMS_BY_CATEGORY.get(c)]
            if categories:
                category = self.random.choices(categories, [RANDOM_CATEGORY_WEIGHTS[c] for c in categories])[0]
                return self.random.choice(RANDOM_ITEMS_BY_CATEGORY[category])
        return self.random.choice(list(FILLER_ITEMS))

    def create_regions(self) -> None:
        menu = Region("Menu", self.player, self.multiworld)
        self.multiworld.regions.append(menu)

        previous = menu
        goal_level = self.options.goal_level.value
        goal_region = None
        per_region = slot_locations(self.race, self.cls, bool(self.options.include_exploration),
                                    bool(self.options.include_rares), bool(self.options.include_trainer_checks))
        for rdata, locs in zip(REGIONS, per_region):
            region = Region(rdata.name, self.player, self.multiworld)
            region.add_locations(dict(locs), WoWLocation)
            self.exclude_grindy(region, rdata)
            self.multiworld.regions.append(region)

            if rdata.key_item:
                previous.connect(region, f"{previous.name} -> {region.name}",
                                 lambda state, k=rdata.key_item: state.has(k, self.player))
            else:
                previous.connect(region, f"{previous.name} -> {region.name}")

            if goal_level in rdata.levels:
                goal_region = region
            previous = region

        goal_region = goal_region or previous
        goal = WoWLocation(self.player, f"Goal: Reach Level {goal_level}", None, goal_region)
        goal.place_locked_item(WoWItem("Victory", ItemClassification.progression, None, self.player))
        goal_region.locations.append(goal)

    def exclude_grindy(self, region: Region, rdata) -> None:
        """Rares (long respawns) and a region's last levels (grinding) only hold filler,
        so nobody's progression waits on them."""
        grindy = {f"Rare: {mob}" for mob in rdata.rares}
        grindy |= {level_location_name(lvl) for lvl in rdata.levels if is_grindy_level(rdata, lvl)}
        grindy |= {name for name, _spell, level, _region in trainer_locations(self.cls)
                   if level in rdata.levels and is_grindy_level(rdata, level)}
        for loc in region.locations:
            if loc.name in grindy:
                loc.progress_type = LocationProgressType.EXCLUDED

    def place_zone_keys(self) -> list:
        """Handle Zone keys per the placement option; returns the keys left for the pool."""
        mode = self.options.zone_key_placement.value
        if mode == ZoneKeyPlacement.option_start_with:
            for name in ZONE_KEYS:
                self.multiworld.push_precollected(self.create_item(name))
            return []
        return list(ZONE_KEYS)

    def create_items(self) -> None:
        unfilled = self.multiworld.get_unfilled_locations(self.player)
        # Filler-only locations (rares, grindy levels) need one filler item each, so the
        # optional items (end-game gear, then quest gear) only take what is left.
        room = len(unfilled) - sum(1 for loc in unfilled if loc.progress_type == LocationProgressType.EXCLUDED)

        pool = [self.create_item(name) for name in self.place_zone_keys()]
        pool += [self.create_item(name) for name in CLASS_SPELLS[self.cls]]
        optional = []
        if self.options.end_game_gear:
            optional += [self.random.choice(END_GAME_BY_SLOT[slot]) for slot in sorted(END_GAME_BY_SLOT)]
        # Gear of this slot's quest locations: the fixed rewards and one of each choose-one.
        gear = []
        for q, _region in slot_quests(self.race, self.cls):
            fixed, choice = GEAR_BY_QUEST.get(q.id, ([], []))
            gear += fixed + ([self.random.choice(choice)] if choice else [])
        self.random.shuffle(gear)
        optional += gear
        pool += [self.create_item(name) for name in optional[:max(0, room - len(pool))]]
        pool += [self.create_item(self.get_filler_item_name()) for _ in range(len(unfilled) - len(pool))]
        self.multiworld.itempool += pool

    def set_rules(self) -> None:
        self.multiworld.completion_condition[self.player] = lambda state: state.has("Victory", self.player)

    def fill_slot_data(self) -> Dict[str, Any]:
        return {
            "goal_level": self.options.goal_level.value,
            "race": self.race,
            "class": self.cls,
            "zone_locks": list(ZONE_KEYS.values()),
        }
