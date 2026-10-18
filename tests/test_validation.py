from pathlib import Path

import pytest

from backintime.core.exceptions import ValidationError
from backintime.core.validation import validate_path_within


def test_accepts_path_inside_root(tmp_path: Path) -> None:
    root = tmp_path / "allowed"
    candidate = root / "documents" / "file.txt"

    result = validate_path_within(candidate, root)

    assert result == candidate.resolve()


def test_rejects_path_outside_root(tmp_path: Path) -> None:
    root = tmp_path / "allowed"
    outside = tmp_path / "outside" / "file.txt"

    with pytest.raises(ValidationError):
        validate_path_within(outside, root)


def test_rejects_root_by_default(tmp_path: Path) -> None:
    root = tmp_path / "allowed"

    with pytest.raises(ValidationError):
        validate_path_within(root, root)


def test_allows_root_when_explicitly_requested(
    tmp_path: Path,
) -> None:
    root = tmp_path / "allowed"

    result = validate_path_within(root, root, allow_root=True)

    assert result == root.resolve()


def test_rejects_null_character_in_path(tmp_path: Path) -> None:
    root = tmp_path / "allowed"
    invalid_path = Path(str(root) + "\0invalid")

    with pytest.raises(ValidationError):
        validate_path_within(invalid_path, root)
