"""
Local replacements for the small number of symbols solidlsp originally imported from
``serena`` and ``sensai``, now that solidlsp has been extracted as a standalone package.

- ``match_path`` and ``MatchedConsecutiveLines``/``TextLine``/``LineType`` are inlined
  from ``serena.util.file_system`` / ``serena.util.text_utils`` (MIT licensed, from
  https://github.com/oraios/serena), trimmed to the subset solidlsp actually uses.
- ``ToStringMixin``, ``mark_used``, ``dump_pickle``/``load_pickle``/``getstate`` are
  minimal reimplementations of the ``sensai-utils`` helpers of the same name, scoped to
  the call sites present in solidlsp (local-file pickling only, no S3/bz2/cloudpickle
  backends; ``ToStringMixin`` supports the include/exclude hooks solidlsp overrides).
"""

from __future__ import annotations

import os
import pickle
from copy import copy
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, Self

from pathspec import PathSpec

# --- inlined from serena.util.file_system -----------------------------------------


def match_path(relative_path: str, path_spec: PathSpec, root_path: str = "") -> bool:
    """
    Match a relative path against a given pathspec. Just pathspec.match_file() is not enough,
    we need to do some massaging to fix issues with pathspec matching.
    """
    if str(relative_path) in {"", "."}:
        return False

    normalized_path = str(relative_path).replace(os.path.sep, "/")

    # We can have patterns like /src/..., which would only match corresponding paths from the repo root
    # Unfortunately, pathspec can't know whether a relative path is relative to the repo root or not,
    # so it will never match src/...
    # The fix is to just always assume that the input path is relative to the repo root and to
    # prefix it with /.
    if not normalized_path.startswith("/"):
        normalized_path = "/" + normalized_path

    # pathspec can't handle the matching of directories if they don't end with a slash!
    # see https://github.com/cpburnz/python-pathspec/issues/89
    abs_path = os.path.abspath(os.path.join(root_path, relative_path))
    if os.path.isdir(abs_path) and not normalized_path.endswith("/"):
        normalized_path = normalized_path + "/"
    return path_spec.match_file(normalized_path)


# --- inlined from serena.util.text_utils -------------------------------------------


class LineType(StrEnum):
    """Enum for different types of lines in search results."""

    MATCH = "match"
    """Part of the matched lines"""
    BEFORE_MATCH = "prefix"
    """Lines before the match"""
    AFTER_MATCH = "postfix"
    """Lines after the match"""


@dataclass(kw_only=True)
class TextLine:
    """Represents a line of text with information on how it relates to the match."""

    line_number: int
    line_content: str
    match_type: LineType
    """Represents the type of line (match, prefix, postfix)"""

    def get_display_prefix(self) -> str:
        """Get the display prefix for this line based on the match type."""
        if self.match_type == LineType.MATCH:
            return "  >"
        return "..."

    def format_line(self, include_line_numbers: bool = True) -> str:
        """Format the line for display (e.g. for logging or passing to an LLM)."""
        prefix = self.get_display_prefix()
        if include_line_numbers:
            line_num = str(self.line_number).rjust(4)
            prefix = f"{prefix}{line_num}"
        return f"{prefix}:{self.line_content}"


@dataclass(kw_only=True)
class MatchedConsecutiveLines:
    """Represents a collection of consecutive lines found through some criterion in a text file or a string.
    May include lines before, after, and matched.
    """

    lines: list[TextLine]
    """All lines in the context of the match. At least one of them is of `match_type` `MATCH`."""
    source_file_path: str | None = None
    """Path to the file where the match was found (Metadata)."""

    # set in post-init
    lines_before_matched: list[TextLine] = field(default_factory=list)
    matched_lines: list[TextLine] = field(default_factory=list)
    lines_after_matched: list[TextLine] = field(default_factory=list)

    def __post_init__(self) -> None:
        for line in self.lines:
            if line.match_type == LineType.BEFORE_MATCH:
                self.lines_before_matched.append(line)
            elif line.match_type == LineType.MATCH:
                self.matched_lines.append(line)
            elif line.match_type == LineType.AFTER_MATCH:
                self.lines_after_matched.append(line)

        assert len(self.matched_lines) > 0, "At least one matched line is required"

    @property
    def start_line(self) -> int:
        return self.lines[0].line_number

    @property
    def end_line(self) -> int:
        return self.lines[-1].line_number

    @property
    def num_matched_lines(self) -> int:
        return len(self.matched_lines)

    def to_display_string(self, include_line_numbers: bool = True) -> str:
        return "\n".join([line.format_line(include_line_numbers) for line in self.lines])

    @classmethod
    def from_file_contents(
        cls,
        file_contents: str,
        line: int,
        context_lines_before: int = 0,
        context_lines_after: int = 0,
        source_file_path: str | None = None,
    ) -> Self:
        from filesystem_supertool2.solidlsp.ls_utils import TextUtils

        line_contents = TextUtils.split_lines(file_contents)
        start_lineno = max(0, line - context_lines_before)
        end_lineno = min(len(line_contents) - 1, line + context_lines_after)
        text_lines: list[TextLine] = []
        for lineno in range(start_lineno, line):
            text_lines.append(TextLine(line_number=lineno, line_content=line_contents[lineno], match_type=LineType.BEFORE_MATCH))
        text_lines.append(TextLine(line_number=line, line_content=line_contents[line], match_type=LineType.MATCH))
        for lineno in range(line + 1, end_lineno + 1):
            text_lines.append(TextLine(line_number=lineno, line_content=line_contents[lineno], match_type=LineType.AFTER_MATCH))

        return cls(lines=text_lines, source_file_path=source_file_path)


# --- reimplementations of sensai-utils helpers -------------------------------------


def mark_used(*args: Any) -> None:
    """Marks identifiers as used (e.g. re-exported names); does nothing."""


def dump_pickle(obj: Any, pickle_path: str | Path, protocol: int = pickle.HIGHEST_PROTOCOL) -> None:
    """Pickles ``obj`` to a local file at ``pickle_path``. Local-file subset of sensai's ``dump_pickle``."""
    with open(pickle_path, "wb") as f:
        pickle.dump(obj, f, protocol=protocol)


def load_pickle(pickle_path: str | Path) -> Any:
    """Unpickles from a local file at ``pickle_path``. Local-file subset of sensai's ``load_pickle``."""
    with open(pickle_path, "rb") as f:
        return pickle.load(f)


def getstate(
    cls: type,
    obj: Any,
    transient_properties: list[str] | None = None,
    excluded_properties: list[str] | None = None,
    override_properties: dict[str, Any] | None = None,
    excluded_default_properties: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Helper for implementing ``__getstate__`` which appropriately handles the case where a parent
    class already implements ``__getstate__`` and the case where it does not.
    """
    s = super(cls, obj)
    if hasattr(s, "__getstate__"):
        d = s.__getstate__()
    else:
        d = obj.__dict__
    d = copy(d)
    if transient_properties is not None:
        for p in transient_properties:
            if p in d:
                d[p] = None
    if excluded_properties is not None:
        for p in excluded_properties:
            if p in d:
                del d[p]
    if override_properties is not None:
        for k, v in override_properties.items():
            d[k] = v
    if excluded_default_properties is not None:
        for p, v in excluded_default_properties.items():
            if p in d and d[p] == v:
                del d[p]
    return d


class ToStringMixin:
    """
    Provides ``__str__``/``__repr__`` implementations of the form ``"<class name>[<object info>]"`` /
    ``"<class name>[id=<object id>, <object info>]"``, where ``<object info>`` defaults to all
    instance attributes as ``name=value`` pairs (private leading underscores stripped for display).

    Reimplementation of sensai-utils' ``ToStringMixin``, scoped to the hooks solidlsp actually
    overrides (``_tostring_excludes``, ``_tostring_includes``). Recursion through nested
    ``ToStringMixin`` values is guarded by object id so a self-referential structure renders
    ``"<class name>[<<]"`` on the repeat instead of recursing forever.
    """

    _INCLUDE_ALL = "__all__"

    def _tostring_class_name(self) -> str:
        return type(self).__qualname__

    def _tostring_excludes(self) -> list[str]:
        """Attribute names to exclude from the string representation."""
        return []

    def _tostring_includes(self) -> list[str]:
        """If not just the all-marker, restricts the representation to only these attributes."""
        return [self._INCLUDE_ALL]

    def _tostring_includes_forced(self) -> list[str]:
        """Attribute names always included, regardless of include/exclude semantics otherwise in effect."""
        return []

    def _tostring_additional_entries(self) -> dict[str, Any]:
        """Extra, non-attribute key/value pairs to include in the representation."""
        return {}

    def _tostring_exclude_private(self) -> bool:
        return False

    def _tostring_exclude_exceptions(self) -> list[str]:
        return []

    def _tostring_object_info(self, _seen: frozenset[int] = frozenset()) -> str:
        include = self._tostring_includes()
        include_forced = self._tostring_includes_forced()
        exclude = self._tostring_excludes()
        exclude_exceptions = self._tostring_exclude_exceptions()

        def is_excluded(k: str) -> bool:
            if k in include_forced or k in exclude_exceptions:
                return False
            if k in exclude:
                return True
            if self._tostring_exclude_private():
                return k.startswith("_")
            return False

        if len(include) == 1 and include[0] == self._INCLUDE_ALL:
            attribute_dict = dict(self.__dict__)
        else:
            names = set(include) | set(include_forced)
            attribute_dict = {k: getattr(self, k) for k in names if hasattr(self, k) and k != self._INCLUDE_ALL}

        d = {k.strip("_"): v for k, v in attribute_dict.items() if not is_excluded(k)}
        d.update(self._tostring_additional_entries())

        seen = _seen | {id(self)}

        def render(v: Any) -> str:
            if isinstance(v, ToStringMixin):
                if id(v) in seen:
                    return f"{v._tostring_class_name()}[<<]"
                return f"{v._tostring_class_name()}[{v._tostring_object_info(seen)}]"
            return str(v)

        return ", ".join(f"{k}={render(v)}" for k, v in d.items())

    def __str__(self) -> str:
        return f"{self._tostring_class_name()}[{self._tostring_object_info()}]"

    def __repr__(self) -> str:
        info = f"id={id(self)}"
        property_info = self._tostring_object_info()
        if len(property_info) > 0:
            info += ", " + property_info
        return f"{self._tostring_class_name()}[{info}]"
