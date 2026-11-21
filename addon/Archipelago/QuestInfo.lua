-- Shows which Archipelago item a quest holds, next to the quest window and the quest log.
local AP = Archipelago

local function makeBox(name, parent)
    local box = CreateFrame("Frame", name, parent)
    box:SetSize(230, 64)
    box:SetBackdrop({
        bgFile = "Interface\\DialogFrame\\UI-DialogBox-Background",
        edgeFile = "Interface\\Tooltips\\UI-Tooltip-Border",
        tile = true, tileSize = 16, edgeSize = 16,
        insets = { left = 4, right = 4, top = 4, bottom = 4 },
    })
    local header = box:CreateFontString(nil, "OVERLAY", "GameFontNormal")
    header:SetPoint("TOPLEFT", 10, -8)
    header:SetText("Archipelago check")
    local text = box:CreateFontString(nil, "OVERLAY", "GameFontHighlightSmall")
    text:SetPoint("TOPLEFT", header, "BOTTOMLEFT", 0, -4)
    text:SetWidth(210)
    text:SetJustifyH("LEFT")
    box.text = text
    box:Hide()
    return box
end

local function fill(box, title)
    local loc = AP:QuestLocation(title)
    if not loc then
        box:Hide()
        return
    end
    local line = AP:ColorContents(loc)
    if loc.checked then line = line .. "\n|cff808080(already checked)|r" end
    box.text:SetText(line)
    box:SetHeight(box.text:GetStringHeight() + 34)
    box:Show()
end

-- Quest giver window (offer, progress and reward pages).
local questBox = makeBox("ArchipelagoQuestBox", QuestFrame)
questBox:SetPoint("TOPLEFT", QuestFrame, "TOPRIGHT", -32, -70)

local events = CreateFrame("Frame")
events:RegisterEvent("QUEST_DETAIL")
events:RegisterEvent("QUEST_PROGRESS")
events:RegisterEvent("QUEST_COMPLETE")
events:RegisterEvent("QUEST_FINISHED")
events:SetScript("OnEvent", function(_, event)
    if event == "QUEST_FINISHED" then
        questBox:Hide()
    else
        fill(questBox, GetTitleText())
    end
end)

-- Quest log (the selected quest).
local logBox = makeBox("ArchipelagoQuestLogBox", QuestLogFrame)
logBox:SetPoint("TOPLEFT", QuestLogFrame, "TOPRIGHT", -2, -40)

local function updateLog()
    if not QuestLogFrame:IsShown() then return end
    local index = GetQuestLogSelection()
    local title, _, _, _, isHeader = GetQuestLogTitle(index)
    if not title or isHeader then
        logBox:Hide()
    else
        fill(logBox, title)
    end
end

if QuestLog_UpdateQuestDetails then
    hooksecurefunc("QuestLog_UpdateQuestDetails", updateLog)
end
QuestLogFrame:HookScript("OnShow", updateLog)

-- Contents can arrive after the window opened; refresh whatever is visible.
function AP:RefreshQuestInfo()
    if questBox:IsShown() or QuestFrame:IsShown() then fill(questBox, GetTitleText()) end
    updateLog()
end
