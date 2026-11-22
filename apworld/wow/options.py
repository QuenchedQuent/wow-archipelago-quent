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


class ZoneKeyPlacement(Choice):
    """Where the Zone keys (Elwynn Forest, Westfall) are. They are always progression.
    Anywhere: shuffled into the multiworld like any other item (any player's game).
    Start With: you start with every Zone key."""
    display_name = "Zone Key Placement"
    option_anywhere = 0
    option_start_with = 1
    default = 0


class CombatLogic(Choice):
    """How many class spells and gear items the logic expects before a zone counts as
    reachable. Spells and gear become progression items when this is on.
    Off: only Zone keys matter.
    Normal: Elwynn Forest needs 2 spells; Westfall needs 5 spells and 2 gear.
    Easy: Elwynn Forest needs 4 spells and 1 gear; Westfall needs 9 spells and 4 gear."""
    display_name = "Combat Logic"
    option_off = 0
    option_normal = 1
    option_easy = 2
    default = 1


@dataclass
class WoWOptions(PerGameCommonOptions):
    goal_level: GoalLevel
    include_exploration: IncludeExploration
    include_rares: IncludeRares
    include_trainer_checks: IncludeTrainerChecks
    zone_key_placement: ZoneKeyPlacement
    combat_logic: CombatLogic
