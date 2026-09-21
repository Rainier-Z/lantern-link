import pytest


@pytest.mark.parametrize("method", ["get", "post"])
def test_missing_token_is_rejected_with_401(client, method, message_payload):
    request = getattr(client, method)
    kwargs = {"json": message_payload} if method == "post" else {}

    response = request("/api/messages", **kwargs)

    assert response.status_code == 401


@pytest.mark.parametrize("method", ["get", "post"])
def test_invalid_token_is_rejected_with_401(client, method, message_payload):
    request = getattr(client, method)
    kwargs = {"json": message_payload} if method == "post" else {}

    response = request(
        "/api/messages",
        headers={"Authorization": "Bearer invalid-token-for-tests"},
        **kwargs,
    )

    assert response.status_code == 401
