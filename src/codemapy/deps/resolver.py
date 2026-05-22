from __future__ import annotations

from pathlib import Path

from codemapy.models import FileNode, ImportRef


JS_EXTENSIONS = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs")


class ImportResolver:
    def __init__(self, root: Path, files: tuple[FileNode, ...]) -> None:
        self.root = root.resolve()
        self.paths = {file.path for file in files}
        self.source_roots = self._detect_source_roots(files)

    def resolve(self, source: FileNode, ref: ImportRef) -> str | None:
        if source.language == "Python":
            return self._resolve_python(source, ref.raw)
        if source.language in {"JavaScript", "TypeScript"}:
            return self._resolve_javascript(source, ref.raw)
        return None

    def _resolve_python(self, source: FileNode, raw: str) -> str | None:
        if raw.startswith("."):
            level = len(raw) - len(raw.lstrip("."))
            remainder = raw[level:]
            base = Path(source.path).parent
            for _ in range(max(level - 1, 0)):
                base = base.parent
            parts = [part for part in remainder.split(".") if part]
            return self._first_existing(self._python_candidates(base, parts))

        parts = [part for part in raw.split(".") if part]
        candidates = []
        for source_root in self.source_roots:
            candidates.extend(self._python_candidates(source_root, parts))
        return self._first_existing(candidates)

    def _python_candidates(self, base: Path, parts: list[str]) -> list[str]:
        joined = base.joinpath(*parts) if parts else base
        candidates = [
            joined.with_suffix(".py").as_posix(),
            joined.joinpath("__init__.py").as_posix(),
        ]
        if parts:
            candidates.append(base.joinpath(parts[0], "__init__.py").as_posix())
        return candidates

    def _resolve_javascript(self, source: FileNode, raw: str) -> str | None:
        if not raw.startswith("."):
            return None
        base = Path(source.path).parent / raw
        candidates: list[str] = []
        if base.suffix:
            candidates.append(base.as_posix())
        else:
            candidates.extend((base.with_suffix(ext).as_posix() for ext in JS_EXTENSIONS))
            candidates.extend((base / f"index{ext}").as_posix() for ext in JS_EXTENSIONS)
        return self._first_existing(candidates)

    def _first_existing(self, candidates: list[str]) -> str | None:
        for candidate in candidates:
            normalized = candidate.replace("\\", "/").lstrip("./")
            if normalized in self.paths:
                return normalized
        return None

    def _detect_source_roots(self, files: tuple[FileNode, ...]) -> tuple[Path, ...]:
        roots = [Path()]
        if any(file.path.startswith("src/") for file in files):
            roots.append(Path("src"))
        return tuple(roots)
