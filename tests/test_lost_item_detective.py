import json
from urllib.parse import parse_qs, urlparse
from uuid import uuid4

import pytest

import app as findit
from app import app, mysql


def _create_user(cur, label):
    cur.execute(
        "INSERT INTO users(name,email,password,role) VALUES(%s,%s,'x','user')",
        (label, f"detective-{uuid4().hex}@example.test"),
    )
    return cur.lastrowid


def _csrf(client, user_id=None, name="Detective Tester"):
    with client.session_transaction() as session:
        session["_csrf_token"] = "detective-test-token"
        if user_id:
            session.update(user_id=user_id, name=name, role="user")
        return session["_csrf_token"]


def test_detective_guides_user_one_question_at_a_time_and_renders_mobile_friendly_form():
    with app.test_client() as client:
        response = client.get("/lost-item-detective")
    page = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "AI Lost Item Detective" in page
    assert "data-detective-step" in page
    assert "Question ${index + 1} of ${steps.length}" in page
    assert "max-width:600px" in page
    assert "No background tracking" in page
    assert "GEMINI_API_KEY is not configured" in page


def test_detective_generates_personalized_headphone_plan_without_external_ai():
    with app.test_client() as client:
        client.get("/lost-item-detective")
        token = _csrf(client)
        response = client.post(
            "/lost-item-detective",
            data={
                "item_name": "Headphones",
                "category": "Electronics",
                "description": "black wireless headphones",
                "last_seen": "2026-10-09T10:30",
                "last_location": "college library",
                "last_used": "used my headphones at the library desk",
                "places": ["college library", "bus stop"],
                "travel_method": "Bus",
                "city": "Pune",
                "state": "Maharashtra",
                "_csrf_token": token,
            },
        )
    page = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "Last-seen place: college library" in page
    assert "Transport operator&#39;s lost-property office" in page or "Transport operator's lost-property office" in page
    assert "Classroom, library, or study area you used" in page
    assert "Local rule-based guidance" in page
    assert "suggestions are estimates" in page.lower()
    assert "Sign in to create a Lost report" in page


def test_detective_handles_incomplete_answers_without_claiming_a_prediction():
    with app.test_client() as client:
        client.get("/lost-item-detective")
        token = _csrf(client)
        response = client.post(
            "/lost-item-detective",
            data={"_csrf_token": token},
        )
    page = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "There is not enough detail for specific suggestions." in page
    assert "Most recent place or activity you can recall" in page
    assert "Low priority" in page


def test_detective_uses_local_ranking_when_consented_gemini_request_fails(monkeypatch):
    monkeypatch.setitem(app.config, "GEMINI_API_KEY", "test-key")

    def fail_request(*_args, **_kwargs):
        raise findit.urllib.error.URLError("provider unavailable")

    monkeypatch.setattr(findit, "rank_detective_suggestions_with_gemini", fail_request)
    with app.test_client() as client:
        client.get("/lost-item-detective")
        token = _csrf(client)
        response = client.post(
            "/lost-item-detective",
            data={
                "item_name": "Wallet",
                "category": "Wallet",
                "last_location": "Pune Junction",
                "use_ai_ranking": "on",
                "_csrf_token": token,
            },
        )
    page = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "Gemini could not rank suggestions" in page
    assert "Local rule-based guidance" in page
    assert "Last-seen place: Pune Junction" in page


def test_detective_does_not_call_gemini_without_explicit_opt_in(monkeypatch):
    monkeypatch.setitem(app.config, "GEMINI_API_KEY", "test-key")

    def unexpected_request(*_args, **_kwargs):
        raise AssertionError("Gemini must not be called without consent")

    monkeypatch.setattr(findit, "rank_detective_suggestions_with_gemini", unexpected_request)
    with app.test_client() as client:
        client.get("/lost-item-detective")
        token = _csrf(client)
        response = client.post(
            "/lost-item-detective",
            data={
                "item_name": "Wallet",
                "category": "Wallet",
                "last_location": "Pune Junction",
                "_csrf_token": token,
            },
        )
    assert response.status_code == 200
    assert b"Local rule-based guidance" in response.data


def test_gemini_ranking_is_limited_to_existing_candidates_and_priority_bands(monkeypatch):
    monkeypatch.setitem(app.config, "GEMINI_API_KEY", "detective-test-key")

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return json.dumps({
                "choices": [{
                    "message": {
                        "content": json.dumps({"order": [3, 2, 0, 1]}),
                    },
                }],
            }).encode()

    def fake_urlopen(request, timeout):
        assert request.get_header("Authorization")
        assert timeout == 12
        return FakeResponse()

    monkeypatch.setattr(findit.urllib.request, "urlopen", fake_urlopen)
    recommendations = [
        {"place": "A", "priority": "High", "reason": "High reason"},
        {"place": "B", "priority": "Medium", "reason": "Medium reason"},
        {"place": "C", "priority": "Medium", "reason": "Medium reason"},
        {"place": "D", "priority": "Low", "reason": "Low reason"},
    ]
    ranked = findit.rank_detective_suggestions_with_gemini(
        {"item_name": "wallet"}, recommendations
    )
    assert [item["place"] for item in ranked] == ["A", "C", "B", "D"]


def test_detective_saves_plan_only_with_consent_and_reopens_owner_private_history():
    with app.app_context():
        cur = mysql.connection.cursor()
        owner_id = _create_user(cur, "Detective Owner")
        other_id = _create_user(cur, "Detective Other")
        mysql.connection.commit()
        cur.close()

    plan_id = None
    try:
        with app.test_client() as client:
            token = _csrf(client, owner_id, "Detective Owner")
            response = client.post(
                "/lost-item-detective",
                data={
                    "item_name": "Blue bag",
                    "category": "Bag",
                    "last_location": "Pune college",
                    "places": ["Pune Junction"],
                    "save_plan": "on",
                    "_csrf_token": token,
                },
            )
            assert response.status_code == 302
            plan_id = int(parse_qs(urlparse(response.location).query)["saved"][0])
            page = client.get(response.location).get_data(as_text=True)
            assert "Saved private investigation" in page
            assert "Your saved private investigations" in page

            with app.app_context():
                cur = mysql.connection.cursor()
                cur.execute(
                    """SELECT plan_json FROM location_prediction_plans
                       WHERE id=%s AND user_id=%s""",
                    (plan_id, owner_id),
                )
                plan = json.loads(cur.fetchone()[0])
                assert plan["kind"] == "detective"
                assert plan["inputs"]["city"] == ""
                cur.close()

            token = _csrf(client, other_id, "Detective Other")
            assert client.get(f"/lost-item-detective?saved={plan_id}").status_code == 404
            assert client.post(
                f"/location-predictor/{plan_id}/check/0",
                data={"status": "Item found", "_csrf_token": token},
            ).status_code == 404

            token = _csrf(client, owner_id, "Detective Owner")
            response = client.post(
                f"/location-predictor/{plan_id}/check/0",
                data={
                    "status": "Contacted — waiting for response",
                    "_csrf_token": token,
                },
            )
            assert response.status_code == 302
            assert response.location.endswith(f"/lost-item-detective?saved={plan_id}")
            assert "Contacted — waiting for response" in client.get(response.location).get_data(as_text=True)

            response = client.post(
                f"/location-predictor/{plan_id}/delete",
                data={"_csrf_token": token},
            )
            assert response.status_code == 302
            assert response.location.endswith("/lost-item-detective")
            plan_id = None
    finally:
        with app.app_context():
            cur = mysql.connection.cursor()
            if plan_id:
                cur.execute("DELETE FROM location_prediction_plans WHERE id=%s", (plan_id,))
            cur.execute("DELETE FROM users WHERE id IN (%s,%s)", (owner_id, other_id))
            mysql.connection.commit()
            cur.close()


def test_detective_prefills_only_report_owners_and_rejects_tampered_report_id():
    with app.app_context():
        cur = mysql.connection.cursor()
        owner_id = _create_user(cur, "Detective Report Owner")
        other_id = _create_user(cur, "Detective Report Other")
        cur.execute(
            """INSERT INTO items(user_id,item_name,category,description,location,event_date,
                      event_time,status,city,state)
               VALUES(%s,'My headphones','Electronics','black headphones','Pune campus',
                      '2026-10-08','14:30','Lost','Pune','Maharashtra')""",
            (owner_id,),
        )
        item_id = cur.lastrowid
        mysql.connection.commit()
        cur.close()
    try:
        with app.test_client() as client:
            assert client.get(f"/lost-item-detective?item_id={item_id}").status_code == 403
            token = _csrf(client, other_id, "Detective Report Other")
            assert client.get(f"/lost-item-detective?item_id={item_id}").status_code == 404
            response = client.post(
                "/lost-item-detective",
                data={
                    "source_item_id": item_id,
                    "item_name": "My headphones",
                    "_csrf_token": token,
                },
            )
            assert response.status_code == 404

            _csrf(client, owner_id, "Detective Report Owner")
            page = client.get(f"/lost-item-detective?item_id={item_id}").get_data(as_text=True)
            assert "My headphones" in page
            assert 'value="Pune"' in page
    finally:
        with app.app_context():
            cur = mysql.connection.cursor()
            cur.execute("DELETE FROM items WHERE id=%s", (item_id,))
            cur.execute("DELETE FROM users WHERE id IN (%s,%s)", (owner_id, other_id))
            mysql.connection.commit()
            cur.close()


def test_detective_continue_report_link_prefills_existing_report_form():
    with app.app_context():
        cur = mysql.connection.cursor()
        user_id = _create_user(cur, "Detective Report Form")
        mysql.connection.commit()
        cur.close()
    try:
        with app.test_client() as client:
            _csrf(client, user_id, "Detective Report Form")
            response = client.get(
                "/report?item_name=Headphones&category=Electronics"
                "&description=Black+wireless+headphones&location=College+library"
                "&event_date=2026-10-09&event_time=10%3A30&city=Pune&state=Maharashtra"
            )
        page = response.get_data(as_text=True)
        assert response.status_code == 200
        assert 'value="Headphones"' in page
        assert 'value="College library"' in page
        assert 'value="2026-10-09"' in page
        assert 'value="Pune"' in page
    finally:
        with app.app_context():
            cur = mysql.connection.cursor()
            cur.execute("DELETE FROM users WHERE id=%s", (user_id,))
            mysql.connection.commit()
            cur.close()


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        ({"category": "Invented"}, b"Choose a valid item category."),
        ({"travel_method": "Background tracking"}, b"Choose a listed travel method."),
        ({"places": [f"place {index}" for index in range(9)]}, b"no more than 8 visited places"),
    ],
)
def test_detective_validates_answers_server_side(data, expected):
    with app.test_client() as client:
        client.get("/lost-item-detective")
        token = _csrf(client)
        data["_csrf_token"] = token
        response = client.post("/lost-item-detective", data=data)
    assert response.status_code == 200
    assert expected in response.data
