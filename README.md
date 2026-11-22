# WoW Archipelago

Archipelago randomizer for World of Warcraft 3.3.5 on an AzerothCore + ALE (Lua engine) private
server, with content limited to classic zones. The MVP covers a Human Warrior going from Northshire to Elwynn to Westfall.

```
AP Server <-WebSocket-> WoW Client (in AP Launcher) <-MySQL ap_* tables-> server script (ALE) <-addon msgs-> addon (UI)
```

WoW addons can't do networking. The Archipelago-side client does the AP connection, the server
script detects checks and grants items, and the addon only displays state.

| Folder | What |
|---|---|
| `apworld/wow/` | Archipelago world (items, locations, logic, options) **and** the client (`client.py`, `db.py`) |
| `server/sql/` | `ap_*` tables for the characters DB |
| `server/lua_scripts/` | ALE script: checks, item grants, zone fence, addon feed |
| `addon/Archipelago/` | 3.3.5 client addon: tracker, item feed, status (`/ap`) |
| `package/` | Windows server package scripts (Setup/Start/Stop/CreateAccount) |
| `.github/workflows/` | CI that builds `wow.apworld` and the prebuilt Windows server zip |

## Setup
- **Players (Windows, recommended):** [docs/windows-setup.md](docs/windows-setup.md). Download a release,
  run `Setup.bat`, done. No WSL, Docker or compilers needed.
- **Docker (Linux/macOS, or if you prefer):** [docs/docker-setup.md](docs/docker-setup.md).

## ID contract
`location = 870000000 + kind*1000000 + gameId`, `item = 880000000 + kind*1000000 + payload`.
The kinds are defined in [apworld/wow/data.py](apworld/wow/data.py) and [archipelago.lua](server/lua_scripts/archipelago.lua). Keep the two in sync.

## Loop test
- Complete "A Threat Within". The check should show on the AP server within about 5 seconds.
- `/send <slot> Spell: Charge` on the AP server console. The character should learn Charge and the addon feed should show it.
- `/send <slot> Zone: Elwynn Forest` lets you leave Northshire. Before that, you're teleported back.
- Restart the WoW Client mid-session. There should be no duplicate items and no lost checks.

## Game data (generated)
Quests, exploration, rares, the zone fence and the spell pool are generated from a real
AzerothCore world DB by [tools/extract_data.py](tools/extract_data.py). Don't hand-edit the outputs:
- `apworld/wow/gen_data.py`: regions, quests, exploration, rares and spell items for the APWorld
- `server/lua_scripts/archipelago_data.lua`: zone fence, locked spells and trainer checks for the server script
- `server/sql/world/archipelago_trainers.sql`: removes pooled spells from class trainers
- `server/sql/world/archipelago_quest_rewards.sql`: removes gear rewards from quest locations

Both SQL files copy the original rows into `ap_trainer_spell_backup` / `ap_quest_reward_backup`
first. The extractor reads from those backups, so it can safely be re-run on a server that already
has the SQL applied.

To regenerate, start the server package's MySQL, then run:
```powershell
py -3 -m venv .venv; .venv\Scripts\pip install pymysql
.venv\Scripts\python tools\extract_data.py --data D:\WoW\Server\data
```
The scope (race, class, zones, max level) is set at the top of the script.

## Spell locks
Every spell in the pool is an Archipelago item. Trainers don't teach them (see the SQL above), and
the server script unlearns any that arrive some other way, such as class quest rewards or
characters created earlier, unless the player received that spell from the multiworld.

## Logic
- **Regions:** Northshire, then Elwynn Forest (needs `Zone: Elwynn Forest`), then Westfall
  (needs `Zone: Westfall`). Each region owns a level range; your level is limited by which
  zones' mobs you can reach.
- **Quest regions:** a quest belongs to the latest of: where it starts and ends, where its
  objectives are (mobs, objects, item drops, vendors, places to reach), its prerequisites, and
  the region owning its minimum level. Quests whose objectives are only outside our zones are
  dropped. Either/or quests (exclusive groups) are one location that any of them completes.
- **Zone keys** are progression. `zone_key_placement`: `anywhere` (default) shuffles them into
  the multiworld; `start_with` gives you both.
- **Fence:** only Northshire (until you have the Elwynn key), Elwynn Forest, Stormwind City
  (Elwynn key) and Westfall (Westfall key) are in the randomizer. Other zones, other
  continents, the tram and dungeons are fenced off. A fenced player is sent back to the last
  spot they stood inside the fence (Northshire Abbey if there is none yet).
- **Grindy checks hold only filler:** rares (long respawns), and the last 2 levels of each
  region (Reach Level and trainer checks), so no one's progression waits on them.

## Filler
Filler is potions, bandages and money (`FILLER_ITEMS` in `data.py`) mixed with random items from
anywhere in the game. `random_item_filler` (0-100, default 50) is the percent that is random.
The extractor's random pool is every item you can loot, buy or get from a quest with no level
requirement, minus grey junk, unique, temporary, holiday, quest, key and currency items, and
items your race or class can't use. Weapons and armor are capped at item level 30, because the
data has end-game gear with no level requirement. A random pick chooses a category first
(gear 50, consumables 20, trade goods 10, other 10, recipes 5; `RANDOM_CATEGORY_WEIGHTS`), then
an item, since recipes are about half the pool.

## End-game gear
Gear above that cap (no level requirement, usable by a warrior) is the end-game pool. With
`end_game_gear` on (default), each seed adds one random piece per slot to the item pool as a
useful item: head, neck, shoulders, back, chest, wrists, hands, waist, legs, feet, finger,
trinket, weapon, off-hand and ranged (`END_GAME_SLOTS` in the extractor).

## Trainer checks
Each trainer spell in the pool (25 for a Warrior, levels 1 to 20) becomes a `Trainer: <spell>`
location. Every class trainer sells them, each at the spell's usual level and price. The addon
lists them in the trainer window, in an "Archipelago" section below the real spells, with what
each check holds (`addon/Archipelago/TrainerChecks.lua` wraps the trainer API that
Blizzard_TrainerUI reads). Training one sends `BUY <spell>` to the server script, which checks
that you are at a class trainer and have the level and money, then takes the money and sends
the check. In logic, a check sits in the region that owns its level. Turn the checks off with
the `include_trainer_checks` option.

## Quest rewards
A quest that is a location gives its normal XP, money and reputation, but its gear is replaced by
the Archipelago check. That gear (for "choose one" rewards, the best pick for a warrior) is added
to the item pool, so it may arrive from any check in the multiworld. Non-gear rewards such as food,
and items other quests need, are left alone. The client scouts every location, and the addon
shows what each quest holds next to the quest window and the quest log.

## Next (see plan)
Vendor checks, equip and flight-path items, and more classes and zones.
