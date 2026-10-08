import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from web.server import download_sync as ds


def _fresh_dirs():
    tmp = Path(tempfile.mkdtemp(prefix="dl-sync-"))
    src = tmp / "lib-downloads"
    dst = tmp / "workspace-downloads"
    src.mkdir(parents=True, exist_ok=True)
    dst.mkdir(parents=True, exist_ok=True)
    return src, dst


def _reset():
    ds._seen.clear()
    ds._oversize_logged.clear()
    ds._idle_logged = True  # silence idle logs (explicit src/dst given)


def test_settle_two_sightings_then_moves():
    _reset()
    src, dst = _fresh_dirs()
    (src / "report.pdf").write_bytes(b"x" * 100)
    assert ds.sync_once(src, dst) == 0
    assert (src / "report.pdf").exists()  # first sighting: stays
    assert ds.sync_once(src, dst) == 1
    assert (dst / "report.pdf").exists()
    assert not (src / "report.pdf").exists()
    print("settle-then-move OK")


def test_growing_file_waits():
    _reset()
    src, dst = _fresh_dirs()
    p = src / "big.zip"
    p.write_bytes(b"a" * 10)
    assert ds.sync_once(src, dst) == 0
    p.write_bytes(b"b" * 20)  # still writing: fingerprint changed
    assert ds.sync_once(src, dst) == 0
    assert p.exists()
    assert ds.sync_once(src, dst) == 1
    print("growing-file waits OK")


def test_temp_suffixes_skipped():
    _reset()
    src, dst = _fresh_dirs()
    (src / "movie.mp4.crdownload").write_bytes(b"z" * 10)
    assert ds.sync_once(src, dst) == 0
    assert ds.sync_once(src, dst) == 0
    assert (src / "movie.mp4.crdownload").exists()
    print("temp suffix skipped OK")


def test_collision_gets_counter_suffix():
    _reset()
    src, dst = _fresh_dirs()
    (dst / "a.pdf").write_bytes(b"old")
    (src / "a.pdf").write_bytes(b"new")
    assert ds.sync_once(src, dst) == 0
    assert ds.sync_once(src, dst) == 1
    assert (dst / "a (1).pdf").read_bytes() == b"new"
    print("collision rename OK")


def test_oversize_stays_put():
    _reset()
    src, dst = _fresh_dirs()
    (src / "huge.iso").write_bytes(b"q" * 50)
    assert ds.sync_once(src, dst, max_bytes=10) == 0
    assert ds.sync_once(src, dst, max_bytes=10) == 0
    assert (src / "huge.iso").exists()
    print("oversize stays OK")


if __name__ == "__main__":
    test_settle_two_sightings_then_moves()
    test_growing_file_waits()
    test_temp_suffixes_skipped()
    test_collision_gets_counter_suffix()
    test_oversize_stays_put()
    print("ALL DOWNLOAD-SYNC TESTS PASSED")
