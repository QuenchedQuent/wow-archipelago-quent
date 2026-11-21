# AzerothCore + ALE (Lua engine) with Docker on Windows

You don't need Visual Studio, Boost, OpenSSL or MySQL on Windows. Everything compiles and runs
inside Linux containers. The first build takes about 30-60 minutes, and the first start downloads
client data (another 10-15 minutes).

> **About the Lua engine:** AzerothCore's Eluna module is now **mod-ale** (AzerothCore Lua
> Engine). `github.com/azerothcore/mod-eluna` redirects there. ALE is no longer compatible with
> upstream Eluna, but its hooks match what `archipelago.lua` uses (checked against ALE's `Hooks.h`).

## 1. Prerequisites

1. **Enable virtualization** in BIOS/UEFI if it isn't already. Task Manager → Performance → CPU
   should show "Virtualization: Enabled".
2. **Install WSL2.** In an admin PowerShell, run `wsl --install`, then reboot.
3. **Install Docker Desktop** from docker.com. Keep the "Use WSL 2 based engine" option checked.
   Start it once and wait for "Engine running".
4. Docker Desktop → Settings → Resources: give it **at least 4 CPUs and 8 GB RAM**. The C++ build
   is heavy and fails with odd errors if it runs out of memory.
5. Verify:
   ```powershell
   docker --version
   docker compose version
   ```

## 2. Clone AzerothCore and ALE

Pick a folder outside this project, for example `E:\Servers`. Clone with `core.autocrlf=false`, because
Windows line endings break the shell scripts that run inside the containers.

```powershell
cd E:\Servers
git clone -c core.autocrlf=false --depth 1 https://github.com/azerothcore/azerothcore-wotlk.git
cd azerothcore-wotlk
git clone -c core.autocrlf=false --depth 1 https://github.com/azerothcore/mod-ale.git modules/mod-ale
```

## 3. Hook up our scripts and SQL

1. Copy [server/docker-compose.override.yml](../server/docker-compose.override.yml) into
   `E:\Servers\azerothcore-wotlk\`. If this project isn't at
   `E:\Programming Projects\Python\WoW`, fix the path inside it.
2. Have the importer create our tables in `acore_characters`:
   ```powershell
   mkdir -Force data\sql\custom\db_characters
   copy "E:\Programming Projects\Python\WoW\server\sql\archipelago_schema.sql" data\sql\custom\db_characters\
   ```

## 4. Build and start

```powershell
docker compose up -d --build
```

Watch progress with `docker compose logs -f`. On the first run, `ac-client-data` downloads maps,
vmaps and mmaps, and `ac-db-import` creates and fills the databases. After that, `ac-authserver` and
`ac-worldserver` start. You're done when the worldserver log shows `[Archipelago] loaded` and then
`WORLD: World Initialized`.

If the build fails with a Git-Bash path error, use `MSYS_NO_PATHCONV=1 ./acore.sh docker build` from Git Bash.

## 5. Create a game account

```powershell
docker attach ac-worldserver
```
At the `AC>` prompt:
```
account create myuser mypassword
account set gmlevel myuser 3 -1
```
**Detach with `Ctrl+P` then `Ctrl+Q`.** `Ctrl+C` kills the worldserver.
(GM level 3 is for testing. Our zone fence ignores GMs, so use a non-GM account to test the fence.)

## 6. Point the client at it

You need your own **WoW 3.3.5a (build 12340)** client. Edit `Data\enUS\realmlist.wtf`
(or `enGB`) so it reads:
```
set realmlist 127.0.0.1
```
Then copy `addon\Archipelago` from this project into the client's `Interface\AddOns\`.

## 7. Connect the WoW Client (Archipelago side)

The Docker MySQL is on `127.0.0.1:3306`, with user `root` and password `password` by default.
The client's defaults are for the Windows package, so edit the `wow_options` section of
Archipelago's `host.yaml`:
```yaml
wow_options:
  db_port: 3306
  db_user: root
  db_password: password
```

You can inspect the DB with HeidiSQL or DBeaver using the same credentials. Our `ap_*` tables
are in `acore_characters`.

## Day-to-day

| Task | Command |
|---|---|
| Start / stop | `docker compose up -d` / `docker compose down` |
| Reload Lua after editing `archipelago.lua` | `.reload ale` in game as a GM, or `docker compose restart ac-worldserver` |
| Worldserver console | `docker attach ac-worldserver` (detach `Ctrl+P`, `Ctrl+Q`) |
| Logs | `docker compose logs -f ac-worldserver` |
| Rebuild after updating AzerothCore/ALE | `git pull` in each repo, then `docker compose up -d --build` |
| Re-run our SQL manually | run `archipelago_schema.sql` against `acore_characters` in HeidiSQL (it's idempotent) |

## Troubleshooting

- **No `[Archipelago] loaded` line:** check that the volume path in the override is right.
  `docker compose exec ac-worldserver ls /azerothcore/lua_scripts` should list `archipelago.lua`.
  Also check the ALE startup lines in the log for the script path it's using.
- **Lua errors:** with `AC_ALE_TRACE_BACK=1` they show in `docker compose logs ac-worldserver`,
  as `[Archipelago] tick error: ...` or ALE stack traces.
- **Port 3306 already in use:** a local MySQL is running. Stop it, or set
  `DOCKER_DB_EXTERNAL_PORT` in a `.env` file next to `docker-compose.yml` (and update the bridge config).
- **Build killed / "c++: fatal error: Killed":** give Docker more RAM (step 1.4).
