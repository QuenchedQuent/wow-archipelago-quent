-- Archipelago addon core: receives state from the server (Eluna) over addon messages.
--
-- Server -> addon messages (prefix "AP", tab separated):
--   S <connected> <slot> <checked> <total> <goalLevel>   status
--   L <locationId> <checked 0/1> <name>                  tracker entry
--   C <locationId>                                       location became checked
--   I <itemName> <sender>                                item received
--   RESET / END                                          full sync boundaries
-- Addon -> server:
--   SYNC                                                 request full sync

Archipelago = {
    PREFIX = "AP",
    LOC_BASE = 870000000,
    KIND_SIZE = 1000000,
    KIND_NAMES = { [0] = "Quests", [1] = "Exploration", [2] = "Levels", [3] = "Rares",
                   [4] = "Vendors", [5] = "Trainers" },
    status = { connected = false, slot = "", checked = 0, total = 0, goal = 0 },
    locations = {},   -- id -> { name, checked, kind }
    feed = {},        -- recent received items, newest first
    syncing = false,
}
local AP = Archipelago
local MAX_FEED = 10

local function split(msg)
    local out, pos = {}, 1
    while true do
        local s, e = string.find(msg, "\t", pos, true)
        if not s then
            table.insert(out, string.sub(msg, pos))
            return out
        end
        table.insert(out, string.sub(msg, pos, s - 1))
        pos = e + 1
    end
end

function AP:Kind(id)
    return math.floor((id - self.LOC_BASE) / self.KIND_SIZE)
end

function AP:RequestSync()
    SendAddonMessage(self.PREFIX, "SYNC", "WHISPER", UnitName("player"))
end

function AP:Print(text)
    DEFAULT_CHAT_FRAME:AddMessage("|cff00ccff[Archipelago]|r " .. text)
end

local handlers = {}

handlers.S = function(f)
    local s = AP.status
    s.connected = f[2] == "1"
    s.slot = f[3] or ""
    s.checked = tonumber(f[4]) or 0
    s.total = tonumber(f[5]) or 0
    s.goal = tonumber(f[6]) or 0
end

handlers.L = function(f)
    local id = tonumber(f[2])
    if not id then return end
    AP.locations[id] = { name = f[4] or ("Location " .. id), checked = f[3] == "1", kind = AP:Kind(id) }
end

handlers.C = function(f)
    local loc = AP.locations[tonumber(f[2])]
    if loc then loc.checked = true end
end

handlers.I = function(f)
    table.insert(AP.feed, 1, { item = f[2] or "?", sender = f[3] or "?", time = date("%H:%M") })
    while #AP.feed > MAX_FEED do table.remove(AP.feed) end
    ArchipelagoDB.feed = AP.feed
end

handlers.RESET = function()
    AP.locations = {}
    AP.syncing = true
end

handlers.END = function()
    AP.syncing = false
end

local frame = CreateFrame("Frame")
frame:RegisterEvent("ADDON_LOADED")
frame:RegisterEvent("PLAYER_ENTERING_WORLD")
frame:RegisterEvent("CHAT_MSG_ADDON")
frame:SetScript("OnEvent", function(_, event, ...)
    if event == "ADDON_LOADED" then
        if ... == "Archipelago" then
            ArchipelagoDB = ArchipelagoDB or {}
            AP.feed = ArchipelagoDB.feed or {}
        end
    elseif event == "PLAYER_ENTERING_WORLD" then
        AP:RequestSync()
    elseif event == "CHAT_MSG_ADDON" then
        local prefix, msg = ...
        if prefix ~= AP.PREFIX then return end
        local fields = split(msg)
        local h = handlers[fields[1]]
        if h then
            h(fields)
            if not AP.syncing and AP.Refresh then AP:Refresh() end
        end
    end
end)

SLASH_ARCHIPELAGO1 = "/ap"
SlashCmdList["ARCHIPELAGO"] = function(arg)
    arg = string.lower(arg or "")
    if arg == "sync" then
        AP:RequestSync()
        AP:Print("Sync requested.")
    elseif arg == "all" then
        ArchipelagoDB.showChecked = not ArchipelagoDB.showChecked
        AP:Print("Showing checked locations: " .. tostring(ArchipelagoDB.showChecked))
        if AP.Refresh then AP:Refresh() end
    elseif arg == "" then
        if AP.Toggle then AP:Toggle() end
    else
        AP:Print("/ap - toggle window, /ap sync - resync, /ap all - show checked too")
    end
end
