"""
Location pattern stop-list (TASK 1). Offline.

Run: .venv/Scripts/python.exe test_pattern_location_stoplist.py

Proves: a whole-town area_name ("Yishun", any case/whitespace) fires no
location alert, while a discriminating sub-area ("Yishun Central") fires one.
"""
import importlib

pd = importlib.import_module("classifiers.pattern_detection")

passed = failed = 0


def check(name, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
        print(f"  PASS  {name}")
    else:
        failed += 1
        print(f"  FAIL  {name} {detail}")


class FakeClient:
    """Captures pattern_alerts inserts; dedup check always says 'not duplicate'."""
    def __init__(self):
        self.alerts = []

    def table(self, name):
        client = self

        class _Q:
            def select(self, *a, **k): return self
            def eq(self, *a, **k): return self
            def gte(self, *a, **k): return self
            def insert(self, row):
                self._row = row
                return self
            def execute(self):
                if name == "pattern_alerts" and hasattr(self, "_row"):
                    client.alerts.append(self._row)
                    return type("R", (), {"data": [{"id": f"a{len(client.alerts)}"}]})()
                # dedup head-count query
                return type("R", (), {"count": 0, "data": []})()
        return _Q()


def _incs(area, n):
    return [{"id": f"{area}-{i}", "title": f"t{i}", "area_name": area} for i in range(n)]


# Broad town name → no alert
c = FakeClient()
created = pd._check_location_patterns(c, _incs("Yishun", 6))
check("bare Yishun fires no location alert", created == 0 and not c.alerts, f"created={created}")

# Sub-area → one alert, labelled correctly
c = FakeClient()
created = pd._check_location_patterns(c, _incs("Yishun Central", 6))
check("Yishun Central fires one alert", created == 1 and len(c.alerts) == 1, f"created={created}")
check("alert pattern_value is Yishun Central",
      bool(c.alerts) and c.alerts[0]["pattern_value"] == "Yishun Central",
      c.alerts[0]["pattern_value"] if c.alerts else "<none>")

# Case / whitespace variants of the town name still hit the stop-list
c = FakeClient()
created = pd._check_location_patterns(c, _incs(" YISHUN ", 6))
check("' YISHUN ' (case/space) fires no alert", created == 0 and not c.alerts, f"created={created}")

c = FakeClient()
created = pd._check_location_patterns(c, _incs("Nee Soon", 6))
check("Nee Soon fires no alert", created == 0 and not c.alerts, f"created={created}")

print(f"\n{passed} passed, {failed} failed")
raise SystemExit(1 if failed else 0)
