# Game Boy Library

Search Homebrew Hub by title, browse Game Boy homebrew by category, view
screenshots, and download ROMs for the SolarOS Game Boy emulator.

Install from Playground and run **Game Boy Library**. Optional arguments:

```text
playground run gameboy-library --search 2048
playground run gameboy-library --category Puzzle
playground run gameboy-library --downloads
```

## Controls

| Screen | Controls |
| --- | --- |
| Menus and game lists | Up/Down select; Enter opens; Esc or Q goes back; PgUp/PgDn scroll local lists |
| Search | Type a title; Backspace edits; Enter searches; Esc cancels |
| Catalog | PgUp/PgDn or Left/Right changes page; Up/Down continues across pages; R refreshes; `/` searches within the category |
| Game details | Up/Down selects an action; Enter activates it |
| Game details | D downloads; L plays, downloading first if needed; S opens the screenshot viewer |
| Screenshots | Left/Right changes image; Esc returns |
| Downloads in progress | Esc or Q cancels and removes the partial file |

Details show the author, tags, license, download state, and a screenshot when
one is available. Images retain their aspect ratio and use integer enlargement
where they fit. Smaller screens have a separate screenshot view.

Catalog pages fit the display's visible list rows, up to 24 games per page.
Entries without downloadable `.gb` files are skipped before filling each page,
so only the final page can be short. Navigation retains the next game's exact
position without skipping or repeating games between pages. The page number
advances when scrolling past the last row.

Until the end is reached, the header shows the current page and `more` rather
than counting entries that were excluded. After reaching the end, it shows the
total number of browsable games and pages. Each search supports up to 1024
pages. R refreshes the catalog and returns to its first page. Cached pages are
separate for different filters and page sizes.

## Downloads and offline use

Data lives under `/Games/GameBoy` on the preferred storage volume, resolved at
startup. Each game has its own directory with `game.gb`, `entry.json`, and
cached screenshots. Emulator saves live beside the ROM as `game.sav`.
Reinstalling the Playground app does not remove the library. Existing ROMs and
saves are preserved; downloading an installed game uses the existing ROM.

The app caches eight catalog pages and previews on disk. Cached pages and the
downloaded-games list work offline. R requests a fresh catalog page and falls
back to the cached page if the request fails. The offline library lists up to
256 games. A title with multiple playable ROM files offers a version choice
before the first download.

ROMs are streamed directly to storage, limited to 4 MiB, and checked for a
valid header, declared size, DMG compatibility, and a supported cartridge
controller before installation. These checks do not guarantee that every
homebrew game will run correctly. Game Boy Color-only ROMs are rejected.

## Requirements

- SolarOS 4.13.8 or newer with graphics, PSRAM, Python, and the HTTP client.
- Wi-Fi for catalog requests and new downloads; mounted writable storage.
- `media.image` for PNG/JPEG/GIF/WebP previews. Browsing and downloading still
  work without it. Screenshots are limited to 512 KiB of compressed data and
  the firmware image decoder's size limits.
- The external Game Boy module for playing: `pkg install gameboy`.

Open a downloaded `game.gb` in Files to play it, or run
`gameboy /Games/GameBoy/GAME-ID/game.gb` from the display shell. Downloading
and screenshot viewing do not require the emulator to be installed.

### Play games

Downloaded games offer **Play** as their first action. Games not yet downloaded
offer **Download and play**. Press L to play either kind. The Game Boy module
must be installed; otherwise the app shows its installation command.

SolarOS 4.16.29 retains the completed Python session after handing control to
Game Boy. On that firmware, use `session list` and close the retained Python
session with `session close ID` after leaving the game before opening another
Python app.

## Catalog

Metadata comes from [Homebrew Hub](https://hh.gbdev.io/) through its JSON API.
ROMs and screenshots come from the corresponding entries in
[gbdev/database](https://github.com/gbdev/database). The library requests
original Game Boy games and supports name/slug search and genre tags. Missing
or inaccurate upstream metadata can affect results; ROM checks provide an
additional compatibility check.

Each game's license and copyright remain those of its authors. The catalog
listing is not a blanket license for the games or their assets.
