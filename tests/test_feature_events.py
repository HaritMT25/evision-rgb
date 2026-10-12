"""Behaviour spec — event log. Written BEFORE the implementation.

The app keeps a short history of what happened, with times, so drop-outs can be explained afterwards ("the
keyboard disconnected at 20:19, on battery"): the keyboard going away and coming back, the laptop changing between
battery and mains, what the battery saver paused, dimmed and gave back, game lighting and OpenRGB programs taking
over, and errors in the background work (each distinct error once a minute, not every tick).

1. The log is a text file next to the settings (one "YYYY-MM-DD HH:MM:SS text" line per event), capped in size
   (the older half moves to events.log.1), read back at start, and never breaks the app when it can't be written.
2. GET /events lists the newest events (newest last). Other Settings shows them.
"""
import os
import tempfile
import threading
import time
import unittest

from test_feature_battery import WEB, _ServerCase, wait_for


class Clock:
    def __init__(self):
        self.t = 1_800_000_000.0

    def __call__(self):
        return self.t


class EventLogFile(unittest.TestCase):
    def setUp(self):
        from evision_rgb.events import EventLog
        self.EventLog = EventLog
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "events.log")
        self.clock = Clock()
        self.log = EventLog(self.path, clock=self.clock)

    def lines(self, path=None):
        with open(path or self.path, encoding="utf-8") as f:
            return f.read().splitlines()

    def test_an_event_is_a_dated_line_in_the_file(self):
        self.log.add("keyboard disconnected")
        stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(self.clock.t))
        self.assertEqual(self.lines(), [f"{stamp} keyboard disconnected"])
        self.assertEqual(self.log.recent(), [{"time": stamp, "text": "keyboard disconnected"}])

    def test_newest_last_and_limited(self):
        for i in range(30):
            self.clock.t += 1
            self.log.add(f"event {i}")
        r = self.log.recent(5)
        self.assertEqual([e["text"] for e in r], [f"event {i}" for i in range(25, 30)])

    def test_events_from_before_a_restart_are_shown_again(self):
        self.log.add("on battery")
        self.log.add("plugged in")
        again = self.EventLog(self.path, clock=self.clock)
        self.assertEqual([e["text"] for e in again.recent()], ["on battery", "plugged in"])

    def test_the_file_is_capped_and_the_older_part_kept_once(self):
        log = self.EventLog(self.path, clock=self.clock, max_bytes=2000)
        for i in range(300):
            log.add(f"event number {i:04d} with some words")
        self.assertLessEqual(os.path.getsize(self.path), 2000 + 100)
        self.assertTrue(os.path.isfile(self.path + ".1"))
        self.assertLessEqual(os.path.getsize(self.path + ".1"), 2000 + 100)
        self.assertFalse(os.path.exists(self.path + ".2"))
        self.assertEqual(log.recent(1)[0]["text"], "event number 0299 with some words")

    def test_the_same_error_is_logged_once_a_minute(self):
        for _ in range(50):
            self.log.error("automation", OSError("read error"))
        self.assertEqual(len(self.lines()), 1)
        self.assertIn("automation", self.lines()[0])
        self.assertIn("read error", self.lines()[0])
        self.log.error("automation", ValueError("other"))
        self.assertEqual(len(self.lines()), 2, "a different error is logged at once")
        self.clock.t += 61
        self.log.error("automation", OSError("read error"))
        self.assertEqual(len(self.lines()), 3, "and the first one again after a minute")

    def test_a_file_that_cannot_be_written_never_breaks_anything(self):
        os.makedirs(os.path.join(self.dir, "blocked"))
        log = self.EventLog(os.path.join(self.dir, "blocked"), clock=self.clock)  # a directory, not a file
        log.add("still remembered")
        self.assertEqual(log.recent()[-1]["text"], "still remembered")

    def test_lines_from_many_threads_stay_whole(self):
        def burst(n):
            for i in range(100):
                self.log.add(f"thread {n} line {i}")
        ts = [threading.Thread(target=burst, args=(n,)) for n in range(4)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        lines = self.lines()
        self.assertEqual(len(lines), 400)
        self.assertTrue(all(" thread " in ln and ln.split(" line ")[1].isdigit() for ln in lines))


def texts(case) -> list[str]:
    return [e["text"] for e in case.call("/events")[1]["events"]]


def logged(case, part) -> bool:
    return any(part in t for t in texts(case))


class EventsFromTheApp(_ServerCase):
    def test_start_up_is_logged_with_the_keyboard_and_power_state(self):
        first = texts(self)[0]
        self.assertIn("app started", first)
        self.assertIn("keyboard connected", first)
        self.assertIn("plugged in", first)

    def test_the_keyboard_going_away_and_coming_back(self):
        self.call("/run", {"effect": "rainbow"})
        self.assertTrue(wait_for(lambda: self.kb.visible()[0] == "direct"))
        self.kb.unplug()
        self.assertTrue(wait_for(lambda: logged(self, "keyboard disconnected"), secs=3))
        self.kb.replug()
        self.assertTrue(wait_for(lambda: logged(self, "keyboard connected again"), secs=4))

    def test_power_changes_and_what_the_battery_saver_did(self):
        self.call("/automation", {"battery": {"brightness": 1}})
        self.call("/run", {"effect": "rainbow"})
        self.battery = True
        self.assertTrue(wait_for(lambda: logged(self, "brightness 3 -> 1")))
        self.battery = False
        self.assertTrue(wait_for(lambda: logged(self, "brightness back to 3")))
        t = texts(self)
        order = [next(i for i, x in enumerate(t) if (x == part if part in ("on battery", "plugged in") else part in x))
                 for part in
                 ("on battery", "paused rainbow", "brightness 3 -> 1", "plugged in", "brightness back to 3", "resumed rainbow")]
        self.assertEqual(order, sorted(order), t)

    def test_a_background_error_is_logged_once_not_every_tick(self):
        def broken():
            raise OSError("probe broke")
        self.ctl.automation.probes["on_battery"] = broken
        time.sleep(0.6)  # 12 automation ticks
        self.assertEqual(sum("probe broke" in t for t in texts(self)), 1)

    def test_the_log_file_is_next_to_the_settings(self):
        from evision_rgb import settings as st
        with open(os.path.join(st.DIR, "events.log"), encoding="utf-8") as f:
            self.assertIn("app started", f.read())

    def test_at_most_100_events_are_listed(self):
        for i in range(150):
            self.ctl.events.add(f"filler {i}")
        ev = self.call("/events")[1]["events"]
        self.assertEqual(len(ev), 100)
        self.assertEqual(ev[-1]["text"], "filler 149")


class EventsWithoutAKeyboard(_ServerCase):
    def setUp(self):
        import warnings
        warnings.simplefilter("ignore", ResourceWarning)
        from fake_device import install
        import evision_rgb.settings as st
        self.kb = install(self)
        self.kb.unplug()
        tmp = tempfile.mkdtemp()
        for attr, val in (("DIR", tmp), ("PATH", os.path.join(tmp, "settings.json"))):
            self.addCleanup(setattr, st, attr, getattr(st, attr))
            setattr(st, attr, val)
        self.make({"on_battery": lambda: self.battery})

    def test_starting_without_the_keyboard_says_so_and_logs_when_it_arrives(self):
        self.assertIn("keyboard not connected", texts(self)[0])
        self.kb.replug()
        self.call("/run", {"effect": "rainbow"})
        self.assertTrue(wait_for(lambda: logged(self, "keyboard connected"), secs=4))


class NoBatteryNoPowerEvents(_ServerCase):
    battery = None

    def test_a_pc_without_a_battery_logs_no_power_changes(self):
        time.sleep(0.3)
        t = texts(self)
        self.assertIn("no battery", t[0])
        self.assertFalse(any(x in ("on battery", "plugged in") for x in t[1:]), t)


class EventsForAgents(unittest.TestCase):
    def test_the_mcp_server_has_a_recent_events_tool(self):
        import asyncio
        from evision_rgb import mcp_server
        names = {t.name for t in asyncio.run(mcp_server.mcp.list_tools())}
        self.assertIn("recent_events", names)


class EventsInTheApp(unittest.TestCase):
    def test_other_settings_shows_the_recent_events(self):
        with open(os.path.join(WEB, "index.html"), encoding="utf-8") as f:
            self.assertIn('id="events"', f.read())
        with open(os.path.join(WEB, "app.js"), encoding="utf-8") as f:
            self.assertIn('"/events"', f.read())


if __name__ == "__main__":
    unittest.main()
