# World of Warcraft Archipelago Setup

1. Download `WoW-Archipelago-Server-win64.zip` and `wow.apworld` from the project's releases.
2. Double-click `wow.apworld` to install it into Archipelago.
3. Extract the server zip to a permanent folder and run `Setup.bat` once. It sets up the server,
   creates your account, points your WoW 3.3.5a client at it and installs the addon.
4. Each session: run `Start.bat`, log in with a **fresh character for each seed**, then open
   **World of Warcraft Client** in the Archipelago Launcher and connect to your room.
5. Type `/ap` in game to open the tracker.

If your server runs elsewhere (for example in Docker), set the `wow_options` section of
Archipelago's `host.yaml` to its database host, port, user and password.
