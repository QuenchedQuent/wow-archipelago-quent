-- Archipelago trainer checks in the class trainer window.
--
-- The server sells one check per pooled spell at class trainers (see the server script).
-- The trainer window only lists what the server sends as trainer spells, so this file
-- appends an "Archipelago" section after the real ones by wrapping the trainer API that
-- Blizzard_TrainerUI reads (it looks these globals up on every call). Indices past the real
-- services are ours; everything else goes to the original functions.
local AP = Archipelago

local LOC_TRAINER = 5
local HEADER = "Archipelago"
local ICON_FALLBACK = "Interface\\Icons\\INV_Misc_QuestionMark"

local orig = {}
for _, name in ipairs({
    "GetNumTrainerServices", "GetTrainerServiceInfo", "GetTrainerServiceCost", "GetTrainerServiceIcon",
    "GetTrainerServiceLevelReq", "GetTrainerServiceSkillReq", "GetTrainerServiceNumAbilityReq",
    "GetTrainerServiceAbilityReq", "GetTrainerServiceStepReq", "GetTrainerServiceDescription",
    "GetTrainerServiceSkillLine", "GetTrainerServiceItemLink", "GetTrainerSelectionIndex",
    "SelectTrainerService", "BuyTrainerService", "ExpandTrainerSkillLine", "CollapseTrainerSkillLine",
}) do
    orig[name] = _G[name]
end

local expanded = true
local selected      -- index into our list, when one of ours is selected

local function locationId(spell)
    return AP.LOC_BASE + LOC_TRAINER * AP.KIND_SIZE + spell
end

-- "available" (buy now), "unavailable" (level too low) or "used" (bought).
local function status(check, loc)
    if loc.checked then return "used" end
    if UnitLevel("player") < check.level then return "unavailable" end
    return "available"
end

-- Our part of the list: a header, then the checks (respecting the window's filter).
local function entries()
    if IsTradeskillTrainer() then return {} end
    local checks = {}
    for spell, check in pairs(AP.trainerChecks) do
        local loc = AP.locations[locationId(spell)]
        if loc then
            local st = status(check, loc)
            if GetTrainerServiceTypeFilter(st) then
                table.insert(checks, { check = check, loc = loc, status = st })
            end
        end
    end
    if #checks == 0 then return {} end
    table.sort(checks, function(a, b)
        if a.check.level ~= b.check.level then return a.check.level < b.check.level end
        return a.check.name < b.check.name
    end)
    local list = { { header = true } }
    if expanded then
        for _, e in ipairs(checks) do table.insert(list, e) end
    end
    return list
end

-- Our entry for a trainer-window index, or nil if the index is a real service.
local function ours(index)
    local real = orig.GetNumTrainerServices()
    if not index or index <= real then return nil end
    return entries()[index - real]
end

local function display(e)
    return e.check.name .. " check"
end

GetNumTrainerServices = function()
    return orig.GetNumTrainerServices() + #entries()
end

GetTrainerServiceInfo = function(index)
    local e = ours(index)
    if not e then return orig.GetTrainerServiceInfo(index) end
    if e.header then return HEADER, nil, "header", expanded end
    return display(e), "Archipelago", e.status, nil
end

GetTrainerServiceCost = function(index)
    local e = ours(index)
    if not e then return orig.GetTrainerServiceCost(index) end
    return e.header and 0 or e.check.cost, 0, 0
end

GetTrainerServiceIcon = function(index)
    local e = ours(index)
    if not e then return orig.GetTrainerServiceIcon(index) end
    if e.header then return nil end
    local _, _, icon = GetSpellInfo(e.check.spell)
    return icon or ICON_FALLBACK
end

GetTrainerServiceLevelReq = function(index)
    local e = ours(index)
    if not e then return orig.GetTrainerServiceLevelReq(index) end
    return e.header and 0 or e.check.level
end

GetTrainerServiceSkillReq = function(index)
    if ours(index) then return nil end
    return orig.GetTrainerServiceSkillReq(index)
end

GetTrainerServiceNumAbilityReq = function(index)
    if ours(index) then return 0 end
    return orig.GetTrainerServiceNumAbilityReq(index)
end

GetTrainerServiceAbilityReq = function(index, i)
    if ours(index) then return nil end
    return orig.GetTrainerServiceAbilityReq(index, i)
end

GetTrainerServiceStepReq = function(index)
    if ours(index) then return nil end
    return orig.GetTrainerServiceStepReq(index)
end

GetTrainerServiceSkillLine = function(index)
    if ours(index) then return HEADER end
    return orig.GetTrainerServiceSkillLine(index)
end

GetTrainerServiceItemLink = function(index)
    if ours(index) then return nil end
    return orig.GetTrainerServiceItemLink(index)
end

GetTrainerServiceDescription = function(index)
    local e = ours(index)
    if not e then return orig.GetTrainerServiceDescription(index) end
    if e.header then return "" end
    local text = "An Archipelago check, sold in place of " .. e.check.name .. ".\n\nHolds: "
        .. AP:ColorContents(e.loc)
    if e.status == "used" then text = text .. "\n\n|cff808080Already bought.|r" end
    return text
end

GetTrainerSelectionIndex = function()
    if selected then
        local list = entries()
        if list[selected] then return orig.GetNumTrainerServices() + selected end
        selected = nil
    end
    return orig.GetTrainerSelectionIndex()
end

SelectTrainerService = function(index)
    local real = orig.GetNumTrainerServices()
    if index and index > real then
        selected = index - real
    else
        selected = nil
        orig.SelectTrainerService(index)
    end
end

BuyTrainerService = function(index)
    local e = ours(index)
    if not e then return orig.BuyTrainerService(index) end
    if e.header or e.status ~= "available" then return end
    if GetMoney() < e.check.cost then
        UIErrorsFrame:AddMessage(ERR_NOT_ENOUGH_MONEY, 1.0, 0.1, 0.1, 1.0)
        return
    end
    SendAddonMessage(AP.PREFIX, "BUY\t" .. e.check.spell, "WHISPER", UnitName("player"))
end

local function redraw()
    if ClassTrainerFrame and ClassTrainerFrame:IsShown() then
        ClassTrainer_SetSelection(GetTrainerSelectionIndex())
        ClassTrainerFrame_Update()
    end
end

ExpandTrainerSkillLine = function(index)
    local e = ours(index)
    if e and e.header then
        expanded = true
        redraw()
        return
    end
    if index == 0 then expanded = true end   -- "expand all"
    orig.ExpandTrainerSkillLine(index)
end

CollapseTrainerSkillLine = function(index)
    local e = ours(index)
    if e and e.header then
        expanded = false
        selected = nil
        redraw()
        return
    end
    if index == 0 then                       -- "collapse all"
        expanded = false
        selected = nil
    end
    orig.CollapseTrainerSkillLine(index)
end

-- The icon in the detail pane shows a tooltip for the selected service.
local origSetTrainerService = GameTooltip.SetTrainerService
GameTooltip.SetTrainerService = function(self, index)
    local e = ours(index)
    if not e then return origSetTrainerService(self, index) end
    if e.header then return end
    self:SetText(display(e))
    self:AddLine("Holds: " .. AP:ColorContents(e.loc), 1, 1, 1, true)
    self:Show()
end

-- New contents, a purchase confirmed, or money/level changes: redraw an open window.
function AP:RefreshTrainer()
    redraw()
end

local events = CreateFrame("Frame")
events:RegisterEvent("PLAYER_MONEY")
events:RegisterEvent("PLAYER_LEVEL_UP")
events:RegisterEvent("TRAINER_CLOSED")
events:SetScript("OnEvent", function(_, event)
    if event == "TRAINER_CLOSED" then
        selected = nil
    else
        redraw()
    end
end)
