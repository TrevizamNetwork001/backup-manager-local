from __future__ import annotations

import stat
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


TEMP_SUFFIXES = frozenset({".part", ".tmp", ".partial", ".filepart", ".upload"})


@dataclass(frozen=True)
class StabilityDecision:
    state: str
    size: int
    mtime_ns: int


@dataclass(frozen=True)
class ValidationIssue:
    code: str
    message: str


def discover(directory: Path) -> list[Path]:
    """Return visible, complete-looking entries without interpreting their content."""
    return [entry for entry in directory.rglob("*")
            if entry.is_file() and entry.suffix.lower() not in TEMP_SUFFIXES
            and not any(part.startswith(".") for part in entry.relative_to(directory).parts)]


def stabilization(existing, info, *, current: datetime, stable_seconds: int) -> StabilityDecision:
    if info.st_size != existing["observed_size"] or info.st_mtime_ns != existing["observed_mtime_ns"]:
        return StabilityDecision("changed", info.st_size, info.st_mtime_ns)
    observed_at = datetime.fromisoformat(existing["updated_at"].replace(" ", "T") + "+00:00")
    if (current - observed_at).total_seconds() < stable_seconds:
        return StabilityDecision("waiting", info.st_size, info.st_mtime_ns)
    return StabilityDecision("stable", info.st_size, info.st_mtime_ns)


def validate_file(info, *, max_size: int, account_quota: int | None) -> ValidationIssue | None:
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        return ValidationIssue("unsafe_file", "Tipo de arquivo inseguro.")
    if info.st_size <= 0:
        return ValidationIssue("empty_file", "Arquivo vazio.")
    if info.st_size > max_size or (account_quota and info.st_size > account_quota):
        return ValidationIssue("file_too_large", "Arquivo acima do limite.")
    return None
