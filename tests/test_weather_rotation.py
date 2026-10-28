import importlib.util
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]


class WeatherRotationTest(unittest.TestCase):
    def setUp(self):
        self.gfx = mock.Mock(KEY_ESCAPE=27)
        self.gfx.size.return_value = (400, 240)
        solaros = SimpleNamespace(gfx=self.gfx, should_exit=lambda: False)
        spec = importlib.util.spec_from_file_location(
            "weather_rotation", ROOT / "apps/weather/weather.py")
        self.weather = importlib.util.module_from_spec(spec)
        with mock.patch.dict("sys.modules", {"solaros": solaros}):
            spec.loader.exec_module(self.weather)

    def test_rotation_redraws_cached_forecast_and_preserves_graph_selection(self):
        def key(timeout):
            count = self.gfx.getch.call_count
            if count == 1:
                self.gfx.size.return_value = (240, 400)
            elif count == 2:
                return ord("+")
            elif count == 3:
                self.gfx.size.return_value = (400, 240)
            else:
                return ord("q")
            return None

        self.gfx.getch.side_effect = key
        forecast = {"cached": True}
        with mock.patch.object(self.weather, "_fetch_weather", return_value=forecast) as fetch, \
                mock.patch.object(self.weather, "_draw_weather") as draw, \
                mock.patch.object(self.weather, "_draw_message"):
            self.weather._run_display({"refresh_minutes": 10})
        fetch.assert_called_once()
        self.assertEqual([(c.args[0], c.args[1], c.args[3]) for c in draw.call_args_list],
                         [(400, 240, "hourly"), (240, 400, "hourly"),
                          (240, 400, "daily"), (400, 240, "daily")])
        self.gfx.end.assert_called_once()

    def test_retry_screen_rotates_without_restarting_its_timer(self):
        def key(timeout):
            if self.gfx.getch.call_count == 1:
                self.gfx.size.return_value = (240, 400)
            return None

        self.gfx.getch.side_effect = key
        with mock.patch.object(self.weather, "_draw_message") as draw:
            self.assertTrue(self.weather._wait_for_retry(1, "Offline"))
        self.assertEqual(self.gfx.getch.call_count, 240)
        self.assertEqual([(c.args[0], c.args[1]) for c in draw.call_args_list],
                         [(400, 240), (240, 400)])
