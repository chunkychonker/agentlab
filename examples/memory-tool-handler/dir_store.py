"""A ``Store`` backed by one real directory on disk.

``MemoryPath`` already rules out ``..`` and friends, so joining its segments
onto the root cannot traverse lexically. As defense in depth, every mapping is
re-checked after ``Path.resolve()`` (which follows symlinks) with
``relative_to(root)``: a symlink planted inside the root that points outside it
is refused with ``PathEscapeError`` instead of being followed.

Known limit: the check and the subsequent operation are not atomic, so a
process racing to swap a directory for a symlink between them is not defended
against (TOCTOU). The failure domain is the configured root directory.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from paths import MemoryPath, MemoryPathError
from store import NodeKind

# Owner-only, matching the SDK's local memory tool: memory can hold anything
# the model was told, so it should not be world-readable under a loose umask.
_DIR_MODE = 0o700
_FILE_MODE = 0o600


class PathEscapeError(MemoryPathError):
    """A valid virtual path resolved to a real location outside the store root."""


class DirStore:
    """``Store`` over ``root``. Violated preconditions raise ``OSError``/``ValueError``."""

    def __init__(self, root: Path) -> None:
        """Bind to an existing directory.

        Raises:
            NotADirectoryError: if ``root`` does not exist or is not a directory.
        """
        resolved = root.resolve()
        if not resolved.is_dir():
            raise NotADirectoryError(f"memory root {root} is not an existing directory")
        self._root = resolved

    def _real(self, path: MemoryPath) -> Path:
        """Map to a real path inside the root.

        Raises:
            PathEscapeError: if the resolved location is outside the root.
        """
        candidate = self._root.joinpath(*path.parts)
        try:
            candidate.resolve().relative_to(self._root)
        except ValueError:
            raise PathEscapeError(f"{path} resolves outside the memory directory") from None
        return candidate

    def kind(self, path: MemoryPath) -> NodeKind:
        real = self._real(path)
        if real.is_dir():
            return NodeKind.DIR
        if real.is_file():
            return NodeKind.FILE
        return NodeKind.MISSING

    def read_text(self, path: MemoryPath) -> str:
        return self._real(path).read_text(encoding="utf-8")

    def write_text(self, path: MemoryPath, text: str) -> None:
        real = self._real(path)
        for anc in path.ancestors():
            self._real(anc).mkdir(mode=_DIR_MODE, exist_ok=True)
        # Write to a sibling temp file then replace, so a crash mid-write never
        # leaves a truncated memory file behind.
        tmp = real.with_name(f".{real.name}.tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, _FILE_MODE)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(text)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, real)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise

    def children(self, path: MemoryPath) -> list[str]:
        return sorted(p.name for p in self._real(path).iterdir())

    def size(self, path: MemoryPath) -> int:
        real = self._real(path)
        if real.is_file():
            return real.stat().st_size
        if real.is_dir():
            return sum(self.size(path.child(name)) for name in self.children(path))
        raise FileNotFoundError(str(path))

    def delete(self, path: MemoryPath) -> None:
        if path.is_root:
            raise ValueError("refusing to delete the root")
        real = self._real(path)
        if real.is_dir():
            shutil.rmtree(real)
        else:
            real.unlink()

    def rename(self, old: MemoryPath, new: MemoryPath) -> None:
        if old.is_root or new.is_root:
            raise ValueError("refusing to rename the root")
        src, dst = self._real(old), self._real(new)
        if dst.exists():
            raise FileExistsError(str(new))
        for anc in new.ancestors():
            self._real(anc).mkdir(mode=_DIR_MODE, exist_ok=True)
        src.rename(dst)
