"""
World of Warcraft client for Archipelago (launched from the Archipelago Launcher).

Connects to the AP server as "World of Warcraft" and syncs through the ap_* tables in the
WoW server's characters DB:
    ap_location_outbox  (server script -> client -> LocationChecks)
    ap_item_inbox       (ReceivedItems -> client -> server script grants)
    ap_locations/state  (tracker + status mirrored for the in-game addon)

DB connection settings come from the `wow_options` section of Archipelago's host.yaml.
The character is linked automatically: log in with a fresh character and connect.
"""
import asyncio
from typing import Any, Dict, Optional, Set

import Utils
from CommonClient import (
    ClientCommandProcessor, CommonContext, get_base_parser, gui_enabled, logger, server_loop,
)
from NetUtils import ClientStatus

from .data import GAME, LOC_BASE, KIND_SIZE
from .db import BridgeDB

GOAL_LOCATION_ID = LOC_BASE + 9 * KIND_SIZE  # kind 9 = goal sentinel, never a real AP location
POLL_SECONDS = 1.0
WAIT_HINT_SECONDS = 30


def load_db_settings() -> Dict[str, Any]:
    defaults = dict(db_host="127.0.0.1", db_port=3307, db_user="acore", db_password="acore",
                    characters_db="acore_characters", character_name="")
    try:
        from settings import get_settings
        group = getattr(get_settings(), "wow_options")
        return {k: getattr(group, k, v) for k, v in defaults.items()}
    except Exception:
        logger.warning("Could not read wow_options from host.yaml; using defaults.")
        return defaults


class WoWCommandProcessor(ClientCommandProcessor):
    def _cmd_character(self, name: str = "") -> bool:
        """Link to a specific WoW character by name (default: auto-detect the one you're logged in with)."""
        ctx: WoWContext = self.ctx  # type: ignore[assignment]
        ctx.character_name = name.strip().capitalize()
        ctx.char_guid = None
        logger.info(f"Character set to {ctx.character_name or '(auto-detect)'}")
        return True

    def _cmd_wow(self) -> bool:
        """Show the link between this slot and the WoW server."""
        ctx: WoWContext = self.ctx  # type: ignore[assignment]
        logger.info(f"Character guid: {ctx.char_guid}, items written: {ctx.items_written}, "
                    f"checked: {len(ctx.checked_locations)}/{len(ctx.all_slot_locations())}")
        return True


class WoWContext(CommonContext):
    game = GAME
    items_handling = 0b111  # receive items from all sources, including our own world
    command_processor = WoWCommandProcessor

    def __init__(self, server_address: Optional[str], password: Optional[str]):
        super().__init__(server_address, password)
        s = load_db_settings()
        self.db = BridgeDB(s["db_host"], s["db_port"], s["db_user"], s["db_password"], s["characters_db"])
        self.character_name: str = str(s["character_name"] or "")
        self.char_guid: Optional[int] = None
        self.slot_data: Dict[str, Any] = {}
        self.need_full_sync = False
        self.items_written = 0
        self.written_checked: Set[int] = set()
        self.written_contents: Set[int] = set()
        self._last_hint = 0.0
        self._db_ok = True

    async def server_auth(self, password_requested: bool = False):
        if password_requested and not self.password:
            await super().server_auth(password_requested)
        await self.get_username()
        await self.send_connect()

    def on_package(self, cmd: str, args: dict):
        if cmd == "RoomInfo":
            # CommonClient doesn't keep it; we need it to tell games apart.
            self.seed_name = args.get("seed_name") or None
        elif cmd == "Connected":
            self.slot_data = args.get("slot_data") or {}
            self.char_guid = None  # re-resolve for this slot/seed
            self.need_full_sync = True

    @property
    def is_linked(self) -> bool:
        return bool(self.server and not self.server.socket.closed and self.slot is not None)

    def all_slot_locations(self) -> Set[int]:
        return set(self.missing_locations) | set(self.checked_locations)

    def hint(self, text: str) -> None:
        now = asyncio.get_running_loop().time()
        if now - self._last_hint >= WAIT_HINT_SECONDS:
            logger.info(text)
            self._last_hint = now

    # --- character resolution --------------------------------------------------------

    async def resolve_character(self) -> Optional[int]:
        db, seed, slot = self.db, self.seed_name or "", self.auth or ""
        linked = await asyncio.to_thread(db.linked_character, seed, slot)
        if linked is not None:
            return linked

        if not seed:
            self.hint("Waiting for the server's seed name...")
            return None

        if self.character_name:
            guid = await asyncio.to_thread(db.character_guid, self.character_name)
            if guid is None:
                self.hint(f"No WoW character named {self.character_name} exists yet.")
                return None
            stored = await asyncio.to_thread(db.stored_seed, guid)
            if stored and stored != seed:
                self.hint(f"{self.character_name} is already linked to another seed. "
                          f"Use a fresh character (/character <name>, or /character to auto-detect).")
                return None
            return await self.new_link(guid)

        candidates = await asyncio.to_thread(db.unlinked_online_characters)
        if len(candidates) == 1:
            return await self.new_link(candidates[0][0])
        if not candidates:
            self.hint("Waiting for you to log in to WoW with a fresh character...")
        else:
            names = ", ".join(n for _, n in candidates)
            self.hint(f"Several fresh characters are online ({names}). Pick one with /character <name>.")
        return None

    async def new_link(self, guid: int) -> int:
        """Link a character to this seed, dropping rows left over from an earlier game
        (a deleted character's reused guid, or links made before seeds were recorded)."""
        await asyncio.to_thread(self.db.reset_character, guid)
        return guid

    # --- sync -----------------------------------------------------------------------

    async def tick(self) -> None:
        if not self.is_linked:
            return
        db = self.db

        if self.char_guid is None:
            self.char_guid = await self.resolve_character()
            if self.char_guid is None:
                return
            name = await asyncio.to_thread(db.character_name, self.char_guid)
            logger.info(f"Linked slot {self.auth} to WoW character {name}.")
            self.need_full_sync = True
        guid = self.char_guid

        if self.need_full_sync:
            await self.full_sync(guid)

        # Game -> AP: new checks.
        unsent = await asyncio.to_thread(db.unsent_locations, guid)
        if unsent:
            await self.send_checks(unsent)
            await asyncio.to_thread(db.mark_sent, guid, unsent)

        # AP -> game: new items.
        if self.items_written < len(self.items_received):
            rows = []
            for index in range(self.items_written, len(self.items_received)):
                item = self.items_received[index]
                rows.append((
                    guid, index, item.item,
                    self.item_names.lookup_in_slot(item.item, self.slot),
                    self.player_names.get(item.player, f"Player {item.player}"),
                    item.flags,
                ))
            await asyncio.to_thread(db.insert_items, rows)
            self.items_written = len(self.items_received)

        # Tracker: newly checked locations.
        newly_checked = set(self.checked_locations) - self.written_checked
        if newly_checked:
            await asyncio.to_thread(db.upsert_locations, guid, [
                (lid, self.location_names.lookup_in_slot(lid), True) for lid in newly_checked])
            self.written_checked |= newly_checked
            await self.write_state(guid)

        # Tracker: what each location holds (answers to our LocationScouts).
        new_info = set(self.locations_info) - self.written_contents
        if new_info:
            await asyncio.to_thread(db.upsert_contents, guid, [
                (lid, self.describe(self.locations_info[lid]), int(self.locations_info[lid].flags))
                for lid in new_info])
            self.written_contents |= new_info

    def describe(self, item) -> str:
        """'Spell: Charge' for our own items, 'Hookshot for Alice (Zelda)' for others."""
        name = self.item_names.lookup_in_slot(item.item, item.player)
        if item.player == self.slot:
            return name
        player = self.player_names.get(item.player, f"Player {item.player}")
        info = self.slot_info.get(item.player)
        game = f" ({info.game})" if info else ""
        return f"{name} for {player}{game}"

    async def full_sync(self, guid: int) -> None:
        db = self.db
        self.items_written = await asyncio.to_thread(db.max_item_index, guid) + 1

        locations = self.all_slot_locations()
        checked = set(self.checked_locations)
        await asyncio.to_thread(db.upsert_locations, guid, [
            (lid, self.location_names.lookup_in_slot(lid), lid in checked) for lid in locations])
        self.written_checked = checked
        await self.write_state(guid)  # creates the ap_state row = the character link

        # Resend everything ever found; the server ignores duplicates. Covers checks
        # made while the client was closed.
        await self.send_checks(await asyncio.to_thread(db.all_outbox_locations, guid))

        # Ask what every location holds, so the addon can show it in the quest window.
        # (No hints are created: create_as_hint 0.)
        self.written_contents = set()
        if locations:
            await self.send_msgs([{"cmd": "LocationScouts", "locations": sorted(locations), "create_as_hint": 0}])
        self.need_full_sync = False
        logger.info(f"Synced {len(locations)} locations.")

    async def send_checks(self, location_ids) -> None:
        ids = set(location_ids)
        if GOAL_LOCATION_ID in ids and not self.finished_game:
            await self.send_msgs([{"cmd": "StatusUpdate", "status": ClientStatus.CLIENT_GOAL}])
            self.finished_game = True
            logger.info("Goal complete!")
        real = sorted(ids & self.all_slot_locations())
        if real:
            await self.send_msgs([{"cmd": "LocationChecks", "locations": real}])

    async def write_state(self, guid: int) -> None:
        await asyncio.to_thread(
            self.db.write_state, guid, self.seed_name or "", self.auth or "", self.is_linked,
            len(self.checked_locations), len(self.all_slot_locations()),
            int(self.slot_data.get("goal_level", 0)))

    async def disconnect(self, allow_autoreconnect: bool = False):
        if self.char_guid is not None:
            try:
                await asyncio.to_thread(self.db.set_disconnected, self.char_guid)
            except Exception:  # DB trouble shouldn't block disconnecting
                logger.exception("Could not mark character disconnected")
        await super().disconnect(allow_autoreconnect)


async def client_loop(ctx: WoWContext) -> None:
    while not ctx.exit_event.is_set():
        try:
            await ctx.tick()
            if not ctx._db_ok:
                logger.info("WoW server database reachable again.")
            ctx._db_ok = True
        except Exception as e:
            if ctx._db_ok:
                logger.error(f"Can't reach the WoW server database ({e}). Is the server running? "
                             f"Check wow_options in host.yaml.")
            ctx._db_ok = False
        await asyncio.sleep(POLL_SECONDS)


async def main(args) -> None:
    ctx = WoWContext(args.connect, args.password)
    if args.name:
        ctx.auth = args.name
    ctx.server_task = asyncio.create_task(server_loop(ctx), name="ServerLoop")
    if gui_enabled:
        ctx.run_gui()
    ctx.run_cli()
    loop_task = asyncio.create_task(client_loop(ctx), name="WoWLoop")

    await ctx.exit_event.wait()
    ctx.server_address = None
    loop_task.cancel()
    await ctx.shutdown()


def launch(*launch_args: str) -> None:
    Utils.init_logging("WoWClient", exception_logger="Client")
    parser = get_base_parser(description="World of Warcraft Archipelago client")
    parser.add_argument("--name", default=None, help="Slot name to connect as")
    args = parser.parse_args(launch_args)
    asyncio.run(main(args))
