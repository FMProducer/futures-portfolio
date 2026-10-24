import io
import zipfile

import pytest

from scripts.download_intraday_90d import parse_archive


def archive(rows):
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w") as zipped:
        zipped.writestr("candles.csv", "\n".join(",".join(map(str, row)) for row in rows))
    return payload.getvalue()


def candle(timestamp=1785024000000):
    return [timestamp, 100, 101, 99, 100, 1, timestamp + 59999, 100, 1, 1, 100, 0]


def test_archive_preserves_utc_time_and_excludes_outside_window():
    rows = [candle(1785023940000), candle(), candle(1785024060000)]
    frame = parse_archive(archive(rows))
    assert frame.open_time.tolist() == [1785024000000, 1785024060000]
    assert frame.time.iloc[0].isoformat() == "2026-07-26T00:00:00+00:00"
    assert str(frame.time.dt.tz) == "UTC"


@pytest.mark.parametrize("kind", ["duplicate", "invalid_ohlc", "microseconds"])
def test_archive_rejects_ambiguous_or_invalid_rows(kind):
    row = candle()
    if kind == "invalid_ohlc":
        row[2] = 90
    elif kind == "microseconds":
        row[0] *= 1000
        row[6] *= 1000
    rows = [row, row] if kind == "duplicate" else [row]
    with pytest.raises(ValueError):
        parse_archive(archive(rows))
