from typing import Any, ClassVar, Dict

import settings
from BaseClasses import ItemClassification, Region, Tutorial
from worlds.AutoWorld import WebWorld, World
from worlds.LauncherComponents import Component, Type, components, launch_subprocess

from .data import FILLER_ITEMS, GAME, GEAR, REGIONS, SPELLS, ZONE_KEYS
from .items import ITEM_CLASSIFICATION, ITEM_GROUPS, ITEM_TABLE, WoWItem
from .locations import LOCATION_TABLE, WoWLocation, region_locations
from .options import WoWOptions


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
    World of Warcraft on a 3.3.5 AzerothCore private server. Quests, levels,
    exploration and rare kills are checks; spells and zone access are items.
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

    def create_item(self, name: str) -> WoWItem:
        return WoWItem(name, ITEM_CLASSIFICATION[name], self.item_name_to_id[name], self.player)

    def get_filler_item_name(self) -> str:
        return self.random.choice(list(FILLER_ITEMS))

    def create_regions(self) -> None:
        menu = Region("Menu", self.player, self.multiworld)
        self.multiworld.regions.append(menu)

        previous = menu
        goal_level = self.options.goal_level.value
        goal_region = None
        for rdata in REGIONS:
            region = Region(rdata.name, self.player, self.multiworld)
            locs = region_locations(rdata, bool(self.options.include_exploration),
                                    bool(self.options.include_rares))
            region.add_locations(dict(locs), WoWLocation)
            self.multiworld.regions.append(region)

            if rdata.key_item:
                key = rdata.key_item
                previous.connect(region, f"{previous.name} -> {region.name}",
                                 lambda state, k=key: state.has(k, self.player))
            else:
                previous.connect(region, f"{previous.name} -> {region.name}")

            if goal_level in rdata.levels:
                goal_region = region
            previous = region

        goal_region = goal_region or previous
        goal = WoWLocation(self.player, f"Goal: Reach Level {goal_level}", None, goal_region)
        goal.place_locked_item(WoWItem("Victory", ItemClassification.progression, None, self.player))
        goal_region.locations.append(goal)

    def create_items(self) -> None:
        pool = [self.create_item(name) for name in ZONE_KEYS]
        pool += [self.create_item(name) for name in SPELLS]
        open_slots = len(self.multiworld.get_unfilled_locations(self.player))
        gear = [name for name, (_entry, copies) in GEAR.items() for _ in range(copies)]
        self.random.shuffle(gear)
        pool += [self.create_item(name) for name in gear[:max(0, open_slots - len(pool))]]
        pool += [self.create_item(self.get_filler_item_name()) for _ in range(open_slots - len(pool))]
        self.multiworld.itempool += pool

    def set_rules(self) -> None:
        self.multiworld.completion_condition[self.player] = lambda state: state.has("Victory", self.player)

    def fill_slot_data(self) -> Dict[str, Any]:
        return {
            "goal_level": self.options.goal_level.value,
            "zone_locks": list(ZONE_KEYS.values()),
        }
