from onramp.benchmarks.load import Distribution, intake_key, percentile, subject_id


def test_nearest_rank_percentiles() -> None:
    values = [float(v) for v in range(1, 101)]

    assert percentile(values, 50) == 50
    assert percentile(values, 99) == 99
    assert percentile(values, 100) == 100
    assert percentile([7.0], 99) == 7


def test_distribution_of_nothing_is_none() -> None:
    assert Distribution.of([]) is None


def test_distribution_sorts_its_input() -> None:
    distribution = Distribution.of([3.0, 1.0, 2.0])

    assert distribution is not None
    assert (distribution.count, distribution.p50, distribution.max) == (3, 2.0, 3.0)


def test_k6_and_the_seeder_name_the_same_subject() -> None:
    # deploy/load/intake.js builds `loadtest_${RUN_ID}_s_${i}` and `${RUN_ID}-${i}`.
    assert subject_id("20261017T000000Z", 7) == "loadtest_20261017T000000Z_s_7"
    assert intake_key("20261017T000000Z", 7) == "20261017T000000Z-7"
