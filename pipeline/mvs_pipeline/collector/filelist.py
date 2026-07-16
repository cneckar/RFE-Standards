"""Plain newline URI-list source (part of T6.8).

A trivial `Source` over a newline-delimited file of URIs (``#`` comments and
blank lines skipped). Useful for cached shards, committed fixtures, and feeding
a hand-curated seed list through the same sampler/orchestrator as the crawl
connectors.
"""

from __future__ import annotations

import gzip
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any

from mvs_pipeline.collector.base import keep_sample


class FileListSource:
    """Stream URIs from one or more newline-delimited files (`.txt` / `.txt.gz`)."""

    name = "file-list"

    def __init__(
        self,
        paths: Sequence[str | Path],
        *,
        name: str | None = None,
        sample_rate: float = 1.0,
        seed: int = 0,
    ) -> None:
        self.paths = [Path(p) for p in paths]
        if name:
            self.name = name
        self.sample_rate = sample_rate
        self.seed = seed
        self._files_read: list[str] = []
        self._urls_read = 0

    def iter_uris(self) -> Iterator[str]:
        """Yield URIs, skipping blanks/comments and applying any sampling."""
        for path in self.paths:
            opener = gzip.open if path.suffix == ".gz" else open
            with opener(path, "rt", encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    uri = line.strip()
                    if not uri or uri.startswith("#"):
                        continue
                    if keep_sample(uri, self.sample_rate, self.seed):
                        self._urls_read += 1
                        yield uri
            self._files_read.append(str(path))

    def provenance(self) -> dict[str, Any]:
        return {
            "source": self.name,
            "sample_rate": self.sample_rate,
            "seed": self.seed,
            "files_read": list(self._files_read),
            "urls_read": self._urls_read,
        }
