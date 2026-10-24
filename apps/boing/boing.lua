-- Boing: a small, asset-free Jet scene inspired by the Amiga demo.
local solaros = require("solaros")
local floor, sin, cos, pi = math.floor, math.sin, math.cos, math.pi
local gfx = solaros.gfx
local synth = solaros.synth
local sound_ready, sound_owned, muted = false, false, false
local sound_seed = solaros.time.uptime_ms() % 65536
local sweep_at, release_at, sweep_note, held_note

local function silence()
    sweep_at, release_at, sweep_note, held_note = nil, nil, nil, nil
    if sound_owned then synth.all_notes_off() end
end

local function prepare_sound()
    if not synth then return end
    for _, name in ipairs({"status", "configure", "configure_oscillator2", "configure_filter",
                           "configure_performance", "note_on", "note_off", "all_notes_off", "stop"}) do
        if type(synth[name]) ~= "function" then return end
    end
    local status = synth.status()
    if status.owner and status.owner ~= "" then return end
    -- Set every part of the patch: synth settings persist between apps.
    -- A soft overtone and a resonant filter give the glide a rubbery edge.
    synth.configure("sine", 3, 100, 65, 210)
    synth.configure_oscillator2("triangle", 1, 4, 18)
    synth.configure_filter(2200, 18, 40, 2, 180, 0, 120)
    synth.configure_performance(true, 135)
    sound_ready = true
end

local function boing(wall, now)
    if not sound_ready or muted then return end
    silence()
    sound_seed = (sound_seed * 25173 + 13849) % 65536
    local variation = sound_seed % 25 - 12
    local high = (wall and 310 or 275) + variation
    sweep_note = (wall and 110 or 88) + variation // 3
    synth.configure_performance(true, 125 + sound_seed % 25)
    synth.note_on(high, wall and 82 or 94)
    sound_owned = true
    held_note = high
    -- Let the native audio worker start the high note, then retarget its
    -- monophonic voice. Glide and envelopes run in the synth, not this loop.
    sweep_at, release_at = now + 25, now + 200
end

local function update_sound(now)
    if sweep_at and now >= sweep_at then
        synth.note_on(sweep_note, 90)
        synth.note_off(held_note)
        held_note, sweep_at = sweep_note, nil
    end
    if release_at and now >= release_at then silence() end
end

local function audio_call(fn, ...)
    local ok, err = pcall(fn, ...)
    if not ok then
        if sound_owned then pcall(synth.stop) end
        sound_ready, sound_owned = false, false
        sweep_at, release_at, sweep_note, held_note = nil, nil, nil, nil
        print("Boing sound unavailable: " .. tostring(err))
    end
end

local function options()
    local result = {}
    local i = 1
    while arg and i <= #arg do
        local key, value = arg[i], arg[i + 1]
        if key == "--foreground" or key == "--background" then
            local hex = value and value:gsub("^#", ""):gsub("^0[xX]", "")
            if not hex or #hex ~= 6 or not hex:match("^%x+$") then
                error(key .. " requires a six-digit RGB color")
            end
            result[key:sub(3)] = tonumber(hex, 16)
        else
            error("Usage: boing [--foreground RRGGBB --background RRGGBB]")
        end
        i = i + 2
    end
    return result
end

local palette = options()
if gfx.theme then
    local theme = gfx.theme()
    palette.foreground = palette.foreground or theme.foreground
    palette.background = palette.background or theme.background
end
if not palette.foreground or not palette.background then
    error("Boing needs gfx.theme(), or --foreground and --background RGB colors")
end
if not solaros.jet then
    error("Boing requires the Jet service and library; install with: pkg install jet")
end

local scene
local ok, err = pcall(function()
    scene = solaros.jet.open(nil, 20)
    local caps = scene:capabilities()
    if not caps.geometry or not caps.scene or not caps.materials or not caps.background_cache then
        error("Boing requires Jet library 1.11 or newer; update with: pkg install jet")
    end
    local size = scene:status()
    -- Jet's FOV is horizontal. Back away on wide displays so the same
    -- design fits both axes, without changing the ball's proportions.
    local distance = floor(650 * math.max(1, (size.width / size.height) / (4 / 3)))
    scene:background(palette.background)
    scene:camera(0, 0, -distance, 0, 0, 0, 60)
    scene:light(0x909090, 0xffffff, 160, -35, 50)

    -- Tile the same square grid over four planes. Filled planes avoid the
    -- gaps that very thin triangles can develop during rasterization, and
    -- perspective-correct UVs keep the wall and floor cells aligned in 3D.
    local grid_pixels = {}
    for y = 0, 15 do
        for x = 0, 15 do
            grid_pixels[#grid_pixels + 1] = string.char((x == 0 or y == 0) and 1 or 0)
        end
    end
    local grid_texture = scene:texture(16, 16, table.concat(grid_pixels), {
        format="index8", palette={palette.background, palette.foreground}
    })
    local ink = scene:material{texture=grid_texture, shading="unlit"}
    local vertices, triangles = {}, {}
    local function quad(a, b, c, d, material)
        local n = #vertices
        vertices[n + 1], vertices[n + 2] = a, b
        vertices[n + 3], vertices[n + 4] = c, d
        triangles[#triangles + 1] = {n + 1, n + 2, n + 3, material or ink}
        triangles[#triangles + 1] = {n + 1, n + 3, n + 4, material or ink}
    end
    local ground, back, front, side = -180, 260, -340, 300
    quad({-side, ground, back, 0, 0}, {side, ground, back, 10, 0},
         {side, 360, back, 10, 9}, {-side, 360, back, 0, 9})
    quad({-side, ground, back, 0, 0}, {side, ground, back, 10, 0},
         {side, ground, front, 10, 10}, {-side, ground, front, 0, 10})
    for _, wall in ipairs({-side, side}) do
        quad({wall, ground, back, 0, 0}, {wall, ground, front, 10, 0},
             {wall, 360, front, 10, 9}, {wall, 360, back, 0, 9})
    end
    local grid = scene:mesh_data(vertices, triangles, ink)
    scene:object_set(grid, {culling="none"})
    -- Freeze the real 3D room's pixels AND depth once. Dynamic geometry still
    -- tests against the floor and walls, without retexturing them every frame.
    scene:settings{background_cache=true}
    scene:render()
    scene:remove(grid)

    -- A tiny indexed texture keeps checker evaluation in native code.
    -- Twice as many longitude tiles keep the checks square at the equator.
    local pixels = {}
    for y = 0, 5 do
        for x = 0, 11 do pixels[#pixels + 1] = string.char((x + y) % 2) end
    end
    local texture = scene:texture(12, 6, table.concat(pixels), {
        format="index8", palette={0xffffff, 0xff0000}
    })
    local checks = scene:material{
        texture=texture, shading="gouraud"
    }
    local ball = scene:primitive{kind="sphere", radius=100, segments=24, material=checks}

    -- A translucent flattened disk lies just above the floor. Its footprint
    -- grows and fades with altitude, while the depth buffer keeps the ball
    -- in front. No second sphere or script fragment shader is needed.
    local shadow_ink = scene:material{color=0x000000, shading="unlit", alpha=160}
    local sv, st = {{0, 0, 0}}, {}
    for i = 0, 31 do
        local angle = i * 2 * pi / 32
        sv[#sv + 1] = {floor(100 * cos(angle)), 0, floor(62 * sin(angle))}
    end
    for i = 0, 31 do st[#st + 1] = {1, i + 2, (i + 1) % 32 + 2} end
    local shadow = scene:mesh_data(sv, st, shadow_ink)
    scene:object_set(shadow, {culling="none", depth_write=false})
    audio_call(prepare_sound)

    local elapsed, last = 0, solaros.time.uptime_ms()
    local radius, speed, gravity = 100, 635, 880
    local limit = side - radius
    local x, y, vx, vy = -limit, ground + radius, 120, speed
    local paused, dirty = false, true
    while not solaros.should_exit() do
        local frame_started = solaros.time.uptime_ms()
        local key = gfx.getch(0)
        if key == gfx.KEY_ESCAPE or key == string.byte("q") then break end
        if key == string.byte(" ") or key == string.byte("p") then
            paused = not paused
            if paused then audio_call(silence) end
        end
        if key == string.byte("m") then
            muted = not muted
            if muted then audio_call(silence) end
        end
        if key == string.byte("r") then
            elapsed, dirty = 0, true
            x, y, vx, vy = -limit, ground + radius, 120, speed
            audio_call(silence)
        end

        local now = solaros.time.uptime_ms()
        if not paused then
            local dt = math.max(0, now - last) / 1000
            if dt > 0.5 then dt = 0 end -- Resume without a large jump.
            elapsed = elapsed + dt * 1000
            x = x + vx * dt
            y = y + vy * dt - gravity * dt * dt / 2
            vy = vy - gravity * dt
            -- Clamp collisions to the sphere's radius and present the exact
            -- contact pose for a frame before moving away from the surface.
            local wall_hit, floor_hit = false, false
            if x >= limit and vx > 0 then x, vx, wall_hit = limit, -math.abs(vx), true end
            if x <= -limit and vx < 0 then x, vx, wall_hit = -limit, math.abs(vx), true end
            if y <= ground + radius and vy < 0 then y, vy, floor_hit = ground + radius, speed, true end
            if floor_hit or wall_hit then audio_call(boing, wall_hit and not floor_hit, now) end
        end
        last = now
        audio_call(update_sound, now)
        if not paused or dirty then
            local t = elapsed / 1000
            local height = y - ground - radius
            scene:transform(ball, floor(x), floor(y), 0,
                            12, floor(t * 75) % 360, -18)
            local spread = 1 + height / 600
            scene:object_set(shadow, {
                x=floor(x + 25 + height / 6), y=ground + 2, z=25,
                scale_x=spread, scale_z=spread
            })
            scene:material_set(shadow_ink, {alpha=floor(180 - height / 3)})
            scene:render()
            dirty = false
        end
        -- Pace complete frames instead of adding a fixed delay to rendering.
        local used = solaros.time.uptime_ms() - frame_started
        solaros.time.sleep_ms(paused and 40 or math.max(1, 50 - used))
    end
end)
local close_ok, close_err = true, nil
if scene then close_ok, close_err = pcall(scene.close, scene) end
local stop_ok, stop_err = true, nil
if sound_owned then stop_ok, stop_err = pcall(synth.stop) end
if not ok then error(err, 0) end
if not close_ok then error(close_err, 0) end
if not stop_ok then error(stop_err, 0) end
