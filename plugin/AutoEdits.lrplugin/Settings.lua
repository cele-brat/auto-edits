--[[ Auto Edits: server settings (address and key), with a connection test. ]]

local LrBinding = import 'LrBinding'
local LrDialogs = import 'LrDialogs'
local LrFunctionContext = import 'LrFunctionContext'
local LrTasks = import 'LrTasks'
local LrView = import 'LrView'

local Server = require 'Server'

local function run(context)
    local prefs = Server.prefs()
    local props = LrBinding.makePropertyTable(context)
    props.serverUrl = Server.url()
    props.serverKey = Server.key()
    props.status = ''

    local f = LrView.osFactory()
    local bind = LrView.bind

    local function save()
        prefs.serverUrl = props.serverUrl
        prefs.serverKey = props.serverKey
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
                        local ok, data = Server.get('/api/ping')
                        if ok then
                            props.status = string.format('Connected. Server version %s, %s GB free.',
                                tostring(data.version), tostring(data.free_gb))
                        else
                            props.status = tostring(data)
                        end
                    end)
                end,
            },
        },
        f:row {
            f:static_text { title = '', width = 110 },
            f:static_text { title = bind 'status', width_in_chars = 36, height_in_lines = 2 },
        },
    }

    local choice = LrDialogs.presentModalDialog {
        title = 'Auto Edits: server settings',
        contents = contents,
        actionVerb = 'Save',
    }
    if choice == 'ok' then save() end
end

LrTasks.startAsyncTask(function()
    LrFunctionContext.callWithContext('AutoEditsSettings', function(context)
        LrDialogs.attachErrorDialogToFunctionContext(context)
        run(context)
    end)
end)
