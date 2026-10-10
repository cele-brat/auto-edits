--[[ Auto Edits: send photos to the server.

  For the selected photos (or, with nothing selected, every photo in the
  current view) this renders a 2560 px preview in Lightroom and sends it to
  the Auto Edits server with the photo's details. The original raw files never
  leave this Mac.

  Photos land in event folders on the server, named after their Lightroom
  folder (Haldi, Sangeet, Baraat ...), or all in one folder you name.

  A send that is interrupted can simply be started again: photos the server
  already has are skipped.
]]

local LrApplication = import 'LrApplication'
local LrBinding = import 'LrBinding'
local LrDate = import 'LrDate'
local LrDialogs = import 'LrDialogs'
local LrExportSession = import 'LrExportSession'
local LrFileUtils = import 'LrFileUtils'
local LrFunctionContext = import 'LrFunctionContext'
local LrPathUtils = import 'LrPathUtils'
local LrProgressScope = import 'LrProgressScope'
local LrTasks = import 'LrTasks'
local LrView = import 'LrView'

local Neutral = require 'Neutral'
local Server = require 'Server'

local PLUGIN_VERSION = '0.6'
local PREVIEW_SIZE = 2560
local STOP_AFTER_FAILURES = 5          -- in a row: the server or the internet is down
local SNAPSHOT_NAME = 'Before Auto Edits'

local RAW_KEYS = { 'uuid', 'path', 'isVirtualCopy', 'rating', 'pickStatus', 'colorNameForLabel', 'dimensions',
                   'isoSpeedRating', 'aperture', 'shutterSpeed', 'focalLength', 'dateTimeOriginal', 'fileFormat' }
local FORMATTED_KEYS = { 'fileName', 'folderName', 'cameraModel', 'lens', 'copyName' }
local DEVELOP_KEYS = { 'WhiteBalance', 'Temperature', 'Tint', 'Exposure2012', 'ProcessVersion', 'CameraProfile',
                       'CropAngle', 'CropLeft', 'CropRight', 'CropTop', 'CropBottom', 'orientation',
                       'LuminanceSmoothing', 'ColorNoiseReduction', 'Sharpness', 'LensProfileEnable' }

local function exportSettings(folder)
    return {
        LR_export_destinationType = 'specificFolder',
        LR_export_destinationPathPrefix = folder,
        LR_export_useSubfolder = false,
        LR_collisionHandling = 'rename',
        LR_format = 'JPEG',
        LR_jpeg_quality = 0.8,
        LR_jpeg_useLimitSize = false,
        LR_export_colorSpace = 'sRGB',
        LR_size_doConstrain = true,
        LR_size_resizeType = 'longEdge',
        LR_size_maxHeight = PREVIEW_SIZE,
        LR_size_maxWidth = PREVIEW_SIZE,
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
    }
end

local function trim(s)
    return (tostring(s or ''):gsub('^%s+', ''):gsub('%s+$', ''))
end

local function details(photo, raw, formatted, folderMode, oneFolder)
    local develop = photo:getDevelopSettings() or {}
    local kept = {}
    for _, k in ipairs(DEVELOP_KEYS) do kept[k] = develop[k] end

    local taken = ''
    if type(raw.dateTimeOriginal) == 'number' then taken = LrDate.timeToW3CDate(raw.dateTimeOriginal) end
    local size = type(raw.dimensions) == 'table' and raw.dimensions or {}

    local lrFolder = trim(formatted.folderName)
    local folder = lrFolder
    if folderMode == 'one' then folder = trim(oneFolder) end
    if folder == '' then folder = 'Unsorted' end

    return {
        uuid = raw.uuid,
        fileName = formatted.fileName,
        folder = folder,
        lrFolder = lrFolder,
        path = raw.path,
        taken = taken,
        iso = raw.isoSpeedRating,
        aperture = raw.aperture,
        shutter = raw.shutterSpeed,
        focal = raw.focalLength,
        camera = formatted.cameraModel,
        lens = formatted.lens,
        rating = raw.rating or 0,
        pick = raw.pickStatus or 0,
        label = raw.colorNameForLabel or '',
        width = size.width,
        height = size.height,
        fileFormat = raw.fileFormat,
        virtualCopy = raw.isVirtualCopy and true or false,
        copyName = formatted.copyName or '',
        develop = kept,
        edited = Neutral.editedKeys(develop),
        previewSize = PREVIEW_SIZE,
        plugin = PLUGIN_VERSION,
    }
end

local function askWhatToSend(context, catalog, count, usingSelection)
    local prefs = Server.prefs()
    local props = LrBinding.makePropertyTable(context)
    props.wedding = prefs.lastWedding or LrPathUtils.removeExtension(LrPathUtils.leafName(catalog:getPath()))
    props.folderMode = prefs.folderMode or 'lightroom'
    props.oneFolder = prefs.oneFolder or ''
    props.resetFirst = false
    props.sendAgain = false

    local f = LrView.osFactory()
    local bind = LrView.bind
    local label = 110

    local contents = f:column {
        bind_to_object = props,
        spacing = f:control_spacing(),
        f:static_text {
            title = string.format('%d photos will be sent (%s).', count,
                usingSelection and 'the selected photos' or 'every photo in the current view'),
            font = '<system/bold>',
        },
        f:row {
            f:static_text { title = 'Wedding name', width = label, alignment = 'right' },
            f:edit_field { value = bind 'wedding', width_in_chars = 32, immediate = true },
        },
        f:row {
            f:static_text { title = 'Event folders', width = label, alignment = 'right' },
            f:popup_menu {
                value = bind 'folderMode',
                items = {
                    { title = 'Same as the Lightroom folder names', value = 'lightroom' },
                    { title = 'Put all of them in one folder', value = 'one' },
                },
            },
        },
        f:row {
            f:static_text { title = 'Folder name', width = label, alignment = 'right',
                            enabled = LrBinding.keyEquals('folderMode', 'one') },
            f:edit_field { value = bind 'oneFolder', width_in_chars = 32, immediate = true,
                           enabled = LrBinding.keyEquals('folderMode', 'one') },
        },
        f:separator { fill_horizontal = 1 },
        f:checkbox {
            title = 'These photos already have edits: reset them to no edit first',
            value = bind 'resetFirst',
        },
        f:static_text {
            title = 'Leave this off for freshly imported photos. When on, each photo gets a snapshot\n' ..
                    '"' .. SNAPSHOT_NAME .. '" first. White balance, crop and lens corrections are kept.',
            height_in_lines = 2, width_in_chars = 52,
        },
        f:checkbox {
            title = 'Send again photos the server already has',
            value = bind 'sendAgain',
        },
        f:static_text {
            title = 'The original raw files stay on this Mac. Only previews are sent.',
            width_in_chars = 52,
        },
    }

    local choice = LrDialogs.presentModalDialog {
        title = 'Auto Edits: send photos to the server',
        contents = contents,
        actionVerb = 'Send',
    }
    if choice ~= 'ok' then return nil end

    local answer = {
        wedding = trim(props.wedding), folderMode = props.folderMode, oneFolder = trim(props.oneFolder),
        resetFirst = props.resetFirst and true or false, sendAgain = props.sendAgain and true or false,
    }
    if answer.wedding == '' then
        LrDialogs.message('Auto Edits', 'Give the wedding a name.', 'info')
        return nil
    end
    if answer.folderMode == 'one' and answer.oneFolder == '' then
        LrDialogs.message('Auto Edits', 'Give the folder a name, or use the Lightroom folder names.', 'info')
        return nil
    end
    prefs.lastWedding = answer.wedding
    prefs.folderMode = answer.folderMode
    prefs.oneFolder = answer.oneFolder
    return answer
end

local function resetToNoEdit(catalog, photos, progress)
    local neutral = Neutral.settings()
    local chunk = 200
    for first = 1, #photos, chunk do
        if progress:isCanceled() then return false end
        progress:setCaption(string.format('Resetting edits: %d of %d', math.min(first + chunk - 1, #photos), #photos))
        catalog:withWriteAccessDo('Auto Edits: reset to no edit', function()
            for i = first, math.min(first + chunk - 1, #photos) do
                photos[i]:createDevelopSnapshot(SNAPSHOT_NAME, true)
                photos[i]:applyDevelopSettings(neutral, 'Auto Edits: no edit')
            end
        end, { timeout = 60 })
    end
    LrTasks.sleep(2)                       -- let Lightroom take the new settings in before rendering
    return true
end

local function run(context)
    local catalog = LrApplication.activeCatalog()
    local usingSelection = #catalog:getTargetPhotos() > 0 and catalog:getTargetPhoto() ~= nil
    local photos = {}
    for _, p in ipairs(catalog:getTargetPhotos()) do
        if not p:getRawMetadata('isVideo') then photos[#photos + 1] = p end
    end
    if #photos == 0 then
        LrDialogs.message('Auto Edits', 'There are no photos to send. Open a folder or select photos in the Library grid first.', 'info')
        return
    end
    if not Server.isSetUp() then
        LrDialogs.message('Auto Edits',
            'The server key is not set yet.\n\nOpen Library > Plug-in Extras > "Auto Edits: server settings" first.', 'info')
        return
    end

    local answer = askWhatToSend(context, catalog, #photos, usingSelection)
    if not answer then return end

    local progress = LrProgressScope { title = 'Auto Edits: sending photos', functionContext = context }
    progress:setCancelable(true)
    progress:setCaption('Connecting to the server')

    local catalogName = LrPathUtils.removeExtension(LrPathUtils.leafName(catalog:getPath()))
    local ok, wedding = Server.postJson('/api/weddings', { name = answer.wedding, catalog = catalogName })
    if not ok then
        progress:done()
        LrDialogs.message('Auto Edits', tostring(wedding), 'warning')
        return
    end

    -- skip what the server already has
    local skipped = 0
    if not answer.sendAgain then
        local gotList, received = Server.get('/api/weddings/' .. wedding.id .. '/received')
        if gotList and type(received.uuids) == 'table' then
            local have = {}
            for _, u in ipairs(received.uuids) do have[u] = true end
            local uuids = catalog:batchGetRawMetadata(photos, { 'uuid' })
            local todo = {}
            for _, p in ipairs(photos) do
                if have[uuids[p].uuid] then skipped = skipped + 1 else todo[#todo + 1] = p end
            end
            photos = todo
        end
    end
    if #photos == 0 then
        progress:done()
        LrDialogs.message('Auto Edits',
            string.format('The server already has all %d of these photos for "%s". Nothing was sent.', skipped, answer.wedding), 'info')
        return
    end

    if answer.resetFirst then
        if not resetToNoEdit(catalog, photos, progress) then
            progress:done()
            LrDialogs.message('Auto Edits', 'Cancelled while resetting edits. Nothing was sent.', 'info')
            return
        end
    end

    progress:setCaption('Reading photo details')
    local raw = catalog:batchGetRawMetadata(photos, RAW_KEYS)
    local formatted = catalog:batchGetFormattedMetadata(photos, FORMATTED_KEYS)

    local tempFolder = LrPathUtils.child(LrPathUtils.getStandardFilePath('temp'), 'AutoEditsSend')
    if LrFileUtils.exists(tempFolder) then LrFileUtils.delete(tempFolder) end
    LrFileUtils.createAllDirectories(tempFolder)
    context:addCleanupHandler(function()
        if LrFileUtils.exists(tempFolder) then LrFileUtils.delete(tempFolder) end
    end)

    local session = LrExportSession { photosToExport = photos, exportSettings = exportSettings(tempFolder) }

    local total, sent, failed, inARow, edited = #photos, 0, {}, 0, 0
    local stoppedBecause = nil
    local done = 0
    for _, rendition in session:renditions { stopIfCanceled = true } do
        if progress:isCanceled() then break end
        local photo = rendition.photo
        local name = tostring(formatted[photo] and formatted[photo].fileName or '?')
        progress:setCaption(string.format('Sending %d of %d: %s', done + 1, total, name))

        local rendered, pathOrMessage = rendition:waitForRender()
        local good, reply = false, nil
        if rendered then
            local meta = details(photo, raw[photo] or {}, formatted[photo] or {}, answer.folderMode, answer.oneFolder)
            if #meta.edited > 0 then edited = edited + 1 end
            good, reply = Server.sendPhoto(wedding.id, meta, pathOrMessage)
            LrFileUtils.delete(pathOrMessage)
        else
            reply = 'Lightroom could not render it'
        end

        done = done + 1
        if good then
            sent = sent + 1
            inARow = 0
        else
            failed[#failed + 1] = name .. ': ' .. tostring(reply)
            inARow = inARow + 1
            if inARow >= STOP_AFTER_FAILURES then
                stoppedBecause = tostring(reply)
                break
            end
        end
        progress:setPortionComplete(done, total)
    end

    local cancelled = progress:isCanceled()
    progress:done()

    local lines = { string.format('Wedding: %s', answer.wedding),
                    string.format('Sent now: %d of %d', sent, total) }
    if skipped > 0 then lines[#lines + 1] = string.format('Already on the server, skipped: %d', skipped) end
    if edited > 0 and not answer.resetFirst then
        lines[#lines + 1] = string.format('\n%d of the sent photos already carry edits. Their preview in the app starts from ' ..
            'those edits. To start clean, send them again with "reset them to no edit first" switched on.', edited)
    end
    if stoppedBecause then
        lines[#lines + 1] = '\nStopped after ' .. STOP_AFTER_FAILURES .. ' photos in a row failed. ' .. stoppedBecause ..
            '\n\nStart the send again later: photos already sent are skipped.'
    elseif cancelled then
        lines[#lines + 1] = '\nCancelled. Start the send again to carry on: photos already sent are skipped.'
    elseif #failed > 0 then
        lines[#lines + 1] = string.format('\nNot sent: %d. Start the send again to retry just these.', #failed)
        for i = 1, math.min(#failed, 8) do lines[#lines + 1] = '  ' .. failed[i] end
        if #failed > 8 then lines[#lines + 1] = '  ...' end
    end
    local trouble = stoppedBecause or #failed > 0
    LrDialogs.message(trouble and 'Auto Edits: sending did not finish' or 'Auto Edits: photos sent',
        table.concat(lines, '\n'), trouble and 'warning' or 'info')
end

LrTasks.startAsyncTask(function()
    LrFunctionContext.callWithContext('AutoEditsSendToServer', function(context)
        LrDialogs.attachErrorDialogToFunctionContext(context)
        run(context)
    end)
end)
