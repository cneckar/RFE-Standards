from mvs_pipeline import corpus


def test_iter_uris_from_list_skips_comments_and_blanks():
    text = "# comment\nhttp://a.example/\n\n  https://b.example/x  \n# another\n"
    assert list(corpus.iter_uris_from_list(text)) == [
        "http://a.example/",
        "https://b.example/x",
    ]


def test_iter_uris_from_warc_targets():
    text = (
        "WARC/1.0\r\n"
        "WARC-Type: response\r\n"
        "WARC-Target-URI: http://example.com/page\r\n"
        "\r\n"
        "WARC/1.0\r\n"
        "WARC-Target-URI: https://example.org/other\r\n"
    )
    assert list(corpus.iter_uris_from_warc(text)) == [
        "http://example.com/page",
        "https://example.org/other",
    ]


def test_iter_uris_dispatch_and_bad_format():
    assert list(corpus.iter_uris("http://a.example/\n", fmt="list")) == ["http://a.example/"]
    import pytest

    with pytest.raises(ValueError):
        list(corpus.iter_uris("x", fmt="nope"))


def test_dedupe_preserves_order():
    got = list(corpus.dedupe(["a", "b", "a", "c", "b"]))
    assert got == ["a", "b", "c"]


def test_write_corpus(tmp_path):
    out = tmp_path / "corpus.txt"
    n = corpus.write_corpus(["http://a.example/", "http://a.example/", "http://b.example/"], out)
    assert n == 2
    assert out.read_text() == "http://a.example/\nhttp://b.example/\n"


def test_write_corpus_no_dedupe(tmp_path):
    out = tmp_path / "corpus.txt"
    n = corpus.write_corpus(["x", "x"], out, deduplicate=False)
    assert n == 2
    assert out.read_text() == "x\nx\n"


def test_telemetry_argv():
    argv = corpus.telemetry_argv("mvs-telemetry", "a.json", "c.txt", "h.json")
    assert argv == [
        "mvs-telemetry",
        "--ast",
        "a.json",
        "--corpus",
        "c.txt",
        "--out",
        "h.json",
    ]
