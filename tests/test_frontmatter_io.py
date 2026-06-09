"""Tests for frontmatter_io.py -- round-trip YAML frontmatter preservation."""

import threading

import pytest
from ruamel.yaml.error import YAMLError

from obsidian_vault_mcp import frontmatter_io


def test_loads_no_frontmatter_returns_empty_metadata():
    """A file with no frontmatter delimiters returns empty metadata and full body."""
    content = "Just body text, no frontmatter.\n"
    metadata, body = frontmatter_io.loads(content)
    assert metadata == {}
    assert body == content


def test_loads_parses_metadata_and_body():
    """A file with frontmatter splits into metadata dict and body."""
    content = "---\nstatus: active\ntype: note\n---\n\nBody text here.\n"
    metadata, body = frontmatter_io.loads(content)
    assert metadata["status"] == "active"
    assert metadata["type"] == "note"
    assert "Body text here." in body


def test_loads_empty_frontmatter_block():
    """A file with empty frontmatter (---\\n---\\n) returns empty metadata."""
    content = "---\n---\nBody after empty frontmatter.\n"
    metadata, body = frontmatter_io.loads(content)
    assert metadata == {}
    assert "Body after empty frontmatter." in body


def test_roundtrip_preserves_quote_styles():
    """Reading and re-dumping unchanged frontmatter produces byte-identical output."""
    content = (
        "---\n"
        "unquoted: value1\n"
        "single: 'value2'\n"
        "double: \"value3\"\n"
        "---\n"
        "Body.\n"
    )
    metadata, body = frontmatter_io.loads(content)
    out = frontmatter_io.dumps(metadata, body)
    assert out == content


def test_roundtrip_preserves_yes_no_booleans():
    """yes/no boolean style is preserved, not normalized to true/false."""
    content = "---\nactive: yes\narchived: no\n---\n\nBody.\n"
    metadata, body = frontmatter_io.loads(content)
    out = frontmatter_io.dumps(metadata, body)
    assert "yes" in out
    assert "no" in out
    assert "true" not in out
    assert "false" not in out


def test_roundtrip_preserves_block_list_style():
    """Block-style lists stay block-style (not flattened to flow style)."""
    content = (
        "---\n"
        "tags:\n"
        "  - alpha\n"
        "  - beta\n"
        "  - gamma\n"
        "---\n"
        "Body.\n"
    )
    metadata, body = frontmatter_io.loads(content)
    out = frontmatter_io.dumps(metadata, body)
    assert out == content


def test_roundtrip_preserves_literal_block_string():
    """Literal-block multi-line strings (|) keep their style and chomping."""
    content = (
        "---\n"
        "description: |\n"
        "  Line one.\n"
        "  Line two.\n"
        "---\n"
        "Body.\n"
    )
    metadata, body = frontmatter_io.loads(content)
    out = frontmatter_io.dumps(metadata, body)
    assert out == content


def test_roundtrip_preserves_comments():
    """Inline comments in frontmatter survive round-trip."""
    content = (
        "---\n"
        "status: active  # current project state\n"
        "priority: 1\n"
        "---\n"
        "Body.\n"
    )
    metadata, body = frontmatter_io.loads(content)
    out = frontmatter_io.dumps(metadata, body)
    assert "# current project state" in out


def test_update_field_preserves_other_formatting():
    """Updating one field does not reformat unrelated fields."""
    content = (
        "---\n"
        "status: 'active'\n"
        "tags:\n"
        "  - alpha\n"
        "  - beta\n"
        "priority: 1\n"
        "---\n"
        "Body.\n"
    )
    metadata, body = frontmatter_io.loads(content)
    metadata["priority"] = 2
    out = frontmatter_io.dumps(metadata, body)
    assert "status: 'active'" in out
    assert "- alpha" in out
    assert "- beta" in out
    assert "priority: 2" in out


def test_update_existing_quoted_value_keeps_quote_style():
    """Overwriting an existing key's value retains that key's original quote style."""
    metadata, body = frontmatter_io.loads("---\nstatus: 'active'\npriority: 1\n---\nx\n")
    metadata["status"] = "draft"
    out = frontmatter_io.dumps(metadata, body)
    assert "status: 'draft'" in out   # value changed, single-quote slot kept
    assert "priority: 1" in out


def test_dumps_no_frontmatter_writes_body_unchanged():
    """Empty metadata produces the body only, no delimiters."""
    body = "Just plain body content.\n"
    out = frontmatter_io.dumps({}, body)
    assert out == body


def test_dumps_ends_with_newline_after_body():
    """Output preserves body exactly as passed (no added/stripped trailing newlines)."""
    content = "---\nkey: value\n---\nBody without trailing newline"
    metadata, body = frontmatter_io.loads(content)
    out = frontmatter_io.dumps(metadata, body)
    assert out.endswith("Body without trailing newline")


def test_roundtrip_preserves_long_scalar_value():
    """A scalar longer than ruamel's default fold width is not wrapped."""
    long_value = "x" * 5000
    content = f"---\nurl: {long_value}\n---\nBody.\n"
    metadata, body = frontmatter_io.loads(content)
    out = frontmatter_io.dumps(metadata, body)
    assert out == content


def test_roundtrip_preserves_crlf_line_endings():
    """A uniformly CRLF file round-trips byte-identically (no mixed endings)."""
    content = (
        "---\r\n"
        "title: hello\r\n"
        "tags:\r\n"
        "  - a\r\n"
        "  - b\r\n"
        "---\r\n"
        "Body line one.\r\n"
        "Body line two.\r\n"
    )
    metadata, body = frontmatter_io.loads(content)
    out = frontmatter_io.dumps(metadata, body)
    assert out == content


def test_dumps_matches_body_line_endings_no_mixed():
    """The YAML block and delimiters use the same newline as the body (no mixing)."""
    metadata, _ = frontmatter_io.loads("---\nkey: value\n---\nx\n")
    out = frontmatter_io.dumps(metadata, "CRLF body\r\n")
    # No lone LF should appear outside the body; every newline is CRLF.
    assert "\n" in out
    assert out.replace("\r\n", "") .count("\n") == 0


def test_concurrent_roundtrips_are_isolated():
    """Concurrent loads/dumps must not corrupt each other via shared parser state."""
    samples = [
        (
            f"---\nidx: {i}\nname: 'item-{i}'\ntags:\n  - a{i}\n  - b{i}\n"
            f"desc: |\n  line one {i}\n  line two {i}\n---\nbody {i}\n"
        )
        for i in range(8)
    ]
    errors: list[str] = []
    mismatches: list[str] = []

    def worker(i: int) -> None:
        src = samples[i % len(samples)]
        for _ in range(200):
            try:
                metadata, body = frontmatter_io.loads(src)
                out = frontmatter_io.dumps(metadata, body)
                if out != src:
                    mismatches.append(out[:80])
            except Exception as e:  # noqa: BLE001 - capture any corruption
                errors.append(repr(e))

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(16)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == [], f"{len(errors)} errors under concurrency, e.g. {errors[:3]}"
    assert mismatches == [], f"{len(mismatches)} corrupted round-trips"


def test_loads_raises_on_malformed_frontmatter():
    """Frontmatter with delimiters but invalid YAML raises, not silently empty."""
    with pytest.raises(YAMLError):
        frontmatter_io.loads("---\nbad: : :\nkeep: me\n---\nbody\n")


def test_loads_no_delimiters_still_returns_empty():
    """Plain text without delimiters is not malformed -- returns empty, no raise."""
    metadata, body = frontmatter_io.loads("plain text, no frontmatter\n")
    assert metadata == {}
    assert body == "plain text, no frontmatter\n"
