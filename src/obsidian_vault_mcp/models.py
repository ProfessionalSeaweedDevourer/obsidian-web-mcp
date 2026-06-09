"""Pydantic input models for obsidian-vault-mcp tool endpoints."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .config import (
    CONTEXT_LINES,
    DEFAULT_SEARCH_RESULTS,
    MAX_BATCH_SIZE,
    MAX_CONTENT_SIZE,
    MAX_LIST_DEPTH,
    MAX_SEARCH_RESULTS,
)


class VaultReadInput(BaseModel):
    """Read a single file from the vault."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    path: str = Field(
        ...,
        description="Relative path from vault root (e.g. 'projects/acme/notes.md')",
        min_length=1,
        max_length=500,
    )


class VaultWriteInput(BaseModel):
    """Write or overwrite a file in the vault."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    path: str = Field(
        ...,
        description="Relative path from vault root",
        min_length=1,
        max_length=500,
    )
    content: str = Field(
        ...,
        description="Full file content to write",
        max_length=MAX_CONTENT_SIZE,
    )
    create_dirs: bool = Field(
        default=True,
        description="Create parent directories if they don't exist",
    )
    merge_frontmatter: bool = Field(
        default=False,
        description="If true, merge YAML frontmatter with existing file's frontmatter instead of replacing",
    )


# Aliases accepted for each canonical edit field. old_str/new_str mirror the
# str_replace_editor tool; old/new are the shorthands models reach for most.
_EDIT_FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "old_text": ("old_str", "old"),
    "new_text": ("new_str", "new"),
}


def normalize_edit_aliases(data: Any) -> Any:
    """Map edit-field aliases onto their canonical old_text/new_text keys.

    Shared by the pydantic model (the MCP schema path) and the write tool (the
    direct-call path) so both accept the same alias set. Raises ValueError,
    naming the offending keys, if a canonical field and one of its aliases (or
    two aliases) are supplied together.
    """
    if not isinstance(data, dict):
        return data

    normalized = dict(data)
    for canonical, aliases in _EDIT_FIELD_ALIASES.items():
        present = [key for key in (canonical, *aliases) if key in normalized]
        if len(present) > 1:
            joined = ", ".join(f"'{key}'" for key in present)
            raise ValueError(f"Use only one of {joined}, not several")
        if present and present[0] != canonical:
            normalized[canonical] = normalized.pop(present[0])

    return normalized


class VaultEditOperationInput(BaseModel):
    """One edit: replace an exact fragment, or insert text at an anchor.

    A *replace* edit supplies old_text (+ new_text); set match='normalized' to
    ignore whitespace/EOL differences between old_text and the file. An *insert*
    edit supplies insert_after or insert_before (a short anchor) plus text, so a
    block can be inserted or moved without duplicating the surrounding body.
    """

    model_config = ConfigDict(str_strip_whitespace=False, extra="forbid")

    @model_validator(mode="before")
    @classmethod
    def normalize_str_replace_aliases(cls, data):
        return normalize_edit_aliases(data)

    old_text: str | None = Field(
        default=None,
        description="Exact existing text fragment to replace; must appear exactly once",
        max_length=MAX_CONTENT_SIZE,
    )
    new_text: str | None = Field(
        default=None,
        description="Replacement text for old_text (use \"\" to delete the matched text)",
        max_length=MAX_CONTENT_SIZE,
    )
    match: Literal["exact", "normalized"] = Field(
        default="exact",
        description="old_text matching: 'exact' byte-for-byte, or 'normalized' to ignore whitespace/EOL differences",
    )
    insert_after: str | None = Field(
        default=None,
        description="Anchor text to insert after; must appear exactly once",
        max_length=MAX_CONTENT_SIZE,
    )
    insert_before: str | None = Field(
        default=None,
        description="Anchor text to insert before; must appear exactly once",
        max_length=MAX_CONTENT_SIZE,
    )
    text: str | None = Field(
        default=None,
        description="Text to insert at the anchor (required for insert_after/insert_before)",
        max_length=MAX_CONTENT_SIZE,
    )

    @model_validator(mode="after")
    def _validate_operation_shape(self):
        is_insert = self.insert_after is not None or self.insert_before is not None
        is_replace = self.old_text is not None or self.new_text is not None

        if is_insert and is_replace:
            raise ValueError(
                "an edit is either a replace (old_text/new_text) or an insert "
                "(insert_after/insert_before), not both"
            )
        if self.insert_after is not None and self.insert_before is not None:
            raise ValueError("use only one of 'insert_after' or 'insert_before'")

        if is_insert:
            anchor = self.insert_after if self.insert_after is not None else self.insert_before
            if not anchor:
                raise ValueError("insert anchor must be non-empty")
            if self.text is None:
                raise ValueError("insert_after/insert_before requires 'text'")
            return self

        # Replace operation.
        if not self.old_text:
            raise ValueError("a replace edit requires a non-empty 'old_text'")
        if self.new_text is None:
            raise ValueError("a replace edit requires 'new_text'")
        return self


class VaultEditInput(BaseModel):
    """Patch an existing file with exact text replacements."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    path: str = Field(
        ...,
        description="Relative path from vault root",
        min_length=1,
        max_length=500,
    )
    edits: list[VaultEditOperationInput] = Field(
        ...,
        description="Ordered exact text replacements to apply without resending the full file",
        min_length=1,
        max_length=MAX_BATCH_SIZE,
    )
    dry_run: bool = Field(
        default=False,
        description="Preview the patch and diff without writing the file",
    )


class VaultAppendInput(BaseModel):
    """Append content to a file without resending the existing body."""

    model_config = ConfigDict(str_strip_whitespace=False, extra="forbid")

    path: str = Field(
        ...,
        description="Relative path from vault root",
        min_length=1,
        max_length=500,
    )
    content: str = Field(
        ...,
        description="Content to append or write if the file does not exist",
        max_length=MAX_CONTENT_SIZE,
    )
    separator: str = Field(
        default="\n\n",
        description="Text inserted between existing content and appended content",
        max_length=100,
    )
    create_dirs: bool = Field(
        default=True,
        description="Create parent directories if they don't exist",
    )


class VaultListInput(BaseModel):
    """List files and directories under a vault path."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    path: str = Field(
        default="",
        description="Relative directory path from vault root; empty string for root",
        max_length=500,
    )
    depth: int = Field(
        default=1,
        ge=1,
        le=MAX_LIST_DEPTH,
        description="How many levels deep to recurse",
    )
    include_files: bool = Field(
        default=True,
        description="Include files in the listing",
    )
    include_dirs: bool = Field(
        default=True,
        description="Include directories in the listing",
    )
    pattern: str | None = Field(
        default=None,
        description="Optional glob pattern to filter results (e.g. '*.md')",
        max_length=100,
    )


class VaultMoveInput(BaseModel):
    """Move or rename a file/directory within the vault."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    source: str = Field(
        ...,
        description="Current relative path of the file or directory",
        min_length=1,
        max_length=500,
    )
    destination: str = Field(
        ...,
        description="New relative path for the file or directory",
        min_length=1,
        max_length=500,
    )
    create_dirs: bool = Field(
        default=True,
        description="Create destination parent directories if they don't exist",
    )


class VaultDeleteInput(BaseModel):
    """Delete a file from the vault."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    path: str = Field(
        ...,
        description="Relative path of the file to delete",
        min_length=1,
        max_length=500,
    )
    confirm: bool = Field(
        ...,
        description="Must be true to execute deletion -- safety gate to prevent accidental deletes",
    )


class VaultSearchInput(BaseModel):
    """Full-text search across vault files."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    query: str = Field(
        ...,
        description="Search string to find in file contents",
        min_length=1,
        max_length=200,
    )
    path_prefix: str | None = Field(
        default=None,
        description="Limit search to files under this directory prefix",
        max_length=500,
    )
    file_pattern: str = Field(
        default="*.md",
        description="Glob pattern for files to search (e.g. '*.md', '*.canvas')",
        max_length=50,
    )
    max_results: int = Field(
        default=DEFAULT_SEARCH_RESULTS,
        ge=1,
        le=MAX_SEARCH_RESULTS,
        description="Maximum number of matching files to return",
    )
    context_lines: int = Field(
        default=CONTEXT_LINES,
        ge=0,
        le=10,
        description="Number of lines of context to show around each match",
    )


class VaultSearchFrontmatterInput(BaseModel):
    """Search vault files by YAML frontmatter field values."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    field: str = Field(
        ...,
        description="Frontmatter field name to search (e.g. 'status', 'tags', 'publish-date')",
        min_length=1,
        max_length=100,
    )
    value: str = Field(
        default="",
        description="Value to match against; ignored when match_type is 'exists'",
        max_length=200,
    )
    match_type: Literal["exact", "contains", "exists"] = Field(
        default="exact",
        description="How to match: 'exact' for equality, 'contains' for substring, 'exists' to check field presence",
    )
    path_prefix: str | None = Field(
        default=None,
        description="Limit search to files under this directory prefix",
        max_length=500,
    )
    max_results: int = Field(
        default=DEFAULT_SEARCH_RESULTS,
        ge=1,
        le=MAX_SEARCH_RESULTS,
        description="Maximum number of matching files to return",
    )


class VaultBatchReadInput(BaseModel):
    """Read multiple vault files in a single request."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    paths: list[str] = Field(
        ...,
        description="List of relative paths to read",
        min_length=1,
        max_length=MAX_BATCH_SIZE,
    )
    include_content: bool = Field(
        default=True,
        description="If false, return metadata only (frontmatter, size) without file body",
    )


class VaultBatchFrontmatterUpdateInput(BaseModel):
    """Update YAML frontmatter on multiple files in one request."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    updates: list[dict] = Field(
        ...,
        description="List of updates, each a dict with 'path' (str) and 'fields' (dict of key-value pairs to set)",
        min_length=1,
        max_length=MAX_BATCH_SIZE,
    )

    @field_validator("updates")
    @classmethod
    def validate_updates(cls, v: list[dict]) -> list[dict]:
        for i, item in enumerate(v):
            if "path" not in item or not isinstance(item["path"], str):
                raise ValueError(f"updates[{i}] must contain a 'path' key with a string value")
            if "fields" not in item or not isinstance(item["fields"], dict):
                raise ValueError(f"updates[{i}] must contain a 'fields' key with a dict value")
        return v
