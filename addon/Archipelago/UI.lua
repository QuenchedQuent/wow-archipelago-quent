-- Archipelago addon UI: status line, location tracker, received-item feed.
local AP = Archipelago

local f = CreateFrame("Frame", "ArchipelagoFrame", UIParent)
f:SetSize(320, 420)
f:SetPoint("RIGHT", UIParent, "RIGHT", -40, 0)
f:SetBackdrop({
    bgFile = "Interface\\DialogFrame\\UI-DialogBox-Background",
    edgeFile = "Interface\\Tooltips\\UI-Tooltip-Border",
    tile = true, tileSize = 16, edgeSize = 16,
    insets = { left = 4, right = 4, top = 4, bottom = 4 },
})
f:SetMovable(true)
f:EnableMouse(true)
f:RegisterForDrag("LeftButton")
f:SetScript("OnDragStart", f.StartMoving)
f:SetScript("OnDragStop", function(self)
    self:StopMovingOrSizing()
    local point, _, rel, x, y = self:GetPoint()
    ArchipelagoDB.pos = { point, rel, x, y }
end)
f:Hide()

local title = f:CreateFontString(nil, "OVERLAY", "GameFontNormalLarge")
title:SetPoint("TOP", 0, -10)
title:SetText("Archipelago")

local close = CreateFrame("Button", nil, f, "UIPanelCloseButton")
close:SetPoint("TOPRIGHT", -2, -2)

local status = f:CreateFontString(nil, "OVERLAY", "GameFontHighlightSmall")
status:SetPoint("TOPLEFT", 12, -34)
status:SetPoint("TOPRIGHT", -12, -34)
status:SetJustifyH("LEFT")

-- Tracker (scrolling)
local scroll = CreateFrame("ScrollFrame", "ArchipelagoScroll", f, "UIPanelScrollFrameTemplate")
scroll:SetPoint("TOPLEFT", 12, -54)
scroll:SetPoint("BOTTOMRIGHT", -32, 130)

local child = CreateFrame("Frame", nil, scroll)
child:SetSize(270, 10)
scroll:SetScrollChild(child)

local list = child:CreateFontString(nil, "OVERLAY", "GameFontHighlightSmall")
list:SetPoint("TOPLEFT")
list:SetWidth(270)
list:SetJustifyH("LEFT")
list:SetJustifyV("TOP")

-- Feed
local feedTitle = f:CreateFontString(nil, "OVERLAY", "GameFontNormal")
feedTitle:SetPoint("BOTTOMLEFT", 12, 112)
feedTitle:SetText("Received")

local feed = f:CreateFontString(nil, "OVERLAY", "GameFontHighlightSmall")
feed:SetPoint("TOPLEFT", feedTitle, "BOTTOMLEFT", 0, -4)
feed:SetWidth(296)
feed:SetJustifyH("LEFT")
feed:SetJustifyV("TOP")

function AP:Refresh()
    local s = self.status
    local conn = s.connected and "|cff00ff00Connected|r" or "|cffff4040Bridge offline|r"
    local slot = s.slot ~= "" and (" as " .. s.slot) or ""
    local goal = s.goal > 0 and ("  Goal: level " .. s.goal) or ""
    status:SetText(string.format("%s%s  %d/%d%s", conn, slot, s.checked, s.total, goal))

    -- Group by kind, sorted by name.
    local showChecked = ArchipelagoDB and ArchipelagoDB.showChecked
    local groups = {}
    for _, loc in pairs(self.locations) do
        if showChecked or not loc.checked then
            groups[loc.kind] = groups[loc.kind] or {}
            table.insert(groups[loc.kind], loc)
        end
    end
    local lines = {}
    for kind = 0, 9 do
        local g = groups[kind]
        if g then
            table.sort(g, function(a, b) return a.name < b.name end)
            table.insert(lines, "|cffffd100" .. (self.KIND_NAMES[kind] or "Other") .. "|r")
            for _, loc in ipairs(g) do
                local color = loc.checked and "|cff808080" or "|cffffffff"
                table.insert(lines, "  " .. color .. loc.name .. "|r")
            end
        end
    end
    if #lines == 0 then
        lines[1] = next(self.locations) and "All checks done!" or "No data yet - is the bridge running? (/ap sync)"
    end
    list:SetText(table.concat(lines, "\n"))
    child:SetHeight(math.max(list:GetStringHeight(), 10))

    local flines = {}
    for i, e in ipairs(self.feed) do
        if i > 7 then break end
        table.insert(flines, string.format("|cff808080%s|r |cffffff00%s|r from %s", e.time, e.item, e.sender))
    end
    feed:SetText(table.concat(flines, "\n"))
end

function AP:Toggle()
    if f:IsShown() then f:Hide() else f:Show(); self:Refresh() end
end

f:SetScript("OnShow", function()
    if ArchipelagoDB and ArchipelagoDB.pos then
        local p = ArchipelagoDB.pos
        f:ClearAllPoints()
        f:SetPoint(p[1], UIParent, p[2], p[3], p[4])
    end
end)
