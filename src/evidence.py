from pathlib import Path


def get_directory_size(root: Path) -> int:
    """计算目录下所有文件的总大小（字节）。"""
    return sum(f.stat().st_size for f in root.rglob("*") if f.is_file())


def _file_creation_time(f: Path) -> float:
    """获取文件创建时间。macOS 上使用 st_birthtime，其他平台回退到 st_mtime。"""
    stat = f.stat()
    return getattr(stat, "st_birthtime", stat.st_mtime)


def list_evidence_files_by_creation(root: Path) -> list[Path]:
    """按文件创建时间从旧到新排列目录下所有文件。

    排序依据为文件创建时间（macOS: st_birthtime），确保最旧的文件排在最前。
    证据文件为一次写入不再修改，因此创建时间即为唯一写入时间。
    """
    files = [f for f in root.rglob("*") if f.is_file()]
    files.sort(key=_file_creation_time)
    return files


def enforce_evidence_quota(root: Path, max_bytes: int) -> None:
    """当证据目录总大小超过 max_bytes 时，按创建时间从旧到新删除文件，直到回到上限以下。

    删除顺序：最旧的文件最先被删除（与需求文档"按文件创建时间从旧到新执行"一致）。
    """
    total = get_directory_size(root)
    for file_path in list_evidence_files_by_creation(root):
        if total <= max_bytes:
            break
        try:
            size = file_path.stat().st_size
            file_path.unlink(missing_ok=True)
            total -= size
        except FileNotFoundError:
            continue
