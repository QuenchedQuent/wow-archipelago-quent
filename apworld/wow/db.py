"""Thin synchronous MySQL layer for the client; called through asyncio.to_thread.

PyMySQL is pure Python and is vendored into the .apworld (wow/vendor/) by the build,
so players on the frozen Archipelago install don't need pip.
"""
import os
import sys
from typing import Iterable, List, Optional, Sequence, Tuple

try:
    import pymysql
except ImportError:  # running from the packaged .apworld
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "vendor"))
    import pymysql


class BridgeDB:
    def __init__(self, host: str, port: int, user: str, password: str, database: str):
        # ssl dict without a CA = use TLS when the server offers it, without cert verification.
        # MySQL 8.4's default auth (caching_sha2_password) then works without the
        # `cryptography` package, which the frozen Archipelago install doesn't have.
        self.params = dict(host=host, port=int(port), user=user, password=password,
                           database=database, autocommit=True, charset="utf8mb4", connect_timeout=5,
                           ssl={"check_hostname": False})
        self.conn = None

    def _c(self):
        if self.conn is None or not self.conn.open:
            self.conn = pymysql.connect(**self.params)
        else:
            self.conn.ping(reconnect=True)
        return self.conn

    def _all(self, sql: str, args: Sequence = ()) -> List[Tuple]:
        with self._c().cursor() as cur:
            cur.execute(sql, args)
            return list(cur.fetchall())

    def _exec(self, sql: str, args: Sequence = ()) -> None:
        with self._c().cursor() as cur:
            cur.execute(sql, args)

    def _many(self, sql: str, rows: Iterable[Sequence]) -> None:
        rows = list(rows)
        if rows:
            with self._c().cursor() as cur:
                cur.executemany(sql, rows)

    # --- character linking ----------------------------------------------------------

    def character_guid(self, name: str) -> Optional[int]:
        rows = self._all("SELECT guid FROM characters WHERE name = %s", (name,))
        return int(rows[0][0]) if rows else None

    def character_name(self, guid: int) -> str:
        rows = self._all("SELECT name FROM characters WHERE guid = %s", (guid,))
        return rows[0][0] if rows else f"guid {guid}"

    def linked_character(self, seed: str, slot: str) -> Optional[int]:
        rows = self._all("SELECT char_guid FROM ap_state WHERE seed_name = %s AND slot_name = %s", (seed, slot))
        return int(rows[0][0]) if rows else None

    def unlinked_online_characters(self) -> List[Tuple[int, str]]:
        return [(int(g), n) for g, n in self._all(
            "SELECT c.guid, c.name FROM characters c LEFT JOIN ap_state s ON s.char_guid = c.guid "
            "WHERE c.online = 1 AND (s.char_guid IS NULL OR s.seed_name = '')")]

    def stored_seed(self, guid: int) -> str:
        rows = self._all("SELECT seed_name FROM ap_state WHERE char_guid = %s", (guid,))
        return rows[0][0] if rows else ""

    # --- state ----------------------------------------------------------------------

    def write_state(self, guid: int, seed: str, slot: str, connected: bool,
                    checked: int, total: int, goal_level: int) -> None:
        self._exec(
            "INSERT INTO ap_state (char_guid, seed_name, slot_name, connected, checked_count, total_count, goal_level) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s) ON DUPLICATE KEY UPDATE seed_name=VALUES(seed_name), "
            "slot_name=VALUES(slot_name), connected=VALUES(connected), checked_count=VALUES(checked_count), "
            "total_count=VALUES(total_count), goal_level=VALUES(goal_level)",
            (guid, seed, slot, int(connected), checked, total, goal_level),
        )

    def set_disconnected(self, guid: int) -> None:
        self._exec("UPDATE ap_state SET connected = 0 WHERE char_guid = %s", (guid,))

    # --- checks (game -> AP) --------------------------------------------------------

    def unsent_locations(self, guid: int) -> List[int]:
        return [int(r[0]) for r in self._all(
            "SELECT location_id FROM ap_location_outbox WHERE char_guid = %s AND sent = 0", (guid,))]

    def all_outbox_locations(self, guid: int) -> List[int]:
        return [int(r[0]) for r in self._all(
            "SELECT location_id FROM ap_location_outbox WHERE char_guid = %s", (guid,))]

    def mark_sent(self, guid: int, location_ids: Iterable[int]) -> None:
        self._many("UPDATE ap_location_outbox SET sent = 1 WHERE char_guid = %s AND location_id = %s",
                   [(guid, lid) for lid in location_ids])

    # --- items (AP -> game) ---------------------------------------------------------

    def max_item_index(self, guid: int) -> int:
        rows = self._all("SELECT COALESCE(MAX(ap_index), -1) FROM ap_item_inbox WHERE char_guid = %s", (guid,))
        return int(rows[0][0])

    def insert_items(self, rows: Iterable[Tuple[int, int, int, str, str, int]]) -> None:
        self._many(
            "INSERT IGNORE INTO ap_item_inbox (char_guid, ap_index, item_id, item_name, sender_name, flags) "
            "VALUES (%s,%s,%s,%s,%s,%s)", rows)

    # --- tracker --------------------------------------------------------------------

    def upsert_locations(self, guid: int, rows: Iterable[Tuple[int, str, bool]]) -> None:
        self._many(
            "INSERT INTO ap_locations (char_guid, location_id, name, checked) VALUES (%s,%s,%s,%s) "
            "ON DUPLICATE KEY UPDATE name=VALUES(name), checked=VALUES(checked)",
            [(guid, lid, name[:128], int(checked)) for lid, name, checked in rows])
