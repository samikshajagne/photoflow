from app.models.enums import ActivationStatus


class TestLicenseAPI:
    def test_activate_license_through_api(
        self, client, make_user, make_license
    ):
        user = make_user(
            email="api-license@example.com",
            password="correct-horse-battery-staple",
        )
        key = "PF-API-TEST-1234"
        license = make_license(user, key=key)

        login = client.post(
            "/api/v1/auth/login",
            json={
                "email": "api-license@example.com",
                "password": "correct-horse-battery-staple",
            },
        )

        assert login.status_code == 200
        token = login.json()["access_token"]

        response = client.post(
            "/api/v1/licenses/activate",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "key": key,
                "machine": "api-test-machine-001",
                "product": "photoflow",
                "version": "0.9.2",
                "platform": "Windows 11",
                "device_name": "Test PC",
            },
        )

        assert response.status_code == 200

        body = response.json()

        assert body["ok"] is True
        assert body["license_id"] == str(license.id)
        assert body["device_id"]
        assert body["message"] == "Activated."


    def test_license_api_requires_authentication(
        self, client, make_user, make_license
    ):
        user = make_user()
        key = "PF-API-AUTH-1234"
        make_license(user, key=key)

        response = client.post(
            "/api/v1/licenses/activate",
            json={
                "key": key,
                "machine": "unauthorized-machine",
                "product": "photoflow",
                "version": "0.9.2",
            },
        )

        assert response.status_code == 401


    def test_wrong_user_cannot_activate_license(
        self, client, make_user, make_license
    ):
        owner = make_user(
            email="owner@example.com",
            password="owner-password",
        )
        attacker = make_user(
            email="attacker@example.com",
            password="attacker-password",
        )

        key = "PF-API-OWNER-1234"
        make_license(owner, key=key)

        login = client.post(
            "/api/v1/auth/login",
            json={
                "email": "attacker@example.com",
                "password": "attacker-password",
            },
        )

        assert login.status_code == 200
        token = login.json()["access_token"]

        response = client.post(
            "/api/v1/licenses/activate",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "key": key,
                "machine": "attacker-machine",
                "product": "photoflow",
                "version": "0.9.2",
            },
        )

        assert response.status_code == 403