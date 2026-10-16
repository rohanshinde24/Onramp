import time

from opentelemetry.metrics import CallbackOptions

from onramp.telemetry.metrics import LastSuccess, LatestValue


def test_age_keeps_growing_while_reads_fail() -> None:
    age = LastSuccess("test.last_success.age", "test")
    age.mark({"group": "g"})
    time.sleep(0.02)

    (first,) = age._observe(CallbackOptions())
    time.sleep(0.02)
    (second,) = age._observe(CallbackOptions())

    assert first.attributes == {"group": "g"}
    assert 0.02 <= first.value < second.value


def test_cleared_value_stops_reporting() -> None:
    gauge = LatestValue("test.latest", "{x}", "test")
    gauge.set(3, {"group": "g"})
    gauge.clear({"group": "g"})

    assert gauge._observe(CallbackOptions()) == []
