import app as findit
from app import app


def test_report_assistant_extracts_only_explicit_description_details():
    result = findit.build_report_assistant_suggestions(
        "Blue Samsung phone with a cracked screen and sticker",
        "Lost",
        "Other",
        "",
        "",
        "",
        "",
    )

    assert result["item_type"] == "Lost"
    assert result["suggestions"] == {
        "item_name": "Phone",
        "category": "Electronics",
        "color": "blue",
        "brand": "Samsung",
        "features": ["cracked", "sticker"],
    }
    assert {"blue", "samsung", "phone", "cracked", "sticker"} <= set(result["keywords"])
    assert result["review_required"] is True
    assert any("location" in prompt.lower() for prompt in result["missing"])
    assert any("date" in prompt.lower() for prompt in result["missing"])


def test_report_assistant_does_not_invent_attributes():
    result = findit.build_report_assistant_suggestions(
        "Unknown object",
        "Found",
        "Electronics",
        "Library",
        "Bengaluru",
        "Karnataka",
        "2026-10-08",
    )

    assert result["item_type"] == "Found"
    assert result["suggestions"] == {
        "item_name": None,
        "category": None,
        "color": None,
        "brand": None,
        "features": [],
    }
    assert result["keywords"] == ["unknown", "object"]


def test_report_assistant_api_requires_authentication():
    with app.test_client() as client:
        response = client.post(
            "/api/report-assistant",
            json={"item_type": "Lost", "description": "Blue phone"},
        )

    assert response.status_code == 401
    assert "error" in response.get_json()


def test_report_form_renders_assistant_for_signed_in_users():
    with app.test_client() as client:
        with client.session_transaction() as session:
            session["user_id"] = 1
        response = client.get("/report")

    assert response.status_code == 200
    assert b"Smart Report Assistant" in response.data
    assert b"Suggest details" in response.data


def test_report_assistant_api_supports_both_report_types_and_short_text():
    with app.test_client() as client:
        with client.session_transaction() as session:
            session["user_id"] = 1

        for report_type in ("Lost", "Found"):
            response = client.post(
                "/api/report-assistant",
                json={"item_type": report_type, "description": "Blue Samsung phone"},
            )
            assert response.status_code == 200
            assert response.get_json()["item_type"] == report_type

        response = client.post(
            "/api/report-assistant",
            json={"item_type": "Lost", "description": "Phone"},
        )
        assert response.status_code == 200
        assert response.get_json()["suggestions"]["brand"] is None
        assert response.get_json()["review_required"] is True


def test_report_assistant_api_rejects_invalid_report_type():
    with app.test_client() as client:
        with client.session_transaction() as session:
            session["user_id"] = 1
        response = client.post(
            "/api/report-assistant",
            json={"item_type": "Claim", "description": "Blue phone"},
        )

    assert response.status_code == 400
