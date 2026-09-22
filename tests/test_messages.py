from __future__ import annotations

def test_post_message_with_valid_token_returns_stored_message(
    client, auth_headers, message_payload
):
    response = client.post(
        "/api/messages", headers=auth_headers, json=message_payload
    )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["message"]["sender"] == "iphone"
    assert body["message"]["type"] == "text"
    assert body["message"]["content"] == "Hello PC"
    assert body["message"]["id"]
    assert body["message"]["created_at"]


def test_get_messages_with_valid_token_returns_posted_message(
    client, auth_headers, message_payload
):
    posted = client.post(
        "/api/messages", headers=auth_headers, json=message_payload
    ).json()["message"]

    response = client.get("/api/messages", headers=auth_headers)

    assert response.status_code == 200
    assert posted in response.json()["messages"]


def test_get_messages_after_id_returns_only_newer_messages(
    client, auth_headers
):
    first = client.post(
        "/api/messages",
        headers=auth_headers,
        json={"sender": "pc", "type": "text", "content": "first"},
    ).json()["message"]
    second = client.post(
        "/api/messages",
        headers=auth_headers,
        json={"sender": "iphone", "type": "text", "content": "second"},
    ).json()["message"]

    response = client.get(
        "/api/messages", headers=auth_headers, params={"after": first["id"]}
    )

    assert response.status_code == 200
    assert response.json()["messages"] == [second]


def test_unicode_message_round_trips_without_loss(client, auth_headers):
    content = "中文\nEnglish 123 😀\nhttps://example.test/多行"
    payload = {"sender": "iphone", "type": "text", "content": content}

    posted = client.post(
        "/api/messages", headers=auth_headers, json=payload
    ).json()["message"]
    fetched = client.get("/api/messages", headers=auth_headers).json()["messages"]

    assert posted["content"] == content
    assert posted in fetched


def test_empty_message_is_rejected_with_bad_request(
    client, auth_headers, message_payload
):
    response = client.post(
        "/api/messages",
        headers=auth_headers,
        json={**message_payload, "content": ""},
    )

    assert response.status_code == 400


def test_message_over_64_kibibytes_in_utf8_is_rejected_with_413(
    client, auth_headers, message_payload
):
    content = "😀" * ((64 * 1024 // len("😀".encode("utf-8"))) + 1)
    assert len(content.encode("utf-8")) > 64 * 1024

    response = client.post(
        "/api/messages",
        headers=auth_headers,
        json={**message_payload, "content": content},
    )

    assert response.status_code == 413
