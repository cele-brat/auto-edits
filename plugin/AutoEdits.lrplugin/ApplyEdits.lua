--[[ Auto Edits: bring the edits back and apply them.

  Asks the server what it worked out for each photo of a wedding and applies it
  in Lightroom, for all photos in one unattended run:

    * the preset. A preset of yours that has AI masks is applied by Lightroom
      itself, so Lightroom makes its own masks; other presets arrive as plain
      settings laid over a no-edit starting point;
    * the per-photo automatic values and your own slider changes;
    * white balance, as a shift from each photo's own Kelvin value.

  Safety: every photo gets a develop snapshot "Before Auto Edits" first. The
  run can be cancelled and started again; photos already done are skipped.
]]

local LrApplication = import 'LrApplication'
local LrBinding = import 'LrBinding'
local LrDialogs = import 'LrDialogs'
local LrFunctionContext = import 'LrFunctionContext'
local LrProgressScope = import 'LrProgressScope'
local LrTasks = import 'LrTasks'
local LrView = import 'LrView'

local Neutral = require 'Neutral'
local Server = require 'Server'

local SNAPSHOT_NAME = 'Before Auto Edits'
local CHUNK = 40

local function clamp(v, lo, hi) return math.max(lo, math.min(hi, v)) end

local function chooseWedding(context, weddings)
    local prefs = Server.prefs()
    local props = LrBinding.makePropertyTable(context)
    local items, chosen = {}, weddings[1].id
    for _, w in ipairs(weddings) do
        items[#items + 1] = { title = string.format('%s (%d photos)', w.name, w.photos or 0), value = w.id }
        if w.name == prefs.lastWedding then chosen = w.id end
    end
    props.wedding = chosen
    props.again = false

    local f = LrView.osFactory()
    local bind = LrView.bind
    local choice = LrDialogs.presentModalDialog {
        title = 'Auto Edits: bring back and apply',
        actionVerb = 'Continue',
        contents = f:column {
            bind_to_object = props,
            spacing = f:control_spacing(),
            f:row {
                f:static_text { title = 'Wedding', width = 80, alignment = 'right' },
                f:popup_menu { value = bind 'wedding', items = items },
            },
            f:checkbox { title = 'Also apply again to photos that were already done', value = bind 'again' },
            f:static_text {
                title = 'Each photo gets a snapshot "' .. SNAPSHOT_NAME .. '" first, so you can always go back.',
                width_in_chars = 50,
            },
        },
    }
    if choice ~= 'ok' then return nil end
    for _, w in ipairs(weddings) do
        if w.id == props.wedding then return w, props.again and true or false end
    end
    return nil
end

local function fetchEdits(weddingId, progress)
    local all, offset, total = {}, 0, nil
    repeat
        local ok, page = Server.get(string.format('/api/weddings/%s/apply?offset=%d&limit=200', weddingId, offset))
        if not ok then return nil, page end
        total = page.total or 0
        for _, item in ipairs(page.photos or {}) do all[#all + 1] = item end
        offset = offset + 200
        progress:setCaption(string.format('Asking the server: %d of %d', math.min(offset, total), total))
        progress:setPortionComplete(math.min(offset, total), math.max(1, total) * 4)   -- asking is the first quarter
    until offset >= total or progress:isCanceled()
    return all
end

-- Everything for one photo that does not depend on reading it back first.
local function firstPass(photo, edit, presets, counts)
    if edit.preset_uuid then
        local preset = presets[edit.preset_uuid]
        if preset == nil then
            preset = LrApplication.developPresetByUuid(edit.preset_uuid) or false
            presets[edit.preset_uuid] = preset
        end
        if preset then
            photo:createDevelopSnapshot(SNAPSHOT_NAME, true)
            photo:applyDevelopPreset(preset)
        else
            counts.presetMissing[edit.preset or '?'] = true
        end
        return
    end
    photo:createDevelopSnapshot(SNAPSHOT_NAME, true)
    local settings = edit.from_no_edit and Neutral.settings() or {}
    for k, v in pairs(edit.settings or {}) do settings[k] = v end
    for k, v in pairs(edit.curves or {}) do settings[k] = v end
    photo:applyDevelopSettings(settings, 'Auto Edits')
end

-- What has to follow in a second step: changes on top of a preset Lightroom applied, and white balance.
local function secondPass(photo, edit, presets, counts)
    if edit.preset_uuid and not presets[edit.preset_uuid] then return end     -- its preset is missing: leave the photo alone
    local settings = {}
    if edit.preset_uuid then
        for k, v in pairs(edit.settings or {}) do settings[k] = v end
    end
    if edit.wb then
        local now = photo:getDevelopSettings() or {}
        if type(now.Temperature) == 'number' and now.Temperature > 1000 then
            local mired = 1000000 / now.Temperature + (edit.wb.mired or 0)
            local kelvin = clamp(1000000 / math.max(20, mired), 2000, 50000)
            settings.WhiteBalance = 'Custom'
            settings.Temperature = math.floor(kelvin / 50 + 0.5) * 50
            settings.Tint = clamp(math.floor((now.Tint or 0) + (edit.wb.tint or 0) + 0.5), -150, 150)
        elseif type(now.IncrementalTemperature) == 'number' or type(now.IncrementalTint) == 'number' then
            settings.IncrementalTemperature = clamp(math.floor((now.IncrementalTemperature or 0) + (edit.wb.relative_temp or 0) + 0.5), -100, 100)
            settings.IncrementalTint = clamp(math.floor((now.IncrementalTint or 0) + (edit.wb.relative_tint or 0) + 0.5), -100, 100)
        else
            counts.wbSkipped = counts.wbSkipped + 1        -- Lightroom gave no white balance value to shift from
        end
    end
    if next(settings) ~= nil then photo:applyDevelopSettings(settings, 'Auto Edits: per photo') end
end

local function run(context)
    if not Server.isSetUp() then
        LrDialogs.message('Auto Edits',
            'The server key is not set yet.\n\nOpen Library > Plug-in Extras > "Auto Edits: server settings" first.', 'info')
        return
    end
    local ok, list = Server.get('/api/weddings')
    if not ok then
        LrDialogs.message('Auto Edits', tostring(list), 'warning')
        return
    end
    if type(list.weddings) ~= 'table' or #list.weddings == 0 then
        LrDialogs.message('Auto Edits', 'There are no weddings on the server yet. Send photos first.', 'info')
        return
    end
    local wedding, again = chooseWedding(context, list.weddings)
    if not wedding then return end

    local progress = LrProgressScope { title = 'Auto Edits: applying edits', functionContext = context }
    progress:setCancelable(true)
    local items, why = fetchEdits(wedding.id, progress)
    if not items then
        progress:done()
        LrDialogs.message('Auto Edits', tostring(why), 'warning')
        return
    end

    local catalog = LrApplication.activeCatalog()
    local todo, noEdit, done, notHere, notYet = {}, 0, 0, 0, {}
    for _, item in ipairs(items) do
        if type(item.edit) ~= 'table' then
            noEdit = noEdit + 1
        elseif item.done and not again then
            done = done + 1
        else
            local photo = catalog:findPhotoByUuid(item.uuid)
            if photo then
                todo[#todo + 1] = { photo = photo, edit = item.edit, uuid = item.uuid, stamp = item.stamp, name = item.name }
                for _, what in ipairs(item.edit.not_yet or {}) do notYet[what] = (notYet[what] or 0) + 1 end
            else
                notHere = notHere + 1
            end
        end
    end

    if #todo == 0 then
        progress:done()
        local lines = { string.format('Nothing to apply for "%s".', wedding.name) }
        if done > 0 then lines[#lines + 1] = string.format('%d photos already have their edit.', done) end
        if noEdit > 0 then lines[#lines + 1] = string.format('%d photos have no preset or setting chosen in the app.', noEdit) end
        if notHere > 0 then lines[#lines + 1] = string.format('%d photos are not in this catalogue.', notHere) end
        LrDialogs.message('Auto Edits', table.concat(lines, '\n'), 'info')
        return
    end

    local question = string.format('%d photos of "%s" will be edited.', #todo, wedding.name)
    if done > 0 then question = question .. string.format('\n%d are already done and will be skipped.', done) end
    if noEdit > 0 then question = question .. string.format('\n%d have nothing chosen in the app and stay as they are.', noEdit) end
    if LrDialogs.confirm('Auto Edits: apply edits', question, 'Apply', 'Cancel') ~= 'ok' then
        progress:done()
        return
    end

    local counts = { wbSkipped = 0, presetMissing = {} }
    local presets, applied, withMasks = {}, 0, {}
    for first = 1, #todo, CHUNK do
        if progress:isCanceled() then break end
        local last = math.min(first + CHUNK - 1, #todo)
        progress:setCaption(string.format('Applying %d to %d of %d', first, last, #todo))
        catalog:withWriteAccessDo('Auto Edits: apply', function()
            for i = first, last do firstPass(todo[i].photo, todo[i].edit, presets, counts) end
        end, { timeout = 120 })
        LrTasks.sleep(0.5)                 -- a preset must have landed before anything is laid over it
        catalog:withWriteAccessDo('Auto Edits: per photo', function()
            for i = first, last do secondPass(todo[i].photo, todo[i].edit, presets, counts) end
        end, { timeout = 120 })

        local stamps = {}
        for i = first, last do
            local t = todo[i]
            local missing = t.edit.preset_uuid and not presets[t.edit.preset_uuid]
            if not missing then stamps[t.uuid] = t.stamp end
            if t.edit.preset_uuid and not missing then withMasks[#withMasks + 1] = t.photo end
        end
        if next(stamps) ~= nil then Server.postJson('/api/weddings/' .. wedding.id .. '/applied', stamps) end
        applied = last
        progress:setPortionComplete(#todo + 3 * applied, #todo * 4)
    end

    -- Ask Lightroom to build the AI masks now, where this version of Lightroom allows a plug-in to.
    local masksAsked = 0
    if #withMasks > 0 and not progress:isCanceled() then
        progress:setCaption('Asking Lightroom to build the AI masks')
        for _, photo in ipairs(withMasks) do
            local can, fn = pcall(function() return photo.updateAISettings end)
            if can and type(fn) == 'function' and pcall(fn, photo) then masksAsked = masksAsked + 1 end
        end
    end

    local cancelled = progress:isCanceled()
    progress:done()

    local lines = { string.format('Wedding: %s', wedding.name), string.format('Edited now: %d of %d', applied, #todo) }
    if done > 0 then lines[#lines + 1] = string.format('Already done, skipped: %d', done) end
    if notHere > 0 then lines[#lines + 1] = string.format('Not in this catalogue: %d', notHere) end
    local missing = {}
    for name in pairs(counts.presetMissing) do missing[#missing + 1] = name end
    if #missing > 0 then
        lines[#lines + 1] = '\nThese presets have AI masks but are not installed in this Lightroom, so their photos were left alone: ' ..
            table.concat(missing, ', ') .. '.'
    end
    if #withMasks > 0 then
        if masksAsked == #withMasks then
            lines[#lines + 1] = string.format('\nLightroom is building AI masks for %d photos.', masksAsked)
        else
            lines[#lines + 1] = string.format('\n%d photos got a preset with AI masks. To build the masks for all of them at once: ' ..
                'select them, then Settings > Update AI Settings (or just export; Lightroom builds them as it goes).', #withMasks)
        end
    end
    if counts.wbSkipped > 0 then
        lines[#lines + 1] = string.format('\nWhite balance was left as shot on %d photos: Lightroom gave no value to start from.', counts.wbSkipped)
    end
    local later = {}
    for what, n in pairs(notYet) do later[#later + 1] = string.format('%s (%d photos)', what, n) end
    if #later > 0 then
        table.sort(later)
        lines[#lines + 1] = '\nChosen in the app but not applied by this version: ' .. table.concat(later, ', ') .. '.'
    end
    if cancelled then lines[#lines + 1] = '\nCancelled. Start again to carry on: photos already done are skipped.' end
    lines[#lines + 1] = '\nTo undo a photo: Develop > Snapshots > "' .. SNAPSHOT_NAME .. '".'
    LrDialogs.message('Auto Edits: edits applied', table.concat(lines, '\n'), 'info')
end

LrTasks.startAsyncTask(function()
    LrFunctionContext.callWithContext('AutoEditsApplyEdits', function(context)
        LrDialogs.attachErrorDialogToFunctionContext(context)
        run(context)
    end)
end)
