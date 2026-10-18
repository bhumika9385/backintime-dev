import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backintime.api.app import create_app
from backintime.core.config import ConfigStore


def _client(config_path: Path) -> TestClient:
    return TestClient(
        create_app(config_store=ConfigStore(config_path)),
        base_url="http://127.0.0.1:8000",
    )


def _write_headers(etag: str) -> dict[str, str]:
    return {"Origin": "http://127.0.0.1:5173", "If-Match": etag}


def _profile_payload(tmp_path: Path, profile_id: str = "new-profile") -> dict[str, object]:
    return {
        "profile_id": profile_id,
        "name": "Disposable profile",
        "sources": [str(tmp_path / "source")],
        "target": str(tmp_path / "target"),
        "excludes": ["*.cache"],
    }


def _write_profiles(config_path: Path, targets: dict[str, Path]) -> None:
    config_path.write_text(
        json.dumps(
            {
                "version": 1,
                "profiles": [
                    {
                        "profile_id": profile_id,
                        "name": profile_id,
                        "sources": [str(config_path.parent / f"source-{profile_id}")],
                        "target": str(target),
                        "excludes": [],
                    }
                    for profile_id, target in targets.items()
                ],
            }
        ),
        encoding="utf-8",
    )


def _write_snapshot(target: Path, snapshot_id: str) -> Path:
    snapshot = target / snapshot_id
    snapshot.mkdir(parents=True)
    (snapshot / "info.json").write_text(
        json.dumps({"snapshot_id": snapshot_id, "state": "complete"}),
        encoding="utf-8",
    )
    return snapshot


def test_health_returns_version_and_request_id(tmp_path: Path) -> None:
    response = _client(tmp_path / "config.json").get("/api/v1/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.headers["x-request-id"]


def test_system_info_reports_runtime_capabilities(tmp_path: Path) -> None:
    response = _client(tmp_path / "config.json").get("/api/v1/system/info")

    assert response.status_code == 200
    assert set(response.json()) == {"platform", "python_version", "rsync_available"}


def test_profiles_are_loaded_from_core_config_store(tmp_path: Path) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "version": 1,
                "profiles": [
                    {
                        "profile_id": "work",
                        "name": "Work",
                        "sources": [str(tmp_path / "source")],
                        "target": str(tmp_path / "target"),
                        "excludes": ["*.tmp"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    response = _client(config_path).get("/api/v1/profiles")

    assert response.status_code == 200
    assert response.json() == {
        "profiles": [
            {
                "profile_id": "work",
                "name": "Work",
                "sources": [str(tmp_path / "source")],
                "target": str(tmp_path / "target"),
                "excludes": ["*.tmp"],
            }
        ]
    }


def test_profile_detail_returns_configured_profile(tmp_path: Path) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "version": 1,
                "profiles": [
                    {
                        "profile_id": "work",
                        "name": "Work",
                        "sources": [str(tmp_path / "source")],
                        "target": str(tmp_path / "target"),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    response = _client(config_path).get("/api/v1/profiles/work")

    assert response.status_code == 200
    assert response.json() == {
        "profile_id": "work",
        "name": "Work",
        "sources": [str(tmp_path / "source")],
        "target": str(tmp_path / "target"),
        "excludes": [],
    }
    assert response.headers["x-request-id"]


def test_profile_detail_returns_404_for_unknown_profile(tmp_path: Path) -> None:
    response = _client(tmp_path / "missing.json").get("/api/v1/profiles/not-configured")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "PROFILE_NOT_FOUND"
    assert response.headers["x-request-id"] == response.json()["error"]["request_id"]


def test_invalid_config_returns_structured_error(tmp_path: Path) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text("{broken", encoding="utf-8")

    response = _client(config_path).get("/api/v1/profiles")

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "CONFIGURATION_INVALID"
    assert response.json()["error"]["message"] == (
        "The application configuration could not be loaded or validated."
    )
    assert str(config_path) not in response.text
    assert response.json()["error"]["request_id"] == response.headers["x-request-id"]


def test_semantically_invalid_config_returns_structured_error(tmp_path: Path) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "version": 1,
                "profiles": [
                    {
                        "profile_id": "work",
                        "name": "Work",
                        "sources": ["relative/source"],
                        "target": str(tmp_path / "target"),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    response = _client(config_path).get("/api/v1/profiles")

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "CONFIGURATION_INVALID"
    assert response.json()["error"]["request_id"] == response.headers["x-request-id"]
    assert "relative/source" not in response.text


def test_invalid_profile_id_is_not_interpreted_as_a_path(tmp_path: Path) -> None:
    response = _client(tmp_path / "missing.json").get("/api/v1/profiles/%2e%2e%2fsecrets")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


def test_loopback_host_and_frontend_origin_are_allowed(tmp_path: Path) -> None:
    response = _client(tmp_path / "config.json").get(
        "/api/v1/health",
        headers={"Origin": "http://127.0.0.1:5173"},
    )

    assert response.status_code == 200


def test_untrusted_host_is_rejected_as_rebinding_attempt(tmp_path: Path) -> None:
    response = _client(tmp_path / "config.json").get(
        "/api/v1/health",
        headers={"Host": "attacker.example"},
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "HOST_NOT_ALLOWED"
    assert response.headers["x-request-id"] == response.json()["error"]["request_id"]


def test_untrusted_origin_is_rejected(tmp_path: Path) -> None:
    response = _client(tmp_path / "config.json").get(
        "/api/v1/health",
        headers={"Origin": "http://attacker.example"},
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "ORIGIN_NOT_ALLOWED"


def test_originless_safe_request_remains_available_to_non_browser_clients(
    tmp_path: Path,
) -> None:
    response = _client(tmp_path / "config.json").get("/api/v1/health")

    assert response.status_code == 200


def test_originless_state_changing_request_is_rejected(tmp_path: Path) -> None:
    response = _client(tmp_path / "config.json").post("/api/v1/profiles")

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "ORIGIN_REQUIRED"


def test_allowed_origin_passes_security_check_for_state_changing_method(
    tmp_path: Path,
) -> None:
    response = _client(tmp_path / "config.json").post(
        "/api/v1/profiles",
        headers={"Origin": "http://127.0.0.1:5173"},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "REQUEST_INVALID"


def test_origin_with_path_or_null_origin_is_rejected(tmp_path: Path) -> None:
    client = _client(tmp_path / "config.json")

    for origin in ("null", "http://127.0.0.1:5173/untrusted"):
        response = client.get("/api/v1/health", headers={"Origin": origin})
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "ORIGIN_NOT_ALLOWED"


def test_profile_creation_uses_config_revision_and_validates_paths(tmp_path: Path) -> None:
    client = _client(tmp_path / "config.json")
    revision = client.get("/api/v1/profiles").headers["etag"]

    response = client.post(
        "/api/v1/profiles",
        json=_profile_payload(tmp_path),
        headers=_write_headers(revision),
    )

    assert response.status_code == 201
    assert response.json()["profile_id"] == "new-profile"
    assert response.headers["etag"] != revision
    assert client.get("/api/v1/profiles/new-profile").status_code == 200


def test_profile_creation_requires_current_etag(tmp_path: Path) -> None:
    config_path = tmp_path / "config.json"
    client = _client(config_path)

    response = client.post(
        "/api/v1/profiles",
        json=_profile_payload(tmp_path),
        headers={"Origin": "http://127.0.0.1:5173"},
    )

    assert response.status_code == 428
    assert response.json()["error"]["code"] == "PRECONDITION_REQUIRED"
    assert not config_path.exists()


def test_duplicate_profile_creation_is_rejected_without_overwriting(tmp_path: Path) -> None:
    config_path = tmp_path / "config.json"
    client = _client(config_path)
    first_revision = client.get("/api/v1/profiles").headers["etag"]
    created = client.post(
        "/api/v1/profiles",
        json=_profile_payload(tmp_path, "duplicate"),
        headers=_write_headers(first_revision),
    )
    original_content = config_path.read_bytes()
    current_revision = created.headers["etag"]

    response = client.post(
        "/api/v1/profiles",
        json=_profile_payload(tmp_path, "duplicate"),
        headers=_write_headers(current_revision),
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "PROFILE_EXISTS"
    assert config_path.read_bytes() == original_content


def test_profile_patch_rejects_stale_revision(tmp_path: Path) -> None:
    client = _client(tmp_path / "config.json")
    initial_revision = client.get("/api/v1/profiles").headers["etag"]
    created = client.post(
        "/api/v1/profiles",
        json=_profile_payload(tmp_path, "editable"),
        headers=_write_headers(initial_revision),
    )
    current_revision = created.headers["etag"]

    updated = client.patch(
        "/api/v1/profiles/editable",
        json={"name": "Updated name"},
        headers=_write_headers(current_revision),
    )
    stale = client.patch(
        "/api/v1/profiles/editable",
        json={"name": "Lost update"},
        headers=_write_headers(current_revision),
    )

    assert updated.status_code == 200
    assert updated.json()["name"] == "Updated name"
    assert stale.status_code == 412
    assert stale.json()["error"]["code"] == "CONFIGURATION_CHANGED"
    assert client.get("/api/v1/profiles/editable").json()["name"] == "Updated name"


def test_invalid_profile_patch_does_not_modify_configuration(tmp_path: Path) -> None:
    config_path = tmp_path / "config.json"
    client = _client(config_path)
    revision = client.get("/api/v1/profiles").headers["etag"]
    created = client.post(
        "/api/v1/profiles",
        json=_profile_payload(tmp_path, "editable"),
        headers=_write_headers(revision),
    )
    original_content = config_path.read_bytes()

    response = client.patch(
        "/api/v1/profiles/editable",
        json={"sources": ["relative/source"]},
        headers=_write_headers(created.headers["etag"]),
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "PROFILE_INVALID"
    assert config_path.read_bytes() == original_content


def test_profile_delete_removes_only_configuration(tmp_path: Path) -> None:
    config_path = tmp_path / "config.json"
    client = _client(config_path)
    payload = _profile_payload(tmp_path, "deletable")
    target = Path(str(payload["target"]))
    target.mkdir()
    marker = target / "keep.txt"
    marker.write_text("preserve backup target", encoding="utf-8")
    revision = client.get("/api/v1/profiles").headers["etag"]
    created = client.post(
        "/api/v1/profiles",
        json=payload,
        headers=_write_headers(revision),
    )

    response = client.delete(
        "/api/v1/profiles/deletable",
        headers=_write_headers(created.headers["etag"]),
    )

    assert response.status_code == 204
    assert client.get("/api/v1/profiles").json() == {"profiles": []}
    assert marker.read_text(encoding="utf-8") == "preserve backup target"


def test_snapshot_file_browsing_is_sorted_and_paginated(tmp_path: Path) -> None:
    target = tmp_path / "repository"
    snapshot = _write_snapshot(target, "20240101-000000-001")
    (snapshot / "z.txt").write_text("z", encoding="utf-8")
    (snapshot / "a.txt").write_text("alpha", encoding="utf-8")
    config_path = tmp_path / "config.json"
    _write_profiles(config_path, {"home": target})
    client = _client(config_path)
    endpoint = "/api/v1/profiles/home/snapshots/20240101-000000-001/files"

    first_page = client.get(endpoint, params={"limit": 2})

    assert first_page.status_code == 200
    assert [entry["name"] for entry in first_page.json()["entries"]] == ["a.txt", "info.json"]
    assert first_page.json()["next_cursor"] == "info.json"
    assert first_page.json()["entries"][0]["kind"] == "file"
    assert first_page.json()["entries"][0]["size_bytes"] == 5

    second_page = client.get(
        endpoint,
        params={"limit": 2, "after": first_page.json()["next_cursor"]},
    )

    assert second_page.status_code == 200
    assert [entry["name"] for entry in second_page.json()["entries"]] == ["z.txt"]
    assert second_page.json()["next_cursor"] is None


def test_snapshot_browsing_rejects_malformed_or_unknown_ids(tmp_path: Path) -> None:
    target = tmp_path / "repository"
    _write_snapshot(target, "20240101-000000-001")
    config_path = tmp_path / "config.json"
    _write_profiles(config_path, {"home": target})
    client = _client(config_path)

    malformed = client.get("/api/v1/profiles/home/snapshots/not-a-snapshot/files")
    unknown = client.get("/api/v1/profiles/home/snapshots/20240102-000000-001/files")

    assert malformed.status_code == 400
    assert malformed.json()["error"]["code"] == "INVALID_SNAPSHOT_ID"
    assert unknown.status_code == 404
    assert unknown.json()["error"]["code"] == "SNAPSHOT_NOT_FOUND"


def test_snapshot_browsing_rejects_absolute_and_traversal_paths(tmp_path: Path) -> None:
    target = tmp_path / "repository"
    _write_snapshot(target, "20240101-000000-001")
    config_path = tmp_path / "config.json"
    _write_profiles(config_path, {"home": target})
    endpoint = "/api/v1/profiles/home/snapshots/20240101-000000-001/files"

    for requested_path in ("../secret", "/etc/passwd", r"..\secret", r"\secret"):
        response = _client(config_path).get(endpoint, params={"path": requested_path})
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "SNAPSHOT_PATH_NOT_ALLOWED"


def test_snapshot_browsing_rejects_symlinks_escaping_snapshot(tmp_path: Path) -> None:
    target = tmp_path / "repository"
    snapshot = _write_snapshot(target, "20240101-000000-001")
    outside = tmp_path / "outside"
    outside.mkdir()
    secret = outside / "secret.txt"
    secret.write_text("outside snapshot", encoding="utf-8")
    link = snapshot / "external"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"Directory symlinks unavailable: {exc}")

    config_path = tmp_path / "config.json"
    _write_profiles(config_path, {"home": target})
    endpoint = "/api/v1/profiles/home/snapshots/20240101-000000-001/files"
    client = _client(config_path)

    root_listing = client.get(endpoint)
    escaped_listing = client.get(endpoint, params={"path": "external"})

    assert root_listing.status_code == 200
    assert (
        next(entry for entry in root_listing.json()["entries"] if entry["name"] == "external")[
            "kind"
        ]
        == "symlink"
    )
    assert escaped_listing.status_code == 400
    assert escaped_listing.json()["error"]["code"] == "SNAPSHOT_PATH_NOT_ALLOWED"
    assert "outside snapshot" not in escaped_listing.text


def test_snapshot_browsing_isolated_to_selected_profile(tmp_path: Path) -> None:
    first_target = tmp_path / "first-repository"
    second_target = tmp_path / "second-repository"
    snapshot_id = "20240101-000000-001"
    first_snapshot = _write_snapshot(first_target, snapshot_id)
    second_snapshot = _write_snapshot(second_target, snapshot_id)
    (first_snapshot / "first.txt").write_text("first", encoding="utf-8")
    (second_snapshot / "second.txt").write_text("second", encoding="utf-8")
    config_path = tmp_path / "config.json"
    _write_profiles(config_path, {"first": first_target, "second": second_target})
    client = _client(config_path)

    first = client.get(f"/api/v1/profiles/first/snapshots/{snapshot_id}/files")
    second = client.get(f"/api/v1/profiles/second/snapshots/{snapshot_id}/files")

    assert first.status_code == 200
    assert second.status_code == 200
    assert "first.txt" in {entry["name"] for entry in first.json()["entries"]}
    assert "second.txt" not in {entry["name"] for entry in first.json()["entries"]}
    assert "second.txt" in {entry["name"] for entry in second.json()["entries"]}
    assert "first.txt" not in {entry["name"] for entry in second.json()["entries"]}


def test_snapshot_browsing_bounds_page_size_and_hides_incomplete_snapshots(
    tmp_path: Path,
) -> None:
    target = tmp_path / "repository"
    incomplete = target / "20240101-000000-001"
    incomplete.mkdir(parents=True)
    (incomplete / "info.json").write_text(
        json.dumps({"snapshot_id": incomplete.name, "state": "running"}),
        encoding="utf-8",
    )
    _write_snapshot(target, "20240102-000000-001")
    config_path = tmp_path / "config.json"
    _write_profiles(config_path, {"home": target})
    client = _client(config_path)

    oversized = client.get(
        "/api/v1/profiles/home/snapshots/20240102-000000-001/files",
        params={"limit": 101},
    )
    incomplete_response = client.get("/api/v1/profiles/home/snapshots/20240101-000000-001/files")

    assert oversized.status_code == 422
    assert oversized.json()["error"]["code"] == "REQUEST_INVALID"
    assert incomplete_response.status_code == 404
    assert incomplete_response.json()["error"]["code"] == "SNAPSHOT_NOT_FOUND"
