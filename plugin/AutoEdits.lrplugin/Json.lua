--[[ Auto Edits: a small JSON reader and writer (Lightroom has none built in). ]]

local Json = {}

local ESCAPES = { ['"'] = '\\"', ['\\'] = '\\\\', ['\b'] = '\\b', ['\f'] = '\\f',
                  ['\n'] = '\\n', ['\r'] = '\\r', ['\t'] = '\\t' }

local function quote(s)
    return '"' .. s:gsub('[%c"\\]', function(c)
        return ESCAPES[c] or string.format('\\u%04x', c:byte())
    end) .. '"'
end

local function isArray(t)
    local n = 0
    for k in pairs(t) do
        if type(k) ~= 'number' then return false end
        n = n + 1
    end
    return n == #t
end

local function encode(v, out)
    local kind = type(v)
    if kind == 'nil' then
        out[#out + 1] = 'null'
    elseif kind == 'boolean' then
        out[#out + 1] = v and 'true' or 'false'
    elseif kind == 'number' then
        if v ~= v or v == math.huge or v == -math.huge then
            out[#out + 1] = 'null'
        elseif v == math.floor(v) and math.abs(v) < 1e15 then
            out[#out + 1] = string.format('%.0f', v)
        else
            out[#out + 1] = string.format('%.14g', v)
        end
    elseif kind == 'string' then
        out[#out + 1] = quote(v)
    elseif kind == 'table' then
        if next(v) ~= nil and isArray(v) then
            out[#out + 1] = '['
            for i = 1, #v do
                if i > 1 then out[#out + 1] = ',' end
                encode(v[i], out)
            end
            out[#out + 1] = ']'
        else
            out[#out + 1] = '{'
            local keys = {}
            for k in pairs(v) do keys[#keys + 1] = tostring(k) end
            table.sort(keys)
            local first = true
            for _, k in ipairs(keys) do
                local item = v[k]
                if item == nil then item = v[tonumber(k)] end
                if type(item) ~= 'function' and type(item) ~= 'userdata' then
                    if not first then out[#out + 1] = ',' end
                    first = false
                    out[#out + 1] = quote(k)
                    out[#out + 1] = ':'
                    encode(item, out)
                end
            end
            out[#out + 1] = '}'
        end
    else
        out[#out + 1] = 'null'
    end
end

function Json.encode(v)
    local out = {}
    encode(v, out)
    return table.concat(out)
end

-- ---- reading ------------------------------------------------------------

local function utf8(code)
    if code < 0x80 then return string.char(code) end
    if code < 0x800 then
        return string.char(0xC0 + math.floor(code / 0x40), 0x80 + code % 0x40)
    end
    return string.char(0xE0 + math.floor(code / 0x1000), 0x80 + math.floor(code / 0x40) % 0x40, 0x80 + code % 0x40)
end

local UNESCAPES = { b = '\b', f = '\f', n = '\n', r = '\r', t = '\t', ['"'] = '"', ['\\'] = '\\', ['/'] = '/' }

local decodeValue

local function skip(s, i)
    return s:find('[^ \t\r\n]', i) or #s + 1
end

local function decodeString(s, i)
    local parts, j = {}, i + 1
    while true do
        local k = s:find('["\\]', j)
        if not k then error('JSON: text is cut off') end
        parts[#parts + 1] = s:sub(j, k - 1)
        if s:sub(k, k) == '"' then return table.concat(parts), k + 1 end
        local e = s:sub(k + 1, k + 1)
        if e == 'u' then
            parts[#parts + 1] = utf8(tonumber(s:sub(k + 2, k + 5), 16) or 63)
            j = k + 6
        else
            parts[#parts + 1] = UNESCAPES[e] or e
            j = k + 2
        end
    end
end

decodeValue = function(s, i)
    i = skip(s, i)
    local c = s:sub(i, i)
    if c == '{' then
        local t = {}
        i = skip(s, i + 1)
        if s:sub(i, i) == '}' then return t, i + 1 end
        while true do
            i = skip(s, i)
            if s:sub(i, i) ~= '"' then error('JSON: a name was expected at ' .. i) end
            local key
            key, i = decodeString(s, i)
            i = skip(s, i)
            if s:sub(i, i) ~= ':' then error('JSON: ":" was expected at ' .. i) end
            t[key], i = decodeValue(s, i + 1)
            i = skip(s, i)
            local d = s:sub(i, i)
            if d == '}' then return t, i + 1 end
            if d ~= ',' then error('JSON: "," was expected at ' .. i) end
            i = i + 1
        end
    elseif c == '[' then
        local t, n = {}, 0
        i = skip(s, i + 1)
        if s:sub(i, i) == ']' then return t, i + 1 end
        while true do
            n = n + 1
            t[n], i = decodeValue(s, i)
            i = skip(s, i)
            local d = s:sub(i, i)
            if d == ']' then return t, i + 1 end
            if d ~= ',' then error('JSON: "," was expected at ' .. i) end
            i = i + 1
        end
    elseif c == '"' then
        return decodeString(s, i)
    elseif s:sub(i, i + 3) == 'true' then
        return true, i + 4
    elseif s:sub(i, i + 4) == 'false' then
        return false, i + 5
    elseif s:sub(i, i + 3) == 'null' then
        return nil, i + 4
    else
        local num = s:match('^-?%d+%.?%d*[eE]?[+-]?%d*', i)
        if not num or num == '' then error('JSON: unexpected text at ' .. i) end
        return tonumber(num), i + #num
    end
end

-- returns the value, or nil and a message when the text is not JSON
function Json.decode(s)
    if type(s) ~= 'string' then return nil, 'no text' end
    local ok, value = pcall(function()
        local v = decodeValue(s, 1)
        return v
    end)
    if ok then return value end
    return nil, value
end

return Json
