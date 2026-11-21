from dataclasses import dataclass

from Options import DefaultOnToggle, PerGameCommonOptions, Range


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


@dataclass
class WoWOptions(PerGameCommonOptions):
    goal_level: GoalLevel
    include_exploration: IncludeExploration
    include_rares: IncludeRares
