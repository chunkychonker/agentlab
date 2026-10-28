"""Storage backends the memory command dispatcher reads and writes through.

``Store`` is the interface the core (commands.py) depends on. Its methods take
only validated ``MemoryPath`` values and assume the caller already checked
preconditions (existence, file-vs-directory); violating a precondition is a
programming error and raises, it is never answered with a default.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Protocol

from paths import MemoryPath, MemoryPathError


class Store(Protocol):
    def exists(self, p: MemoryPath) -> bool: ...
    def is_dir(self, p: MemoryPath) -> bool: ...
    def read(self, p: MemoryPath) -> str:
        """Text of the file at ``p``. Raises if ``p`` is not an existing file."""
    def write(self, p: MemoryPath, text: str) -> None:
        """Create or overwrite file ``p``, creating parent directories.
        Raises if ``p`` is a directory or an ancestor is a file."""
    def delete(self, p: MemoryPath) -> None:
        """Remove ``p``; directories recursively. Raises if missing or root."""
    def rename(self, src: MemoryPath, dst: MemoryPath) -> None:
        """Move ``src`` to ``dst``, creating parents. Raises if src missing,
        dst exists, or either is root."""
    def children(self, p: MemoryPath) -> list[MemoryPath]:
        """Direct children of directory ``p``, sorted. Raises if not a dir."""
    def size(self, p: MemoryPath) -> int:
        """Byte size (UTF-8) of file ``p``. Raises if not an existing file."""


class InMemoryStore:
    """Store kept in a dict; nothing outlives the process."""

    def __init__(self) -> None:
        self._files: dict[tuple[str, ...], str] = {}
        self._dirs: set[tuple[str, ...]] = {()}

    def exists(self, p: MemoryPath) -> bool:
        return p.parts in self._files or p.parts in self._dirs

    def is_dir(self, p: MemoryPath) -> bool:
        return p.parts in self._dirs

    def read(self, p: MemoryPath) -> str:
        return self._files[p.parts]  # KeyError if absent: precondition violated

    def write(self, p: MemoryPath, text: str) -> None:
        if p.parts in self._dirs:
            raise IsADirectoryError(p.virtual)
        for anc in p.ancestors():
            if anc.parts in self._files:
                raise NotADirectoryError(anc.virtual)
            self._dirs.add(anc.parts)
        self._files[p.parts] = text

    def delete(self, p: MemoryPath) -> None:
        if p.is_root:
            raise PermissionError("refusing to delete the memory root")
        if not self.exists(p):
            raise FileNotFoundError(p.virtual)
        n = len(p.parts)
        self._files = {k: v for k, v in self._files.items() if k[:n] != p.parts}
        self._dirs = {d for d in self._dirs if d[:n] != p.parts}

    def rename(self, src: MemoryPath, dst: MemoryPath) -> None:
        if src.is_root or dst.is_root:
            raise PermissionError("refusing to rename the memory root")
        if not self.exists(src):
            raise FileNotFoundError(src.virtual)
        if self.exists(dst):
            raise FileExistsError(dst.virtual)
        for anc in dst.ancestors():
            if anc.parts in self._files:
                raise NotADirectoryError(anc.virtual)
            self._dirs.add(anc.parts)
        n = len(src.parts)
        self._files = {
            (dst.parts + k[n:] if k[:n] == src.parts else k): v for k, v in self._files.items()
        }
        self._dirs = {(dst.parts + d[n:] if d[:n] == src.parts else d) for d in self._dirs}

    def children(self, p: MemoryPath) -> list[MemoryPath]:
        if p.parts not in self._dirs:
            raise NotADirectoryError(p.virtual)
        n = len(p.parts)
        names = {
            k[n]
            for k in (*self._files, *self._dirs)
            if len(k) > n and k[:n] == p.parts
        }
        return [p.child(name) for name in sorted(names)]

    def size(self, p: MemoryPath) -> int:
        return len(self._files[p.parts].encode("utf-8"))


class DirStore:
    """Store backed by a real directory; virtual /memories maps onto ``root``.

    Every operation re-resolves the real path and checks it is still inside
    ``root`` (defense in depth: catches a symlink planted inside the root that
    points outside it). Escape attempts raise MemoryPathError.
    """

    def __init__(self, root: Path) -> None:
        """Raises OSError if ``root`` cannot be created."""
        root.mkdir(parents=True, exist_ok=True)
        self._root = root.resolve()

    def _real(self, p: MemoryPath) -> Path:
        candidate = self._root.joinpath(*p.parts)
        try:
            candidate.resolve().relative_to(self._root)
        except ValueError:
            raise MemoryPathError(f"{p.virtual} resolves outside the memory directory") from None
        return candidate

    def exists(self, p: MemoryPath) -> bool:
        return self._real(p).exists()

    def is_dir(self, p: MemoryPath) -> bool:
        return self._real(p).is_dir()

    def read(self, p: MemoryPath) -> str:
        return self._real(p).read_text(encoding="utf-8")

    def write(self, p: MemoryPath, text: str) -> None:
        real = self._real(p)
        real.parent.mkdir(parents=True, exist_ok=True)
        real.write_text(text, encoding="utf-8")

    def delete(self, p: MemoryPath) -> None:
        if p.is_root:
            raise PermissionError("refusing to delete the memory root")
        real = self._real(p)
        if real.is_dir():
            shutil.rmtree(real)
        else:
            real.unlink()

    def rename(self, src: MemoryPath, dst: MemoryPath) -> None:
        if src.is_root or dst.is_root:
            raise PermissionError("refusing to rename the memory root")
        real_src, real_dst = self._real(src), self._real(dst)
        if real_dst.exists():
            raise FileExistsError(dst.virtual)
        real_dst.parent.mkdir(parents=True, exist_ok=True)
        real_src.rename(real_dst)

    def children(self, p: MemoryPath) -> list[MemoryPath]:
        return [p.child(entry.name) for entry in sorted(self._real(p).iterdir())]

    def size(self, p: MemoryPath) -> int:
        return len(self.read(p).encode("utf-8"))
