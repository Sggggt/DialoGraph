import hashlib
import shutil
from datetime import datetime
from pathlib import Path

from fastapi import UploadFile

from app.core.config import get_settings


def compute_checksum(path: Path) -> str:
    sha = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            sha.update(chunk)
    return sha.hexdigest()


def build_storage_path(filename: str, course_name: str | None = None) -> Path:
    settings = get_settings()
    stamp = datetime.utcnow().strftime("%Y%m%d")
    target_dir = settings.course_paths_for_name(course_name or settings.course_name)["storage_root"] / stamp
    target_dir.mkdir(parents=True, exist_ok=True)
    return target_dir / filename


async def save_upload(upload: UploadFile, course_name: str | None = None) -> Path:
    target = build_storage_path(upload.filename or "upload.bin", course_name=course_name)
    with target.open("wb") as handle:
        while chunk := await upload.read(1024 * 1024):
            handle.write(chunk)
    return target


def copy_source_file(source_path: Path, course_name: str | None = None) -> Path:
    target = build_storage_path(source_path.name, course_name=course_name)
    shutil.copy2(source_path, target)
    return target
