WoW Archipelago - Windows server package
=========================================

Everything runs locally on this PC. Nothing is installed system-wide, and the server
only listens on 127.0.0.1 (not reachable from other machines, no firewall prompts).

You need:
  - A World of Warcraft 3.3.5a client (build 12340)
  - Archipelago (archipelago.gg) with wow.apworld installed
  - About 6 GB of free disk space

FIRST TIME
  1. Extract this folder somewhere permanent, e.g. C:\Games\WoW-Archipelago-Server
     (avoid Program Files).
  2. Double-click Setup.bat. It will:
       - set up a private database
       - download the server's map data (several GB, one time)
       - build the game databases (5-15 minutes, one time)
       - create your game account
       - point your WoW client at this server and install the Archipelago addon
     If Windows SmartScreen warns about the scripts or programs, choose
     "More info" -> "Run anyway".

EVERY SESSION
  1. Double-click Start.bat. It starts the server and launches WoW.
     Keep the "worldserver" window open while you play.
  2. Log in with a NEW character for each Archipelago seed.
  3. In the Archipelago Launcher, open "World of Warcraft Client" and connect.
  4. In game, type /ap to show the tracker.
  5. When done: log out of WoW, then run Stop.bat.

OTHER
  CreateAccount.bat        - make another account
  CreateAccount.bat -GM    - make a GM account (GMs ignore the zone fence)
  logs\                    - server logs if something goes wrong
  Moved this folder?       - run Setup.bat again (it keeps your data)
