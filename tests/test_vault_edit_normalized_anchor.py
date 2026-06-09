"""Tests for the remaining High-priority vault_edit friction items.

Feature A: ``match: "normalized"`` on a replace edit, so a whitespace/EOL
    difference between old_text and the document no longer fails the match.
    Solves friction #2 (long-anchor fragility): a section anchor need not
    reproduce internal whitespace and line breaks byte-for-byte.

Feature B: anchor inserts (``insert_after`` / ``insert_before`` + ``text``),
    so inserting or moving a block does not require duplicating the opposite
    block's full body into new_text. Solves friction #3 (block move is not
    first-class).
"""

import asyncio
import json

import pytest

from obsidian_vault_mcp.models import VaultEditOperationInput
from obsidian_vault_mcp.server import mcp
from obsidian_vault_mcp.tools.write import vault_edit


def _vault_edit_input_schema() -> dict:
    async def _collect() -> dict:
        for tool in await mcp.list_tools():
            if tool.name == "vault_edit":
                return tool.inputSchema
        raise AssertionError("vault_edit tool not registered")

    return asyncio.run(_collect())


# --- Feature A: normalized matching -----------------------------------------

def test_normalized_match_ignores_internal_whitespace_runs(vault_dir):
    """A single space in old_text matches a run of spaces in the document."""
    (vault_dir / "n.md").write_text("---\nx: 1\n---\n\nalpha    beta gamma\n")

    result = json.loads(vault_edit(
        "n.md",
        [{"old_text": "alpha beta", "new_text": "ALPHABETA", "match": "normalized"}],
    ))

    assert "error" not in result, result
    assert result["changed"] is True
    body = (vault_dir / "n.md").read_text()
    assert "ALPHABETA gamma" in body
    assert "alpha" not in body


def test_normalized_match_spans_line_breaks(vault_dir):
    """old_text written on one line matches text split across an EOL."""
    (vault_dir / "n.md").write_text("---\nx: 1\n---\n\nline one\nline two\nrest\n")

    result = json.loads(vault_edit(
        "n.md",
        [{"old_text": "line one line two", "new_text": "MERGED", "match": "normalized"}],
    ))

    assert "error" not in result, result
    body = (vault_dir / "n.md").read_text()
    assert "MERGED\nrest" in body


def test_normalized_match_tolerates_leading_trailing_whitespace(vault_dir):
    """Surrounding whitespace in old_text is ignored under normalized match."""
    (vault_dir / "n.md").write_text("---\nx: 1\n---\n\nkeep some content keep\n")

    result = json.loads(vault_edit(
        "n.md",
        [{"old_text": "   some content   ", "new_text": "X", "match": "normalized"}],
    ))

    assert "error" not in result, result
    body = (vault_dir / "n.md").read_text()
    assert "keep X keep" in body


def test_exact_match_remains_the_default(vault_dir):
    """Without match=normalized, a whitespace difference still fails (no silent fuzz)."""
    (vault_dir / "n.md").write_text("---\nx: 1\n---\n\nalpha    beta gamma\n")
    before = (vault_dir / "n.md").read_text()

    result = json.loads(vault_edit(
        "n.md",
        [{"old_text": "alpha beta", "new_text": "X"}],  # exact, single space
    ))

    assert "error" in result
    assert result["changed"] is False
    assert (vault_dir / "n.md").read_text() == before


def test_normalized_match_still_enforces_uniqueness(vault_dir):
    """Two normalized-equal occurrences are ambiguous and must error, not pick one."""
    (vault_dir / "n.md").write_text("---\nx: 1\n---\n\nfoo  bar\nfoo bar\n")
    before = (vault_dir / "n.md").read_text()

    result = json.loads(vault_edit(
        "n.md",
        [{"old_text": "foo bar", "new_text": "X", "match": "normalized"}],
    ))

    assert "error" in result
    assert "2" in result["error"]
    assert result["changed"] is False
    assert (vault_dir / "n.md").read_text() == before


def test_normalized_match_replacement_is_verbatim(vault_dir):
    """new_text is inserted verbatim; only the matched span is replaced."""
    (vault_dir / "n.md").write_text("---\nx: 1\n---\n\npre alpha\n\nbeta post\n")

    result = json.loads(vault_edit(
        "n.md",
        [{"old_text": "alpha beta", "new_text": "A   B", "match": "normalized"}],
    ))

    assert "error" not in result, result
    body = (vault_dir / "n.md").read_text()
    assert "pre A   B post" in body


def test_normalized_match_whitespace_only_old_text_is_rejected(vault_dir):
    """old_text that is only whitespace has no tokens to match and must error."""
    (vault_dir / "n.md").write_text("---\nx: 1\n---\n\nalpha beta\n")
    before = (vault_dir / "n.md").read_text()

    result = json.loads(vault_edit(
        "n.md",
        [{"old_text": "   ", "new_text": "X", "match": "normalized"}],
    ))

    assert "error" in result
    assert result["changed"] is False
    assert (vault_dir / "n.md").read_text() == before


# --- Feature B: anchor inserts ----------------------------------------------

def test_insert_after_places_text_immediately_after_anchor(vault_dir):
    """insert_after keeps the anchor and puts text right after it, no duplication."""
    (vault_dir / "n.md").write_text("# Title\n\nbody text\n")

    result = json.loads(vault_edit(
        "n.md",
        [{"insert_after": "# Title", "text": "\n\nNEW SECTION"}],
    ))

    assert "error" not in result, result
    assert result["changed"] is True
    assert (vault_dir / "n.md").read_text() == "# Title\n\nNEW SECTION\n\nbody text\n"


def test_insert_before_places_text_immediately_before_anchor(vault_dir):
    """insert_before keeps the anchor and puts text right before it."""
    (vault_dir / "n.md").write_text("# Title\n\nbody text\n")

    result = json.loads(vault_edit(
        "n.md",
        [{"insert_before": "body text", "text": "PREFIX\n"}],
    ))

    assert "error" not in result, result
    assert (vault_dir / "n.md").read_text() == "# Title\n\nPREFIX\nbody text\n"


def test_insert_anchor_must_match_exactly_once(vault_dir):
    """A non-unique anchor is ambiguous and must error without writing."""
    (vault_dir / "n.md").write_text("## H\nfoo\n## H\nbar\n")
    before = (vault_dir / "n.md").read_text()

    result = json.loads(vault_edit(
        "n.md",
        [{"insert_after": "## H", "text": "X"}],
    ))

    assert "error" in result
    assert result["changed"] is False
    assert (vault_dir / "n.md").read_text() == before


def test_insert_anchor_zero_match_errors_with_near_miss(vault_dir):
    """A missing anchor errors and offers a near-miss hint like a failed replace."""
    (vault_dir / "n.md").write_text("# Title\n\nbody text\n")
    before = (vault_dir / "n.md").read_text()

    result = json.loads(vault_edit(
        "n.md",
        [{"insert_after": "# Titel", "text": "X"}],  # typo
    ))

    assert "error" in result
    assert result["changed"] is False
    assert (vault_dir / "n.md").read_text() == before
    nm = result.get("near_miss")
    assert nm and "# Title" in nm["line"]


def test_insert_requires_text(vault_dir):
    """An insert operation without text is malformed and must error."""
    (vault_dir / "n.md").write_text("# Title\n\nbody\n")
    before = (vault_dir / "n.md").read_text()

    result = json.loads(vault_edit(
        "n.md",
        [{"insert_after": "# Title"}],
    ))

    assert "error" in result
    assert result["changed"] is False
    assert (vault_dir / "n.md").read_text() == before


def test_block_move_via_delete_plus_insert_no_duplication(vault_dir):
    """Reordering two blocks needs only delete + anchor-insert in one call."""
    (vault_dir / "n.md").write_text(
        "# Top\n\n## Backlog\nb1\nb2\n\n## Evening\ne1\ne2\n"
    )

    # Move the Evening block to sit right after "# Top", before Backlog.
    result = json.loads(vault_edit(
        "n.md",
        [
            {"old_text": "\n\n## Evening\ne1\ne2", "new_text": ""},     # delete in place
            {"insert_after": "# Top", "text": "\n\n## Evening\ne1\ne2"},  # reinsert
        ],
    ))

    assert "error" not in result, result
    body = (vault_dir / "n.md").read_text()
    assert body == "# Top\n\n## Evening\ne1\ne2\n\n## Backlog\nb1\nb2\n"
    # No duplicate Evening block.
    assert body.count("## Evening") == 1


def test_dry_run_reports_anchor_match_count_for_insert(vault_dir):
    """dry_run surfaces the anchor match count for an insert without writing."""
    (vault_dir / "n.md").write_text("# Title\n\nbody\n")
    before = (vault_dir / "n.md").read_text()

    result = json.loads(vault_edit(
        "n.md",
        [{"insert_after": "# Title", "text": "X"}],
        dry_run=True,
    ))

    assert result["changed"] is False
    matches = result["match_counts"]
    assert matches[0]["count"] == 1
    assert (vault_dir / "n.md").read_text() == before


# --- Model / schema ---------------------------------------------------------

def test_model_accepts_insert_after_form():
    op = VaultEditOperationInput(insert_after="## H", text="body")
    assert op.insert_after == "## H"
    assert op.text == "body"
    assert op.old_text is None


def test_model_accepts_match_normalized_on_replace():
    op = VaultEditOperationInput(old_text="a", new_text="b", match="normalized")
    assert op.match == "normalized"


def test_model_rejects_mixing_replace_and_insert():
    with pytest.raises(ValueError):
        VaultEditOperationInput(old_text="a", new_text="b", insert_after="## H", text="x")


def test_model_rejects_both_insert_directions():
    with pytest.raises(ValueError):
        VaultEditOperationInput(insert_after="## H", insert_before="## J", text="x")


def test_model_rejects_insert_without_text():
    with pytest.raises(ValueError):
        VaultEditOperationInput(insert_after="## H")


def test_model_rejects_invalid_match_value():
    with pytest.raises(ValueError):
        VaultEditOperationInput(old_text="a", new_text="b", match="loose")


def test_schema_exposes_insert_and_match_fields():
    schema = _vault_edit_input_schema()
    blob = json.dumps(schema)
    assert "insert_after" in blob
    assert "insert_before" in blob
    assert "normalized" in blob
