"""
Generate the randomizer's game data from a real AzerothCore world DB + server data files.

Reads:
  - acore_world (quests, quest givers/enders, creature spawns, trainers)
  - <data>/dbc/AreaTable.dbc, <data>/dbc/Spell.dbc   (names, zone tree, learn effects)
  - <data>/maps/*.map                                (area grid, to place spawns in zones)

Writes:
  - apworld/wow/gen_data.py                     regions, quests, exploration, rares, spells
  - server/lua_scripts/archipelago_data.lua     zone fence, locked spells, trainer checks
  - server/sql/world/archipelago_trainers.sql   removes pooled spells from class trainers
  - server/sql/world/archipelago_quest_rewards.sql  removes gear rewards from quest locations

Usage (MySQL of the Windows package must be running, e.g. via Start.bat):
  .venv\\Scripts\\python tools\\extract_data.py --data D:\\WoW\\Server\\data
"""
from __future__ import annotations

import argparse
import os
import pprint
import struct
import sys
from collections import Counter, defaultdict
from typing import Dict, Iterable, List, Optional, Set, Tuple

import pymysql

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# ---------------------------------------------------------------------------------
# Scope of the MVP. Everything else is derived from the data.
# ---------------------------------------------------------------------------------
RACE_MASK = 1        # Human
CLASS_ID = 1         # Warrior
CLASS_MASK = 1 << (CLASS_ID - 1)
CLASS_QUEST_SORT = -81   # QuestSort "Warrior"
MAX_LEVEL = 20
MAP_ID = 0           # Eastern Kingdoms

NORTHSHIRE_AREA = 9
ELWYNN_ZONE = 12
WESTFALL_ZONE = 40
STORMWIND_ZONE = 1519

# Ordered regions. A point/quest belongs to the first region whose test matches.
#   zones: whole zones counted in this region; areas: specific areas (checked first).
REGION_DEFS = [
    {"name": "Northshire Valley", "key": None, "levels": (2, 6),
     "areas": "NORTHSHIRE", "zones": []},
    {"name": "Elwynn Forest", "key": "Zone: Elwynn Forest", "levels": (7, 12),
     "areas": None, "zones": [ELWYNN_ZONE, STORMWIND_ZONE]},
    {"name": "Westfall", "key": "Zone: Westfall", "levels": (13, MAX_LEVEL),
     "areas": None, "zones": [WESTFALL_ZONE]},
]
# Zones that get explore checks (Stormwind is a city: no exploration checks there).
EXPLORE_ZONES = {ELWYNN_ZONE, WESTFALL_ZONE}
ZONE_KEYS = {"Zone: Elwynn Forest": ELWYNN_ZONE, "Zone: Westfall": WESTFALL_ZONE}

# Quest filters
QUEST_FLAG_TRACKING = 0x400
QUEST_FLAG_DAILY = 0x1000
QUEST_FLAG_WEEKLY = 0x8000
SPECIAL_FLAG_REPEATABLE = 0x1
EXCLUDED_QUEST_INFO = {41, 62, 81, 85, 88, 89}   # PvP, raid, dungeon, heroic, raid10/25
# Quests the filters can't recognise as unobtainable.
QUEST_BLOCKLIST = {
    5805,   # "Welcome!" - started by the Goldshire Gift Voucher promo item
}

# Creature ranks that count as rares.
RARE_RANKS = (2, 4)    # rare elite, rare

SPELL_EFFECT_LEARN_SPELL = 36

# Quest reward gear (weapons/armor) becomes Archipelago items; the quest's AP check replaces it.
ITEM_CLASS_WEAPON = 2
ITEM_CLASS_ARMOR = 4
# Warrior proficiencies before level 40 (no plate yet).
WARRIOR_WEAPONS = {0, 1, 2, 3, 4, 5, 6, 7, 8, 10, 13, 15, 16, 18}
ARMOR_SCORE = {0: 3, 1: 2, 2: 3, 3: 4, 6: 3}    # misc, cloth, leather, mail, shield
INVTYPE_CLOAK = 16

# Random filler items: anything obtainable in the game (loot, vendors, quest rewards) with no
# level requirement. Weapons/armor are capped at this item level so filler can't outclass
# the content (the data has level-80 gear with RequiredLevel 0).
RANDOM_GEAR_MAX_ITEM_LEVEL = MAX_LEVEL + 5
RANDOM_EXCLUDED_CLASSES = (10, 12, 13)          # currency, quest, key
RANDOM_NOT_GEAR_INVTYPES = (0, 4, 19)           # not equippable, shirt, tabard: no power
ITEM_FLAG_DEPRECATED = 0x10
# item class -> filler category (the APWorld weights categories, then picks within one)
RANDOM_CATEGORIES = {0: "consumable", 7: "trade goods", 9: "recipe"}
RANDOM_NAME_BLOCKLIST = r"test|deprecated|\[PH\]|^OLD|unused|monster -|DEBUG|NPC Equip|^QA"

# Our SQL removes rows from these tables; the originals live in backup tables so the
# extractor always sees vanilla data, even on a server that already has our SQL applied.
TRAINER_BACKUP = "ap_trainer_spell_backup"
REWARD_BACKUP = "ap_quest_reward_backup"
REWARD_COLUMNS = ([f"RewardItem{i}" for i in range(1, 5)] + [f"RewardAmount{i}" for i in range(1, 5)]
                  + [f"RewardChoiceItemID{i}" for i in range(1, 7)]
                  + [f"RewardChoiceItemQuantity{i}" for i in range(1, 7)])


# ---------------------------------------------------------------------------------
# DBC / map readers
# ---------------------------------------------------------------------------------
class DBC:
    def __init__(self, path: str):
        with open(path, "rb") as f:
            data = f.read()
        magic, self.count, self.fields, self.size, strsize = struct.unpack_from("<4s4I", data)
        if magic != b"WDBC":
            raise ValueError(f"{path}: not a WDBC file")
        self.records = memoryview(data)[20:20 + self.count * self.size]
        self.strings = data[20 + self.count * self.size:]

    def rows(self):
        for i in range(self.count):
            yield i * self.size

    def u32(self, off: int, field: int) -> int:
        return struct.unpack_from("<I", self.records, off + field * 4)[0]

    def i32(self, off: int, field: int) -> int:
        return struct.unpack_from("<i", self.records, off + field * 4)[0]

    def string(self, off: int, field: int) -> str:
        start = self.u32(off, field)
        end = self.strings.index(b"\0", start)
        return self.strings[start:end].decode("utf-8", "replace")


class Area:
    __slots__ = ("id", "map", "parent", "explore_flag", "level", "name")

    def __init__(self, id, map, parent, explore_flag, level, name):
        self.id, self.map, self.parent = id, map, parent
        self.explore_flag, self.level, self.name = explore_flag, level, name

    @property
    def zone(self) -> int:
        return self.parent or self.id


def load_areas(dbc_dir: str) -> Dict[int, Area]:
    dbc = DBC(os.path.join(dbc_dir, "AreaTable.dbc"))
    if dbc.fields != 36:
        raise ValueError(f"AreaTable.dbc has {dbc.fields} fields; expected 36 (3.3.5a)")
    areas = {}
    for off in dbc.rows():
        a = Area(dbc.u32(off, 0), dbc.u32(off, 1), dbc.u32(off, 2), dbc.u32(off, 3),
                 dbc.i32(off, 10), dbc.string(off, 11))
        areas[a.id] = a
    return areas


class Spell:
    __slots__ = ("id", "name", "rank", "effects", "triggers")


def load_spells(dbc_dir: str) -> Dict[int, Spell]:
    dbc = DBC(os.path.join(dbc_dir, "Spell.dbc"))
    if dbc.fields != 234:
        raise ValueError(f"Spell.dbc has {dbc.fields} fields; expected 234 (3.3.5a)")
    spells = {}
    for off in dbc.rows():
        s = Spell()
        s.id = dbc.u32(off, 0)
        s.effects = [dbc.u32(off, 71 + i) for i in range(3)]
        s.triggers = [dbc.u32(off, 116 + i) for i in range(3)]
        s.name = dbc.string(off, 136)
        s.rank = dbc.string(off, 153)
        spells[s.id] = s
    return spells


class AreaGrid:
    """Area lookup from AzerothCore .map files (same math as GridMap::getArea)."""
    GRID = 533.3333333
    CENTER = 32

    def __init__(self, maps_dir: str):
        self.dir = maps_dir
        self.cache: Dict[Tuple[int, int, int], Optional[object]] = {}

    def _load(self, map_id: int, gx: int, gy: int):
        key = (map_id, gx, gy)
        if key in self.cache:
            return self.cache[key]
        path = os.path.join(self.dir, f"{map_id:03d}{gx:02d}{gy:02d}.map")
        grid = None
        if os.path.exists(path):
            with open(path, "rb") as f:
                data = f.read()
            magic, _ver, _build, area_off = struct.unpack_from("<4sIII", data)
            if magic != b"MAPS":
                raise ValueError(f"{path}: bad magic")
            fourcc, flags, grid_area = struct.unpack_from("<4sHH", data, area_off)
            if fourcc != b"AREA":
                raise ValueError(f"{path}: no AREA block")
            if flags & 0x1:     # MAP_AREA_NO_AREA: whole grid is one area
                grid = grid_area
            else:
                grid = struct.unpack_from("<256H", data, area_off + 8)
        self.cache[key] = grid
        return grid

    def area_at(self, map_id: int, x: float, y: float) -> int:
        fx = self.CENTER - x / self.GRID
        fy = self.CENTER - y / self.GRID
        grid = self._load(map_id, int(fx), int(fy))
        if grid is None:
            return 0
        if isinstance(grid, int):
            return grid
        lx = int(fx * 16) & 15
        ly = int(fy * 16) & 15
        return grid[lx * 16 + ly]


def load_area_triggers(dbc_dir: str) -> Dict[int, Tuple[int, float, float]]:
    """AreaTrigger id -> (map, x, y)."""
    dbc = DBC(os.path.join(dbc_dir, "AreaTrigger.dbc"))
    if dbc.fields != 10:
        raise ValueError(f"AreaTrigger.dbc has {dbc.fields} fields; expected 10 (3.3.5a)")
    out = {}
    for off in dbc.rows():
        x, y = struct.unpack_from("<ff", dbc.records, off + 8)
        out[dbc.u32(off, 0)] = (dbc.u32(off, 1), x, y)
    return out


# ---------------------------------------------------------------------------------
# Region classification
# ---------------------------------------------------------------------------------
class Regions:
    def __init__(self, areas: Dict[int, Area], northshire: Set[int]):
        self.areas = areas
        self.northshire = northshire

    def of_area(self, area_id: int) -> Optional[int]:
        if area_id in self.northshire:
            return 0
        a = self.areas.get(area_id)
        if not a:
            return None
        for i, r in enumerate(REGION_DEFS):
            if a.zone in r["zones"] or a.id in r["zones"]:
                return i
        return None


# Northshire's sub-areas are parented to Elwynn Forest in AreaTable, not to Northshire,
# so they are listed explicitly (checked by name below). Matches the in-game fence test.
NORTHSHIRE_SUBAREAS = {23: "Northshire River", 24: "Northshire Abbey",
                       34: "Echo Ridge Mine", 59: "Northshire Vineyards"}
# Areas that exist only inside buildings (WMO), so they never show up on the terrain grid.
INDOOR_AREAS = {24}
EXPLORE_NAME_BLOCKLIST = ("UNUSED", "Not Used", "The Great Sea")


def northshire_areas(areas: Dict[int, Area]) -> Set[int]:
    for aid, name in NORTHSHIRE_SUBAREAS.items():
        if areas[aid].name != name:
            raise ValueError(f"Area {aid} is '{areas[aid].name}', expected '{name}'")
    result = {NORTHSHIRE_AREA} | set(NORTHSHIRE_SUBAREAS)
    result |= {a.id for a in areas.values() if a.parent == NORTHSHIRE_AREA}
    return result


def areas_on_grid(maps_dir: str, map_id: int) -> Set[int]:
    """Every area id that appears on the terrain grid of a map."""
    found: Set[int] = set()
    prefix = f"{map_id:03d}"
    grid = AreaGrid(maps_dir)
    for fn in os.listdir(maps_dir):
        if fn.startswith(prefix) and fn.endswith(".map") and len(fn) == 11:
            g = grid._load(map_id, int(fn[3:5]), int(fn[5:7]))
            if isinstance(g, int):
                found.add(g)
            elif g is not None:
                found.update(g)
    return found


# ---------------------------------------------------------------------------------
# DB extraction
# ---------------------------------------------------------------------------------
def query(cur, sql: str, args: Iterable = ()):
    cur.execute(sql, tuple(args))
    return cur.fetchall()


def table_exists(cur, name: str) -> bool:
    return bool(query(cur, "SELECT 1 FROM information_schema.TABLES "
                           "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s", (name,)))


def spawn_regions(cur, grid: AreaGrid, regions: Regions, table: str, event_table: str,
                  ids: Set[int]) -> Dict[int, Set[Optional[int]]]:
    """entry -> set of regions it spawns in (None = outside all regions)."""
    out: Dict[int, Set[Optional[int]]] = defaultdict(set)
    if not ids:
        return out
    event_guids = {r[0] for r in query(cur, f"SELECT guid FROM {event_table} WHERE eventEntry > 0")}
    rows = query(cur, f"SELECT guid, id, map, position_x, position_y, phaseMask FROM {table} "
                      f"WHERE id IN ({','.join(map(str, ids))})")
    for guid, entry, map_id, x, y, phase in rows:
        if guid in event_guids or not (phase & 1):
            continue
        if map_id != MAP_ID:
            out[entry].add(None)
            continue
        out[entry].add(regions.of_area(grid.area_at(map_id, x, y)))
    return out


def best(regions: Set[Optional[int]]) -> Optional[int]:
    known = [r for r in regions if r is not None]
    return min(known) if known else None


def extract_quests(cur, grid: AreaGrid, regions: Regions, areas: Dict[int, Area],
                   triggers: Dict[int, Tuple[int, float, float]]):
    rows = query(cur, """
        SELECT q.ID, q.LogTitle, q.QuestLevel, q.MinLevel, q.QuestSortID, q.QuestInfoID,
               q.AllowableRaces, q.Flags, q.RewardSpell, q.RewardDisplaySpell,
               COALESCE(a.AllowableClasses, 0), COALESCE(a.PrevQuestID, 0),
               COALESCE(a.ExclusiveGroup, 0), COALESCE(a.BreadcrumbForQuestId, 0),
               COALESCE(a.SpecialFlags, 0), COALESCE(a.RequiredSkillID, 0)
        FROM quest_template q LEFT JOIN quest_template_addon a ON a.ID = q.ID""")
    objectives = {r[0]: r[1:] for r in query(cur, """
        SELECT ID, StartItem, RequiredNpcOrGo1, RequiredNpcOrGo2, RequiredNpcOrGo3, RequiredNpcOrGo4,
               RequiredItemId1, RequiredItemId2, RequiredItemId3, RequiredItemId4, RequiredItemId5,
               RequiredItemId6 FROM quest_template""")}
    disabled = {r[0] for r in query(cur, "SELECT entry FROM disables WHERE sourceType = 1")}
    event_quests = {r[0] for r in query(cur, "SELECT quest FROM game_event_creature_quest")}
    event_quests |= {r[0] for r in query(cur, "SELECT quest FROM game_event_gameobject_quest")}
    event_quests |= {r[0] for r in query(cur, "SELECT questId FROM game_event_seasonal_questrelation")}

    sort_ok = set(EXPLORE_ZONES) | {NORTHSHIRE_AREA} | {
        a.id for a in areas.values() if a.zone in EXPLORE_ZONES}

    # A breadcrumb that is also a hard prerequisite (PrevQuestID) of a quest is mandatory.
    required = {abs(r[11]) for r in rows if r[11]}

    cand = {}
    for (qid, title, qlevel, minlevel, sort, info, races, flags, rspell, rdspell,
         classes, prev, excl, breadcrumb, special, skill) in rows:
        if qid in disabled or qid in event_quests or qid in QUEST_BLOCKLIST or not title:
            continue
        if breadcrumb and qid not in required:
            continue    # optional breadcrumb: unavailable once its target quest is done
        if races and not (races & RACE_MASK):
            continue
        if classes and not (classes & CLASS_MASK):
            continue
        if sort not in sort_ok and sort != CLASS_QUEST_SORT:
            continue
        if minlevel > MAX_LEVEL or info in EXCLUDED_QUEST_INFO or skill:
            continue
        if flags & (QUEST_FLAG_TRACKING | QUEST_FLAG_DAILY | QUEST_FLAG_WEEKLY):
            continue
        if special & SPECIAL_FLAG_REPEATABLE:
            continue
        cand[qid] = dict(id=qid, title=title, level=qlevel, minlevel=minlevel, sort=sort,
                         prev=prev, excl=excl, class_quest=bool(classes),
                         reward_spells=[s for s in (rspell, rdspell) if s])

    ids = set(cand)
    starters = defaultdict(set)
    enders = defaultdict(set)
    for table, kind, target in (("creature_queststarter", "creature", starters),
                                ("gameobject_queststarter", "gameobject", starters),
                                ("creature_questender", "creature", enders),
                                ("gameobject_questender", "gameobject", enders)):
        rel = query(cur, f"SELECT id, quest FROM {table}")
        by_entry = defaultdict(list)
        for entry, quest in rel:
            if quest in ids:
                by_entry[entry].append(quest)
        spawns = spawn_regions(cur, grid, regions, kind, f"game_event_{kind}", set(by_entry))
        for entry, quests in by_entry.items():
            for quest in quests:
                target[quest] |= spawns.get(entry, set())
    item_started = {r[0] for r in query(cur, "SELECT startquest FROM item_template WHERE startquest > 0")}

    sort_region = {}
    for qid, q in cand.items():
        sort_region[qid] = regions.of_area(q["sort"]) if q["sort"] > 0 else None

    result = {}
    for qid, q in cand.items():
        s = starters.get(qid, set())
        e = enders.get(qid, set())
        if s:
            start = best(s)
            if start is None:
                continue            # only offered outside our regions
        elif qid in item_started and sort_region[qid] is not None:
            start = sort_region[qid]
        else:
            continue                # nobody offers it
        if not e:
            continue
        end = best(e)
        if end is None:
            continue                # turned in outside our regions
        parts = [start, end] + ([sort_region[qid]] if sort_region[qid] is not None else [])
        q["region"] = max(parts)
        result[qid] = q

    # Objectives (mobs to kill, objects to use, items to collect, places to reach) must be
    # doable inside our regions, and push the quest into the region where they are.
    obj = objective_regions(cur, grid, regions, {qid: objectives[qid] for qid in result}, triggers)
    for qid in list(result):
        for where in obj.get(qid, []):
            if not where:
                continue                    # source unknown (e.g. made by a spell): no info
            r = best(where)
            if r is None:
                del result[qid]             # only obtainable outside our regions
                break
            result[qid]["region"] = max(result[qid]["region"], r)

    # A quest can't be picked up before its MinLevel: it belongs to the region owning that level.
    for q in result.values():
        q["region"] = max(q["region"], level_region(q["minlevel"]))

    # Prerequisites must be in the set too, and push the quest into the later region.
    changed = True
    while changed:
        changed = False
        for qid in list(result):
            prev = abs(result[qid]["prev"])
            if not prev:
                continue
            if prev not in result:
                del result[qid]
                changed = True
            elif result[prev]["region"] > result[qid]["region"]:
                result[qid]["region"] = result[prev]["region"]
                changed = True

    # Exclusive groups (>0): only one quest of the group can ever be done. The group is one
    # location (lowest id) that any member completes, in the earliest member's region.
    groups = defaultdict(list)
    for qid, q in result.items():
        if q["excl"] > 0:
            groups[q["excl"]].append(qid)
    aliases: Dict[int, int] = {}
    for members in groups.values():
        if len(members) < 2:
            continue
        main, *others = sorted(members)
        result[main]["region"] = min(result[m]["region"] for m in members)
        for qid in others:
            aliases[qid] = main
            del result[qid]
    # A quest after a merged member is reachable once the group is (any member counts).
    for q in result.values():
        prev = abs(q["prev"])
        if prev in aliases:
            q["prev"] = aliases[prev]
    return result, aliases


def level_region(level: int) -> int:
    """First region whose level range reaches `level` (levels below the first count as region 0)."""
    for i, r in enumerate(REGION_DEFS):
        if level <= r["levels"][1]:
            return i
    return len(REGION_DEFS) - 1


def objective_regions(cur, grid: AreaGrid, regions: Regions, objectives: Dict[int, tuple],
                      triggers: Dict[int, Tuple[int, float, float]]) -> Dict[int, List[Set[Optional[int]]]]:
    """quest -> one set of regions per objective (where it can be done; empty = unknown)."""
    npcs, gos, items = set(), set(), set()
    for start_item, *rest in objectives.values():
        npcs |= {n for n in rest[:4] if n > 0}
        gos |= {-n for n in rest[:4] if n < 0}
        items |= {i for i in rest[4:] if i and i != start_item}

    # Kill credit: an objective npc may be credited by other creatures (KillCredit1/2).
    creditors = defaultdict(set)
    for entry, k1, k2 in query(cur, "SELECT entry, KillCredit1, KillCredit2 FROM creature_template "
                                    "WHERE KillCredit1 > 0 OR KillCredit2 > 0"):
        for k in (k1, k2):
            if k in npcs:
                creditors[k].add(entry)

    # Item sources: creature loot, chest loot, vendors.
    item_npcs, item_gos = defaultdict(set), defaultdict(set)
    if items:
        ids = ",".join(map(str, items))
        for entry, item in query(cur, f"""
                SELECT t.entry, l.Item FROM creature_loot_template l
                JOIN creature_template t ON t.lootid = l.Entry
                WHERE l.Reference = 0 AND l.Item IN ({ids})"""):
            item_npcs[item].add(entry)
        for entry, item in query(cur, f"SELECT entry, item FROM npc_vendor WHERE item IN ({ids})"):
            item_npcs[item].add(entry)
        for entry, item in query(cur, f"""
                SELECT g.entry, l.Item FROM gameobject_loot_template l
                JOIN gameobject_template g ON g.type = 3 AND g.Data1 = l.Entry
                WHERE l.Reference = 0 AND l.Item IN ({ids})"""):
            item_gos[item].add(entry)

    all_npcs = npcs | {c for cs in creditors.values() for c in cs} | {e for es in item_npcs.values() for e in es}
    all_gos = gos | {g for gs in item_gos.values() for g in gs}
    npc_at = spawn_regions(cur, grid, regions, "creature", "game_event_creature", all_npcs)
    go_at = spawn_regions(cur, grid, regions, "gameobject", "game_event_gameobject", all_gos)

    def where(npc_ids=(), go_ids=()):
        out: Set[Optional[int]] = set()
        for n in npc_ids:
            out |= npc_at.get(n, set())
        for g in go_ids:
            out |= go_at.get(g, set())
        return out

    trig_quests = defaultdict(list)
    for trig, quest in query(cur, "SELECT id, quest FROM areatrigger_involvedrelation"):
        if quest in objectives:
            trig_quests[quest].append(trig)

    result: Dict[int, List[Set[Optional[int]]]] = {}
    for qid, (start_item, *rest) in objectives.items():
        found = []
        for n in rest[:4]:
            if n > 0:
                found.append(where([n] + sorted(creditors.get(n, ()))))
            elif n < 0:
                found.append(where(go_ids=[-n]))
        for i in rest[4:]:
            if i and i != start_item:
                found.append(where(item_npcs.get(i, ()), item_gos.get(i, ())))
        for trig in trig_quests.get(qid, []):
            t = triggers.get(trig)
            if t:
                m, x, y = t
                found.append({regions.of_area(grid.area_at(m, x, y)) if m == MAP_ID else None})
        result[qid] = found
    return result


def warrior_score(item: dict) -> Optional[int]:
    """How much a warrior wants this item (None = can't use it)."""
    if item["classes"] not in (-1, 0) and not (item["classes"] & CLASS_MASK):
        return None
    if item["races"] not in (-1, 0) and not (item["races"] & RACE_MASK):
        return None
    if item["cls"] == ITEM_CLASS_WEAPON:
        return 3 if item["sub"] in WARRIOR_WEAPONS else None
    if item["inv"] == INVTYPE_CLOAK:
        return 3
    return ARMOR_SCORE.get(item["sub"])


def extract_rewards(cur, quests: Dict[int, dict]):
    """Pick the gear each quest location gives, and which reward columns to clear."""
    ids = ",".join(map(str, quests))
    cols = ", ".join(REWARD_COLUMNS)
    rows = {r[0]: r for r in query(cur, f"SELECT ID, {cols} FROM quest_template WHERE ID IN ({ids})")}
    if table_exists(cur, REWARD_BACKUP):   # originals of rewards our SQL already removed
        rows.update({r[0]: r for r in query(
            cur, f"SELECT ID, {cols} FROM {REWARD_BACKUP} WHERE ID IN ({ids})")})
    rows = [dict(zip(["ID"] + REWARD_COLUMNS, r)) for r in rows.values()]

    # Items some quest needs or starts must stay where they are.
    protected = set()
    req_cols = [f"RequiredItemId{i}" for i in range(1, 7)] + [f"ItemDrop{i}" for i in range(1, 5)] + ["StartItem"]
    for r in query(cur, f"SELECT {', '.join(req_cols)} FROM quest_template"):
        protected.update(x for x in r if x)

    wanted = {r[c] for r in rows for c in REWARD_COLUMNS if c.startswith(("RewardItem", "RewardChoiceItemID")) and r[c]}
    items = {}
    if wanted:
        for entry, name, cls, sub, inv, classes, races, ilvl, req, quality, startquest in query(
                cur, f"SELECT entry, name, class, subclass, InventoryType, AllowableClass, AllowableRace, "
                     f"ItemLevel, RequiredLevel, Quality, startquest FROM item_template "
                     f"WHERE entry IN ({','.join(map(str, wanted))})"):
            items[entry] = dict(id=entry, name=name, cls=cls, sub=sub, inv=inv, classes=classes,
                                races=races, ilvl=ilvl, req=req, quality=quality, startquest=startquest)

    def is_gear(entry: int) -> bool:
        it = items.get(entry)
        return bool(it and it["cls"] in (ITEM_CLASS_WEAPON, ITEM_CLASS_ARMOR) and it["inv"]
                    and not it["startquest"] and entry not in protected)

    gear: List[dict] = []                 # one entry per copy in the pool
    new_rewards: Dict[int, Dict[str, int]] = {}   # quest -> every reward column, gear removed
    for row in rows:
        fixed = [(row[f"RewardItem{i}"], row[f"RewardAmount{i}"]) for i in range(1, 5)]
        choices = [(row[f"RewardChoiceItemID{i}"], row[f"RewardChoiceItemQuantity{i}"]) for i in range(1, 7)]
        fixed_gear = [e for e, _ in fixed if e and is_gear(e)]
        choice_gear = [e for e, _ in choices if e and is_gear(e)]
        if not fixed_gear and not choice_gear:
            continue
        gear += [items[e] for e in fixed_gear]
        scored = [(warrior_score(items[e]), items[e]["ilvl"], items[e]["quality"], e) for e in choice_gear]
        scored = [s for s in scored if s[0] is not None]
        if scored:
            gear.append(items[max(scored)[3]])

        # The core stops reading reward slots at the first empty one, so the remaining
        # rewards are moved up instead of leaving gaps.
        keep_fixed = [(e, n) for e, n in fixed if e and e not in fixed_gear]
        keep_choice = [(e, n) for e, n in choices if e and e not in choice_gear]
        cols: Dict[str, int] = {}
        for i in range(4):
            e, n = keep_fixed[i] if i < len(keep_fixed) else (0, 0)
            cols[f"RewardItem{i + 1}"], cols[f"RewardAmount{i + 1}"] = e, n
        for i in range(6):
            e, n = keep_choice[i] if i < len(keep_choice) else (0, 0)
            cols[f"RewardChoiceItemID{i + 1}"], cols[f"RewardChoiceItemQuantity{i + 1}"] = e, n
        new_rewards[row["ID"]] = cols
    return gear, new_rewards


def extract_random_items(cur, skip: Set[int]) -> List[dict]:
    """Filler pool: obtainable items without a level requirement (see RANDOM_* above).
    Leaves out grey junk, unique items (a second copy can't be delivered), temporary and
    holiday items, quest/key/currency items, and items another race or class can't use."""
    obtainable: Set[int] = set()
    for table in ("creature_loot_template", "gameobject_loot_template", "reference_loot_template",
                  "item_loot_template"):
        obtainable |= {r[0] for r in query(cur, f"SELECT DISTINCT Item FROM {table} WHERE Reference = 0")}
    obtainable |= {r[0] for r in query(cur, "SELECT DISTINCT item FROM npc_vendor")}
    for row in query(cur, "SELECT " + ", ".join(
            [f"RewardItem{i}" for i in range(1, 5)] + [f"RewardChoiceItemID{i}" for i in range(1, 7)])
            + " FROM quest_template"):
        obtainable |= set(row)
    if table_exists(cur, REWARD_BACKUP):   # gear we took off quests is still "obtainable" in vanilla
        for row in query(cur, "SELECT " + ", ".join(
                [f"RewardItem{i}" for i in range(1, 5)] + [f"RewardChoiceItemID{i}" for i in range(1, 7)])
                + f" FROM {REWARD_BACKUP}"):
            obtainable |= set(row)

    rows = query(cur, f"""
        SELECT entry, name, class, ItemLevel, InventoryType FROM item_template
        WHERE RequiredLevel = 0 AND class NOT IN ({','.join(map(str, RANDOM_EXCLUDED_CLASSES))})
          AND startquest = 0 AND Quality BETWEEN 1 AND 4 AND duration = 0 AND HolidayId = 0
          AND (Flags & {ITEM_FLAG_DEPRECATED}) = 0 AND maxcount = 0 AND bonding NOT IN (4, 5)
          AND (AllowableRace IN (-1, 0) OR (AllowableRace & {RACE_MASK}))
          AND (AllowableClass IN (-1, 0) OR (AllowableClass & {CLASS_MASK}))
          AND name NOT REGEXP %s""", (RANDOM_NAME_BLOCKLIST,))
    out = []
    for entry, name, cls, ilvl, inv in rows:
        if entry in skip or entry not in obtainable:
            continue
        gear = cls in (ITEM_CLASS_WEAPON, ITEM_CLASS_ARMOR) and inv not in RANDOM_NOT_GEAR_INVTYPES
        if gear and ilvl > RANDOM_GEAR_MAX_ITEM_LEVEL:
            continue
        category = "gear" if gear else RANDOM_CATEGORIES.get(cls, "other")
        out.append(dict(id=entry, name=name, category=category))
    return out


def extract_rares(cur, grid: AreaGrid, regions: Regions):
    rows = query(cur, f"SELECT entry, name, maxlevel FROM creature_template "
                      f"WHERE `rank` IN ({','.join(map(str, RARE_RANKS))})")
    info = {entry: (name, maxlevel) for entry, name, maxlevel in rows}
    spawns = spawn_regions(cur, grid, regions, "creature", "game_event_creature", set(info))
    result = {}
    for entry, regs in spawns.items():
        r = best(regs)
        name, maxlevel = info[entry]
        if r is not None and maxlevel <= MAX_LEVEL + 2:
            result[entry] = dict(id=entry, name=name, region=r, level=maxlevel)
    return result


def extract_spells(cur, spells: Dict[int, Spell], quests: Dict[int, dict]):
    trainers = [r[0] for r in query(cur, "SELECT Id FROM trainer WHERE Type = 0 AND Requirement = %s",
                                    (CLASS_ID,))]
    pool: Dict[int, dict] = {}
    source = "trainer_spell"
    if table_exists(cur, TRAINER_BACKUP):
        source = f"(SELECT * FROM trainer_spell UNION SELECT * FROM {TRAINER_BACKUP})"
    for spell_id, req_level, cost in query(
            cur, f"SELECT SpellId, MIN(ReqLevel), MIN(MoneyCost) FROM {source} t "
                 f"WHERE TrainerId IN ({','.join(map(str, trainers))}) AND ReqLevel <= %s "
                 f"GROUP BY SpellId",
            (MAX_LEVEL,)):
        pool[spell_id] = dict(id=spell_id, level=int(req_level), cost=int(cost), source="trainer")

    # Spells taught by class quests (e.g. stances), resolved through LEARN_SPELL effects.
    for q in quests.values():
        if not q["class_quest"]:
            continue
        for sid in q["reward_spells"]:
            s = spells.get(sid)
            if not s:
                continue
            taught = [t for e, t in zip(s.effects, s.triggers) if e == SPELL_EFFECT_LEARN_SPELL and t]
            for t in taught or [sid]:
                pool.setdefault(t, dict(id=t, level=q["minlevel"], source=f"quest {q['id']}"))

    for sid, entry in pool.items():
        s = spells.get(sid)
        if not s:
            raise ValueError(f"Spell {sid} not in Spell.dbc")
        entry["base"] = s.name
        entry["rank"] = s.rank
    return {"trainers": trainers, "pool": pool, "npcs": extract_trainer_npcs(cur, trainers)}


def extract_trainer_npcs(cur, trainers: List[int]) -> Dict[int, int]:
    """Class trainer creature entry -> greeting npc_text id (the one shown to our class).
    Only trainers with a gossip menu: the server script replaces that menu."""
    out: Dict[int, int] = {}
    rows = query(cur, f"""
        SELECT t.entry, g.TextID, c.ConditionTypeOrReference, c.ConditionValue1, c.NegativeCondition
        FROM creature_default_trainer d
        JOIN creature_template t ON t.entry = d.CreatureId
        LEFT JOIN gossip_menu g ON g.MenuID = t.gossip_menu_id
        LEFT JOIN conditions c ON c.SourceTypeOrReferenceId = 14 AND c.SourceGroup = g.MenuID
             AND c.SourceEntry = g.TextID
        WHERE d.TrainerId IN ({','.join(map(str, trainers))}) AND (t.npcflag & 1)
        ORDER BY t.entry, g.TextID""")
    for entry, text_id, ctype, cvalue, negative in rows:
        if text_id is None:
            out.setdefault(entry, 1)
            continue
        if ctype == 15:   # CONDITION_CLASS
            ok = bool(cvalue & CLASS_MASK) != bool(negative)
            if ok:
                out[entry] = text_id
        else:
            out.setdefault(entry, text_id)
    return out


# ---------------------------------------------------------------------------------
# naming + output
# ---------------------------------------------------------------------------------
def unique_names(items: List[dict], name_of) -> Dict[str, dict]:
    counts = defaultdict(int)
    for it in items:
        counts[name_of(it)] += 1
    out = {}
    for it in items:
        name = name_of(it)
        if counts[name] > 1:
            name = f"{name} ({it['id']})"
        out[name] = it
    return out


def spell_item_name(s: dict, base_counts: Dict[str, int]) -> str:
    if base_counts[s["base"]] > 1 and s["rank"]:
        return f"Spell: {s['base']} ({s['rank']})"
    return f"Spell: {s['base']}"


def write_python(path: str, regions_out: list, spells_out: Dict[str, dict], gear_out: Dict[str, dict],
                 trainer_out: Dict[str, dict], random_out: Dict[str, int]):
    lines = [
        '"""Generated by tools/extract_data.py from the AzerothCore world DB. Do not edit."""',
        "",
        f"MAX_LEVEL = {MAX_LEVEL}",
        "",
        "# name, key item, (first level, last level), quests, explore, rares",
        "REGIONS = " + pprint.pformat(regions_out, width=100, sort_dicts=False),
        "",
        "ZONE_KEYS = " + pprint.pformat(ZONE_KEYS, width=100),
        "",
        "# item name -> (spell id, level it is normally available)",
        "SPELLS = " + pprint.pformat({n: (s["id"], s["level"]) for n, s in spells_out.items()},
                                     width=100, sort_dicts=False),
        "",
        "# Class trainer checks (bought in place of a pooled spell): location name -> (spell id, level, copper)",
        "TRAINER_CHECKS = " + pprint.pformat({n: (s["id"], s["level"], s["cost"]) for n, s in trainer_out.items()},
                                             width=100, sort_dicts=False),
        "",
        "# Quest reward gear moved into the item pool: item name -> (item entry, copies, required level)",
        "GEAR = " + pprint.pformat({n: (g["id"], g["copies"], g["req"]) for n, g in gear_out.items()},
                                   width=100, sort_dicts=False),
        "",
        "# Random filler items (obtainable, no level requirement): item name -> (item entry, category)",
        "RANDOM_ITEMS = " + pprint.pformat(random_out, width=100, sort_dicts=False),
        "",
    ]
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines))


def fence_zones(areas: Dict[int, Area], free_areas: Set[int]) -> List[dict]:
    """Every zone of our regions, with the Zone key it needs. Northshire is part of Elwynn's
    zone, so it is Elwynn's free_areas."""
    out = []
    for r in REGION_DEFS:
        if not r["key"]:
            continue
        for zone in r["zones"]:
            out.append(dict(zone=zone, name=areas[zone].name, key=ZONE_KEYS[r["key"]], key_name=r["key"],
                            free=free_areas if zone == ELWYNN_ZONE else set()))
    return out


def write_lua(path: str, fence: List[dict], locked: List[int], trainer_out: Dict[str, dict],
              npcs: Dict[int, int], aliases: Dict[int, int]):
    def lua_set(ids):
        return "{ " + ", ".join(f"[{i}] = true" for i in sorted(ids)) + " }"
    def lua_str(t):
        return '"' + t.replace("\\", "\\\\").replace('"', '\\"') + '"'
    checks = "\n".join(
        f"        {{ spell = {s['id']}, level = {s['level']}, cost = {s['cost']}, "
        f"name = {lua_str(n.removeprefix('Trainer: '))} }},"
        for n, s in trainer_out.items())
    trainers = ", ".join(f"[{e}] = {t}" for e, t in sorted(npcs.items()))
    alias_text = ", ".join(f"[{q}] = {m}" for q, m in sorted(aliases.items()))
    zones = "\n".join(
        f"        [{z['zone']}] = {{ name = {lua_str(z['name'])}, key = {z['key']}, "
        f"key_name = {lua_str(z['key_name'])}"
        + (f", free_areas = {lua_set(z['free'])}" if z["free"] else "") + " },"
        for z in fence)
    text = f"""-- Generated by tools/extract_data.py from the AzerothCore world DB. Do not edit.
-- Read lazily by archipelago.lua (script load order does not matter).
ArchipelagoData = {{
    -- The randomizer's world: only these zones of map {MAP_ID}. Each needs the Zone key `key`
    -- (an AreaTable zone id); free_areas are always allowed (the start area). Anything else
    -- (other zones, other maps, dungeons) is outside the randomizer and fenced off.
    fence_map = {MAP_ID},
    zone_locks = {{
{zones}
    }},
    -- Spells that are Archipelago items: unlearned unless received from the multiworld.
    locked_spells = {lua_set(locked)},
    -- Either/or quests: completing one of these counts as the listed quest's location.
    quest_aliases = {{ {alias_text} }},
    -- Class trainer NPC entries (-> greeting text); you must stand at one to buy a check.
    trainers = {{ {trainers} }},
    -- Trainer checks: bought from a class trainer in place of the spell (location kind 5).
    trainer_checks = {{
{checks}
    }},
}}
"""
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def write_sql(path: str, trainers: List[int], locked: List[int]):
    where = (f"`TrainerId` IN ({', '.join(map(str, trainers))})\n"
             f"  AND `SpellId` IN ({', '.join(map(str, sorted(locked)))})")
    text = f"""-- Generated by tools/extract_data.py. Do not edit.
-- Class trainers no longer teach spells that are Archipelago items (they arrive from the
-- multiworld instead), so players can't pay for a spell the server would take away again.
-- The original rows are kept in {TRAINER_BACKUP}, which the extractor reads, so it can
-- be re-run on a server that already has this applied.
CREATE TABLE IF NOT EXISTS `{TRAINER_BACKUP}` LIKE `trainer_spell`;
INSERT IGNORE INTO `{TRAINER_BACKUP}` SELECT * FROM `trainer_spell` WHERE {where};
DELETE FROM `trainer_spell` WHERE {where};
"""
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def write_reward_sql(path: str, strip: Dict[int, Dict[str, int]]):
    cols = ", ".join(f"`{c}`" for c in REWARD_COLUMNS)
    col_defs = ",\n  ".join(f"`{c}` INT NOT NULL DEFAULT 0" for c in REWARD_COLUMNS)
    lines = [
        "-- Generated by tools/extract_data.py. Do not edit.",
        "-- Quest locations no longer hand out their gear; the Archipelago check replaces it and the",
        "-- gear itself is shuffled into the multiworld item pool. Original rewards are kept in",
        f"-- {REWARD_BACKUP} (first copy wins), which the extractor reads.",
        f"CREATE TABLE IF NOT EXISTS `{REWARD_BACKUP}` (\n  `ID` INT UNSIGNED NOT NULL PRIMARY KEY,\n  {col_defs}\n);",
        f"INSERT IGNORE INTO `{REWARD_BACKUP}` (`ID`, {cols})",
        f"  SELECT `ID`, {cols} FROM `quest_template` WHERE `ID` IN ({', '.join(map(str, sorted(strip)))});",
    ]
    for qid in sorted(strip):
        sets = ", ".join(f"`{c}` = {v}" for c, v in strip[qid].items())
        lines.append(f"UPDATE `quest_template` SET {sets} WHERE `ID` = {qid};")
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines) + "\n")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", required=True, help="server data folder containing dbc\\ and maps\\")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=3307)
    ap.add_argument("--user", default="root")
    ap.add_argument("--password", default="")
    ap.add_argument("--world-db", default="acore_world")
    args = ap.parse_args(argv)

    areas = load_areas(os.path.join(args.data, "dbc"))
    spells = load_spells(os.path.join(args.data, "dbc"))
    triggers = load_area_triggers(os.path.join(args.data, "dbc"))
    grid = AreaGrid(os.path.join(args.data, "maps"))
    northshire = northshire_areas(areas)
    regions = Regions(areas, northshire)

    # Sanity: the human start position must be in Northshire.
    start_area = grid.area_at(0, -8949.95, -132.49)
    if regions.of_area(start_area) != 0:
        sys.exit(f"Self-check failed: human start position is area {start_area}, not Northshire")
    if spells[100].name != "Charge":
        sys.exit("Self-check failed: Spell.dbc layout (spell 100 is not Charge)")

    conn = pymysql.connect(host=args.host, port=args.port, user=args.user, password=args.password,
                           database=args.world_db, ssl={"check_hostname": False})
    with conn.cursor() as cur:
        quests, quest_aliases = extract_quests(cur, grid, regions, areas, triggers)
        rares = extract_rares(cur, grid, regions)
        sp = extract_spells(cur, spells, quests)
        gear, strip = extract_rewards(cur, quests)
        random_items = extract_random_items(cur, {g["id"] for g in gear})
    conn.close()

    by_entry: Dict[int, dict] = {}
    for g in gear:
        by_entry.setdefault(g["id"], dict(g, copies=0))["copies"] += 1
    gear_out = {f"Item: {n}": g for n, g in unique_names(
        sorted(by_entry.values(), key=lambda g: (g["req"], g["id"])), lambda g: g["name"]).items()}

    on_grid = areas_on_grid(os.path.join(args.data, "maps"), MAP_ID) | INDOOR_AREAS
    explore = [dict(id=a.id, name=a.name, region=regions.of_area(a.id))
               for a in areas.values()
               if a.map == MAP_ID and a.explore_flag and a.id in on_grid
               and (a.zone in EXPLORE_ZONES or a.id in northshire)
               and not any(b in a.name for b in EXPLORE_NAME_BLOCKLIST)]

    # Names must be unique across all regions (they become AP location names).
    all_q = unique_names(sorted(quests.values(), key=lambda q: q["id"]), lambda q: q["title"])
    all_e = unique_names(sorted(explore, key=lambda a: a["id"]), lambda a: a["name"])
    all_r = unique_names(sorted(rares.values(), key=lambda c: c["id"]), lambda c: c["name"])
    regions_out = []
    for i, r in enumerate(REGION_DEFS):
        regions_out.append({
            "name": r["name"], "key": r["key"], "levels": r["levels"],
            "quests": {n: q["id"] for n, q in all_q.items() if q["region"] == i},
            "explore": {n: a["id"] for n, a in all_e.items() if a["region"] == i},
            "rares": {n: c["id"] for n, c in all_r.items() if c["region"] == i},
        })

    pool = sorted(sp["pool"].values(), key=lambda s: (s["level"], s["id"]))
    base_counts = defaultdict(int)
    for s in pool:
        base_counts[s["base"]] += 1
    spells_out = {spell_item_name(s, base_counts): s for s in pool}
    if len(spells_out) != len(pool):
        sys.exit("Duplicate spell item names")
    locked = [s["id"] for s in pool]
    random_out = {}
    for n, it in unique_names(sorted(random_items, key=lambda i: i["id"]), lambda i: i["name"]).items():
        name = f"Item: {n}"
        random_out[name if name not in gear_out else f"{name} ({it['id']})"] = (it["id"], it["category"])
    trainer_out = {"Trainer: " + n.removeprefix("Spell: "): s for n, s in spells_out.items()
                   if s["source"] == "trainer"}

    write_python(os.path.join(ROOT, "apworld", "wow", "gen_data.py"), regions_out, spells_out, gear_out,
                 trainer_out, random_out)
    write_reward_sql(os.path.join(ROOT, "server", "sql", "world", "archipelago_quest_rewards.sql"), strip)
    write_lua(os.path.join(ROOT, "server", "lua_scripts", "archipelago_data.lua"), fence_zones(areas, northshire), locked,
              trainer_out, sp["npcs"], quest_aliases)
    os.makedirs(os.path.join(ROOT, "server", "sql", "world"), exist_ok=True)
    write_sql(os.path.join(ROOT, "server", "sql", "world", "archipelago_trainers.sql"), sp["trainers"], locked)

    print(f"Northshire areas: {sorted(northshire)}  (start position area {start_area})")
    print(f"Either/or quest aliases: {quest_aliases or 'none'}")
    for r in regions_out:
        print(f"{r['name']:18} quests {len(r['quests']):3}  explore {len(r['explore']):2}  rares {len(r['rares']):2}")
    print(f"Gear: {sum(g['copies'] for g in gear_out.values())} items from {len(strip)} quests")
    for name, g in gear_out.items():
        print(f"  L{g['req']:<2} {g['id']:>6} x{g['copies']}  {name}")
    print(f"Random filler items: {len(random_out)}  "
          + ", ".join(f"{c} {n}" for c, n in sorted(Counter(c for _, c in random_out.values()).items())))
    print(f"Trainer checks: {len(trainer_out)}, sold by {len(sp['npcs'])} trainer NPCs")
    print(f"Spells: {len(spells_out)}")
    for name, s in spells_out.items():
        print(f"  L{s['level']:<2} {s['id']:>6}  {name}  [{s['source']}]")


if __name__ == "__main__":
    main()
