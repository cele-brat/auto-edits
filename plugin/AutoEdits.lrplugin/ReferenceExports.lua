--[[ Auto Edits: reference exports.

  For the selected photos this makes, inside a folder you choose:

      base/                  the photos with neutral settings (no preset)
      <preset name>/         the same photos with one preset applied, one folder per preset

  The Auto Edits engine is tuned by comparing its own rendering with these.

  Version 0.2: presets are applied as plain develop settings carried inside the
  plug-in (TestPresets.lua), one write per step, and a log of what Lightroom
  reports after each step is saved next to the exports.

  Safety: before anything changes, each photo gets a develop snapshot named
  "Before Auto Edits reference", and its settings are put back when the run
  ends, also when the run fails or is cancelled.
]]

local LrApplication = import 'LrApplication'
local LrDialogs = import 'LrDialogs'
local LrExportSession = import 'LrExportSession'
local LrFileUtils = import 'LrFileUtils'
local LrFunctionContext = import 'LrFunctionContext'
local LrPathUtils = import 'LrPathUtils'
local LrProgressScope = import 'LrProgressScope'
local LrTasks = import 'LrTasks'

local MAX_PHOTOS = 30

-- The test presets and their settings travel with the plug-in.
local TEST_PRESETS = require 'TestPresets'

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

-- neutral settings with one preset's settings laid over them
local function withPreset(neutral, presetSettings)
    local t = {}
    for k, v in pairs(neutral) do t[k] = v end
    for k, v in pairs(presetSettings) do t[k] = v end
    return t
end

-- what Lightroom itself reports for a photo, for the log
local LOG_KEYS = { 'Exposure2012', 'Contrast2012', 'Highlights2012', 'Shadows2012', 'Whites2012', 'Blacks2012',
                   'Vibrance', 'Saturation', 'SplitToningShadowHue', 'SplitToningShadowSaturation',
                   'PostCropVignetteAmount', 'GrainAmount', 'ToneCurveName2012', 'CameraProfile', 'ProcessVersion',
                   'WhiteBalance', 'Temperature', 'Tint' }

local function describe(photo)
    local s = photo:getDevelopSettings()
    local parts = {}
    for _, k in ipairs(LOG_KEYS) do
        table.insert(parts, k .. '=' .. tostring(s[k]))
    end
    local curve = s.ToneCurvePV2012
    if type(curve) == 'table' then
        local nums = {}
        for _, v in ipairs(curve) do table.insert(nums, tostring(v)) end
        table.insert(parts, 'ToneCurvePV2012={' .. table.concat(nums, ',') .. '}')
    end
    return table.concat(parts, ' ')
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
            LR_size_maxHeight = 2560,
            LR_size_maxWidth = 2560,
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

local function run(context)
    local catalog = LrApplication.activeCatalog()
    local photos = catalog:getTargetPhotos()

    local stills = {}
    for _, p in ipairs(photos) do
        if not p:getRawMetadata('isVideo') then table.insert(stills, p) end
    end
    photos = stills

    if #photos == 0 then
        LrDialogs.message('Auto Edits', 'Select 10 to 15 photos in the Library grid first.', 'info')
        return
    end
    if #photos > MAX_PHOTOS then
        LrDialogs.message('Auto Edits',
            string.format('%d photos are selected. Select at most %d; 10 to 15 varied photos are enough.', #photos, MAX_PHOTOS),
            'info')
        return
    end

    local found, missing = TEST_PRESETS, {}

    local message = string.format(
        '%d photos will be exported %d times: once with no preset and once for each of %d presets.\n\n' ..
        'Each photo gets a snapshot first and its current settings are put back at the end.',
        #photos, #found + 1, #found)
    if #missing > 0 then
        message = message .. '\n\nNot installed, so skipped: ' .. table.concat(missing, ', ')
    end
    if LrDialogs.confirm('Auto Edits: reference exports', message, 'Choose folder and start', 'Cancel') ~= 'ok' then
        return
    end

    local chosen = LrDialogs.runOpenPanel {
        title = 'Folder for the reference exports',
        prompt = 'Use this folder',
        canChooseFiles = false, canChooseDirectories = true, canCreateDirectories = true, allowsMultipleSelection = false,
    }
    if not chosen or not chosen[1] then return end
    local outFolder = LrPathUtils.child(chosen[1], 'LR exports')
    LrFileUtils.createAllDirectories(outFolder)

    local progress = LrProgressScope { title = 'Auto Edits: reference exports', functionContext = context }
    progress:setCancelable(true)

    -- remember every photo's settings, and put them back whatever happens
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
        for _, p in ipairs(photos) do p:createDevelopSnapshot('Before Auto Edits reference', true) end
    end, { timeout = 30 })

    local neutral = neutralSettings()
    local steps = #found + 1
    local failures = 0
    local log = { 'Auto Edits reference exports, plug-in 0.2', os.date('%Y-%m-%d %H:%M:%S'), '' }
    for i, p in ipairs(photos) do
        table.insert(log, string.format('photo %d: %s', i, tostring(p:getFormattedMetadata('fileName'))))
    end
    table.insert(log, '')
    table.insert(log, 'ORIGINAL   ' .. describe(photos[1]))

    local function applyToAll(settings, historyName)
        catalog:withWriteAccessDo('Auto Edits: ' .. historyName, function()
            for _, p in ipairs(photos) do p:applyDevelopSettings(settings, 'Auto Edits: ' .. historyName) end
        end, { timeout = 30 })
        LrTasks.sleep(1.5)                 -- let Lightroom take the new settings in before rendering
    end

    -- 1. base: neutral settings, no preset
    progress:setCaption('Exporting: base (no preset)')
    applyToAll(neutral, 'neutral')
    table.insert(log, 'BASE       ' .. describe(photos[1]))
    failures = failures + exportTo(photos, outFolder, 'base')
    progress:setPortionComplete(1, steps)

    -- 2. one export per preset, starting from neutral each time
    for i, item in ipairs(found) do
        if progress:isCanceled() then break end
        progress:setCaption('Exporting: ' .. item.folder)
        applyToAll(withPreset(neutral, item.settings), item.folder)
        table.insert(log, string.format('%-10s ', item.id) .. describe(photos[1]))
        failures = failures + exportTo(photos, outFolder, item.folder)
        progress:setPortionComplete(i + 1, steps)
    end

    local fh = io.open(LrPathUtils.child(outFolder, 'reference_log.txt'), 'w')
    if fh then
        fh:write(table.concat(log, '\n') .. '\n')
        fh:close()
    end

    local cancelled = progress:isCanceled()
    progress:done()

    if cancelled then
        LrDialogs.message('Auto Edits', 'Cancelled. The photos have their original settings back.', 'info')
    elseif failures > 0 then
        LrDialogs.message('Auto Edits',
            string.format('Finished, but %d exports failed. The photos have their original settings back.\n\nFolder: %s', failures, outFolder),
            'warning')
    else
        LrDialogs.message('Auto Edits',
            string.format('Done: %d folders written. The photos have their original settings back.\n\nFolder: %s', steps, outFolder),
            'info')
    end
end

LrTasks.startAsyncTask(function()
    LrFunctionContext.callWithContext('AutoEditsReferenceExports', function(context)
        LrDialogs.attachErrorDialogToFunctionContext(context)
        run(context)
    end)
end)
