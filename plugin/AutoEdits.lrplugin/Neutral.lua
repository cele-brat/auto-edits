--[[ Auto Edits: the "no edit" starting point.

  Every setting a preset can touch, at its neutral value. White balance, crop,
  lens corrections, sharpening and noise reduction are left alone. ]]

local Neutral = {}

function Neutral.settings()
    local s = {
        Exposure2012 = 0, Contrast2012 = 0, Highlights2012 = 0, Shadows2012 = 0, Whites2012 = 0, Blacks2012 = 0,
        Texture = 0, Clarity2012 = 0, Dehaze = 0, Vibrance = 0, Saturation = 0,
        ParametricShadows = 0, ParametricDarks = 0, ParametricLights = 0, ParametricHighlights = 0,
        ParametricShadowSplit = 25, ParametricMidtoneSplit = 50, ParametricHighlightSplit = 75,
        SplitToningShadowHue = 0, SplitToningShadowSaturation = 0,
        SplitToningHighlightHue = 0, SplitToningHighlightSaturation = 0, SplitToningBalance = 0,
        ColorGradeMidtoneHue = 0, ColorGradeMidtoneSat = 0, ColorGradeMidtoneLum = 0,
        ColorGradeShadowLum = 0, ColorGradeHighlightLum = 0,
        ColorGradeGlobalHue = 0, ColorGradeGlobalSat = 0, ColorGradeGlobalLum = 0, ColorGradeBlending = 50,
        ShadowTint = 0, RedHue = 0, RedSaturation = 0, GreenHue = 0, GreenSaturation = 0, BlueHue = 0, BlueSaturation = 0,
        GrainAmount = 0, PostCropVignetteAmount = 0,
        ConvertToGrayscale = false,
        ToneCurveName2012 = 'Linear',
        ToneCurvePV2012 = { 0, 0, 255, 255 },
        ToneCurvePV2012Red = { 0, 0, 255, 255 },
        ToneCurvePV2012Green = { 0, 0, 255, 255 },
        ToneCurvePV2012Blue = { 0, 0, 255, 255 },
    }
    for _, c in ipairs { 'Red', 'Orange', 'Yellow', 'Green', 'Aqua', 'Blue', 'Purple', 'Magenta' } do
        s['HueAdjustment' .. c] = 0
        s['SaturationAdjustment' .. c] = 0
        s['LuminanceAdjustment' .. c] = 0
    end
    return s
end

-- Settings whose hue only matters when its saturation is above zero.
local HUE_OF = { SplitToningShadowHue = 'SplitToningShadowSaturation',
                 SplitToningHighlightHue = 'SplitToningHighlightSaturation',
                 ColorGradeMidtoneHue = 'ColorGradeMidtoneSat', ColorGradeGlobalHue = 'ColorGradeGlobalSat' }
-- Split points and balances do nothing on their own.
local INERT = { ParametricShadowSplit = true, ParametricMidtoneSplit = true, ParametricHighlightSplit = true,
                SplitToningBalance = true, ColorGradeBlending = true, ToneCurveName2012 = true }

local function straightCurve(c)
    if type(c) ~= 'table' then return true end
    for i = 1, #c - 1, 2 do
        if math.abs((c[i] or 0) - (c[i + 1] or 0)) > 0.5 then return false end
    end
    return true
end

-- The names of the settings on this photo that are not neutral (an empty list
-- means the photo is unedited as far as presets go).
function Neutral.editedKeys(develop)
    local edited = {}
    for k, v in pairs(Neutral.settings()) do
        local have = develop[k]
        if have ~= nil and not INERT[k] then
            if type(v) == 'table' then
                if not straightCurve(have) then edited[#edited + 1] = k end
            elseif type(v) == 'boolean' then
                if have ~= v then edited[#edited + 1] = k end
            elseif type(have) == 'number' and math.abs(have - v) > 0.001 then
                local sat = HUE_OF[k]
                if not sat or (tonumber(develop[sat]) or 0) > 0 then edited[#edited + 1] = k end
            end
        end
    end
    table.sort(edited)
    return edited
end

return Neutral
