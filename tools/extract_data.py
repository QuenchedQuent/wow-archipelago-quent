"""
Generate the randomizer's game data from a real AzerothCore world DB + server data files.

Reads:
  - acore_world (quests, quest givers/enders, creature spawns, trainers)
  - <data>/dbc/AreaTable.dbc, <data>/dbc/Spell.dbc   (names, zone tree, learn effects)
  - <data>/maps/*.map                                (area grid, to place spawns in zones)

Writes:
  - apworld/wow/gen_data.py                     regions, quests, exploration, rares, spells
  - server/lua_scripts/archipelago_data.lua     zone fence + locked spell list for the server
  - server/sql/world/archipelago_trainers.sql   removes pooled spells from class trainers

Usage (MySQL of the Windows package must be running, e.g. via Start.bat):
  .venv\\Scripts\\python tools\\extract_data.py --data D:\\WoW\\Server\\data
"""
from __future__ import annotations

import argparse
import os
import pprint
import struct
import sys
from collections import defaultdict
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


def extract_quests(cur, grid: AreaGrid, regions: Regions, areas: Dict[int, Area]):
    rows = query(cur, """
        SELECT q.ID, q.LogTitle, q.QuestLevel, q.MinLevel, q.QuestSortID, q.QuestInfoID,
               q.AllowableRaces, q.Flags, q.RewardSpell, q.RewardDisplaySpell,
               COALESCE(a.AllowableClasses, 0), COALESCE(a.PrevQuestID, 0),
               COALESCE(a.ExclusiveGroup, 0), COALESCE(a.BreadcrumbForQuestId, 0),
               COALESCE(a.SpecialFlags, 0), COALESCE(a.RequiredSkillID, 0)
        FROM quest_template q LEFT JOIN quest_template_addon a ON a.ID = q.ID""")
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

    # Exclusive groups (>0): only one of the group can ever be done; keep the lowest id.
    groups = defaultdict(list)
    for qid, q in result.items():
        if q["excl"] > 0:
            groups[q["excl"]].append(qid)
    for members in groups.values():
        for qid in sorted(members)[1:]:
            result.pop(qid, None)

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
    return result


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
    for spell_id, req_level in query(
            cur, f"SELECT DISTINCT SpellId, ReqLevel FROM trainer_spell "
                 f"WHERE TrainerId IN ({','.join(map(str, trainers))}) AND ReqLevel <= %s",
            (MAX_LEVEL,)):
        pool[spell_id] = dict(id=spell_id, level=req_level, source="trainer")

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
    return {"trainers": trainers, "pool": pool}


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


def write_python(path: str, regions_out: list, spells_out: Dict[str, dict]):
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
    ]
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines))


def write_lua(path: str, free_areas: Set[int], locked: List[int]):
    def lua_set(ids):
        return "{ " + ", ".join(f"[{i}] = true" for i in sorted(ids)) + " }"
    text = f"""-- Generated by tools/extract_data.py from the AzerothCore world DB. Do not edit.
-- Read lazily by archipelago.lua (script load order does not matter).
ArchipelagoData = {{
    -- Zones gated by a "Zone: X" item; free_areas are always allowed (start area).
    zone_locks = {{
        [{ELWYNN_ZONE}] = {{ name = "Elwynn Forest", free_areas = {lua_set(free_areas)} }},
        [{WESTFALL_ZONE}] = {{ name = "Westfall" }},
    }},
    -- Spells that are Archipelago items: unlearned unless received from the multiworld.
    locked_spells = {lua_set(locked)},
}}
"""
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def write_sql(path: str, trainers: List[int], locked: List[int]):
    text = f"""-- Generated by tools/extract_data.py. Do not edit.
-- Class trainers no longer teach spells that are Archipelago items (they arrive from the
-- multiworld instead), so players can't pay for a spell the server would take away again.
DELETE FROM `trainer_spell` WHERE `TrainerId` IN ({', '.join(map(str, trainers))})
  AND `SpellId` IN ({', '.join(map(str, sorted(locked)))});
"""
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


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
        quests = extract_quests(cur, grid, regions, areas)
        rares = extract_rares(cur, grid, regions)
        sp = extract_spells(cur, spells, quests)
    conn.close()

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

    write_python(os.path.join(ROOT, "apworld", "wow", "gen_data.py"), regions_out, spells_out)
    write_lua(os.path.join(ROOT, "server", "lua_scripts", "archipelago_data.lua"), northshire, locked)
    os.makedirs(os.path.join(ROOT, "server", "sql", "world"), exist_ok=True)
    write_sql(os.path.join(ROOT, "server", "sql", "world", "archipelago_trainers.sql"), sp["trainers"], locked)

    print(f"Northshire areas: {sorted(northshire)}  (start position area {start_area})")
    for r in regions_out:
        print(f"{r['name']:18} quests {len(r['quests']):3}  explore {len(r['explore']):2}  rares {len(r['rares']):2}")
    print(f"Spells: {len(spells_out)}")
    for name, s in spells_out.items():
        print(f"  L{s['level']:<2} {s['id']:>6}  {name}  [{s['source']}]")


if __name__ == "__main__":
    main()
