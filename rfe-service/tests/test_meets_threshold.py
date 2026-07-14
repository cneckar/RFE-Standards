from rfe_service import meets_threshold


def test_meets_when_at_or_above():
    assert meets_threshold(1, 1000, 0.001) is True
    assert meets_threshold(2, 1000, 0.001) is True


def test_below_threshold():
    assert meets_threshold(0, 1000, 0.001) is False


def test_empty_corpus_never_meets():
    assert meets_threshold(10, 0, 0.001) is False
