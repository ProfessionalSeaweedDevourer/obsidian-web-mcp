"""YAML frontmatter I/O that preserves formatting across round-trips.

Uses ruamel.yaml in round-trip mode so quote style, comments, block/flow
style, boolean forms (yes/no vs true/false), and key order survive a
load-then-dump cycle. PyYAML (via python-frontmatter) normalizes all of
these, which rewrites users' carefully-formatted frontmatter on every
update.
"""

from __future__ import annotations

import io
import re
import sys

from ruamel.yaml import YAML

_FRONTMATTER_RE = re.compile(
    r"\A---[ \t]*\r?\n(.*?)(?:\r?\n)?---[ \t]*\r?\n?(.*)\Z",
    re.DOTALL,
)


def _make_yaml() -> YAML:
    """Build a fresh round-trip YAML handler.

    A handler is created per call rather than shared at module scope: ruamel's
    YAML instances hold mutable parser/emitter state and are not thread-safe,
    so a shared instance corrupts output when sync MCP tools run concurrently
    in the server's threadpool.
    """
    yaml = YAML(typ="rt")
    yaml.preserve_quotes = True
    # Disable line wrapping entirely: any finite width re-folds long scalars
    # (URLs, descriptions) on dump, which is exactly the formatting churn this
    # module exists to avoid. sys.maxsize means "never auto-wrap".
    yaml.width = sys.maxsize
    yaml.indent(mapping=2, sequence=4, offset=2)
    return yaml


def loads(content: str) -> tuple[dict, str]:
    """Parse a markdown file into (metadata, body).

    When frontmatter is present, metadata is a ruamel.yaml CommentedMap that
    retains the original formatting for round-trip dumping. When no frontmatter
    delimiters are present (or the block is empty), returns ({}, content).

    Raises ruamel.yaml.error.YAMLError when delimiters ARE present but the YAML
    inside is malformed. Callers must not treat that as "no frontmatter":
    swallowing it would silently drop the user's existing keys on a merge.
    """
    match = _FRONTMATTER_RE.match(content)
    if match is None:
        return {}, content

    raw_yaml, body = match.group(1), match.group(2)

    if raw_yaml.strip() == "":
        return {}, body

    # Ensure the YAML text ends with a newline so ruamel correctly parses
    # trailing-newline chomping on literal/folded block scalars at EOF.
    if not raw_yaml.endswith("\n"):
        raw_yaml += "\n"

    metadata = _make_yaml().load(raw_yaml)

    if metadata is None:
        return {}, body

    return metadata, body


def dumps(metadata: dict | None, body: str) -> str:
    """Serialize (metadata, body) back to a markdown file.

    Empty metadata writes the body unchanged (no delimiters). The YAML block
    and its delimiters adopt the body's line-ending style (CRLF if the body
    uses CRLF, else LF) so the result never mixes endings: ruamel always emits
    LF, which would otherwise leave a CRLF body with an LF frontmatter block.
    """
    if not metadata:
        return body

    buf = io.StringIO()
    _make_yaml().dump(metadata, buf)
    yaml_text = buf.getvalue()

    newline = "\r\n" if "\r\n" in body else "\n"
    if newline != "\n":
        yaml_text = yaml_text.replace("\n", newline)

    return f"---{newline}{yaml_text}---{newline}{body}"
