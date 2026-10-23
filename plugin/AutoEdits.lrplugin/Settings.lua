--[[ Auto Edits: server settings (address and key), with a connection test.

  Once the key works, the window shows who is connected and the credits left, and, when the
  server has a newer plug-in than this one, a line saying so with a Download button. ]]

local LrBinding = import 'LrBinding'
local LrDialogs = import 'LrDialogs'
local LrFunctionContext = import 'LrFunctionContext'
local LrHttp = import 'LrHttp'
local LrTasks = import 'LrTasks'
local LrView = import 'LrView'

local Server = require 'Server'

local Settings = {}

-- "0.9.0" -> { 0, 9, 0 }; anything unreadable -> nil
function Settings.parseVersion(text)
    if type(text) ~= 'string' then return nil end
    local a, b, c = text:match('^%s*(%d+)%.(%d+)%.?(%d*)')
    if not a then return nil end
    return { tonumber(a), tonumber(b), tonumber(c) or 0 }
end

function Settings.isNewer(server, mine)
    local s, m = Settings.parseVersion(server), Settings.parseVersion(mine)
    if not s or not m then return false end
    for i = 1, 3 do
        if s[i] > m[i] then return true end
        if s[i] < m[i] then return false end
    end
    return false
end

function Settings.myVersion()
    local ok, info = pcall(require, 'Info')
    local v = ok and type(info) == 'table' and info.VERSION or {}
    return string.format('%d.%d.%d', v.major or 0, v.minor or 0, v.revision or 0)
end

-- The lines the window shows after a successful call to /api/me/plugin.
function Settings.describe(data, mine)
    local who = 'Connected as ' .. tostring(data.email or '?')
    local credits
    if data.unlimited then
        credits = 'Credits: unlimited (studio account)'
    else
        credits = string.format('Credits: %s', tostring(data.credits or 0))
    end
    local newer = Settings.isNewer(data.plugin_version, mine)
    local version
    if newer then
        version = string.format('A new version of the plug-in is available: %s (this is %s).', tostring(data.plugin_version), mine)
    else
        version = string.format('Plug-in %s is up to date.', mine)
    end
    return who, credits, version, newer
end

local function run(context)
    local prefs = Server.prefs()
    local props = LrBinding.makePropertyTable(context)
    props.serverUrl = Server.url()
    props.serverKey = Server.key()
    props.status = ''
    props.who = ''
    props.credits = ''
    props.version = ''
    props.newer = false
    local mine = Settings.myVersion()

    local f = LrView.osFactory()
    local bind = LrView.bind

    local function save()
        prefs.serverUrl = props.serverUrl
        prefs.serverKey = props.serverKey
    end

    local function refresh()
        local ok, data = Server.get('/api/me/plugin')
        if ok then
            props.who, props.credits, props.version, props.newer = Settings.describe(data, mine)
            props.status = 'Connected.'
        else
            props.who, props.credits, props.version, props.newer = '', '', '', false
            props.status = tostring(data)
        end
        return ok
    end

    if Server.isSetUp() then
        LrTasks.startAsyncTask(function() refresh() end)
    end

    local contents = f:column {
        bind_to_object = props,
        spacing = f:control_spacing(),
        f:row {
            f:static_text { title = 'Server address', width = 110, alignment = 'right' },
            f:edit_field { value = bind 'serverUrl', width_in_chars = 36, immediate = true },
        },
        f:row {
            f:static_text { title = 'Key', width = 110, alignment = 'right' },
            f:password_field { value = bind 'serverKey', width_in_chars = 36, immediate = true },
        },
        f:row {
            f:static_text { title = '', width = 110 },
            f:push_button {
                title = 'Test connection',
                action = function()
                    save()
                    props.status = 'Testing...'
                    LrTasks.startAsyncTask(function()
                        if refresh() then
                            local ok, data = Server.get('/api/ping')
                            if ok then
                                props.status = string.format('Connected. Server version %s, %s GB free.',
                                    tostring(data.version), tostring(data.free_gb))
                            end
                        end
                    end)
                end,
            },
        },
        f:row {
            f:static_text { title = '', width = 110 },
            f:static_text { title = bind 'status', width_in_chars = 36, height_in_lines = 2 },
        },
        f:row {
            f:static_text { title = '', width = 110 },
            f:column {
                f:static_text { title = bind 'who', width_in_chars = 36 },
                f:static_text { title = bind 'credits', width_in_chars = 36 },
                f:static_text { title = bind 'version', width_in_chars = 36, height_in_lines = 2 },
            },
        },
        f:row {
            f:static_text { title = '', width = 110 },
            f:push_button {
                title = 'Download the new version',
                visible = bind 'newer',
                action = function()
                    LrHttp.openUrlInBrowser(Server.url() .. '/download/lightroom-plugin')
                end,
            },
        },
    }

    local choice = LrDialogs.presentModalDialog {
        title = 'Auto Edits: server settings',
        contents = contents,
        actionVerb = 'Save',
    }
    if choice == 'ok' then save() end
end

-- When required by another file (or the test rig) only the helpers are wanted; the window opens
-- when Lightroom runs this file from its menu.
if ... == 'Settings' then return Settings end

LrTasks.startAsyncTask(function()
    LrFunctionContext.callWithContext('AutoEditsSettings', function(context)
        LrDialogs.attachErrorDialogToFunctionContext(context)
        run(context)
    end)
end)

return Settings
