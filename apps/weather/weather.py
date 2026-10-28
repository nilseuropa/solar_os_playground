"""Open-Meteo weather display for SolarOS."""

import json
import sys

import solaros
from solaros import gfx


FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"
HTTP_TIMEOUT_MS = 15000
FORECAST_MAX_BYTES = 32768
GEOCODING_MAX_BYTES = 16384
HOURLY_COUNT = 24
DAILY_COUNT = 7
DEFAULT_REFRESH_MINUTES = 10
MIN_REFRESH_MINUTES = 1
MAX_REFRESH_MINUTES = 1440
INPUT_POLL_MS = 250

KEY_PLUS = ord("+")
KEY_EQUALS = ord("=")
KEY_MINUS = ord("-")
KEY_Q = ord("q")
KEY_Q_UPPER = ord("Q")
KEY_R = ord("r")
KEY_R_UPPER = ord("R")


class WeatherError(Exception):
    pass


def _script_directory():
    path = ""
    if len(sys.argv) > 0:
        path = sys.argv[0]
    if not path or "/" not in path:
        try:
            path = __file__
        except NameError:
            path = "weather.py"
    separator = path.rfind("/")
    if separator < 0:
        return "."
    if separator == 0:
        return "/"
    return path[:separator]


APP_DIRECTORY = _script_directory()
CONFIG_PATH = (APP_DIRECTORY + "/config.json") if APP_DIRECTORY != "/" else "/config.json"


def _is_number(value):
    return isinstance(value, (int, float)) and value == value


def _bounded_number(value, low, high, name):
    if not _is_number(value):
        raise WeatherError("{} must be a number".format(name))
    value = float(value)
    if value < low or value > high:
        raise WeatherError("{} must be {}..{}".format(name, low, high))
    return value


def _refresh_minutes(value):
    if isinstance(value, bool) or not _is_number(value):
        raise WeatherError("refresh_minutes must be an integer")
    integer = int(value)
    if value != integer:
        raise WeatherError("refresh_minutes must be an integer")
    if integer < MIN_REFRESH_MINUTES or integer > MAX_REFRESH_MINUTES:
        raise WeatherError("refresh_minutes must be {}..{}".format(
            MIN_REFRESH_MINUTES, MAX_REFRESH_MINUTES))
    return integer


def _validate_config(config):
    if not isinstance(config, dict):
        raise WeatherError("config.json must contain an object")

    has_lat = "latitude" in config
    has_lon = "longitude" in config
    has_city = isinstance(config.get("city"), str) and bool(config.get("city").strip())
    if has_lat or has_lon:
        if not has_lat or not has_lon:
            raise WeatherError("config needs both latitude and longitude")
        result = {
            "latitude": _bounded_number(config["latitude"], -90.0, 90.0, "latitude"),
            "longitude": _bounded_number(config["longitude"], -180.0, 180.0, "longitude"),
        }
        name = config.get("name")
        if isinstance(name, str) and name.strip():
            result["name"] = name.strip()
        result["refresh_minutes"] = _refresh_minutes(
            config.get("refresh_minutes", DEFAULT_REFRESH_MINUTES))
        return result

    if has_city:
        result = {"city": config["city"].strip()}
        country = config.get("country")
        if isinstance(country, str) and country.strip():
            result["country"] = country.strip()
        result["refresh_minutes"] = _refresh_minutes(
            config.get("refresh_minutes", DEFAULT_REFRESH_MINUTES))
        return result

    raise WeatherError("config needs coordinates or city")


def _load_config():
    try:
        with open(CONFIG_PATH, "r") as source:
            content = source.read()
    except OSError:
        return None
    try:
        return _validate_config(json.loads(content))
    except (ValueError, TypeError) as exc:
        raise WeatherError("invalid config.json: {}".format(exc))


def _save_config(config):
    config = _validate_config(config)
    with open(CONFIG_PATH, "w") as output:
        output.write(json.dumps(config))
        output.write("\n")
    return config


def _usage():
    print("Weather for SolarOS")
    print("  --lat VALUE --lon VALUE [--name TEXT] [--refresh-minutes N] [--save-only]")
    print("  --city TEXT [--country TEXT] [--refresh-minutes N] [--save-only]")
    print("  --refresh-minutes N [--save-only]")
    print("Config: {}".format(CONFIG_PATH))


def _parse_arguments(arguments):
    values = {}
    save_only = False
    help_requested = False
    index = 0
    while index < len(arguments):
        argument = arguments[index]
        if argument in ("--help", "-h"):
            help_requested = True
            index += 1
            continue
        if argument == "--save-only":
            save_only = True
            index += 1
            continue
        if argument not in ("--lat", "--lon", "--name", "--city", "--country",
                            "--refresh-minutes"):
            raise WeatherError("unknown argument: {}".format(argument))
        if index + 1 >= len(arguments):
            raise WeatherError("{} needs a value".format(argument))
        values[argument[2:]] = arguments[index + 1]
        index += 2

    if help_requested:
        return None, save_only, True
    if not values:
        return None, save_only, False

    refresh_minutes = None
    if "refresh-minutes" in values:
        try:
            refresh_minutes = _refresh_minutes(float(values.pop("refresh-minutes")))
        except (ValueError, OverflowError):
            raise WeatherError("--refresh-minutes must be an integer")
    if not values:
        return {"refresh_minutes": refresh_minutes}, save_only, False

    coordinate_mode = "lat" in values or "lon" in values or "name" in values
    city_mode = "city" in values or "country" in values
    if coordinate_mode and city_mode:
        raise WeatherError("do not mix coordinates and city")
    if coordinate_mode:
        if "lat" not in values or "lon" not in values:
            raise WeatherError("--lat and --lon must be used together")
        try:
            config = {
                "latitude": float(values["lat"]),
                "longitude": float(values["lon"]),
            }
        except ValueError:
            raise WeatherError("latitude and longitude must be numbers")
        if values.get("name"):
            config["name"] = values["name"]
        if refresh_minutes is not None:
            config["refresh_minutes"] = refresh_minutes
        return _validate_config(config), save_only, False

    if "city" not in values:
        raise WeatherError("--country requires --city")
    config = {"city": values["city"]}
    if values.get("country"):
        config["country"] = values["country"]
    if refresh_minutes is not None:
        config["refresh_minutes"] = refresh_minutes
    return _validate_config(config), save_only, False


def _url_encode(text):
    result = []
    for value in text.encode("utf-8"):
        character = chr(value)
        if ((value >= ord("a") and value <= ord("z")) or
                (value >= ord("A") and value <= ord("Z")) or
                (value >= ord("0") and value <= ord("9")) or
                character in "-_.~"):
            result.append(character)
        else:
            result.append("%{:02X}".format(value))
    return "".join(result)


def _http_json(url, max_bytes):
    http = getattr(solaros, "http", None)
    if http is None or getattr(http, "get", None) is None:
        raise WeatherError("SolarOS HTTP client is unavailable")
    response = http.get(url, None, HTTP_TIMEOUT_MS, max_bytes, True)
    status = response.get("status_code", 0)
    if status != 200:
        raise WeatherError("HTTP status {}".format(status))
    if response.get("truncated"):
        raise WeatherError("HTTP response is too large")
    try:
        return json.loads(response["body"].decode("utf-8"))
    except (KeyError, ValueError, TypeError) as exc:
        raise WeatherError("invalid JSON response: {}".format(exc))


def _resolve_location(config):
    if "latitude" in config:
        name = config.get("name")
        if not name:
            name = "{:.3f}, {:.3f}".format(config["latitude"], config["longitude"])
        return config["latitude"], config["longitude"], name

    query = config["city"]
    if config.get("country"):
        query += ", " + config["country"]
    url = (GEOCODING_URL + "?name=" + _url_encode(query) +
           "&count=1&language=en&format=json")
    document = _http_json(url, GEOCODING_MAX_BYTES)
    results = document.get("results") if isinstance(document, dict) else None
    if not isinstance(results, list) or not results:
        raise WeatherError("location not found: {}".format(query))
    location = results[0]
    latitude = _bounded_number(location.get("latitude"), -90.0, 90.0, "latitude")
    longitude = _bounded_number(location.get("longitude"), -180.0, 180.0, "longitude")
    name = location.get("name") or config["city"]
    country = location.get("country_code") or location.get("country")
    if country:
        name = "{}, {}".format(name, country)
    return latitude, longitude, name


def _forecast_url(latitude, longitude):
    return (FORECAST_URL +
            "?latitude={:.5f}&longitude={:.5f}".format(latitude, longitude) +
            "&current=temperature_2m,relative_humidity_2m,wind_speed_10m,wind_direction_10m" +
            "&hourly=temperature_2m,precipitation" +
            "&timezone=auto&forecast_days=7")


def _valid_reading(value):
    return _is_number(value) and value > -1000.0 and value < 10000.0


def _local_environment():
    sensors = getattr(solaros, "sensors", None)
    if sensors is None or getattr(sensors, "environment", None) is None:
        return {}
    try:
        environment = sensors.environment()
    except OSError:
        return {}
    if not isinstance(environment, dict):
        return {}
    result = {}
    temperature = environment.get("temperature_c")
    humidity = environment.get("humidity_percent")
    if _valid_reading(temperature):
        result["temperature"] = float(temperature)
    if _valid_reading(humidity) and humidity >= 0.0 and humidity <= 100.0:
        result["humidity"] = float(humidity)
    return result


def _fetch_weather(config):
    latitude, longitude, location_name = _resolve_location(config)
    document = _http_json(_forecast_url(latitude, longitude), FORECAST_MAX_BYTES)
    if not isinstance(document, dict):
        raise WeatherError("forecast response is not an object")
    current = document.get("current")
    hourly = document.get("hourly")
    if not isinstance(current, dict) or not isinstance(hourly, dict):
        raise WeatherError("forecast response is incomplete")

    times = hourly.get("time")
    temperatures = hourly.get("temperature_2m")
    precipitation = hourly.get("precipitation")
    if not isinstance(times, list) or not isinstance(temperatures, list) or not isinstance(precipitation, list):
        raise WeatherError("hourly forecast is incomplete")
    count = min(len(times), len(temperatures), len(precipitation))
    if count < HOURLY_COUNT:
        raise WeatherError("hourly forecast is too short")

    temperature = current.get("temperature_2m")
    humidity = current.get("relative_humidity_2m")
    wind_speed = current.get("wind_speed_10m")
    wind_direction = current.get("wind_direction_10m")
    for value, label in ((temperature, "temperature"), (humidity, "humidity"),
                         (wind_speed, "wind speed"), (wind_direction, "wind direction")):
        if not _valid_reading(value):
            raise WeatherError("current {} is unavailable".format(label))

    local = _local_environment()
    temperature_source = "meteo"
    humidity_source = "meteo"
    if "temperature" in local:
        temperature = local["temperature"]
        temperature_source = "local"
    if "humidity" in local:
        humidity = local["humidity"]
        humidity_source = "local"

    return {
        "location": location_name,
        "current_time": current.get("time") or "",
        "temperature": float(temperature),
        "temperature_source": temperature_source,
        "humidity": float(humidity),
        "humidity_source": humidity_source,
        "wind_speed": float(wind_speed),
        "wind_direction": float(wind_direction),
        "times": times[:count],
        "temperatures": temperatures[:count],
        "precipitation": precipitation[:count],
    }


def _forecast_start(times, current_time):
    current_hour = current_time[:13]
    for index in range(len(times)):
        if str(times[index])[:13] >= current_hour:
            return index
    return 0


def _hourly_samples(weather):
    start = _forecast_start(weather["times"], weather["current_time"])
    end = min(start + HOURLY_COUNT, len(weather["times"]))
    labels = []
    temperatures = []
    precipitation = []
    for index in range(start, end):
        labels.append(str(weather["times"][index])[11:13])
        temperatures.append(weather["temperatures"][index])
        precipitation.append(weather["precipitation"][index])
    return labels, temperatures, precipitation


def _daily_samples(weather):
    start = _forecast_start(weather["times"], weather["current_time"])
    labels = []
    temperature_sums = []
    temperature_counts = []
    precipitation_sums = []
    for index in range(start, len(weather["times"])):
        day = str(weather["times"][index])[:10]
        if not labels or labels[-1] != day:
            if len(labels) >= DAILY_COUNT:
                break
            labels.append(day)
            temperature_sums.append(0.0)
            temperature_counts.append(0)
            precipitation_sums.append(0.0)
        temperature = weather["temperatures"][index]
        rain = weather["precipitation"][index]
        if _valid_reading(temperature):
            temperature_sums[-1] += float(temperature)
            temperature_counts[-1] += 1
        if _valid_reading(rain) and rain >= 0.0:
            precipitation_sums[-1] += float(rain)

    temperatures = []
    short_labels = []
    for index in range(len(labels)):
        if temperature_counts[index] == 0:
            temperatures.append(None)
        else:
            temperatures.append(temperature_sums[index] / temperature_counts[index])
        short_labels.append(labels[index][5:])
    return short_labels, temperatures, precipitation_sums


def _clip(text, characters):
    text = str(text)
    if characters <= 0:
        return ""
    if len(text) <= characters:
        return text
    if characters <= 3:
        return text[:characters]
    return text[:characters - 3] + "..."


def _compass(degrees):
    names = ("N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
             "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW")
    return names[int((degrees + 11.25) / 22.5) % 16]


def _draw_centered(x, width, baseline, text, character_width=6):
    text = _clip(text, max(1, width // character_width))
    text_x = x + max(1, (width - len(text) * character_width) // 2)
    gfx.text(text_x, baseline, text)


def _draw_box(x, y, width, height, label, value, detail):
    gfx.color(gfx.BLACK)
    gfx.rect(x, y, width, height)
    gfx.font(gfx.FONT_SMALL)
    _draw_centered(x, width, y + 11, label)
    gfx.font(gfx.FONT_BOLD_14)
    _draw_centered(x, width, y + 29, value, 8)
    gfx.font(gfx.FONT_SMALL)
    _draw_centered(x, width, y + height - 4, detail)


def _current_boxes(width, start_y, weather):
    margin = 5
    gap = 4
    if width >= 300:
        box_height = 50
        box_width = (width - margin * 2 - gap * 3) // 4
        positions = []
        for index in range(4):
            positions.append((margin + index * (box_width + gap), start_y, box_width, box_height))
        bottom = start_y + box_height
    else:
        box_height = 42
        box_width = (width - margin * 2 - gap) // 2
        positions = [
            (margin, start_y, box_width, box_height),
            (margin + box_width + gap, start_y, box_width, box_height),
            (margin, start_y + box_height + gap, box_width, box_height),
            (margin + box_width + gap, start_y + box_height + gap, box_width, box_height),
        ]
        bottom = start_y + box_height * 2 + gap

    direction = weather["wind_direction"]
    values = (
        ("TEMP", "{:.1f} C".format(weather["temperature"]), weather["temperature_source"]),
        ("HUMIDITY", "{:.0f} %".format(weather["humidity"]), weather["humidity_source"]),
        ("WIND", "{:.1f}".format(weather["wind_speed"]), "km/h meteo"),
        ("DIRECTION", _compass(direction), "{:.0f} deg meteo".format(direction)),
    )
    for index in range(4):
        x, y, box_width, box_height = positions[index]
        label, value, detail = values[index]
        _draw_box(x, y, box_width, box_height, label, value, detail)
    return bottom


def _numeric_values(values):
    result = []
    for value in values:
        if _valid_reading(value):
            result.append(float(value))
    return result


def _map_y(value, low, high, top, bottom):
    if high <= low:
        return (top + bottom) // 2
    ratio = (float(value) - low) / (high - low)
    return bottom - int(ratio * (bottom - top))


def _draw_graph(x, y, width, height, title, labels, values, bars=False):
    gfx.color(gfx.BLACK)
    gfx.rect(x, y, width, height)
    gfx.font(gfx.FONT_SMALL)
    gfx.text(x + 4, y + 11, _clip(title, max(1, (width - 8) // 6)))

    numeric = _numeric_values(values)
    if not numeric:
        gfx.text(x + 6, y + height // 2, "No data")
        return
    low = min(numeric)
    high = max(numeric)
    if bars:
        low = 0.0
        if high < 0.5:
            high = 0.5
    elif high - low < 1.0:
        low -= 0.5
        high += 0.5
    else:
        padding = (high - low) * 0.08
        low -= padding
        high += padding

    plot_left = x + 30
    plot_right = x + width - 5
    plot_top = y + 16
    plot_bottom = y + height - 14
    if plot_right <= plot_left or plot_bottom <= plot_top:
        return

    gfx.color(gfx.LIGHT)
    gfx.line(plot_left, plot_top, plot_right, plot_top)
    gfx.line(plot_left, plot_bottom, plot_right, plot_bottom)
    gfx.color(gfx.BLACK)
    gfx.font(gfx.FONT_SMALL)
    gfx.text(x + 2, plot_top + 7, "{:.0f}".format(high))
    gfx.text(x + 2, plot_bottom, "{:.0f}".format(low))

    count = min(len(labels), len(values))
    if count == 0:
        return
    step = max(1, count // 5)
    previous = None
    bar_width = max(1, (plot_right - plot_left + 1) // max(1, count) - 1)
    for index in range(count):
        if count == 1:
            px = (plot_left + plot_right) // 2
        elif bars:
            px = plot_left + (plot_right - plot_left) * (index * 2 + 1) // (count * 2)
        else:
            px = plot_left + (plot_right - plot_left) * index // (count - 1)
        value = values[index]
        if _valid_reading(value):
            py = _map_y(value, low, high, plot_top, plot_bottom)
            gfx.color(gfx.BLACK)
            if bars:
                bar_height = max(1, plot_bottom - py)
                gfx.fill_rect(px - bar_width // 2, plot_bottom - bar_height, bar_width, bar_height)
            else:
                if previous is not None:
                    gfx.line(previous[0], previous[1], px, py)
                gfx.fill_rect(px - 1, py - 1, 3, 3)
                previous = (px, py)
        if index % step == 0 or index == count - 1:
            label = str(labels[index])
            gfx.font(gfx.FONT_SMALL)
            gfx.text(max(x + 1, min(px - len(label) * 3, x + width - len(label) * 6 - 1)),
                     y + height - 3, label)


def _draw_weather(width, height, weather, scale, refresh_minutes):
    gfx.clear(gfx.WHITE)
    gfx.color(gfx.BLACK)
    gfx.font(gfx.FONT_BOLD_14)
    gfx.text(5, 15, "Weather")
    gfx.font(gfx.FONT_SMALL)
    location_chars = max(1, (width - 78) // 6)
    gfx.text(76, 14, _clip(weather["location"], location_chars))

    boxes_bottom = _current_boxes(width, 21, weather)
    graph_top = boxes_bottom + 5
    footer_height = 14
    graph_gap = 4
    graph_height = max(34, (height - graph_top - footer_height - graph_gap) // 2)
    second_top = graph_top + graph_height + graph_gap
    second_height = max(28, height - footer_height - second_top)

    if scale == "daily":
        labels, temperatures, precipitation = _daily_samples(weather)
        temperature_title = "Temperature C - daily mean"
        precipitation_title = "Precipitation mm - daily sum"
    else:
        labels, temperatures, precipitation = _hourly_samples(weather)
        temperature_title = "Temperature C - next 24 hours"
        precipitation_title = "Precipitation mm - next 24 hours"

    _draw_graph(4, graph_top, width - 8, graph_height,
                temperature_title, labels, temperatures, False)
    _draw_graph(4, second_top, width - 8, second_height,
                precipitation_title, labels, precipitation, True)
    gfx.color(gfx.BLACK)
    gfx.font(gfx.FONT_SMALL)
    footer = "Open-Meteo  +daily  -hourly  R refresh  A:{}m".format(refresh_minutes)
    gfx.text(5, height - 2, _clip(footer, max(1, (width - 10) // 6)))
    gfx.present()


def _draw_message(width, height, title, lines):
    gfx.clear(gfx.WHITE)
    gfx.color(gfx.BLACK)
    gfx.font(gfx.FONT_BOLD_14)
    gfx.text(6, 18, _clip(title, max(1, (width - 12) // 8)))
    gfx.font(gfx.FONT_SMALL)
    baseline = 37
    for line in lines:
        if baseline > height - 18:
            break
        gfx.text(6, baseline, _clip(line, max(1, (width - 12) // 6)))
        baseline += 13
    gfx.text(6, height - 3, _clip("R retry  Q/Esc quit", max(1, (width - 12) // 6)))
    gfx.present()


def _wait_for_retry(refresh_minutes, message=None):
    width, height = gfx.size()
    if message is not None:
        _draw_message(width, height, "Weather", [message, "R retry   Q exit"])
    remaining_ms = refresh_minutes * 60 * 1000
    while not solaros.should_exit():
        key = gfx.getch(min(INPUT_POLL_MS, remaining_ms))
        size = gfx.size()
        if size != (width, height):
            width, height = size
            if message is not None:
                _draw_message(width, height, "Weather", [message, "R retry   Q exit"])
        if key in (gfx.KEY_ESCAPE, KEY_Q, KEY_Q_UPPER):
            return False
        if key in (KEY_R, KEY_R_UPPER):
            return True
        if key is None:
            remaining_ms -= min(INPUT_POLL_MS, remaining_ms)
            if remaining_ms <= 0:
                return True
    return False


def _run_display(config):
    gfx.begin()
    try:
        width, height = gfx.size()
        scale = "hourly"
        refresh_minutes = config["refresh_minutes"]
        while not solaros.should_exit():
            width, height = gfx.size()
            _draw_message(width, height, "Weather", ["Loading Open-Meteo forecast..."])
            try:
                weather = _fetch_weather(config)
            except (WeatherError, OSError) as exc:
                print("weather: {}".format(exc))
                if not _wait_for_retry(refresh_minutes, str(exc)):
                    break
                continue

            width, height = gfx.size()
            _draw_weather(width, height, weather, scale, refresh_minutes)
            refresh = False
            remaining_ms = refresh_minutes * 60 * 1000
            while not solaros.should_exit():
                wait_ms = min(INPUT_POLL_MS, remaining_ms)
                key = gfx.getch(wait_ms)
                size = gfx.size()
                if size != (width, height):
                    width, height = size
                    _draw_weather(width, height, weather, scale, refresh_minutes)
                if key in (gfx.KEY_ESCAPE, KEY_Q, KEY_Q_UPPER):
                    return
                if key in (KEY_R, KEY_R_UPPER):
                    refresh = True
                    break
                if key in (KEY_PLUS, KEY_EQUALS) and scale != "daily":
                    scale = "daily"
                    _draw_weather(width, height, weather, scale, refresh_minutes)
                elif key == KEY_MINUS and scale != "hourly":
                    scale = "hourly"
                    _draw_weather(width, height, weather, scale, refresh_minutes)
                elif key is None:
                    remaining_ms -= wait_ms
                    if remaining_ms <= 0:
                        refresh = True
                        break
            if not refresh:
                break
    finally:
        gfx.end()


def main():
    try:
        supplied, save_only, help_requested = _parse_arguments(sys.argv[1:])
        if help_requested:
            _usage()
            return
        if supplied is not None:
            if "latitude" not in supplied and "city" not in supplied:
                config = _load_config()
                if config is None:
                    raise WeatherError("--refresh-minutes needs an existing location config")
                config["refresh_minutes"] = supplied["refresh_minutes"]
            else:
                config = supplied
            config = _save_config(config)
            print("weather: saved {}".format(CONFIG_PATH))
        else:
            config = _load_config()
        if save_only:
            if config is None:
                raise WeatherError("--save-only needs location options or an existing config")
            return
        if config is None:
            _usage()
            raise WeatherError("no config.json; configure a location first")
        _run_display(config)
    except WeatherError as exc:
        print("weather: {}".format(exc))


if __name__ == "__main__":
    main()
