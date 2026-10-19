-- A stand-in for the parts of the Lightroom SDK the plug-in uses, for tests.
-- `py` is provided by the test: py.http(method, url, body, headers, filePath), py.writeJpeg(path), py.remove(path)
local M = { installed = {}, confirmAnswer = 'ok', confirms = {}, messages = {}, prefs = {}, photos = {}, dialogAnswer = 'ok', dialogEdits = {}, cleanup = {}, history = {} }

local function anything()
    local t = {}
    return setmetatable(t, { __index = function() return function() return {} end end })
end

local function headerTable(headers)
    local h = {}
    for _, item in ipairs(headers or {}) do h[item.field] = item.value end
    return h
end

local function http(method, url, body, headers, filePath)
    local status, text = py.http(method, url, body, headerTable(headers), filePath)
    if status == 0 then return nil, { error = { name = 'offline' } } end
    return text, { status = status }
end

local modules = {
    LrApplication = { activeCatalog = function() return M.catalog end,
                      developPresetByUuid = function(uuid) return M.installed[uuid] end },
    LrBinding = { makePropertyTable = function() M.props = {}; return M.props end, keyEquals = function() return {} end },
    LrDate = { timeToW3CDate = function(t) return 'T' .. string.format('%.0f', t) end },
    LrDialogs = {
        message = function(title, text) M.messages[#M.messages + 1] = { title = title, text = text } end,
        presentModalDialog = function()
            for k, v in pairs(M.dialogEdits) do M.props[k] = v end
            return M.dialogAnswer
        end,
        confirm = function(title, text) M.confirms[#M.confirms + 1] = text; return M.confirmAnswer end,
        attachErrorDialogToFunctionContext = function() end,
    },
    LrExportSession = function(args)
        M.exportSettings = args.exportSettings
        return {
            renditions = function()
                local i = 0
                return function()
                    i = i + 1
                    local photo = args.photosToExport[i]
                    if not photo then return nil end
                    return i, {
                        photo = photo,
                        waitForRender = function()
                            if photo.broken then return false, 'render failed' end
                            local path = args.exportSettings.LR_export_destinationPathPrefix .. '/r' .. i .. '.jpg'
                            py.writeJpeg(path)
                            return true, path
                        end,
                    }
                end
            end,
        }
    end,
    LrFileUtils = { exists = function(p) return py.exists(p) end, delete = function(p) py.remove(p) end,
                    createAllDirectories = function(p) py.mkdir(p) end },
    LrFunctionContext = { callWithContext = function(_, f)
        local context = { addCleanupHandler = function(_, h) M.cleanup[#M.cleanup + 1] = h end }
        f(context)
        for _, h in ipairs(M.cleanup) do h() end
    end },
    LrHttp = {
        get = function(url, headers) return http('GET', url, nil, headers) end,
        post = function(url, body, headers) return http('POST', url, body, headers) end,
        openUrlInBrowser = function(url) M.opened = url end,
        postMultipart = function(url, content, headers) return http('MULTI', url, content[1].value, headers, content[2].filePath) end,
    },
    LrPathUtils = {
        child = function(a, b) return a .. '/' .. b end,
        leafName = function(p) return p:match('[^/]+$') end,
        removeExtension = function(p) return (p:gsub('%.[^.]+$', '')) end,
        getStandardFilePath = function() return py.temp end,
    },
    LrPrefs = { prefsForPlugin = function() return M.prefs end },
    LrProgressScope = function()
        return { setCancelable = function() end, setCaption = function() end, setPortionComplete = function() end,
                 isCanceled = function() return false end, done = function() end }
    end,
    LrTasks = { startAsyncTask = function(f) f() end, sleep = function() end },
    LrView = { osFactory = anything, bind = function() return {} end },
}

function import(name)
    return modules[name] or error('not mocked: ' .. name)
end

function M.photo(spec)
    local p = { spec = spec, broken = spec.broken, develop = spec.develop or {}, snapshots = {} }
    function p:getRawMetadata(k) return self.spec.raw[k] end
    function p:getDevelopSettings() return self.develop end
    -- like Lightroom: update=true replaces a snapshot of the same name (losing what it held)
    function p:createDevelopSnapshot(name, update)
        for i, s in ipairs(self.snapshots) do
            if s == name and update then self.snapshotDevelop[i] = self:copyDevelop(); return end
        end
        self.snapshots[#self.snapshots + 1] = name
        self.snapshotDevelop[#self.snapshots] = self:copyDevelop()
    end
    function p:getDevelopSnapshots()
        local out = {}
        for i, s in ipairs(self.snapshots) do out[#out + 1] = { snapshotID = tostring(i), name = s } end
        return out
    end
    function p:copyDevelop()
        local c = {}
        for k, v in pairs(self.develop) do c[k] = v end
        return c
    end
    p.snapshotDevelop = {}
    p.steps = {}
    function p:applyDevelopSettings(s, name) self.steps[#self.steps + 1] = name or 'settings'; for k, v in pairs(s) do self.develop[k] = v end end
    function p:applyDevelopPreset(preset)
        self.steps[#self.steps + 1] = 'preset ' .. preset.name
        for k, v in pairs(preset.settings) do self.develop[k] = v end
    end
    return p
end

M.catalog = {
    selected = true,
    getTargetPhotos = function() return M.photos end,
    findPhotoByUuid = function(_, uuid)
        for _, p in ipairs(M.photos) do if p.spec.raw.uuid == uuid then return p end end
        return nil
    end,
    getTargetPhoto = function() return M.catalog.selected and M.photos[1] or nil end,
    getPath = function() return '/Users/b/Weddings 2026.lrcat' end,
    batchGetRawMetadata = function(_, photos, keys)
        local out = {}
        for _, p in ipairs(photos) do
            out[p] = {}
            for _, k in ipairs(keys) do out[p][k] = p.spec.raw[k] end
        end
        return out
    end,
    batchGetFormattedMetadata = function(_, photos, keys)
        local out = {}
        for _, p in ipairs(photos) do
            out[p] = {}
            for _, k in ipairs(keys) do out[p][k] = p.spec.formatted[k] end
        end
        return out
    end,
    withWriteAccessDo = function(_, name, f) M.history[#M.history + 1] = name; f() end,
}

return M
