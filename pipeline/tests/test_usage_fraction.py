from mvs_pipeline import usage_fraction


def test_basic_fraction():
    assert usage_fraction(1, 4) == 0.25


def test_zero_hits():
    assert usage_fraction(0, 1000) == 0.0


def test_empty_corpus_does_not_divide_by_zero():
    assert usage_fraction(0, 0) == 0.0
    assert usage_fraction(5, 0) == 0.0
