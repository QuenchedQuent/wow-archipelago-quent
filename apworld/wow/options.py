from dataclasses import dataclass

from Options import Choice, DefaultOnToggle, PerGameCommonOptions, Range


class GoalLevel(Range):
    """Reaching this character level completes your goal."""
    display_name = "Goal Level"
    range_start = 8
    range_end = 20
    default = 15


class IncludeExploration(DefaultOnToggle):
    """Discovering certain subzones counts as a check."""
    display_name = "Include Exploration"


class IncludeRares(DefaultOnToggle):
    """Killing rare mobs counts as a check."""
    display_name = "Include Rares"


class IncludeTrainerChecks(DefaultOnToggle):
    """Class trainers sell Archipelago checks in place of the spells that are now items.
    Each costs what the spell would have, and needs the spell's level."""
    display_name = "Include Trainer Checks"


class RandomItemFiller(Range):
    """Percent of filler that is a random item from anywhere in the game (anything you could
    loot, buy or get from a quest, with no level requirement; gear only up to item level 30).
    The rest is potions, bandages and money."""
    display_name = "Random Item Filler"
    range_start = 0
    range_end = 100
    default = 50


class EndGameGear(DefaultOnToggle):
    """Add one random end-game piece of gear per slot (head, shoulders, chest, weapon, off-hand,
    ...) to the item pool: gear above item level 30 with no level requirement. Any class's gear
    can come up, so a piece may be one you can't use. Different pieces every seed."""
    display_name = "End-Game Gear"


class ZoneKeyPlacement(Choice):
    """Where the Zone keys (Elwynn Forest, Westfall) are. They are always progression.
    Anywhere: shuffled into the multiworld like any other item (any player's game).
    Start With: you start with every Zone key."""
    display_name = "Zone Key Placement"
    option_anywhere = 0
    option_start_with = 1
    default = 0


@dataclass
class WoWOptions(PerGameCommonOptions):
    goal_level: GoalLevel
    include_exploration: IncludeExploration
    include_rares: IncludeRares
    include_trainer_checks: IncludeTrainerChecks
    zone_key_placement: ZoneKeyPlacement
    random_item_filler: RandomItemFiller
    end_game_gear: EndGameGear
