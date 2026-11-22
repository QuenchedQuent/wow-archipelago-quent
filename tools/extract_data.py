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
# Playable characters: the Alliance races (Elwynn Forest is hostile to the Horde; other races
# are fenced into Northshire from their own start) and every class but the Death Knight,
# which starts at level 55 in its own zone. Quests, spells and trainer checks are kept per
# race/class; the APWorld picks a slot's own from its race and class options.
RACES = {1: "Human", 3: "Dwarf", 4: "Night Elf", 7: "Gnome", 11: "Draenei"}
CLASSES = {1: "Warrior", 2: "Paladin", 3: "Hunter", 4: "Rogue", 5: "Priest", 7: "Shaman",
           8: "Mage", 9: "Warlock", 11: "Druid"}
RACE_MASK = sum(1 << (r - 1) for r in RACES)       # quests some playable race can take
CLASS_MASK = sum(1 << (c - 1) for c in CLASSES)
CLASS_QUEST_SORTS = {-81: 1, -141: 2, -261: 3, -162: 4, -262: 5, -82: 7, -161: 8, -61: 9, -263: 11}
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

# Random filler items: anything obtainable in the game (loot, vendors, quest rewards) with no
# level requirement. Weapons/armor are capped at this item level so filler can't outclass
# the content (the data has level-80 gear with RequiredLevel 0). Gear above the cap is the
# end-game pool: each seed adds one random piece per slot (END_GAME_SLOTS). Neither pool is
# limited to our race or class: any character may get anything, even if they can't use it.
RANDOM_GEAR_MAX_ITEM_LEVEL = 30
END_GAME_SLOTS = {   # slot name -> InventoryTypes
    "Head": (1,), "Neck": (2,), "Shoulders": (3,), "Back": (16,), "Chest": (5, 20), "Wrists": (9,),
    "Hands": (10,), "Waist": (6,), "Legs": (7,), "Feet": (8,), "Finger": (11,), "Trinket": (12,),
    "Weapon": (13, 17, 21), "Off-hand": (14, 22, 23), "Ranged": (15, 25, 26, 28),
}
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
        if sort not in sort_ok and sort not in CLASS_QUEST_SORTS:
            continue
        if minlevel > MAX_LEVEL or info in EXCLUDED_QUEST_INFO or skill:
            continue
        if flags & (QUEST_FLAG_TRACKING | QUEST_FLAG_DAILY | QUEST_FLAG_WEEKLY):
            continue
        if special & SPECIAL_FLAG_REPEATABLE:
            continue
        cand[qid] = dict(id=qid, title=title, level=qlevel, minlevel=minlevel, sort=sort,
                         prev=prev, excl=excl, class_quest=bool(classes), races=races, classes=classes,
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

    # Exclusive groups (>0): only one quest of a group can ever be done (often one per race or
    # class). The APWorld makes the members a slot can take one location; any of them checks it.
    groups = defaultdict(list)
    for qid, q in result.items():
        if q["excl"] > 0:
            groups[q["excl"]].append(qid)
    groups = {g: sorted(m) for g, m in groups.items() if len(m) > 1}
    for q in result.values():
        q["group"] = q["excl"] if q["excl"] in groups else 0
    return result, groups


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

    gear: Dict[int, dict] = {}            # quest -> {"fixed": [items], "choice": [items]}
    new_rewards: Dict[int, Dict[str, int]] = {}   # quest -> every reward column, gear removed
    for row in rows:
        fixed = [(row[f"RewardItem{i}"], row[f"RewardAmount{i}"]) for i in range(1, 5)]
        choices = [(row[f"RewardChoiceItemID{i}"], row[f"RewardChoiceItemQuantity{i}"]) for i in range(1, 7)]
        fixed_gear = [e for e, _ in fixed if e and is_gear(e)]
        choice_gear = [e for e, _ in choices if e and is_gear(e)]
        if not fixed_gear and not choice_gear:
            continue
        # Every class can play, so a "choose one" reward keeps all its options; each seed
        # puts one of them in the pool (the APWorld picks).
        gear[row["ID"]] = {"fixed": [items[e] for e in fixed_gear], "choice": [items[e] for e in choice_gear]}

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


def extract_random_items(cur, skip: Set[int]) -> Tuple[List[dict], List[dict]]:
    """(filler pool, end-game pool): obtainable items without a level requirement (see
    RANDOM_* above). Leaves out grey junk, unique items (a second copy can't be delivered),
    temporary and holiday items, and quest/key/currency items. Race and class don't matter.
    End-game: gear above the item level cap, by slot."""
    slot_of = {inv: slot for slot, invs in END_GAME_SLOTS.items() for inv in invs}
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
        SELECT entry, name, class, subclass, ItemLevel, InventoryType, AllowableClass, AllowableRace
        FROM item_template
        WHERE RequiredLevel = 0 AND class NOT IN ({','.join(map(str, RANDOM_EXCLUDED_CLASSES))})
          AND startquest = 0 AND Quality BETWEEN 1 AND 4 AND duration = 0 AND HolidayId = 0
          AND (Flags & {ITEM_FLAG_DEPRECATED}) = 0 AND maxcount = 0 AND bonding NOT IN (4, 5)
          AND name NOT REGEXP %s""", (RANDOM_NAME_BLOCKLIST,))
    filler, end_game = [], []
    for entry, name, cls, sub, ilvl, inv, classes, races in rows:
        if entry in skip or entry not in obtainable:
            continue
        gear = cls in (ITEM_CLASS_WEAPON, ITEM_CLASS_ARMOR) and inv not in RANDOM_NOT_GEAR_INVTYPES
        if gear and ilvl > RANDOM_GEAR_MAX_ITEM_LEVEL:
            if inv in slot_of:
                end_game.append(dict(id=entry, name=name, slot=slot_of[inv], level=ilvl))
            continue
        category = "gear" if gear else RANDOM_CATEGORIES.get(cls, "other")
        filler.append(dict(id=entry, name=name, category=category))
    return filler, end_game


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


def extract_spells(cur, spells: Dict[int, Spell], quests: Dict[int, dict], grid: AreaGrid,
                   regions: Regions) -> Dict[int, dict]:
    """Per class: trainer ids, spell pool (trainer spells up to MAX_LEVEL plus spells taught by
    class quests), trainer NPC entries, and the earliest region with one of its trainers."""
    source = "trainer_spell"
    if table_exists(cur, TRAINER_BACKUP):
        source = f"(SELECT * FROM trainer_spell UNION SELECT * FROM {TRAINER_BACKUP})"
    out: Dict[int, dict] = {}
    for cls in CLASSES:
        trainers = [r[0] for r in query(cur, "SELECT Id FROM trainer WHERE Type = 0 AND Requirement = %s", (cls,))]
        pool: Dict[int, dict] = {}
        if trainers:
            for spell_id, req_level, cost in query(
                    cur, f"SELECT SpellId, MIN(ReqLevel), MIN(MoneyCost) FROM {source} t "
                         f"WHERE TrainerId IN ({','.join(map(str, trainers))}) AND ReqLevel <= %s "
                         f"GROUP BY SpellId", (MAX_LEVEL,)):
                pool[spell_id] = dict(id=spell_id, level=int(req_level), cost=int(cost), source="trainer")

        # Spells taught by this class's quests up to MAX_LEVEL (stances, pet taming, forms, totems),
        # through LEARN_SPELL effects. Wherever the quest is: many are in racial starting zones
        # outside the fence, so these arrive from the multiworld instead.
        for qid, minlevel, rspell, rdspell in query(cur, f"""
                SELECT q.ID, q.MinLevel, q.RewardSpell, q.RewardDisplaySpell
                FROM quest_template q JOIN quest_template_addon a ON a.ID = q.ID
                WHERE (a.AllowableClasses & {1 << (cls - 1)}) AND q.MinLevel <= %s
                  AND (q.RewardSpell > 0 OR q.RewardDisplaySpell > 0)
                  AND q.ID NOT IN (SELECT entry FROM disables WHERE sourceType = 1)""", (MAX_LEVEL,)):
            for sid in {x for x in (rspell, rdspell) if x}:
                sp = spells.get(sid)
                if not sp:
                    continue
                taught = [t for e, t in zip(sp.effects, sp.triggers) if e == SPELL_EFFECT_LEARN_SPELL and t]
                for t in taught or [sid]:
                    if t in spells:
                        pool.setdefault(t, dict(id=t, level=max(1, minlevel), source=f"quest {qid}"))

        for sid, entry in pool.items():
            sp = spells.get(sid)
            if not sp:
                raise ValueError(f"Spell {sid} not in Spell.dbc")
            entry["base"], entry["rank"] = sp.name, sp.rank

        npcs = set()
        if trainers:
            npcs = {r[0] for r in query(cur, "SELECT CreatureId FROM creature_default_trainer "
                                             f"WHERE TrainerId IN ({','.join(map(str, trainers))})")}
        at = spawn_regions(cur, grid, regions, "creature", "game_event_creature", npcs)
        region = best(set().union(*at.values()) if at else set())
        out[cls] = dict(trainers=trainers, pool=pool, npcs=npcs, region=region)
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


def spell_names(per_class: Dict[int, dict]) -> Dict[int, str]:
    """Spell id -> unique item name across all classes: "Spell: Charge", with the rank when
    a name has several ranks, and the class when two classes have different spells of the
    same name and rank."""
    info: Dict[int, dict] = {}
    for cls, c in per_class.items():
        for sid, sp in c["pool"].items():
            info.setdefault(sid, dict(sp, classes=set()))["classes"].add(cls)
    by_base = defaultdict(set)
    for sid, sp in info.items():
        by_base[sp["base"]].add(sid)
    names: Dict[int, str] = {}
    for sid, sp in info.items():
        name = f"Spell: {sp['base']}"
        if len(by_base[sp["base"]]) > 1 and sp["rank"]:
            name += f" ({sp['rank']})"
        names[sid] = name
    counts = Counter(names.values())
    for sid, name in names.items():
        if counts[name] > 1:
            names[sid] = f"{name} - {'/'.join(CLASSES[c] for c in sorted(info[sid]['classes']))}"
    counts = Counter(names.values())
    for sid, name in names.items():
        if counts[name] > 1:
            names[sid] = f"{name} ({sid})"
    return names


def write_python(path: str, regions_out: list, quests_out: list, spell_names_: Dict[int, str],
                 per_class: Dict[int, dict], gear_names: Dict[int, str], gear_by_quest: Dict[int, dict],
                 random_out: Dict[str, tuple], end_game_out: Dict[str, tuple]):
    def fmt(obj):
        return pprint.pformat(obj, width=100, sort_dicts=False)
    class_spells = {cls: {spell_names_[sid]: sp["level"] for sid, sp in
                          sorted(c["pool"].items(), key=lambda kv: (kv[1]["level"], kv[0]))}
                    for cls, c in per_class.items()}
    trainer_checks = {cls: {"Trainer: " + spell_names_[sid].removeprefix("Spell: "): (sid, sp["level"], sp["cost"])
                            for sid, sp in sorted(c["pool"].items(), key=lambda kv: (kv[1]["level"], kv[0]))
                            if sp["source"] == "trainer"}
                      for cls, c in per_class.items() if c["region"] is not None}
    lines = [
        '"""Generated by tools/extract_data.py from the AzerothCore world DB. Do not edit."""',
        "",
        f"MAX_LEVEL = {MAX_LEVEL}",
        "",
        "# Playable races and classes (game ids)",
        "RACES = " + fmt(RACES),
        "CLASSES = " + fmt(CLASSES),
        "",
        "# name, key item, (first level, last level), explore, rares",
        "REGIONS = " + fmt(regions_out),
        "",
        "ZONE_KEYS = " + fmt(ZONE_KEYS),
        "",
        "# Quests: location name, quest id, region, race/class masks (0 = any), prerequisite quest,",
        "# exclusive group (0 = none; only one member can ever be done)",
        "QUESTS = " + fmt(quests_out),
        "",
        "# Spell items: item name -> spell id",
        "SPELLS = " + fmt({spell_names_[sid]: sid for sid in sorted(spell_names_)}),
        "",
        "# Each class's spell pool: class -> {item name: level it is normally learned}",
        "CLASS_SPELLS = " + fmt(class_spells),
        "",
        "# Class trainer checks (bought in place of a pooled spell):",
        "# class -> {location name: (spell id, level, copper)}",
        "TRAINER_CHECKS = " + fmt(trainer_checks),
        "",
        "# Earliest region with a trainer of each class (trainer checks need it too)",
        "TRAINER_REGION = " + fmt({cls: c["region"] for cls, c in per_class.items() if c["region"] is not None}),
        "",
        "# Quest reward gear moved into the item pool: item name -> item entry",
        "GEAR_ITEMS = " + fmt({gear_names[e]: e for e in sorted(gear_names)}),
        "# quest -> (fixed gear names, choose-one gear names); a seed adds the fixed ones and",
        "# one of the choices for each quest that is a location in that slot",
        "GEAR_BY_QUEST = " + fmt({q: ([gear_names[i["id"]] for i in g["fixed"]],
                                      [gear_names[i["id"]] for i in g["choice"]])
                                  for q, g in sorted(gear_by_quest.items())}),
        "",
        "# Random filler items (obtainable, no level requirement): item name -> (item entry, category)",
        "RANDOM_ITEMS = " + fmt(random_out),
        "",
        "# End-game gear (no level requirement, above the filler item level cap):",
        "# item name -> (item entry, slot). Each seed adds one random piece per slot to the pool.",
        "END_GAME_GEAR = " + fmt(end_game_out),
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


def write_lua(path: str, fence: List[dict], per_class: Dict[int, dict], spell_names_: Dict[int, str],
              groups: Dict[int, List[int]]):
    def lua_set(ids):
        return "{ " + ", ".join(f"[{i}] = true" for i in sorted(ids)) + " }"
    def lua_str(t):
        return '"' + t.replace("\\", "\\\\").replace('"', '\\"') + '"'
    locked = "\n".join(f"        [{cls}] = {lua_set(c['pool'])},  -- {CLASSES[cls]}"
                        for cls, c in per_class.items())
    checks = []
    for cls, c in per_class.items():
        if c["region"] is None:
            continue
        rows = "\n".join(
            f"            {{ spell = {sid}, level = {sp['level']}, cost = {sp['cost']}, "
            f"name = {lua_str(spell_names_[sid].removeprefix('Spell: '))} }},"
            for sid, sp in sorted(c["pool"].items(), key=lambda kv: (kv[1]["level"], kv[0]))
            if sp["source"] == "trainer")
        checks.append(f"        [{cls}] = {{  -- {CLASSES[cls]}\n{rows}\n        }},")
    trainers = ", ".join(f"[{e}] = {cls}" for cls, c in per_class.items() for e in sorted(c["npcs"]))
    group_text = ", ".join(f"[{q}] = {{ {', '.join(map(str, members))} }}"
                           for members in groups.values() for q in members)
    zones = "\n".join(
        f"        [{z['zone']}] = {{ name = {lua_str(z['name'])}, key = {z['key']}, "
        f"key_name = {lua_str(z['key_name'])}"
        + (f", free_areas = {lua_set(z['free'])}" if z["free"] else "") + " },"
        for z in fence)
    nl = "\n"
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
    -- Per class: spells that are Archipelago items, unlearned unless received from the multiworld.
    locked_spells = {{
{locked}
    }},
    -- Either/or quests: quest -> every member of its exclusive group (completing any one
    -- checks whichever member is the slot's location).
    quest_groups = {{ {group_text} }},
    -- Class trainer NPC entry -> class; you must stand at one of your class to buy a check.
    trainers = {{ {trainers} }},
    -- Per class: trainer checks, bought from a class trainer in place of the spell (location kind 5).
    trainer_checks = {{
{nl.join(checks)}
    }},
}}
"""
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def write_sql(path: str, per_class: Dict[int, dict]):
    conds = [f"(`TrainerId` IN ({', '.join(map(str, c['trainers']))})\n"
             f"   AND `SpellId` IN ({', '.join(map(str, sorted(c['pool'])))}))"
             for c in per_class.values() if c["trainers"] and c["pool"]]
    where = "\n  OR ".join(conds)
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
        quests, groups = extract_quests(cur, grid, regions, areas, triggers)
        rares = extract_rares(cur, grid, regions)
        per_class = extract_spells(cur, spells, quests, grid, regions)
        gear_by_quest, strip = extract_rewards(cur, quests)
        gear_items = {i["id"]: i for g in gear_by_quest.values() for i in g["fixed"] + g["choice"]}
        random_items, end_game = extract_random_items(cur, set(gear_items))
    conn.close()

    gear_names = {it["id"]: f"Item: {n}" for n, it in unique_names(
        sorted(gear_items.values(), key=lambda i: i["id"]), lambda i: i["name"]).items()}

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
            "explore": {n: a["id"] for n, a in all_e.items() if a["region"] == i},
            "rares": {n: c["id"] for n, c in all_r.items() if c["region"] == i},
        })
    quests_out = [dict(name=n, id=q["id"], region=q["region"], races=q["races"], classes=q["classes"],
                       prev=abs(q["prev"]), group=q["group"]) for n, q in all_q.items()]

    names = spell_names(per_class)
    taken = set(gear_names.values())
    random_out = {}
    for n, it in unique_names(sorted(random_items, key=lambda i: i["id"]), lambda i: i["name"]).items():
        name = f"Item: {n}"
        random_out[name if name not in taken else f"{name} ({it['id']})"] = (it["id"], it["category"])
    taken |= set(random_out)
    end_game_out = {}
    for n, it in unique_names(sorted(end_game, key=lambda i: i["id"]), lambda i: i["name"]).items():
        name = f"Item: {n}"
        end_game_out[name if name not in taken else f"{name} ({it['id']})"] = (it["id"], it["slot"])

    write_python(os.path.join(ROOT, "apworld", "wow", "gen_data.py"), regions_out, quests_out, names,
                 per_class, gear_names, gear_by_quest, random_out, end_game_out)
    write_reward_sql(os.path.join(ROOT, "server", "sql", "world", "archipelago_quest_rewards.sql"), strip)
    write_lua(os.path.join(ROOT, "server", "lua_scripts", "archipelago_data.lua"),
              fence_zones(areas, northshire), per_class, names, groups)
    os.makedirs(os.path.join(ROOT, "server", "sql", "world"), exist_ok=True)
    write_sql(os.path.join(ROOT, "server", "sql", "world", "archipelago_trainers.sql"), per_class)

    print(f"Northshire areas: {sorted(northshire)}  (start position area {start_area})")
    print(f"Either/or quest groups: {len(groups)}")
    for i, r in enumerate(regions_out):
        n = sum(1 for q in quests_out if q["region"] == i)
        print(f"{r['name']:18} quests {n:3} (all races/classes)  explore {len(r['explore']):2}  rares {len(r['rares']):2}")
    print(f"Gear: {len(gear_names)} items from {len(strip)} quests")
    print(f"Random filler items: {len(random_out)}  "
          + ", ".join(f"{c} {n}" for c, n in sorted(Counter(c for _, c in random_out.values()).items())))
    print(f"End-game gear: {len(end_game_out)}  "
          + ", ".join(f"{s} {n}" for s, n in sorted(Counter(s for _, s in end_game_out.values()).items())))
    for cls, c in per_class.items():
        n_tr = sum(1 for sp in c["pool"].values() if sp["source"] == "trainer")
        where = REGION_DEFS[c["region"]]["name"] if c["region"] is not None else "none in our zones"
        print(f"{CLASSES[cls]:8} spells {len(c['pool']):2}  trainer checks {n_tr:2}  "
              f"trainers {len(c['npcs']):2} (first in {where})")


if __name__ == "__main__":
    main()
