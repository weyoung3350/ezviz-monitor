import os
import time
from pathlib import Path

from src.evidence import enforce_evidence_quota, get_directory_size


def test_enforce_evidence_quota_deletes_oldest_files(tmp_path: Path):
    oldest = tmp_path / "old.jpg"
    newest = tmp_path / "new.mp4"

    oldest.write_bytes(b"a" * 8)
    time.sleep(0.05)
    newest.write_bytes(b"b" * 8)

    # 设置不同的修改时间确保排序正确
    os.utime(oldest, (1000, 1000))
    os.utime(newest, (2000, 2000))

    enforce_evidence_quota(tmp_path, max_bytes=10)

    assert not oldest.exists()
    assert newest.exists()


def test_enforce_evidence_quota_no_delete_under_limit(tmp_path: Path):
    f1 = tmp_path / "a.jpg"
    f2 = tmp_path / "b.mp4"

    f1.write_bytes(b"a" * 4)
    f2.write_bytes(b"b" * 4)

    enforce_evidence_quota(tmp_path, max_bytes=100)

    assert f1.exists()
    assert f2.exists()


def test_enforce_evidence_quota_handles_both_images_and_videos(tmp_path: Path):
    old_img = tmp_path / "old.jpg"
    old_vid = tmp_path / "old.mp4"
    new_img = tmp_path / "new.jpg"

    old_img.write_bytes(b"x" * 10)
    old_vid.write_bytes(b"y" * 10)
    new_img.write_bytes(b"z" * 10)

    os.utime(old_img, (1000, 1000))
    os.utime(old_vid, (1001, 1001))
    os.utime(new_img, (2000, 2000))

    enforce_evidence_quota(tmp_path, max_bytes=15)

    assert not old_img.exists()
    assert not old_vid.exists()
    assert new_img.exists()


def test_enforce_evidence_quota_handles_missing_files(tmp_path: Path):
    f = tmp_path / "gone.jpg"
    f.write_bytes(b"x" * 10)
    f.unlink()

    # 不应崩溃
    enforce_evidence_quota(tmp_path, max_bytes=5)


def test_enforce_evidence_quota_handles_subdirectories(tmp_path: Path):
    sub = tmp_path / "客厅"
    sub.mkdir()

    old_file = sub / "old.jpg"
    new_file = sub / "new.jpg"

    old_file.write_bytes(b"a" * 10)
    new_file.write_bytes(b"b" * 10)

    os.utime(old_file, (1000, 1000))
    os.utime(new_file, (2000, 2000))

    enforce_evidence_quota(tmp_path, max_bytes=15)

    assert not old_file.exists()
    assert new_file.exists()


def test_get_directory_size(tmp_path: Path):
    (tmp_path / "a.txt").write_bytes(b"x" * 100)
    (tmp_path / "b.txt").write_bytes(b"y" * 200)

    size = get_directory_size(tmp_path)
    assert size == 300
