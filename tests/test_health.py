def test_health_check_reports_service_status(client):
    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "lantern_link"}
