"""
Focused API tests for release registration, publishing, and public lookup.

Mirrors ``test_license_api.py``'s pattern (success case, 401 no-auth, 403
wrong-role) and extends it with the public "current release" endpoint that
the website's download page and the admin dashboard both depend on.
"""

from app.models.enums import ReleaseStatus


VALID_PAYLOAD = {
    "version": "0.9.0",
    "product": "photoflow",
    "platform": "Windows",
    "channel": "stable",
    "installer_filename": "PhotoFlow-Setup-0.9.0.exe",
    "size_bytes": 109_000_000,
    "download_url": "https://github.com/example/photoflow/releases/download/v0.9.0/PhotoFlow-Setup-0.9.0.exe",
}


class TestAdminCreateRelease:
    def test_admin_creates_a_release_as_draft(self, admin_client):
        client, headers, _admin = admin_client

        response = client.post(
            "/api/v1/admin/releases", headers=headers, json=VALID_PAYLOAD
        )

        assert response.status_code == 201
        body = response.json()
        assert body["version"] == "0.9.0"
        assert body["product"] == "photoflow"
        assert body["platform"] == "Windows"
        assert body["status"] == "DRAFT"
        assert body["size_bytes"] == 109_000_000
        assert body["published_at"] is None

    def test_duplicate_version_and_channel_is_rejected(self, admin_client):
        client, headers, _admin = admin_client

        first = client.post(
            "/api/v1/admin/releases", headers=headers, json=VALID_PAYLOAD
        )
        assert first.status_code == 201

        second = client.post(
            "/api/v1/admin/releases", headers=headers, json=VALID_PAYLOAD
        )
        assert second.status_code == 409

    def test_create_release_requires_authentication(self, client):
        response = client.post("/api/v1/admin/releases", json=VALID_PAYLOAD)
        assert response.status_code == 401

    def test_create_release_requires_admin_role(self, client_role_headers, client):
        headers, _user = client_role_headers

        response = client.post(
            "/api/v1/admin/releases", headers=headers, json=VALID_PAYLOAD
        )
        assert response.status_code == 403


class TestAdminPublishRelease:
    def test_publish_sets_status_and_published_at(self, admin_client, make_release):
        client, headers, _admin = admin_client
        release = make_release(status=ReleaseStatus.DRAFT)

        response = client.post(
            f"/api/v1/admin/releases/{release.id}/publish", headers=headers
        )

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "PUBLISHED"
        assert body["published_at"] is not None

    def test_yank_removes_it_from_public_view(self, admin_client, make_release, db):
        client, headers, _admin = admin_client
        release = make_release(status=ReleaseStatus.PUBLISHED)

        response = client.post(
            f"/api/v1/admin/releases/{release.id}/yank", headers=headers
        )

        assert response.status_code == 200
        assert response.json()["status"] == "YANKED"

        public = client.get(
            "/api/v1/releases/current",
            params={"product": release.product, "platform": release.platform,
                    "channel": release.channel},
        )
        assert public.status_code == 404

    def test_publish_requires_admin_role(self, client_role_headers, client, make_release):
        headers, _user = client_role_headers
        release = make_release()

        response = client.post(
            f"/api/v1/admin/releases/{release.id}/publish", headers=headers
        )
        assert response.status_code == 403


class TestPublicCurrentRelease:
    def test_returns_the_published_release(self, client, make_release):
        make_release(
            version="0.9.0", status=ReleaseStatus.PUBLISHED,
        )

        response = client.get(
            "/api/v1/releases/current",
            params={"product": "photoflow", "platform": "Windows", "channel": "stable"},
        )

        assert response.status_code == 200
        body = response.json()
        assert body["version"] == "0.9.0"
        assert "id" not in body
        assert "status" not in body

    def test_draft_release_is_not_returned(self, client, make_release):
        make_release(version="0.9.1", status=ReleaseStatus.DRAFT)

        response = client.get(
            "/api/v1/releases/current",
            params={"product": "photoflow", "platform": "Windows", "channel": "stable"},
        )

        assert response.status_code == 404

    def test_requires_no_authentication(self, client, make_release):
        """The public endpoint must be reachable with no bearer token at all."""
        make_release(version="0.9.2", status=ReleaseStatus.PUBLISHED)

        response = client.get(
            "/api/v1/releases/current",
            params={"product": "photoflow", "platform": "Windows", "channel": "stable"},
        )

        assert response.status_code == 200

    def test_no_published_release_is_404(self, client):
        response = client.get(
            "/api/v1/releases/current",
            params={"product": "photoflow", "platform": "Windows", "channel": "stable"},
        )
        assert response.status_code == 404
