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

## To verify on a real server
- All quest, area, creature and spell IDs in `data.py` were written from memory. Check them against the world DB.
- Check the Northshire subzone IDs in `AP.ZONE_LOCKS[12].free_areas` (use `.ap where` in game).
- Check the items listed in docs/windows-setup.md → "To verify on the first real run".

## Next (see plan)
Spell stripping (block trainer and level-up learning), vendor/trainer checks, equip and flight-path
items, `tools/extract_data.py` to generate the data from the DB, and more classes and zones.
