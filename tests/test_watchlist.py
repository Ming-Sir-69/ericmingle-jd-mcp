import importlib.util
from pathlib import Path
import tempfile
import unittest


class WatchlistTests(unittest.TestCase):
    def setUp(self):
        path = Path(__file__).parents[1] / "src" / "watchlist.py"
        self.assertTrue(path.exists(), "Persistent categorized watchlist is not implemented")
        spec = importlib.util.spec_from_file_location("watchlist_under_test", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.now = [1000]
        self.url = "https://item.taobao.com/item.htm?id=123"
        def validate(url):
            if url != self.url:
                raise ValueError("invalid product")
            return url
        self.make = lambda: mod.Watchlist("taobao", validate, Path(self.tmp.name), clock=lambda: self.now[0])
        self.store = self.make()

    def test_categories_and_target_persist_across_instances(self):
        self.store.upsert(self.url, "办公", 12.5)
        rows = self.make().list("办公")["data"]["items"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["target_price"], 12.5)
        self.assertEqual(self.store.list("厨房")["data"]["items"], [])

    def test_check_only_uses_fresh_observed_price(self):
        self.store.upsert(self.url, "办公", 12.5)
        self.assertEqual(self.store.check()["data"]["matches"], [])
        self.store.observe({"items": [{"url": self.url, "title": "fixture item", "price": 9.9}]})
        self.assertEqual(len(self.store.check()["data"]["matches"]), 1)
        self.now[0] += 3601
        self.assertEqual(self.store.check()["data"]["matches"], [])

    def test_starting_or_failed_prices_do_not_trigger(self):
        self.store.upsert(self.url, "办公", 100)
        for result in [
            {"url": self.url, "price": 9.9, "price_is_from": True},
            {"url": self.url, "price": float("nan")},
            {"ok": False, "url": self.url, "price": 9.9},
        ]:
            self.store.observe(result)
        self.assertEqual(self.store.check()["data"]["matches"], [])

    def test_untrusted_url_and_invalid_target_rejected(self):
        with self.assertRaises(ValueError):
            self.store.upsert("https://attacker.example", "办公", 10)
        for target in [float("nan"), -1, True]:
            with self.assertRaises(ValueError):
                self.store.upsert(self.url, "办公", target)

    def test_cache_storage_failure_does_not_fail_platform_read(self):
        obstacle = Path(self.tmp.name) / "ordinary_file"
        obstacle.write_text("fixture")
        self.store.path = obstacle / "cannot_create.sqlite3"
        self.store.observe({"url": self.url, "price": 9.9})

    def test_remove_persists_and_clears_only_matching_local_records(self):
        self.store.upsert(self.url, "办公", 12.5)
        self.store.observe({"url": self.url, "title": "fixture", "price": 9.9})
        with self.store.connect() as db:
            db.execute("CREATE TABLE notify_refresh(url TEXT PRIMARY KEY, next_at REAL)")
            db.executemany("INSERT INTO notify_refresh VALUES(?,?)", [(self.url, 1), ("other", 2)])
        self.assertTrue(self.store.remove(self.url)["data"]["removed"])
        self.assertEqual(self.make().list()["data"]["items"], [])
        with self.make().connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM quotes").fetchone()[0], 0)
            self.assertEqual([r[0] for r in db.execute("SELECT url FROM notify_refresh")], ["other"])
        self.assertFalse(self.make().remove(self.url)["data"]["removed"])
