--[[ Auto Edits: talking to the Auto Edits server.

  The server address and key are kept in Lightroom's plug-in preferences on
  this Mac. Call these functions from inside an async task only. ]]

local LrHttp = import 'LrHttp'
local LrPrefs = import 'LrPrefs'
local LrTasks = import 'LrTasks'

local Json = require 'Json'

local Server = {}

Server.DEFAULT_URL = 'https://edits.cele-brat.solutions'

function Server.prefs()
    return LrPrefs.prefsForPlugin()
end

function Server.url()
    local url = Server.prefs().serverUrl
    if type(url) ~= 'string' or url == '' then url = Server.DEFAULT_URL end
    return (url:gsub('%s+', ''):gsub('/+$', ''))
end

function Server.key()
    local key = Server.prefs().serverKey
    if type(key) ~= 'string' then return '' end
    return (key:gsub('%s+', ''))
end

function Server.isSetUp()
    return Server.key() ~= ''
end

local function headers(extra)
    local h = { { field = 'X-Auto-Edits-Key', value = Server.key() } }
    for _, item in ipairs(extra or {}) do h[#h + 1] = item end
    return h
end

-- Turns what LrHttp returns into: ok, data-or-message, status
local function result(body, info)
    local status = info and info.status
    if not status then
        local why = info and info.error and (info.error.name or info.error.errorCode)
        return false, 'The server could not be reached' .. (why and (' (' .. tostring(why) .. ')') or '') .. '.', nil
    end
    local data = Json.decode(body or '')
    if status >= 200 and status < 300 then
        if type(data) ~= 'table' then return false, 'The server sent an answer that could not be read.', status end
        return true, data, status
    end
    if status == 401 then return false, 'The server did not accept the key. Check it in "Auto Edits: server settings".', status end
    local detail = type(data) == 'table' and data.detail
    if type(detail) ~= 'string' then detail = 'error ' .. tostring(status) end
    return false, 'The server said: ' .. detail, status
end

function Server.get(path)
    return result(LrHttp.get(Server.url() .. path, headers(), 30))
end

function Server.postJson(path, value)
    return result(LrHttp.post(Server.url() .. path, Json.encode(value),
        headers { { field = 'Content-Type', value = 'application/json' } }, 'POST', 30))
end

-- Sends one preview with its details; tries again on a network problem or a
-- busy server, but not when the server refuses the photo.
function Server.sendPhoto(weddingId, meta, filePath)
    local ok, data, status
    for attempt = 1, 3 do
        ok, data, status = result(LrHttp.postMultipart(
            Server.url() .. '/api/weddings/' .. weddingId .. '/photos',
            {
                { name = 'meta', value = Json.encode(meta) },
                { name = 'preview', fileName = 'preview.jpg', filePath = filePath, contentType = 'image/jpeg' },
            },
            headers(), 120))
        if ok or (status and status >= 400 and status < 500) or status == 507 then break end
        LrTasks.sleep(2 * attempt)
    end
    return ok, data, status
end

return Server
