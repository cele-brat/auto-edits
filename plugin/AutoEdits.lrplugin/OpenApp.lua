--[[ Auto Edits: open the app in the browser, already signed in.

  Asks the server for a link that works once and for two minutes, and opens it.
  The key itself never appears in the browser's address bar. ]]

local LrDialogs = import 'LrDialogs'
local LrFunctionContext = import 'LrFunctionContext'
local LrHttp = import 'LrHttp'
local LrTasks = import 'LrTasks'

local Server = require 'Server'

LrTasks.startAsyncTask(function()
    LrFunctionContext.callWithContext('AutoEditsOpenApp', function(context)
        LrDialogs.attachErrorDialogToFunctionContext(context)
        if not Server.isSetUp() then
            LrDialogs.message('Auto Edits',
                'The server key is not set yet.\n\nOpen Library > Plug-in Extras > "Auto Edits: server settings" first.', 'info')
            return
        end
        local ok, data = Server.postJson('/api/login-link', {})
        if not ok then
            LrDialogs.message('Auto Edits', tostring(data), 'warning')
            return
        end
        LrHttp.openUrlInBrowser(Server.url() .. tostring(data.path))
    end)
end)
