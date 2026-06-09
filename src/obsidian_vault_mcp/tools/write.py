"""Write tools for the Obsidian vault MCP server."""

import difflib
import logging
import re

import frontmatter

from ..models import normalize_edit_aliases
from ..serialization import dumps
from ..vault import resolve_vault_path, read_file, write_file_atomic

logger = logging.getLogger(__name__)


def vault_write(path: str, content: str, create_dirs: bool = True, merge_frontmatter: bool = False) -> str:
    """Write a file to the vault, optionally merging frontmatter with existing content."""
    try:
        resolve_vault_path(path)

        if merge_frontmatter:
            try:
                existing_content, _ = read_file(path)
                existing_post = frontmatter.loads(existing_content)
                new_post = frontmatter.loads(content)

                merged_meta = dict(existing_post.metadata)
                merged_meta.update(new_post.metadata)

                new_post.metadata = merged_meta
                content = frontmatter.dumps(new_post)
            except FileNotFoundError:
                pass
            except Exception as e:
                logger.warning(f"Frontmatter merge failed for {path}, writing as-is: {e}")

        is_new, size = write_file_atomic(path, content, create_dirs=create_dirs)

        return dumps({"path": path, "created": is_new, "size": size})
    except ValueError as e:
        return dumps({"error": str(e), "path": path})
    except Exception as e:
        logger.error(f"vault_write error for {path}: {e}")
        return dumps({"error": str(e), "path": path})


def _unified_diff(path: str, before: str, after: str) -> str:
    """Return a compact unified diff for an edit preview or result."""
    return "".join(difflib.unified_diff(
        before.splitlines(keepends=True),
        after.splitlines(keepends=True),
        fromfile=f"{path} before",
        tofile=f"{path} after",
        lineterm="",
    ))


# A near-miss hint is only emitted when the closest line shares at least this
# fraction of old_text. Below it the "hint" is noise (unrelated input, blank
# lines) and would mislead more than help.
_NEAR_MISS_MIN_SIMILARITY = 0.5


def _normalize_edit_aliases(edit: dict) -> tuple[dict | None, str | None]:
    """Apply the shared alias normalization, returning (normalized, error).

    This guards the direct-dict callers of vault_edit (tests, internal use).
    The MCP path is already canonicalized: server.py validates each edit as a
    VaultEditOperationInput and passes model_dump() output, so this is a no-op
    there rather than dead code.
    """
    try:
        return normalize_edit_aliases(edit), None
    except ValueError as exc:
        return None, str(exc)


def _find_near_miss(content: str, old_text: str) -> dict | None:
    """Return the document line closest to old_text for a zero-match edit.

    Line-scoped: one SequenceMatcher pass per line, so work grows with file
    size rather than quadratically across lines (for a fixed old_text). The
    similarity is the fraction of old_text matched on the closest line, which
    separates a one-character typo (high) from text that is simply absent
    (low). Returns None when the best line is below the similarity floor, so
    unrelated input produces no misleading hint.
    """
    lines = content.splitlines()
    if not lines or not old_text:
        return None

    matcher = difflib.SequenceMatcher(a=old_text)
    best_similarity, best_index = 0.0, 0
    for index, line in enumerate(lines):
        matcher.set_seq2(line)
        matched = sum(block.size for block in matcher.get_matching_blocks())
        similarity = matched / len(old_text)
        if similarity > best_similarity:
            best_similarity, best_index = similarity, index

    if best_similarity < _NEAR_MISS_MIN_SIMILARITY:
        return None

    return {
        "line_number": best_index + 1,
        "line": lines[best_index],
        "similarity": round(best_similarity, 2),
    }


def _normalized_pattern(old_text: str) -> "re.Pattern | None":
    """Compile old_text into a whitespace-tolerant regex, or None if it is blank.

    Splitting on whitespace runs and rejoining the escaped tokens with ``\\s+``
    makes the match ignore how much whitespace (spaces, tabs, line breaks)
    separates the words and any leading/trailing whitespace, while keeping the
    word sequence exact. The tokens are literals joined by a single quantifier,
    so there is no nested repetition to backtrack on.
    """
    tokens = old_text.split()
    if not tokens:
        return None
    return re.compile(r"\s+".join(re.escape(token) for token in tokens))


def _malformed_edit(kind: str, why: str) -> dict:
    """Build the resolve result for an edit that is itself invalid."""
    return {"kind": kind, "malformed": why, "count": 0, "near_miss": None, "result": None}


def _resolve_edit(content: str, edit: dict) -> dict:
    """Resolve one normalized edit against ``content`` without mutating it.

    Returns a dict describing the outcome so both the dry-run and apply paths
    share one matching implementation:

    - ``kind``: ``"replace"`` or ``"insert"`` (drives the error wording).
    - ``malformed``: a message when the edit itself is invalid (empty old_text,
      insert without text/anchor, whitespace-only normalized old_text); else None.
    - ``count``: occurrences of the matched text/anchor in ``content``.
    - ``near_miss``: closest-line hint, present only when ``count == 0``.
    - ``result``: the content after applying this edit, present only when
      ``count == 1``. It is computed from the ``content`` passed here.
    """
    insert_after = edit.get("insert_after")
    insert_before = edit.get("insert_before")

    if insert_after is not None or insert_before is not None:
        anchor = insert_after if insert_after is not None else insert_before
        text = edit.get("text")
        if text is None:
            return _malformed_edit("insert", "is an insert without text")
        if not anchor:
            return _malformed_edit("insert", "has an empty insert anchor")

        count = content.count(anchor)
        result = None
        if count == 1:
            cut = content.index(anchor)
            if insert_after is not None:
                cut += len(anchor)
            result = content[:cut] + text + content[cut:]
        return {
            "kind": "insert", "malformed": None, "count": count,
            "near_miss": _find_near_miss(content, anchor) if count == 0 else None,
            "result": result,
        }

    # Replace operation.
    old_text = edit.get("old_text", "")
    new_text = edit.get("new_text", "")
    if not old_text:
        # An empty old_text would make str.count() report a phantom match for
        # every position; reject it as the malformed edit it is.
        return _malformed_edit("replace", "has no old_text to match")

    if edit.get("match") == "normalized":
        pattern = _normalized_pattern(old_text)
        if pattern is None:
            return _malformed_edit("replace", "has a whitespace-only old_text")
        matches = list(pattern.finditer(content))
        count = len(matches)
        result = None
        if count == 1:
            start, end = matches[0].span()
            result = content[:start] + new_text + content[end:]
        return {
            "kind": "replace", "malformed": None, "count": count,
            "near_miss": _find_near_miss(content, old_text) if count == 0 else None,
            "result": result,
        }

    count = content.count(old_text)
    return {
        "kind": "replace", "malformed": None, "count": count,
        "near_miss": _find_near_miss(content, old_text) if count == 0 else None,
        "result": content.replace(old_text, new_text, 1) if count == 1 else None,
    }


def _dry_run_report(path: str, original_content: str, normalized_edits: list[dict]) -> str:
    """Preview edits without writing, reporting each edit's match outcome.

    Unlike the apply path this does not fail fast: every edit's match count
    (0, 1, or many) is reported so one response surfaces all mismatches. Counts
    are measured independently against the original document.

    When every edit matches exactly once, a diff preview is produced by
    applying the edits sequentially against an evolving copy. For chained edits
    (one edit's output feeds another's match) that sequential pass can still
    fail even though the per-edit counts looked unique; in that case the diff is
    omitted and the apply path's fail-fast remains the safety net.
    """
    match_counts = []
    all_unique = True
    for index, edit in enumerate(normalized_edits):
        info = _resolve_edit(original_content, edit)
        entry = {"index": index}
        if info["malformed"]:
            entry["count"] = 0
            entry["error"] = info["malformed"]
            all_unique = False
            match_counts.append(entry)
            continue
        entry["count"] = info["count"]
        if info["count"] == 0 and info["near_miss"]:
            entry["near_miss"] = info["near_miss"]
        if info["count"] != 1:
            all_unique = False
        match_counts.append(entry)

    diff = ""
    size = len(original_content.encode("utf-8"))
    if all_unique:
        preview = original_content
        for edit in normalized_edits:
            step = _resolve_edit(preview, edit)
            if step["count"] != 1 or step["malformed"] or step["result"] is None:
                preview = original_content
                break
            preview = step["result"]
        diff = _unified_diff(path, original_content, preview)
        size = len(preview.encode("utf-8"))

    return dumps({
        "path": path,
        "changed": False,
        "dry_run": True,
        "diff": diff,
        "match_counts": match_counts,
        "edits_applied": len(normalized_edits) if all_unique else 0,
        "size": size,
    })


def vault_edit(path: str, edits: list[dict], dry_run: bool = False) -> str:
    """Apply exact text replacements to an existing file without resending the full body."""
    try:
        content, _ = read_file(path)
        original_content = content

        # Normalize aliases up front; an alias conflict fails fast in either mode.
        normalized_edits = []
        for index, edit in enumerate(edits):
            normalized_edit, alias_error = _normalize_edit_aliases(edit)
            if alias_error:
                return dumps({
                    "error": f"Edit {index}: {alias_error}",
                    "path": path,
                    "changed": False,
                    "dry_run": dry_run,
                    "diff": "",
                    "edits_applied": 0,
                    "size": len(original_content.encode("utf-8")),
                })
            normalized_edits.append(normalized_edit)

        if dry_run:
            return _dry_run_report(path, original_content, normalized_edits)

        for index, normalized_edit in enumerate(normalized_edits):
            info = _resolve_edit(content, normalized_edit)

            if info["malformed"]:
                return dumps({
                    "error": f"Edit {index} {info['malformed']}",
                    "path": path,
                    "changed": False,
                    "dry_run": dry_run,
                    "diff": "",
                    "edits_applied": 0,
                    "size": len(original_content.encode("utf-8")),
                })

            if info["count"] != 1:
                target = "anchor" if info["kind"] == "insert" else "old_text"
                payload = {
                    "error": (
                        f"Edit {index} {target} must match exactly once; "
                        f"found {info['count']} matches"
                    ),
                    "path": path,
                    "changed": False,
                    "dry_run": dry_run,
                    "diff": "",
                    "edits_applied": 0,
                    "size": len(original_content.encode("utf-8")),
                }
                if info["count"] == 0 and info["near_miss"]:
                    payload["near_miss"] = info["near_miss"]
                return dumps(payload)

            content = info["result"]

        diff = _unified_diff(path, original_content, content)
        size = len(content.encode("utf-8"))

        changed = content != original_content
        if changed:
            write_file_atomic(path, content, create_dirs=False)

        return dumps({
            "path": path,
            "changed": changed,
            "dry_run": False,
            "diff": diff,
            "edits_applied": len(edits),
            "size": size,
        })
    except ValueError as e:
        return dumps({
            "error": str(e),
            "path": path,
            "changed": False,
            "dry_run": dry_run,
            "diff": "",
            "edits_applied": 0,
            "size": 0,
        })
    except FileNotFoundError:
        return dumps({
            "error": f"File not found: {path}",
            "path": path,
            "changed": False,
            "dry_run": dry_run,
            "diff": "",
            "edits_applied": 0,
            "size": 0,
        })
    except Exception as e:
        logger.error(f"vault_edit error for {path}: {e}")
        return dumps({
            "error": str(e),
            "path": path,
            "changed": False,
            "dry_run": dry_run,
            "diff": "",
            "edits_applied": 0,
            "size": 0,
        })


def vault_append(
    path: str,
    content: str,
    separator: str = "\n\n",
    create_dirs: bool = True,
) -> str:
    """Append content to a file without requiring the caller to send the full body."""
    try:
        resolve_vault_path(path)

        created = False
        try:
            existing_content, _ = read_file(path)
        except FileNotFoundError:
            existing_content = ""
            created = True

        if created or not existing_content:
            new_content = content
        elif content:
            new_content = f"{existing_content}{separator}{content}"
        else:
            new_content = existing_content

        changed = new_content != existing_content
        if changed:
            _, size = write_file_atomic(path, new_content, create_dirs=create_dirs)
        else:
            size = len(existing_content.encode("utf-8"))

        return dumps({
            "path": path,
            "changed": changed,
            "created": created,
            "appended": not created and changed,
            "size": size,
        })
    except ValueError as e:
        return dumps({
            "error": str(e),
            "path": path,
            "changed": False,
            "created": False,
            "appended": False,
            "size": 0,
        })
    except Exception as e:
        logger.error(f"vault_append error for {path}: {e}")
        return dumps({
            "error": str(e),
            "path": path,
            "changed": False,
            "created": False,
            "appended": False,
            "size": 0,
        })


def vault_batch_frontmatter_update(updates: list[dict]) -> str:
    """Update frontmatter fields on multiple files without changing body content."""
    results = []

    for update in updates:
        file_path = update.get("path", "")
        fields = update.get("fields", {})

        try:
            content, _ = read_file(file_path)
            post = frontmatter.loads(content)

            for key, value in fields.items():
                post.metadata[key] = value

            new_content = frontmatter.dumps(post)
            write_file_atomic(file_path, new_content, create_dirs=False)

            results.append({"path": file_path, "updated": True})
        except FileNotFoundError:
            results.append({"path": file_path, "updated": False, "error": "File not found"})
        except ValueError as e:
            results.append({"path": file_path, "updated": False, "error": str(e)})
        except Exception as e:
            results.append({"path": file_path, "updated": False, "error": str(e)})

    return dumps({"results": results})
