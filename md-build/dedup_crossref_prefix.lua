-- dedup_crossref_prefix.lua -- md-build: strip a hand-written "Figure"/"Fig."/"Table"
-- (or "图"/"表") that directly precedes a figure/table cross-reference ([@fig:N] /
-- [@tbl:N]). pandoc-crossref renders the reference itself ("Figure 1" etc.), so a
-- leftover word would produce "Figure Figure 1". Only removes the word when it is
-- IMMEDIATELY followed by the reference (with or without a space); hand-typed
-- "Figure 2 shows [@fig:2]" text is untouched because a number sits in between.
--
-- Runs BEFORE pandoc-crossref (build.ps1 puts it first in the filter chain) so the
-- Cite nodes are still intact here. ASCII + UTF-8, LF, no BOM.

local PREFIX_WORDS = {
  ["figure"] = true, ["figures"] = true, ["fig"] = true, ["fig."] = true,
  ["table"] = true, ["tables"] = true,
  ["图"] = true, ["表"] = true,
}

-- Longest-first so "tables" wins over "table" when splitting a trailing prefix.
local ORDERED_PREFIXES = {
  "figures", "tables", "figure", "table", "fig.", "fig", "图", "表",
}

local function strip_prefix_word(s)
  local lead = s:match("^[^%a\u{4e00}-\u{9fff}]*") or ""
  local tail = s:match("[^%a\u{4e00}-\u{9fff}]*$") or ""
  local core = s:sub(#lead + 1, #s - #tail)
  return lead, core, tail
end

-- pandoc keeps CJK runs together ("展示了结果。表" is ONE Str), so a note-leading
-- "表"/"图" can be glued after punctuation instead of standing alone. Split it when
-- the previous character is a separator (or start of text) -- "地图" is NOT split.
local SEPARATORS = {
  [" "] = true, ["\t"] = true,
  ["。"] = true, ["，"] = true, ["、"] = true, ["；"] = true, ["："] = true,
  ["！"] = true, ["？"] = true, ["”"] = true, ["’"] = true, ["）"] = true, ["】"] = true,
  [","] = true, ["."] = true, [";"] = true, [":"] = true,
  ["!"] = true, ["?"] = true, ['"'] = true, ["'"] = true, [")"] = true, ["]"] = true,
}

local function split_trailing_prefix(s)
  local n = pandoc.text.len(s)
  if not n then
    return nil
  end
  for _, w in ipairs(ORDERED_PREFIXES) do
    local wn = pandoc.text.len(w)
    if n > wn then
      local tail = pandoc.text.sub(s, -wn)
      if pandoc.text.lower(tail) == w then
        local before = pandoc.text.sub(s, -wn - 1, -wn - 1)
        if before == "" or SEPARATORS[before] then
          return pandoc.text.sub(s, 1, -wn - 1), tail
        end
      end
    end
  end
  return nil
end

local function is_fig_tbl_cite(el)
  if el.tag ~= "Cite" then
    return false
  end
  for _, c in ipairs(el.citations) do
    local id = c.id or ""
    if id:sub(1, 4) == "fig:" or id:sub(1, 4) == "tbl:" then
      return true
    end
  end
  return false
end

function Para(el)
  local content = el.content
  local out = {}
  local i = 1
  while i <= #content do
    local cur = content[i]
    local nxt = content[i + 1]
    local nxt2 = content[i + 2]
    if cur.tag == "Str" then
      local lead, core, tail = strip_prefix_word(cur.text)
      local whole = PREFIX_WORDS[cur.text:lower()]
      local core_pref = PREFIX_WORDS[core:lower()]
      local head, trailing = split_trailing_prefix(cur.text)
      local trailing_pref = trailing and PREFIX_WORDS[trailing:lower()]
      local pref = whole or core_pref or trailing_pref
      if pref and nxt and nxt.tag == "Space" and nxt2 and is_fig_tbl_cite(nxt2) then
        if trailing_pref and not (whole or core_pref) and head ~= "" then
          table.insert(out, pandoc.Str(head))           -- keep "展示了结果。"
        elseif core_pref and not whole and (lead ~= "" or tail ~= "") then
          table.insert(out, pandoc.Str(lead .. tail))   -- keep "(" or "," around the word
        end
        i = i + 2              -- drop Str (+ optional punctuation) + Space, keep the Cite
      elseif pref and nxt and is_fig_tbl_cite(nxt) then
        if trailing_pref and not (whole or core_pref) and head ~= "" then
          table.insert(out, pandoc.Str(head))
        elseif core_pref and not whole and (lead ~= "" or tail ~= "") then
          table.insert(out, pandoc.Str(lead .. tail))
        end
        i = i + 1              -- drop Str, keep the Cite
      else
        table.insert(out, cur)
        i = i + 1
      end
    else
      table.insert(out, cur)
      i = i + 1
    end
  end
  el.content = out
  return el
end
