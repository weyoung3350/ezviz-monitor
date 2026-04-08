import pytest
from pathlib import Path

from src.face_registry import scan_face_directories, FaceRegistryError


def test_scan_face_directories_single_person(tmp_path: Path):
    person_dir = tmp_path / "爸爸"
    person_dir.mkdir()
    (person_dir / "01.jpg").write_bytes(b"fake-image")

    result = scan_face_directories(tmp_path)

    assert result.people == ["爸爸"]
    assert result.warnings == []


def test_scan_face_directories_multiple_people(tmp_path: Path):
    for name in ["爸爸", "妈妈", "外婆"]:
        d = tmp_path / name
        d.mkdir()
        (d / "01.jpg").write_bytes(b"fake-image")

    result = scan_face_directories(tmp_path)

    assert sorted(result.people) == ["外婆", "妈妈", "爸爸"]


def test_empty_person_dir_gives_warning(tmp_path: Path):
    person_dir = tmp_path / "爸爸"
    person_dir.mkdir()
    # 空目录，无照片

    other_dir = tmp_path / "妈妈"
    other_dir.mkdir()
    (other_dir / "01.jpg").write_bytes(b"fake-image")

    result = scan_face_directories(tmp_path)

    assert "爸爸" in result.warnings[0]
    assert "妈妈" in result.people


def test_root_dir_not_exists_raises(tmp_path: Path):
    with pytest.raises(FaceRegistryError):
        scan_face_directories(tmp_path / "nonexistent")


def test_no_valid_person_dirs_raises(tmp_path: Path):
    # 目录存在但没有子目录
    with pytest.raises(FaceRegistryError):
        scan_face_directories(tmp_path)


def test_ignores_files_in_root(tmp_path: Path):
    (tmp_path / "readme.txt").write_bytes(b"ignore me")
    person_dir = tmp_path / "爸爸"
    person_dir.mkdir()
    (person_dir / "01.jpg").write_bytes(b"fake")

    result = scan_face_directories(tmp_path)
    assert result.people == ["爸爸"]
