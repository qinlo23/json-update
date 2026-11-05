from __future__ import annotations

import copy
import json
import os
import re
import stat
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

PathParts = tuple[str | int, ...]


def strict_loads(text: str) -> Any:
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError(f"重复键：{key}")
            result[key] = value
        return result

    def invalid(value):
        raise ValueError(f"不支持非标准数值：{value}")

    # parse_float also rejects overflow such as 1e999 rather than losing data.
    def number(value):
        import math
        from decimal import Decimal

        parsed = float(value)
        if not math.isfinite(parsed) or Decimal(str(parsed)) != Decimal(value):
            raise ValueError(f"数值超出安全精度或支持范围：{value}")
        return parsed

    return json.loads(text, object_pairs_hook=pairs, parse_constant=invalid,
                      parse_float=number)


@dataclass(frozen=True)
class Format:
    indent: str | int | None
    newline: str
    trailing: bool
    ascii: bool
    compact: bool = False

    @classmethod
    def detect(cls, text: str, internal: bool = False) -> Format:
        match = re.search(r'\n([ \t]+)\S', text)
        compact = internal and '\n' not in text and '\r' not in text
        return cls(match[1] if match else (None if compact else 2),
                   '\r\n' if '\r\n' in text else '\n',
                   text.endswith(('\n', '\r')), bool(re.search(r'\\u[0-9a-fA-F]{4}', text)),
                   compact)

    def dump(self, data: Any) -> str:
        text = json.dumps(data, ensure_ascii=self.ascii, allow_nan=False,
                          indent=self.indent,
                          separators=(',', ':') if self.compact else None)
        text = text.replace('\n', self.newline)
        return text + (self.newline if self.trailing else '')


def display_path(parts: PathParts, prefix: str = '$') -> str:
    for part in parts:
        if isinstance(part, int):
            prefix += f'[{part}]'
        elif re.fullmatch(r'[A-Za-z_][A-Za-z_0-9]*', part):
            prefix += '.' + part
        else:
            prefix += '[' + json.dumps(part, ensure_ascii=False) + ']'
    return prefix


@dataclass(frozen=True)
class Field:
    parts: PathParts
    embedded: bool
    name: str
    path: str
    original: str


def walk(data: Any, parts: PathParts = ()):
    if isinstance(data, dict):
        for key, value in data.items():
            yield from walk(value, parts + (key,))
    elif isinstance(data, list):
        for index, value in enumerate(data):
            yield from walk(value, parts + (index,))
    elif isinstance(data, str):
        yield parts, data


def assign(data: Any, parts: PathParts, value: str):
    if not parts:
        return value
    target = data
    for part in parts[:-1]:
        target = target[part]
    target[parts[-1]] = value
    return data


@dataclass
class Document:
    path: Path
    raw: bytes
    data: Any
    formatting: Format
    bom: bool
    fields: list[Field]
    embedded: Any = None
    embedded_format: Format | None = None
    errors: list[str] = field(default_factory=list)
    edits: dict[Field, str] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path) -> Document:
        return cls.from_bytes(path, path.read_bytes())

    @classmethod
    def from_bytes(cls, path: Path, raw: bytes) -> Document:
        text = raw.decode('utf-8-sig')
        data = strict_loads(text)
        doc = cls(path, raw, data, Format.detect(text), raw.startswith(b'\xef\xbb\xbf'), [])
        has_embedded = (isinstance(data, dict) and isinstance(data.get('annotation'), dict)
                        and isinstance(data['annotation'].get('result'), str))
        for parts, value in walk(data):
            if has_embedded and parts == ('annotation', 'result'):
                try:
                    doc.embedded = strict_loads(value)
                    doc.embedded_format = Format.detect(value, internal=True)
                    for inner, original in walk(doc.embedded):
                        doc.fields.append(Field(inner, True, str(inner[-1]) if inner else 'result',
                                                display_path(inner, '$.annotation.result::<json>'), original))
                except (ValueError, RecursionError) as exc:
                    doc.errors.append(f'annotation.result 内部 JSON 无效：{exc}')
                continue
            doc.fields.append(Field(parts, False, str(parts[-1]) if parts else '$',
                                    display_path(parts), value))
        return doc

    def matching(self, pattern: re.Pattern) -> list[Field]:
        return [f for f in self.fields if pattern.search(f.name) or pattern.search(f.path)]

    def edit(self, target: Field, value: str):
        if value == target.original:
            self.edits.pop(target, None)
        else:
            self.edits[target] = value

    def label_groups(self) -> dict[str, list[Field]]:
        """Group every label in this document by its current (possibly edited) value."""
        groups: dict[str, list[Field]] = {}
        for target in self.fields:
            if target.name == 'label':
                groups.setdefault(self.edits.get(target, target.original), []).append(target)
        return groups

    def editor_groups(self, pattern: re.Pattern) -> list[list[Field]]:
        """One editor per current label value, with fixed targets while typing."""
        labels = self.label_groups()
        seen = set()
        groups = []
        for target in self.matching(pattern):
            if target.name != 'label':
                groups.append([target])
                continue
            value = self.edits.get(target, target.original)
            if value not in seen:
                seen.add(value)
                groups.append(labels[value])
        return groups

    def edit_group(self, targets: list[Field], value: str):
        for target in targets:
            self.edit(target, value)

    def render(self) -> bytes:
        data, inner = copy.deepcopy(self.data), copy.deepcopy(self.embedded)
        for target, value in self.edits.items():
            if target.embedded:
                inner = assign(inner, target.parts, value)
            else:
                data = assign(data, target.parts, value)
        if any(f.embedded for f in self.edits):
            data['annotation']['result'] = self.embedded_format.dump(inner)
        text = self.formatting.dump(data)
        if strict_loads(text) != data:
            raise ValueError('序列化校验失败')
        return (b'\xef\xbb\xbf' if self.bom else b'') + text.encode('utf-8')

    def save(self) -> int:
        if not self.edits:
            return 0
        output = self.render()
        fresh = Document.from_bytes(self.path, output)
        if self.path.is_symlink():
            raise ValueError('不覆盖符号链接文件')
        if self.path.read_bytes() != self.raw:
            raise ValueError('文件已被外部修改，请保留当前编辑并重新加载文件')
        count = len(self.edits)
        temporary = None
        try:
            mode = stat.S_IMODE(self.path.stat().st_mode)
            with tempfile.NamedTemporaryFile(dir=self.path.parent, prefix='.json-update-',
                                             suffix='.tmp', delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(output)
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(temporary, mode)
            if self.path.read_bytes() != self.raw:
                raise ValueError('文件在保存期间被外部修改')
            os.replace(temporary, self.path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        self.__dict__.update(fresh.__dict__)
        return count


def scan(root: Path, cancelled=lambda: False):
    paths, errors, seen = [], [], set()
    def onerror(exc):
        errors.append(str(exc))
    for directory, dirs, files in os.walk(root, followlinks=False, onerror=onerror):
        if cancelled():
            break
        dirs[:] = sorted(d for d in dirs if not Path(directory, d).is_symlink())
        for name in sorted(files):
            path = Path(directory, name)
            if path.suffix.lower() != '.json' or path.is_symlink():
                continue
            try:
                normalized = os.path.normcase(str(path.resolve()))
            except OSError as exc:
                errors.append(f"{path}: {exc}")
                continue
            if normalized not in seen:
                seen.add(normalized)
                paths.append(path)
    return paths, errors
