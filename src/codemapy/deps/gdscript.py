from __future__ import annotations

import re
from pathlib import Path

from codemapy.deps.base import DependencyExtractor
from codemapy.models import ImportRef


COMMENT_RE = re.compile(r"#.*")
CLASS_NAME_RE = re.compile(r"^\s*class_name\s+([A-Za-z_][A-Za-z0-9_]*)\b", re.MULTILINE)
EXTENDS_RE = re.compile(
    r"^\s*extends\s+(?:"
    r"[\"']([^\"']+)[\"']"
    r"|(?:preload|load)\(\s*[\"']([^\"']+)[\"']\s*\)"
    r"|([A-Za-z_][A-Za-z0-9_]*)"
    r")",
    re.MULTILINE,
)
RESOURCE_CALL_RE = re.compile(r"\b(?:preload|load)\(\s*[\"']([^\"']+)[\"']\s*\)")

GDSCRIPT_BUILTINS = {
    "AcceptDialog",
    "AnimatableBody2D",
    "AnimatableBody3D",
    "AnimationPlayer",
    "Area2D",
    "Area3D",
    "AudioStreamPlayer",
    "Button",
    "Camera2D",
    "Camera3D",
    "CanvasItem",
    "CanvasLayer",
    "CharacterBody2D",
    "CharacterBody3D",
    "CollisionObject2D",
    "CollisionObject3D",
    "ColorRect",
    "Control",
    "EditorPlugin",
    "FileAccess",
    "GridContainer",
    "HBoxContainer",
    "HTTPClient",
    "HTTPRequest",
    "InputEvent",
    "ItemList",
    "Label",
    "LineEdit",
    "MarginContainer",
    "MeshInstance3D",
    "Node",
    "Node2D",
    "Node3D",
    "Object",
    "Panel",
    "PanelContainer",
    "Path2D",
    "Path3D",
    "Popup",
    "RefCounted",
    "Resource",
    "RichTextLabel",
    "RigidBody2D",
    "RigidBody3D",
    "Sprite2D",
    "Sprite3D",
    "StaticBody2D",
    "StaticBody3D",
    "TextureRect",
    "TileMap",
    "Timer",
    "Tree",
    "VBoxContainer",
    "Window",
}


class GDScriptExtractor(DependencyExtractor):
    language = "GDScript"

    def extract(self, path: Path) -> tuple[ImportRef, ...]:
        try:
            source = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            return ()

        searchable = strip_comments(source)
        refs: list[ImportRef] = []
        for match in EXTENDS_RE.finditer(searchable):
            line = _line_number(searchable, match.start())
            resource_path = match.group(1) or match.group(2)
            class_name = match.group(3)
            if resource_path:
                refs.append(ImportRef(raw=resource_path, kind="gdscript-path", line=line))
            elif class_name and class_name not in GDSCRIPT_BUILTINS:
                refs.append(ImportRef(raw=class_name, kind="gdscript-class", line=line))

        for match in RESOURCE_CALL_RE.finditer(searchable):
            raw = match.group(1)
            if raw.startswith(("uid://", "user://")):
                continue
            refs.append(ImportRef(raw=raw, kind="gdscript-path", line=_line_number(searchable, match.start())))

        unique = {(ref.raw, ref.kind, ref.line): ref for ref in refs}
        return tuple(unique.values())


def declared_classes(path: Path) -> tuple[str, ...]:
    try:
        source = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ()
    return tuple(match.group(1) for match in CLASS_NAME_RE.finditer(strip_comments(source)))


def strip_comments(source: str) -> str:
    return COMMENT_RE.sub("", source)


def _line_number(source: str, position: int) -> int:
    return source.count("\n", 0, position) + 1
