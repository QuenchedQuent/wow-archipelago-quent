# Windows-only setup (no WSL, no Docker, no Visual Studio)

Players download two files from this project's GitHub Releases:

| File | What it is |
|---|---|
| `WoW-Archipelago-Server-win64.zip` | A prebuilt AzerothCore 3.3.5 server with the ALE Lua engine, a portable MySQL, our Lua script and SQL, the addon, and Setup/Start/Stop scripts |
| `wow.apworld` | The Archipelago world **and** the "World of Warcraft Client" (the bridge), which runs inside the Archipelago Launcher, so players don't need Python |

## Player steps

1. Install [Archipelago](https://archipelago.gg) and double-click `wow.apworld` to install it.
2. Extract the server zip to a permanent folder, for example `C:\Games\WoW-Archipelago-Server`.
3. Run `Setup.bat` once. It sets up the database, downloads map data, builds the game DBs,
   creates an account, sets the realmlist and installs the addon. Re-running it is safe.
4. Each session: run `Start.bat`, log in with a fresh character, open
   **World of Warcraft Client** in the Archipelago Launcher and connect. When finished,
   log out and run `Stop.bat`.

The full player-facing text ships as [package/README.txt](../package/README.txt).

### What the scripts do
- **MySQL** runs from `mysql\` with its data in `db\`, on **127.0.0.1:3307**. That port avoids any
  MySQL the player already has. The `acore`/`acore` user can only connect from localhost.
- **Auth and world servers** bind to 127.0.0.1 only, so there are no firewall prompts and no exposure.
- **Configs** are generated from the `.dist` templates with absolute paths. Moving the folder means
  re-running Setup.
- **Databases** are built by the worldserver's own auto-setup (`Updates.AutoSetup`), using the
  bundled `source\data\sql`. Our `archipelago_schema.sql` is in `custom\db_characters`, so it's
  applied the same way.
- **Map data** (dbc, maps, vmaps, mmaps) comes from the latest
  [wowgaming/client-data](https://github.com/wowgaming/client-data/releases) release, the same
  source the official Docker setup uses.
- **Accounts** are written straight to `acore_auth.account` using AzerothCore's SRP6 verifier math
  (`New-SRP6` in [Common.ps1](../package/scripts/Common.ps1)), so players never need the server console.
- **The client** ([apworld/wow/client.py](../apworld/wow/client.py)) reads DB settings from the
  `wow_options` section of Archipelago's `host.yaml`. The defaults match this package. It
  auto-links to the one online character that isn't tied to another seed yet.

## Maintainer: producing a release

You don't need any compilers. GitHub's Windows runners build everything:

1. Push this project to a GitHub repo.
2. Either go to Actions → **Build Windows package** → *Run workflow* (artifacts only), or push a tag:
   `git tag v0.1.0 && git push --tags`. That also creates a GitHub Release with both files.
3. The build takes about 1-2 hours (AzerothCore plus tools on a 4-core runner).
   `versions.txt` in the zip records the exact AzerothCore and ALE commits.

Workflow: [.github/workflows/build-windows-package.yml](../.github/workflows/build-windows-package.yml).
Pin `azerothcore_ref`/`ale_ref` to known-good commits once something works, so releases are reproducible.

## Developing locally on Windows
Use the same package for your own server. Point the volume at your working copy by replacing the
package's `lua_scripts\archipelago.lua` with a symlink (from an admin PowerShell):
```powershell
Remove-Item C:\Games\WoW-Archipelago-Server\lua_scripts\archipelago.lua
New-Item -ItemType SymbolicLink -Path C:\Games\WoW-Archipelago-Server\lua_scripts\archipelago.lua `
  -Target "E:\Programming Projects\Python\WoW\server\lua_scripts\archipelago.lua"
```
Then `.reload ale` in game (GM account), or restart the worldserver.

## To verify on the first real run
- **Config keys:** the `.dist` key names Setup edits (`SourceDirectory`, `MySQLExecutable`, `BindIP`,
  `ALE.ScriptPath`) are in each file. Setup appends any key that's missing, so check that the generated
  `.conf` files look right.
- **Worldserver flag:** the worldserver accepts `-c <path>` and loads module configs from `configs\modules\`.
- **Accounts:** an account created by Setup can log in. That validates the SRP6 code.
- **Client data:** the release zip layout is handled by searching for the `dbc\` folder.
- **Release size:** check the zip size, aiming for roughly 300-500 MB without the map data.
