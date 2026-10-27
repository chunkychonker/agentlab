"""A ``Store`` held entirely in process memory, for tests and dry runs."""

from __future__ import annotations

from paths import ROOT, MemoryPath
from store import NodeKind


class InMemoryStore:
    """Dict-backed ``Store``. Violated preconditions raise ``KeyError``/``ValueError``."""

    def __init__(self) -> None:
        self._files: dict[MemoryPath, str] = {}
        self._dirs: set[MemoryPath] = {ROOT}

    def kind(self, path: MemoryPath) -> NodeKind:
        if path in self._dirs:
            return NodeKind.DIR
        if path in self._files:
            return NodeKind.FILE
        return NodeKind.MISSING

    def read_text(self, path: MemoryPath) -> str:
        return self._files[path]

    def write_text(self, path: MemoryPath, text: str) -> None:
        if path in self._dirs:
            raise ValueError(f"{path} is a directory")
        for anc in path.ancestors():
            if anc in self._files:
                raise ValueError(f"ancestor {anc} is a file")
            self._dirs.add(anc)
        self._files[path] = text

    def children(self, path: MemoryPath) -> list[str]:
        if path not in self._dirs:
            raise KeyError(str(path))
        depth = len(path.parts) + 1
        names = {
            p.parts[-1]
            for p in (*self._files, *self._dirs)
            if len(p.parts) == depth and p.is_within(path)
        }
        return sorted(names)

    def size(self, path: MemoryPath) -> int:
        if path in self._files:
            return len(self._files[path].encode("utf-8"))
        if path in self._dirs:
            return sum(len(t.encode("utf-8")) for p, t in self._files.items() if p.is_within(path))
        raise KeyError(str(path))

    def delete(self, path: MemoryPath) -> None:
        if path.is_root:
            raise ValueError("refusing to delete the root")
        if self.kind(path) is NodeKind.MISSING:
            raise KeyError(str(path))
        self._files = {p: t for p, t in self._files.items() if not p.is_within(path)}
        self._dirs = {p for p in self._dirs if not p.is_within(path)}

    def rename(self, old: MemoryPath, new: MemoryPath) -> None:
        if old.is_root or new.is_root:
            raise ValueError("refusing to rename the root")
        if self.kind(old) is NodeKind.MISSING:
            raise KeyError(str(old))
        if self.kind(new) is not NodeKind.MISSING:
            raise ValueError(f"{new} exists")
        if new.is_within(old):
            raise ValueError(f"{new} is inside {old}")
        for anc in new.ancestors():
            if anc in self._files:
                raise ValueError(f"ancestor {anc} is a file")

        def moved(p: MemoryPath) -> MemoryPath:
            return MemoryPath(new.parts + p.parts[len(old.parts):]) if p.is_within(old) else p

        self._files = {moved(p): t for p, t in self._files.items()}
        self._dirs = {moved(p) for p in self._dirs} | set(new.ancestors())
