from fastapi.testclient import TestClient

from app.main import app


def test_login_page_and_session_flow():
    client = TestClient(app, follow_redirects=False)

    root = client.get("/")
    assert root.status_code == 303
    assert root.headers["location"] == "/login"

    page = client.get("/login")
    assert page.status_code == 200
    assert "欢迎回来" in page.text
    assert "用户名" in page.text
    assert "密码" in page.text

    bad = client.post("/login", data={"username": "wrong", "password": "wrong"})
    assert bad.status_code == 401
    assert "用户名或密码不正确" in bad.text

    good = client.post("/login", data={"username": "admin", "password": "test-password"})
    assert good.status_code == 303
    assert good.headers["location"] == "/"
    assert "sublink_session=" in good.headers["set-cookie"]

    authed = client.get("/")
    assert authed.status_code == 200
    assert "多个节点" in authed.text

    api = client.get("/api/subscriptions")
    assert api.status_code == 200
    assert api.json() == []

    logout = client.post("/logout")
    assert logout.status_code == 303
    assert logout.headers["location"] == "/login"


def test_basic_auth_is_not_used_anymore():
    client = TestClient(app, follow_redirects=False)
    response = client.get("/", auth=("admin", "test-password"))
    assert response.status_code == 303
    assert response.headers["location"] == "/login"
