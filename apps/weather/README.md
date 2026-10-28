# Weather

Weather shows current conditions and a seven-day Open-Meteo forecast on the
graphic display. The upper boxes show temperature, relative humidity, wind
speed, and wind direction. The two graphs show forecast temperature and
precipitation.

When the board provides the optional SolarOS environmental sensor service,
valid local readings replace Open-Meteo's current temperature and humidity.
Wind and forecast data always come from Open-Meteo. Each temperature and
humidity box identifies its source as `local` or `meteo`.

## Configure

Weather stores `config.json` beside `weather.py`. For an SD installation, the
normal path is:

```text
/sdcard/playground/python/weather/config.json
```

Save coordinates from a shell:

```text
python /sdcard/playground/python/weather/weather.py --lat 52.5200 --lon 13.4050 --save-only
```

Or save a city and optional country qualifier:

```text
python /sdcard/playground/python/weather/weather.py --city Berlin --country Germany --save-only
```

The equivalent configuration files are:

```json
{"latitude": 52.52, "longitude": 13.405, "name": "Berlin", "refresh_minutes": 10}
```

```json
{"city": "Berlin", "country": "Germany", "refresh_minutes": 10}
```

City names are resolved with the Open-Meteo Geocoding API at startup. Use a
country name or two-letter country code when the city name is ambiguous.

`refresh_minutes` controls automatic forecast and sensor refreshes. It defaults
to 10 when omitted and accepts an integer from 1 to 1440. Change only the
interval in an existing configuration with:

```text
python /sdcard/playground/python/weather/weather.py --refresh-minutes 15 --save-only
```

Run `weather.py --help` for the command-line summary. Omit `--save-only` to
save the supplied configuration and then open Weather on a display-owned
session.

## Controls

- `+` or `=` selects seven daily aggregates.
- `-` selects the next 24 hourly forecast samples.
- `R` refreshes the forecast and local sensor readings.
- Escape or `Q` exits.

Weather refreshes the forecast and local sensor readings automatically at the
configured interval. The active hourly or daily graph scale is preserved.

The daily temperature graph uses the mean of each day's hourly temperatures.
The daily precipitation graph uses the sum of each day's hourly precipitation.

## Requirements

Weather requires SolarOS 4.8.4, Wi-Fi, a graphic display, the Python and
Playground packages, and the package-gated `solaros.http` API. A local
temperature/humidity sensor is optional. HTTPS requests use the SolarOS
certificate bundle. No Open-Meteo API key is required for non-commercial use;
review Open-Meteo's current terms for other use.

Display rotation redraws the cached forecast and preserves the selected
hourly or daily view and refresh timer.
