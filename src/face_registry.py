from dataclasses import dataclass, field
from pathlib import Path


IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}


class FaceRegistryError(Exception):
    pass


@dataclass
class FaceDirectoryScan:
    people: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def scan_face_directories(root: Path) -> FaceDirectoryScan:
    if not root.exists():
        raise FaceRegistryError(f"人脸目录不存在: {root}")

    person_dirs = sorted(d for d in root.iterdir() if d.is_dir())

    if not person_dirs:
        raise FaceRegistryError(f"人脸目录中没有有效的人物子目录: {root}")

    people: list[str] = []
    warnings: list[str] = []

    for person_dir in person_dirs:
        images = [
            f for f in person_dir.iterdir()
            if f.is_file() and f.suffix.lower() in IMAGE_EXTENSIONS
        ]
        if not images:
            warnings.append(f"人物目录 '{person_dir.name}' 中没有有效照片")
        else:
            people.append(person_dir.name)

    if not people:
        raise FaceRegistryError("没有任何人物目录包含有效照片")

    return FaceDirectoryScan(people=people, warnings=warnings)


def ensure_target_person_exists(scan: FaceDirectoryScan, target_name: str) -> None:
    """校验目标人物的样本目录存在且包含有效照片。"""
    if target_name not in scan.people:
        raise FaceRegistryError(
            f"目标人物 '{target_name}' 的样本目录缺失或没有有效照片"
        )
