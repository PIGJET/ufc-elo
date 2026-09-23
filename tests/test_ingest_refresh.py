from __future__ import annotations

from data.ingest import ingest_historical


def test_refresh_raw_files_downloads_each_csv_atomically(tmp_path, monkeypatch):
    requested: list[tuple[str, int]] = []

    class FakeResponse:
        content = b"column\nvalue\n"

        @staticmethod
        def raise_for_status() -> None:
            return None

    class FakeSession:
        def __init__(self) -> None:
            self.headers: dict[str, str] = {}

        def get(self, url: str, timeout: int) -> FakeResponse:
            requested.append((url, timeout))
            return FakeResponse()

    monkeypatch.setattr(ingest_historical, "RAW_DIR", tmp_path)
    monkeypatch.setattr(ingest_historical.requests, "Session", FakeSession)

    ingest_historical.refresh_raw_files()

    assert [url.rsplit("/", 1)[-1] for url, _ in requested] == list(
        ingest_historical.RAW_FILES
    )
    assert all(timeout == 60 for _, timeout in requested)
    for filename in ingest_historical.RAW_FILES:
        assert (tmp_path / filename).read_bytes() == FakeResponse.content
        assert not (tmp_path / f"{filename}.tmp").exists()
