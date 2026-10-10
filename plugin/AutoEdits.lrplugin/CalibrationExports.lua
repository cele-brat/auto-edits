--[[ Auto Edits: calibration exports.

  For the selected photos (at most 6) this exports, inside a folder you choose:

      LR calibration/base/            neutral settings
      LR calibration/<name>/          neutral plus ONE setting changed, one folder per setting
      LR calibration/manifest.json    which setting each folder holds

  One setting at a time lets the Auto Edits engine learn exactly how Lightroom
  renders each slider. Photos get a snapshot first and their settings are put
  back when the run ends, also when it fails or is cancelled.
]]

local LrApplication = import 'LrApplication'
local LrDialogs = import 'LrDialogs'
local LrExportSession = import 'LrExportSession'
local LrFileUtils = import 'LrFileUtils'
local LrFunctionContext = import 'LrFunctionContext'
local LrPathUtils = import 'LrPathUtils'
local LrProgressScope = import 'LrProgressScope'
local LrTasks = import 'LrTasks'

local MAX_PHOTOS = 6
local LONG_EDGE = 1600

local VARIATIONS = {
    { folder = "exposure_p100", settings = { Exposure2012 = 1.0 }, json = [[{"Exposure2012": 1.0}]] },
    { folder = "exposure_m100", settings = { Exposure2012 = -1.0 }, json = [[{"Exposure2012": -1.0}]] },
    { folder = "contrast_p40", settings = { Contrast2012 = 40 }, json = [[{"Contrast2012": 40}]] },
    { folder = "contrast_m40", settings = { Contrast2012 = -40 }, json = [[{"Contrast2012": -40}]] },
    { folder = "highlights_m60", settings = { Highlights2012 = -60 }, json = [[{"Highlights2012": -60}]] },
    { folder = "highlights_p40", settings = { Highlights2012 = 40 }, json = [[{"Highlights2012": 40}]] },
    { folder = "shadows_p50", settings = { Shadows2012 = 50 }, json = [[{"Shadows2012": 50}]] },
    { folder = "shadows_m40", settings = { Shadows2012 = -40 }, json = [[{"Shadows2012": -40}]] },
    { folder = "whites_p40", settings = { Whites2012 = 40 }, json = [[{"Whites2012": 40}]] },
    { folder = "whites_m40", settings = { Whites2012 = -40 }, json = [[{"Whites2012": -40}]] },
    { folder = "blacks_p40", settings = { Blacks2012 = 40 }, json = [[{"Blacks2012": 40}]] },
    { folder = "blacks_m40", settings = { Blacks2012 = -40 }, json = [[{"Blacks2012": -40}]] },
    { folder = "texture_p50", settings = { Texture = 50 }, json = [[{"Texture": 50}]] },
    { folder = "clarity_p40", settings = { Clarity2012 = 40 }, json = [[{"Clarity2012": 40}]] },
    { folder = "clarity_m40", settings = { Clarity2012 = -40 }, json = [[{"Clarity2012": -40}]] },
    { folder = "vibrance_p50", settings = { Vibrance = 50 }, json = [[{"Vibrance": 50}]] },
    { folder = "vibrance_m50", settings = { Vibrance = -50 }, json = [[{"Vibrance": -50}]] },
    { folder = "saturation_p40", settings = { Saturation = 40 }, json = [[{"Saturation": 40}]] },
    { folder = "saturation_m40", settings = { Saturation = -40 }, json = [[{"Saturation": -40}]] },
    { folder = "curve_s", settings = { ToneCurveName2012 = "Custom", ToneCurvePV2012 = { 0, 0, 64, 48, 128, 128, 192, 208, 255, 255 } }, json = [[{"ToneCurveName2012": "Custom", "ToneCurvePV2012": [0, 0, 64, 48, 128, 128, 192, 208, 255, 255]}]] },
    { folder = "curve_matte", settings = { ToneCurveName2012 = "Custom", ToneCurvePV2012 = { 0, 40, 128, 140, 255, 240 } }, json = [[{"ToneCurveName2012": "Custom", "ToneCurvePV2012": [0, 40, 128, 140, 255, 240]}]] },
    { folder = "hsl_sat_red_m60", settings = { SaturationAdjustmentRed = -60 }, json = [[{"SaturationAdjustmentRed": -60}]] },
    { folder = "hsl_sat_orange_p40", settings = { SaturationAdjustmentOrange = 40 }, json = [[{"SaturationAdjustmentOrange": 40}]] },
    { folder = "hsl_sat_green_m60", settings = { SaturationAdjustmentGreen = -60 }, json = [[{"SaturationAdjustmentGreen": -60}]] },
    { folder = "hsl_sat_blue_m60", settings = { SaturationAdjustmentBlue = -60 }, json = [[{"SaturationAdjustmentBlue": -60}]] },
    { folder = "hsl_hue_yellow_m40", settings = { HueAdjustmentYellow = -40 }, json = [[{"HueAdjustmentYellow": -40}]] },
    { folder = "hsl_hue_green_p40", settings = { HueAdjustmentGreen = 40 }, json = [[{"HueAdjustmentGreen": 40}]] },
    { folder = "hsl_hue_orange_p30", settings = { HueAdjustmentOrange = 30 }, json = [[{"HueAdjustmentOrange": 30}]] },
    { folder = "hsl_hue_blue_p30", settings = { HueAdjustmentBlue = 30 }, json = [[{"HueAdjustmentBlue": 30}]] },
    { folder = "hsl_lum_orange_p30", settings = { LuminanceAdjustmentOrange = 30 }, json = [[{"LuminanceAdjustmentOrange": 30}]] },
    { folder = "hsl_lum_blue_m40", settings = { LuminanceAdjustmentBlue = -40 }, json = [[{"LuminanceAdjustmentBlue": -40}]] },
    { folder = "hsl_lum_green_p30", settings = { LuminanceAdjustmentGreen = 30 }, json = [[{"LuminanceAdjustmentGreen": 30}]] },
    { folder = "grade_shadow_blue", settings = { SplitToningShadowHue = 240, SplitToningShadowSaturation = 30 }, json = [[{"SplitToningShadowHue": 240, "SplitToningShadowSaturation": 30}]] },
    { folder = "grade_highlight_warm", settings = { SplitToningHighlightHue = 50, SplitToningHighlightSaturation = 30 }, json = [[{"SplitToningHighlightHue": 50, "SplitToningHighlightSaturation": 30}]] },
    { folder = "grade_mid_orange", settings = { ColorGradeMidtoneHue = 30, ColorGradeMidtoneSat = 20 }, json = [[{"ColorGradeMidtoneHue": 30, "ColorGradeMidtoneSat": 20}]] },
    { folder = "grade_shadow_lum_m20", settings = { ColorGradeShadowLum = -20 }, json = [[{"ColorGradeShadowLum": -20}]] },
    { folder = "grade_highlight_lum_p20", settings = { ColorGradeHighlightLum = 20 }, json = [[{"ColorGradeHighlightLum": 20}]] },
    { folder = "grade_balance_p50", settings = { SplitToningShadowHue = 240, SplitToningShadowSaturation = 30, SplitToningBalance = 50 }, json = [[{"SplitToningShadowHue": 240, "SplitToningShadowSaturation": 30, "SplitToningBalance": 50}]] },
    { folder = "calib_red_hue_p20", settings = { RedHue = 20 }, json = [[{"RedHue": 20}]] },
    { folder = "calib_red_sat_p30", settings = { RedSaturation = 30 }, json = [[{"RedSaturation": 30}]] },
    { folder = "calib_green_hue_m20", settings = { GreenHue = -20 }, json = [[{"GreenHue": -20}]] },
    { folder = "calib_green_sat_p30", settings = { GreenSaturation = 30 }, json = [[{"GreenSaturation": 30}]] },
    { folder = "calib_blue_hue_p20", settings = { BlueHue = 20 }, json = [[{"BlueHue": 20}]] },
    { folder = "calib_blue_sat_p40", settings = { BlueSaturation = 40 }, json = [[{"BlueSaturation": 40}]] },
    { folder = "vignette_m30", settings = { PostCropVignetteAmount = -30, PostCropVignetteMidpoint = 50, PostCropVignetteFeather = 50, PostCropVignetteRoundness = 0, PostCropVignetteStyle = 1 }, json = [[{"PostCropVignetteAmount": -30, "PostCropVignetteMidpoint": 50, "PostCropVignetteFeather": 50, "PostCropVignetteRoundness": 0, "PostCropVignetteStyle": 1}]] },
    { folder = "wb_temp_p1000", relative = { Temperature = 1000 }, json = [[{"relative": {"Temperature": 1000}}]] },
    { folder = "wb_temp_m1000", relative = { Temperature = -1000 }, json = [[{"relative": {"Temperature": -1000}}]] },
    { folder = "wb_tint_p20", relative = { Tint = 20 }, json = [[{"relative": {"Tint": 20}}]] },
}

-- Every setting a slider preset can touch, at its neutral value. Applying this
-- gives the "no preset" starting point without touching white balance, crop,
-- lens corrections, sharpening or noise reduction.
local function neutralSettings()
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

local function exportTo(photos, parentFolder, subfolder)
    local session = LrExportSession {
        photosToExport = photos,
        exportSettings = {
            LR_export_destinationType = 'specificFolder',
            LR_export_destinationPathPrefix = parentFolder,
            LR_export_useSubfolder = true,
            LR_export_destinationPathSuffix = subfolder,
            LR_collisionHandling = 'overwrite',
            LR_format = 'JPEG',
            LR_jpeg_quality = 0.9,
            LR_jpeg_useLimitSize = false,
            LR_export_colorSpace = 'sRGB',
            LR_size_doConstrain = true,
            LR_size_resizeType = 'longEdge',
            LR_size_maxHeight = LONG_EDGE,
            LR_size_maxWidth = LONG_EDGE,
            LR_size_units = 'pixels',
            LR_size_resolution = 240,
            LR_size_resolutionUnits = 'inch',
            LR_size_doNotEnlarge = true,
            LR_outputSharpeningOn = false,
            LR_useWatermark = false,
            LR_renamingTokensOn = false,
            LR_minimizeEmbeddedMetadata = true,
            LR_removeLocationMetadata = true,
            LR_reimportExportedPhoto = false,
            LR_export_postProcessing = 'doNothing',
            LR_includeVideoFiles = false,
        },
    }
    local failed = 0
    for _, rendition in session:renditions { stopIfCanceled = true } do
        local ok = rendition:waitForRender()
        if not ok then failed = failed + 1 end
    end
    return failed
end

local function copy(t)
    local c = {}
    for k, v in pairs(t) do c[k] = v end
    return c
end

local function run(context)
    local catalog = LrApplication.activeCatalog()
    local photos = {}
    for _, p in ipairs(catalog:getTargetPhotos()) do
        if not p:getRawMetadata('isVideo') then table.insert(photos, p) end
    end
    if #photos == 0 then
        LrDialogs.message('Auto Edits', 'Select 4 to 6 varied photos in the Library grid first.', 'info')
        return
    end
    if #photos > MAX_PHOTOS then
        LrDialogs.message('Auto Edits',
            string.format('%d photos are selected. Select at most %d for calibration.', #photos, MAX_PHOTOS), 'info')
        return
    end

    local steps = #VARIATIONS + 1
    local message = string.format(
        '%d photos will be exported %d times each (%d files), one setting changed at a time.\n\n' ..
        'This can take 20 to 40 minutes and runs on its own. Each photo gets a snapshot first and its ' ..
        'current settings are put back at the end.', #photos, steps, #photos * steps)
    if LrDialogs.confirm('Auto Edits: calibration exports', message, 'Choose folder and start', 'Cancel') ~= 'ok' then
        return
    end
    local chosen = LrDialogs.runOpenPanel {
        title = 'Folder for the calibration exports', prompt = 'Use this folder',
        canChooseFiles = false, canChooseDirectories = true, canCreateDirectories = true, allowsMultipleSelection = false,
    }
    if not chosen or not chosen[1] then return end
    local outFolder = LrPathUtils.child(chosen[1], 'LR calibration')
    LrFileUtils.createAllDirectories(outFolder)

    local progress = LrProgressScope { title = 'Auto Edits: calibration exports', functionContext = context }
    progress:setCancelable(true)

    local original = {}
    for i, p in ipairs(photos) do original[i] = p:getDevelopSettings() end
    context:addCleanupHandler(function()
        catalog:withWriteAccessDo('Auto Edits: restore settings', function()
            for i, p in ipairs(photos) do
                if original[i] then p:applyDevelopSettings(original[i]) end
            end
        end, { timeout = 30 })
    end)
    catalog:withWriteAccessDo('Auto Edits: snapshot', function()
        for _, p in ipairs(photos) do p:createDevelopSnapshot('Before Auto Edits calibration', true) end
    end, { timeout = 30 })

    local neutral = neutralSettings()
    local failures = 0
    local manifest = {}

    -- settings is a table for all photos, or a function(index) returning the table for one photo
    local function applyToAll(settings, historyName)
        catalog:withWriteAccessDo('Auto Edits: ' .. historyName, function()
            for i, p in ipairs(photos) do
                local s = type(settings) == 'function' and settings(i) or settings
                p:applyDevelopSettings(s, 'Auto Edits: ' .. historyName)
            end
        end, { timeout = 30 })
        LrTasks.sleep(1.5)
    end

    -- the base keeps each photo's own white balance, fixed as numbers so later steps can shift it
    local function baseFor(i)
        local s = copy(neutral)
        s.WhiteBalance = 'Custom'
        s.Temperature = original[i].Temperature
        s.Tint = original[i].Tint
        return s
    end

    progress:setCaption('Exporting: base')
    applyToAll(baseFor, 'calibration base')
    failures = failures + exportTo(photos, outFolder, 'base')
    progress:setPortionComplete(1, steps)

    local wb = {}
    for i, p in ipairs(photos) do
        table.insert(wb, string.format('{"file": "%s", "Temperature": %s, "Tint": %s}',
            tostring(p:getFormattedMetadata('fileName')), tostring(original[i].Temperature), tostring(original[i].Tint)))
    end

    for n, item in ipairs(VARIATIONS) do
        if progress:isCanceled() then break end
        progress:setCaption(string.format('Exporting %d of %d: %s', n, #VARIATIONS, item.folder))
        applyToAll(function(i)
            local s = baseFor(i)
            if item.settings then
                for k, v in pairs(item.settings) do s[k] = v end
            end
            if item.relative then
                for k, v in pairs(item.relative) do s[k] = (original[i][k] or 0) + v end
            end
            return s
        end, item.folder)
        failures = failures + exportTo(photos, outFolder, item.folder)
        table.insert(manifest, string.format('    "%s": %s', item.folder, item.json))
        progress:setPortionComplete(n + 1, steps)
    end

    local fh = io.open(LrPathUtils.child(outFolder, 'manifest.json'), 'w')
    if fh then
        fh:write('{\n  "plugin": "0.3",\n  "long_edge": ' .. LONG_EDGE .. ',\n  "white_balance": [' ..
                 table.concat(wb, ', ') .. '],\n  "variations": {\n' .. table.concat(manifest, ',\n') .. '\n  }\n}\n')
        fh:close()
    end

    local cancelled = progress:isCanceled()
    progress:done()
    if cancelled then
        LrDialogs.message('Auto Edits', 'Cancelled. The photos have their original settings back.', 'info')
    elseif failures > 0 then
        LrDialogs.message('Auto Edits', string.format('Finished, but %d exports failed.\n\nFolder: %s', failures, outFolder), 'warning')
    else
        LrDialogs.message('Auto Edits', string.format('Done: %d folders written. The photos have their original settings back.\n\nFolder: %s', steps, outFolder), 'info')
    end
end

LrTasks.startAsyncTask(function()
    LrFunctionContext.callWithContext('AutoEditsCalibrationExports', function(context)
        LrDialogs.attachErrorDialogToFunctionContext(context)
        run(context)
    end)
end)
