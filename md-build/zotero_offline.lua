-- zotero_offline.lua  (md-build, third-gen md-*)
-- OFFLINE live-citation rebuild: inject the ORIGINAL Zotero field XML (captured at ingest and
-- stored in manifest/objects.json) for each [@citekey] group -- WITHOUT querying a running
-- Zotero and WITHOUT needing reconciled Better BibTeX keys. This is the third-gen port of the
-- second-gen rebuild_citations.ps1: it emits the full Word field structure
--   begin -> instrText(ADDIN ZOTERO_ITEM ...) -> separate -> result(displayText) -> end
-- with a freshly generated citationID, so Word/Zotero recognize the field and "Refresh" works.
--
-- Input map (built by build.ps1 from objects.json + build/citemap.tsv), one line per group:
--   <sorted;citekeys> \t <displayText> \t <fieldCode>
-- Path: $env:MD_OFFLINE_CITEMAP, else build/offline_citemap.tsv (pandoc runs with cwd=WorkDir).
--
-- Cites whose key-set is NOT in the map (e.g. NEW references added during revision) are left
-- as-is and reported; those still need -Mode live (a running Zotero) to resolve. No lunajson
-- dependency on purpose (reads a flat TSV), so this filter is self-contained in the skill dir.

local map = {}            -- "k1;k2" -> { display = "...", code = "ADDIN ZOTERO_ITEM ..." }
local matched, unmatched = 0, 0
local unmatched_keys = {}

local function load_map(path)
  local fh = io.open(path, 'r')
  if not fh then io.stderr:write('[zotero_offline] map not found: ' .. path .. '\n'); return end
  for line in fh:lines() do
    local k, disp, code = line:match('^(.-)\t(.-)\t(.*)$')
    if k and code and k ~= '' then map[k] = { display = disp or '', code = code } end
  end
  fh:close()
end

local function esc(s)
  s = s:gsub('&', '&amp;'):gsub('<', '&lt;'):gsub('>', '&gt;')
  return s
end

math.randomseed(os.time())
local function new_citeid()
  local cs = '0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz'
  local t = {}
  for i = 1, 8 do local n = math.random(1, #cs); t[i] = cs:sub(n, n) end
  return table.concat(t)
end

local function field_xml(code, disp)
  -- regenerate citationID to avoid duplicate-id collisions on Zotero refresh
  code = code:gsub('("citationID"%s*:%s*")[^"]*(")', '%1' .. new_citeid() .. '%2')
  if code:sub(1, 1) ~= ' ' then code = ' ' .. code end
  if code:sub(-1) ~= ' ' then code = code .. ' ' end
  return
    '<w:r><w:fldChar w:fldCharType="begin"/></w:r>' ..
    '<w:r><w:instrText xml:space="preserve">' .. esc(code) .. '</w:instrText></w:r>' ..
    '<w:r><w:fldChar w:fldCharType="separate"/></w:r>' ..
    '<w:r><w:t xml:space="preserve">' .. esc(disp) .. '</w:t></w:r>' ..
    '<w:r><w:fldChar w:fldCharType="end"/></w:r>'
end

-- load the map once, at filter load (order-safe, no dependence on Meta traversal order)
load_map(os.getenv('MD_OFFLINE_CITEMAP') or 'build/offline_citemap.tsv')

function Cite(el)
  local ids = {}
  for _, c in ipairs(el.citations) do ids[#ids + 1] = c.id end
  table.sort(ids)
  local key = table.concat(ids, ';')
  local hit = map[key]
  if hit then
    matched = matched + 1
    return pandoc.RawInline('openxml', field_xml(hit.code, hit.display))
  else
    unmatched = unmatched + 1
    unmatched_keys[key] = true
    return nil   -- leave as-is; new refs need -Mode live (Zotero)
  end
end

function Pandoc(doc)
  io.stderr:write(('[zotero_offline] matched %d group(s), unmatched %d\n'):format(matched, unmatched))
  if unmatched > 0 then
    local ks = {}
    for k, _ in pairs(unmatched_keys) do ks[#ks + 1] = k end
    io.stderr:write('[zotero_offline] unmatched key-sets (new refs? use -Mode live): ' .. table.concat(ks, ' | ') .. '\n')
  end
  return doc
end
