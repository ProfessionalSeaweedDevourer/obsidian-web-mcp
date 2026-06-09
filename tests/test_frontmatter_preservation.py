"""vault_write must not rewrite YAML frontmatter formatting on merge."""

import json

from obsidian_vault_mcp import config
from obsidian_vault_mcp.tools.write import vault_write


def test_merge_frontmatter_preserves_quotes_and_block_lists(vault_dir):
    path = "fmt.md"
    (config.VAULT_PATH / path).write_text(
        "---\n"
        "title: 'single quoted'\n"
        "tags:\n"
        "  - alpha\n"
        "  - beta\n"
        "pinned: yes\n"
        "---\n"
        "body\n"
    )

    # New frontmatter is carried in the content itself (upstream merge contract).
    new_content = "---\nstatus: draft\n---\nbody\n"
    vault_write(path, new_content, create_dirs=True, merge_frontmatter=True)

    result = (config.VAULT_PATH / path).read_text()
    assert "title: 'single quoted'" in result   # quote style kept
    assert "  - alpha" in result                 # block list kept (not flow)
    assert "pinned: yes" in result               # yes/no not rewritten
    assert "status: draft" in result             # new key merged


def test_merge_aborts_on_malformed_existing_frontmatter(vault_dir):
    """Unparseable existing frontmatter must not be silently overwritten/dropped."""
    path = "broken.md"
    original = "---\nbad: : :\nkeep: me\n---\nexisting body\n"
    (config.VAULT_PATH / path).write_text(original)

    result = json.loads(
        vault_write(path, "---\nstatus: draft\n---\nnew body\n", merge_frontmatter=True)
    )

    assert (config.VAULT_PATH / path).read_text() == original  # left untouched
    assert "error" in result


def test_merge_aborts_on_malformed_new_frontmatter(vault_dir):
    """Malformed new frontmatter must not produce nested '---' or clobber the file."""
    path = "fmt.md"
    original = "---\ntitle: ok\n---\nbody\n"
    (config.VAULT_PATH / path).write_text(original)

    result = json.loads(
        vault_write(path, "---\nbad: : :\n---\nnew\n", merge_frontmatter=True)
    )

    assert (config.VAULT_PATH / path).read_text() == original  # left untouched
    assert "error" in result


def test_merge_overrides_existing_key_value(vault_dir):
    """A new value for an existing key wins, while other keys keep their formatting."""
    path = "fmt.md"
    (config.VAULT_PATH / path).write_text(
        "---\nstatus: 'active'\ntags:\n  - alpha\n  - beta\n---\nbody\n"
    )

    vault_write(path, "---\nstatus: archived\n---\nbody\n", merge_frontmatter=True)

    result = (config.VAULT_PATH / path).read_text()
    assert "status: 'archived'" in result   # value overridden, quote slot kept
    assert "status: 'active'" not in result  # old value gone
    assert "  - alpha" in result             # untouched key keeps block style


def test_merge_bodyless_new_content_keeps_frontmatter(vault_dir):
    """New content with no frontmatter preserves existing frontmatter, replaces body."""
    path = "fmt.md"
    (config.VAULT_PATH / path).write_text(
        "---\ntitle: 'kept'\npinned: yes\n---\nold body\n"
    )

    vault_write(path, "brand new body only\n", merge_frontmatter=True)

    result = (config.VAULT_PATH / path).read_text()
    assert "title: 'kept'" in result      # existing frontmatter preserved
    assert "pinned: yes" in result
    assert "brand new body only" in result  # body replaced
    assert "old body" not in result


def test_no_merge_writes_content_byte_identical(vault_dir):
    """With merge_frontmatter=False the content is written verbatim (no YAML rewrite)."""
    path = "fmt.md"
    (config.VAULT_PATH / path).write_text("---\nold: 1\n---\nold body\n")

    # Forms PyYAML would normalize (yes->true, flow list, quotes) must survive as-is.
    content = "---\nstatus: yes\ntags: [a, b]\nq: 'x'\n---\nbody\n"
    vault_write(path, content, merge_frontmatter=False)

    assert (config.VAULT_PATH / path).read_text() == content
