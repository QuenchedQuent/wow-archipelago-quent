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
3. The build takes about 20-25 minutes. Before zipping, it smoke-tests the package: worldserver,
   authserver and mysqld must start using only the bundled DLLs.

Workflow: [.github/workflows/build-windows-package.yml](../.github/workflows/build-windows-package.yml).

**Versions are pinned.** `AC_REF`/`ALE_REF` at the top of the workflow are known-good commits, and
releases always build those. To upgrade AzerothCore or ALE:
1. Run the workflow manually with `master` in the inputs.
2. Test the resulting package.
3. Copy the commits from its `versions.txt` into `AC_REF`/`ALE_REF`.

## Developing locally on Windows
Use the same package for your own server. Point the volume at your working copy by replacing the
package's `lua_scripts\archipelago.lua` with a symlink (from an admin PowerShell):
```powershell
Remove-Item C:\Games\WoW-Archipelago-Server\lua_scripts\archipelago.lua
New-Item -ItemType SymbolicLink -Path C:\Games\WoW-Archipelago-Server\lua_scripts\archipelago.lua `
  -Target "E:\Programming Projects\Python\WoW\server\lua_scripts\archipelago.lua"
```
Then `.reload ale` in game (GM account), or restart the worldserver.

## Verified on the first real run (2026-11-21, run 44945822646)
- Setup: configs, `-c` flag, module config, DB auto-setup, our schema, client-data download.
- An account created by Setup logs in, so the SRP6 code is correct.
- Quest checks reach AP, items (spell, zone key) are granted, and progress survives a logout.
- Release zip is 274 MB without the map data.
- **Windows runtime gotchas** (both are now enforced by the workflow): bundle the *newest* MSVC CRT
  (an old one crashes with 0xC0000005 in msvcp140), and ship OpenSSL's `legacy.dll` next to the exe
  (otherwise there's a startup assertion that only shows on stderr).
