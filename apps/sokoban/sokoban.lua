local solaros = require("solaros")
local gfx = solaros.gfx

local KEY_BACKSPACE = 8
local KEY_LF = 10
local KEY_ENTER = 13
local KEY_SPACE = 32
local KEY_LEFT_BRACKET = 91
local KEY_RIGHT_BRACKET = 93
local KEY_A = 97
local KEY_D = 100
local KEY_H = 104
local KEY_J = 106
local KEY_K = 107
local KEY_L = 108
local KEY_N = 110
local KEY_P = 112
local KEY_Q = 113
local KEY_R = 114
local KEY_S = 115
local KEY_U = 117
local KEY_W = 119

local HEADER_H = 28
local FOOTER_H = 20
local MARGIN = 8
local MAX_CELL = 30
local MAX_UNDO = 128

-- These original levels were generated locally and then checked with a full
-- state-space solver. The order follows the number of explored solution states.
local LEVELS = {
    {
        name = "Crate training",
        rows = {
            "########",
            "#     ##",
            "#.# #  #",
            "# ###@ #",
            "#. $ $ #",
            "#      #",
            "########",
        },
    },
    {
        name = "Crossbeam",
        rows = {
            "#########",
            "#    #  #",
            "#$#  #@ #",
            "#  . $  #",
            "# $ #   #",
            "#.#.  ###",
            "#########",
        },
    },
    {
        name = "Switchback",
        rows = {
            "########",
            "#    $.#",
            "## $   #",
            "# ## .##",
            "# # #. #",
            "#    $@#",
            "## #   #",
            "########",
        },
    },
    {
        name = "Long way home",
        rows = {
            "########",
            "#.    .#",
            "# #  ###",
            "##     #",
            "##@ $  #",
            "# $ #  #",
            "#      #",
            "########",
        },
    },
    {
        name = "Side pocket",
        rows = {
            "########",
            "# . # @#",
            "#      #",
            "# $    #",
            "#. ## $#",
            "#   #$.#",
            "#      #",
            "########",
        },
    },
    {
        name = "Forklift",
        rows = {
            "########",
            "#   .###",
            "# #  . #",
            "#$ #  ##",
            "#      #",
            "#.$$@# #",
            "#      #",
            "########",
        },
    },
    {
        name = "Packing order",
        rows = {
            "########",
            "#      #",
            "##  .#.#",
            "##$ .  #",
            "# @$$  #",
            "#     ##",
            "########",
        },
    },
    {
        name = "Blind corner",
        rows = {
            "#########",
            "#.  #   #",
            "#  $$  .#",
            "#  $    #",
            "#  @#  .#",
            "##      #",
            "#########",
        },
    },
    {
        name = "The yard",
        rows = {
            "#########",
            "#   @   #",
            "#  $ #  #",
            "#       #",
            "#  #  . #",
            "# #  $  #",
            "# . $  .#",
            "#########",
        },
    },
    {
        name = "Final shift",
        rows = {
            "########",
            "#     ##",
            "## $   #",
            "#  $ $ #",
            "#     ##",
            "#   . .#",
            "#. #@ ##",
            "########",
        },
    },
}

-- Sol is a 16 x 16 warehouse cat. Rows are also kept as text so firmware
-- before the native sprite binding can draw the same art in short runs.
local PLAYER_SPRITE_ROWS = {
    "...##......##...",
    "..####....####..",
    "..############..",
    ".###..####..###.",
    ".##..#....#..##.",
    ".##..........##.",
    "..##..#..#..##..",
    "...##......##...",
    "....########....",
    ".....######.....",
    "...##########...",
    "..###.####.###..",
    "..##..####..##..",
    "......####......",
    ".....##..##.....",
    "....##....##....",
}

local function pack_sprite(rows)
    local bytes = {}
    local width = #rows[1]
    for _, row in ipairs(rows) do
        for start_x = 1, width, 8 do
            local value = 0
            for bit = 0, 7 do
                local x = start_x + bit
                if x <= width and row:sub(x, x) == "#" then
                    value = value + 2 ^ bit
                end
            end
            bytes[#bytes + 1] = string.char(value)
        end
    end
    return table.concat(bytes)
end

local PLAYER_SPRITE_XBM = pack_sprite(PLAYER_SPRITE_ROWS)

local function point_key(x, y)
    return x .. "," .. y
end

local function copy_set(source)
    local result = {}
    for key, value in pairs(source) do
        result[key] = value
    end
    return result
end

local function parse_level(definition)
    local state = {
        name = definition.name,
        rows = #definition.rows,
        cols = 0,
        walls = {},
        floor = {},
        goals = {},
        boxes = {},
        player_x = 1,
        player_y = 1,
        moves = 0,
        pushes = 0,
        undo = {},
        solved = false,
    }

    for y, row in ipairs(definition.rows) do
        state.cols = math.max(state.cols, #row)
        for x = 1, #row do
            local tile = row:sub(x, x)
            if tile ~= "_" then
                state.floor[point_key(x, y)] = true
            end
            if tile == "#" then
                state.walls[point_key(x, y)] = true
            elseif tile == "." then
                state.goals[point_key(x, y)] = true
            elseif tile == "$" then
                state.boxes[point_key(x, y)] = true
            elseif tile == "*" then
                state.goals[point_key(x, y)] = true
                state.boxes[point_key(x, y)] = true
            elseif tile == "@" then
                state.player_x = x
                state.player_y = y
            elseif tile == "+" then
                state.goals[point_key(x, y)] = true
                state.player_x = x
                state.player_y = y
            end
        end
    end
    return state
end

local function is_solved(state)
    for key, _ in pairs(state.goals) do
        if not state.boxes[key] then
            return false
        end
    end
    return true
end

local function save_undo(state)
    state.undo[#state.undo + 1] = {
        player_x = state.player_x,
        player_y = state.player_y,
        boxes = copy_set(state.boxes),
        moves = state.moves,
        pushes = state.pushes,
    }
    if #state.undo > MAX_UNDO then
        table.remove(state.undo, 1)
    end
end

local function move_player(state, dx, dy)
    local next_x = state.player_x + dx
    local next_y = state.player_y + dy
    local next_key = point_key(next_x, next_y)
    if not state.floor[next_key] or state.walls[next_key] then
        return false
    end

    local pushed = state.boxes[next_key]
    if pushed then
        local box_x = next_x + dx
        local box_y = next_y + dy
        local box_key = point_key(box_x, box_y)
        if not state.floor[box_key] or state.walls[box_key] or state.boxes[box_key] then
            return false
        end
        save_undo(state)
        state.boxes[next_key] = nil
        state.boxes[box_key] = true
        state.pushes = state.pushes + 1
    else
        save_undo(state)
    end

    state.player_x = next_x
    state.player_y = next_y
    state.moves = state.moves + 1
    state.solved = is_solved(state)
    return true
end

local function undo(state)
    local previous = table.remove(state.undo)
    if previous == nil then
        return false
    end
    state.player_x = previous.player_x
    state.player_y = previous.player_y
    state.boxes = previous.boxes
    state.moves = previous.moves
    state.pushes = previous.pushes
    state.solved = false
    return true
end

local function draw_goal(cx, cy, cell)
    local radius = math.max(2, cell // 7)
    gfx.color(gfx.DARK)
    gfx.circle(cx, cy, radius)
    gfx.pixel(cx, cy)
end

local function draw_box(px, py, cell, on_goal)
    local inset = math.max(2, cell // 8)
    local size = cell - inset * 2
    gfx.color(on_goal and gfx.BLACK or gfx.DARK)
    gfx.fill_rect(px + inset, py + inset, size, size)
    gfx.color(gfx.BLACK)
    gfx.rect(px + inset, py + inset, size, size)
    gfx.line(px + inset + 2, py + inset + 2, px + cell - inset - 3, py + cell - inset - 3)
    gfx.line(px + cell - inset - 3, py + inset + 2, px + inset + 2, py + cell - inset - 3)
end

local function draw_sprite_fallback(x, y, rows)
    for row_index, row in ipairs(rows) do
        local run_start = nil
        for col = 1, #row + 1 do
            local ink = col <= #row and row:sub(col, col) == "#"
            if ink and run_start == nil then
                run_start = col
            elseif not ink and run_start ~= nil then
                gfx.fill_rect(x + run_start - 1, y + row_index - 1, col - run_start, 1)
                run_start = nil
            end
        end
    end
end

local function draw_player(px, py, cell, on_goal)
    local sprite_w = #PLAYER_SPRITE_ROWS[1]
    local sprite_h = #PLAYER_SPRITE_ROWS
    local sprite_x = px + (cell - sprite_w) // 2
    local sprite_y = py + (cell - sprite_h) // 2
    gfx.color(gfx.BLACK)
    if gfx.sprite ~= nil then
        gfx.sprite(sprite_x, sprite_y, sprite_w, sprite_h, PLAYER_SPRITE_XBM)
    else
        draw_sprite_fallback(sprite_x, sprite_y, PLAYER_SPRITE_ROWS)
    end
    if on_goal then
        gfx.circle(px + cell // 2, py + cell // 2, math.max(9, cell // 2 - 2))
    end
end

local function centered_text(w, y, message)
    gfx.text(math.max(0, (w - #message * 7) // 2), y, message)
end

local function draw(state, level_index, w, h)
    local available_w = w - MARGIN * 2
    local available_h = h - HEADER_H - FOOTER_H - MARGIN * 2
    local cell = math.min(available_w // state.cols, available_h // state.rows, MAX_CELL)
    cell = math.max(6, cell)
    local board_w = state.cols * cell
    local board_h = state.rows * cell
    local board_x = (w - board_w) // 2
    local board_y = HEADER_H + MARGIN + math.max(0, (available_h - board_h) // 2)

    gfx.clear(gfx.WHITE)

    gfx.color(gfx.BLACK)
    gfx.font(gfx.FONT_BOLD_14)
    gfx.text(MARGIN, 17, "Sokoban " .. level_index .. "/" .. #LEVELS)
    gfx.font(gfx.FONT_MONO_12)
    local stats = "moves " .. state.moves .. "  pushes " .. state.pushes
    gfx.text(math.max(MARGIN, w - MARGIN - #stats * 7), 17, stats)

    for y = 1, state.rows do
        for x = 1, state.cols do
            local key = point_key(x, y)
            if state.floor[key] then
                local px = board_x + (x - 1) * cell
                local py = board_y + (y - 1) * cell
                if state.walls[key] then
                    gfx.color(gfx.BLACK)
                    gfx.fill_rect(px, py, cell, cell)
                    if cell >= 12 then
                        gfx.color(gfx.DARK)
                        gfx.line(px + 2, py + cell // 2, px + cell - 3, py + cell // 2)
                    end
                else
                    gfx.color(gfx.LIGHT)
                    gfx.rect(px, py, cell, cell)
                    if state.goals[key] then
                        draw_goal(px + cell // 2, py + cell // 2, cell)
                    end
                    if state.boxes[key] then
                        draw_box(px, py, cell, state.goals[key])
                    end
                    if state.player_x == x and state.player_y == y then
                        draw_player(px, py, cell, state.goals[key])
                    end
                end
            end
        end
    end

    gfx.color(gfx.BLACK)
    gfx.font(gfx.FONT_MONO_12)
    if state.solved then
        centered_text(w, h - 7, "Solved! Enter continues | U undo")
    else
        centered_text(w, h - 7, state.name .. " | U undo R restart Q quit")
    end
    gfx.refresh()
end

local function direction_for_key(key)
    if key == gfx.KEY_LEFT or key == KEY_A or key == KEY_H then
        return -1, 0
    elseif key == gfx.KEY_RIGHT or key == KEY_D or key == KEY_L then
        return 1, 0
    elseif key == gfx.KEY_UP or key == KEY_W or key == KEY_K then
        return 0, -1
    elseif key == gfx.KEY_DOWN or key == KEY_S or key == KEY_J then
        return 0, 1
    end
    return nil, nil
end

gfx.begin()

local ok, err = pcall(function()
    local w, h = gfx.size()
    local level_index = 1
    local state = parse_level(LEVELS[level_index])
    draw(state, level_index, w, h)

    while not solaros.should_exit() do
        local key = gfx.getch(250)
        local width, height = gfx.size()
        if width ~= w or height ~= h then
            w, h = width, height
            draw(state, level_index, w, h)
        end
        if key ~= nil then
            if key == gfx.KEY_ESCAPE or key == KEY_Q then
                break
            elseif key == KEY_R then
                state = parse_level(LEVELS[level_index])
                draw(state, level_index, w, h)
            elseif key == KEY_U or key == KEY_BACKSPACE then
                if undo(state) then
                    draw(state, level_index, w, h)
                end
            elseif key == KEY_N or key == KEY_RIGHT_BRACKET or key == gfx.KEY_PAGE_DOWN or
                (state.solved and (key == KEY_ENTER or key == KEY_LF or key == KEY_SPACE)) then
                level_index = level_index % #LEVELS + 1
                state = parse_level(LEVELS[level_index])
                draw(state, level_index, w, h)
            elseif key == KEY_P or key == KEY_LEFT_BRACKET or key == gfx.KEY_PAGE_UP then
                level_index = (level_index - 2) % #LEVELS + 1
                state = parse_level(LEVELS[level_index])
                draw(state, level_index, w, h)
            else
                local dx, dy = direction_for_key(key)
                if dx ~= nil and move_player(state, dx, dy) then
                    draw(state, level_index, w, h)
                end
            end
        end
    end
end)

gfx["end"]()

if not ok then
    error(err)
end
