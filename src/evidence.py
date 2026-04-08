from pathlib import Path


def get_directory_size(root: Path) -> int:
    return sum(f.stat().st_size for f in root.rglob("*") if f.is_file())


def _list_evidence_files_by_age(root: Path) -> list[Path]:
    files = [f for f in root.rglob("*") if f.is_file()]
    files.sort(key=lambda f: f.stat().st_mtime)
    return files


def enforce_evidence_quota(root: Path, max_bytes: int) -> None:
    total = get_directory_size(root)
    for file_path in _list_evidence_files_by_age(root):
        if total <= max_bytes:
            break
        try:
            size = file_path.stat().st_size
            file_path.unlink(missing_ok=True)
            total -= size
        except FileNotFoundError:
            continue
