# World of Warcraft

## What does randomization do?

Play any race and any class but the Death Knight (the `character_race` and
`character_class` options), levels 1-20 in the zones around your race's starting area:
the starting valley, the zone around it with its capital, then the usual next zone
(e.g. Northshire, Elwynn Forest and Westfall for humans; Valley of Trials, Durotar and
The Barrens for orcs and trolls). Your character starts with almost no class abilities
and cannot leave the valley. Completing quests, reaching levels, exploring subzones
and killing rare mobs sends checks. Zone access keys, class spells and quest reward
gear are shuffled into the multiworld.

Class trainers no longer teach the shuffled spells. Instead they sell one
Archipelago check per spell, at the spell's usual level and price. They are listed
in the trainer window under "Archipelago", along with what each one holds.

Only your race's zones are part of the game, each later one behind a Zone key; walking
anywhere else sends you back. Rares and the last levels of each zone only hold filler.

Filler is a mix of potions, bandages, money and random items from anywhere in the
game that have no level requirement (the `random_item_filler` option sets the mix).
Each seed also hides one random end-game piece of gear per slot somewhere in the
multiworld (the `end_game_gear` option).

## What is the goal?

Reach the configured goal level.

## Requirements

A 3.3.5 AzerothCore private server with Eluna, the Archipelago bridge client,
and the Archipelago in-game addon. See the setup guide.
