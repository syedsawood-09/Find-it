import json
from datetime import datetime, timedelta
from urllib.parse import parse_qs, urlparse
from uuid import uuid4

import pytest

import app as findit
from app import app, mysql


@pytest.mark.parametrize(
    ("item_name", "category", "description", "expected_text"),
    [
        ("Wallet", "Wallet", "black leather wallet", "pockets"),
        ("Phone", "Electronics", "phone used at a charger", "charging area"),
        ("Backpack", "Bag", "blue backpack", "storage area"),
        ("Keys", "Keys", "keys with a red tag", "entry area"),
        ("College ID card", "Documents", "college ID card", "reception"),
    ],
)
def test_location_predictor_builds_item_relevant_local_guidance(
    item_name, category, description, expected_text
):
    result = findit.build_location_predictions({
        "item_name": item_name,
        "category": category,
        "description": description,
        "last_seen": "",
        "last_location": "Pune campus",
        "places": ["Pune Junction", "Cafe near the station"],
        "travel_method": "Bus",
        "last_used": "",
    })

    recommendations = result["recommendations"]
    rendered = " ".join(item["place"] + " " + item["reason"] for item in recommendations).casefold()
    assert expected_text.casefold() in rendered
    assert "Pune campus" in recommendations[0]["place"]
    assert recommendations[0]["priority"] == "High"
    assert any("Pune Junction" in item["place"] for item in recommendations)
    assert any("lost-property office" in item["place"].casefold() for item in recommendations)
    assert result["engine"] == "Local rule-based guidance"
    assert "not confirmed locations" in result["notice"]
    assert all("probab" not in item["priority"].casefold() for item in recommendations)


def test_location_predictor_uses_elapsed_time_and_handles_insufficient_information():
    old_seen = (datetime.now() - timedelta(days=3)).strftime("%Y-%m-%dT%H:%M")
    result = findit.build_location_predictions({
        "item_name": "Phone",
        "category": "Electronics",
        "description": "phone",
        "last_seen": old_seen,
        "last_location": "",
        "places": [],
        "travel_method": "Train",
        "last_used": "",
    })
    assert any("lost-property desk" in item["place"].casefold() for item in result["recommendations"])

    insufficient = findit.build_location_predictions({
        "item_name": "",
        "category": "",
        "description": "",
        "last_seen": "",
        "last_location": "",
        "places": [],
        "travel_method": "",
        "last_used": "",
    })
    assert insufficient["insufficient"] is True
    assert len(insufficient["recommendations"]) == 1
    assert insufficient["recommendations"][0]["priority"] == "Low"
    assert "not enough" in insufficient["recommendations"][0]["reason"].casefold()


def _create_user(cur, label):
    cur.execute(
        "INSERT INTO users(name,email,password,role) VALUES(%s,%s,'x','user')",
        (label, f"predictor-{uuid4().hex}@example.test"),
    )
    return cur.lastrowid


def test_location_predictor_runs_without_auth_but_saves_only_after_explicit_consent():
    with app.test_client() as client:
        response = client.get("/location-predictor")
        assert response.status_code == 200
        assert b"FindIt AI Lost Item Location Predictor" in response.data
        assert b"No background location tracking" in response.data
        with client.session_transaction() as session:
            csrf_token = session["_csrf_token"]
        response = client.post(
            "/location-predictor",
            data={
                "item_name": "Wallet",
                "category": "Wallet",
                "last_location": "Pune station",
                "places": ["Cafe"],
                "_csrf_token": csrf_token,
            },
        )
        assert response.status_code == 200
        assert b"Last-seen place: Pune station" in response.data
        assert b"Local rule-based guidance" in response.data

        response = client.post(
            "/location-predictor",
            data={
                "item_name": "Wallet",
                "category": "Wallet",
                "last_location": "Private unconsented place",
                "save_plan": "on",
                "_csrf_token": csrf_token,
            },
        )
        assert response.status_code == 200
        assert b"Sign in to save this search plan" in response.data
        with app.app_context():
            cur = mysql.connection.cursor()
            cur.execute("SELECT COUNT(*) FROM location_prediction_plans")
            assert cur.fetchone()[0] == 0
            cur.close()


def test_location_predictor_saves_private_plan_checklist_and_enforces_owner_scope():
    with app.app_context():
        cur = mysql.connection.cursor()
        owner_id = _create_user(cur, "Predictor Owner")
        other_id = _create_user(cur, "Predictor Other")
        mysql.connection.commit()
        cur.close()

    plan_id = None
    try:
        with app.test_client() as client:
            with client.session_transaction() as session:
                session.update(
                    user_id=owner_id, name="Predictor Owner", role="user",
                    _csrf_token="predictor-owner-token",
                )
            response = client.post(
                "/location-predictor",
                data={
                    "item_name": "College ID card",
                    "category": "Documents",
                    "description": "Blue college identity card",
                    "last_seen": "2026-10-09T10:30",
                    "last_location": "Pune campus",
                    "places": ["Pune Junction", "Campus library"],
                    "travel_method": "Bus",
                    "city": "Pune",
                    "state": "Maharashtra",
                    "save_plan": "on",
                    "_csrf_token": "predictor-owner-token",
                },
            )
            assert response.status_code == 302
            plan_id = int(parse_qs(urlparse(response.location).query)["saved"][0])
            response = client.get(response.location)
            page = response.get_data(as_text=True)
            assert response.status_code == 200
            assert "Reception, security desk, or campus lost-property office" in page
            assert "Transport operator" in page
            assert "your saved private plans" in page.lower()

            with app.app_context():
                cur = mysql.connection.cursor()
                cur.execute(
                    "SELECT plan_json FROM location_prediction_plans WHERE id=%s AND user_id=%s",
                    (plan_id, owner_id),
                )
                plan = json.loads(cur.fetchone()[0])
                assert plan["inputs"]["city"] == "Pune"
                assert len(plan["recommendations"]) >= 4
                cur.close()

            response = client.post(
                f"/location-predictor/{plan_id}/check/0",
                data={
                    "status": "Contacted — waiting for response",
                    "_csrf_token": "predictor-owner-token",
                },
            )
            assert response.status_code == 302
            response = client.get(f"/location-predictor?saved={plan_id}")
            assert "Contacted — waiting for response" in response.get_data(as_text=True)

            with client.session_transaction() as session:
                session.update(
                    user_id=other_id, name="Predictor Other", role="user",
                    _csrf_token="predictor-other-token",
                )
            assert client.get(f"/location-predictor?saved={plan_id}").status_code == 404
            assert client.post(
                f"/location-predictor/{plan_id}/check/0",
                data={
                    "status": "Item found",
                    "_csrf_token": "predictor-other-token",
                },
            ).status_code == 404
            assert client.post(
                f"/location-predictor/{plan_id}/delete",
                data={"_csrf_token": "predictor-other-token"},
            ).status_code == 404

        with app.test_client() as client:
            with client.session_transaction() as session:
                session.update(
                    user_id=owner_id, name="Predictor Owner", role="user",
                    _csrf_token="predictor-owner-token",
                )
            assert client.post(
                f"/location-predictor/{plan_id}/delete",
                data={"_csrf_token": "predictor-owner-token"},
            ).status_code == 302
    finally:
        with app.app_context():
            cur = mysql.connection.cursor()
            if plan_id is not None:
                cur.execute("DELETE FROM location_prediction_plans WHERE id=%s", (plan_id,))
            cur.execute("DELETE FROM users WHERE id IN (%s,%s)", (owner_id, other_id))
            mysql.connection.commit()
            cur.close()


def test_location_predictor_prefills_only_the_authenticated_lost_report_owners_memory_map():
    with app.app_context():
        cur = mysql.connection.cursor()
        owner_id = _create_user(cur, "Prefill Owner")
        other_id = _create_user(cur, "Prefill Other")
        cur.execute(
            """INSERT INTO items(user_id,item_name,category,description,location,event_date,
                      event_time,status,city,state)
               VALUES(%s,'My lost phone','Electronics','black phone','Pune campus',
                      '2026-10-08','14:30','Lost','Pune','Maharashtra')""",
            (owner_id,),
        )
        item_id = cur.lastrowid
        cur.execute(
            """INSERT INTO journey_places(item_id,user_id,place_name,city,state,visited_at)
               VALUES(%s,%s,'Pune Junction','Pune','Maharashtra','2026-10-08 15:00')""",
            (item_id, owner_id),
        )
        mysql.connection.commit()
        cur.close()

    try:
        with app.test_client() as client:
            assert client.get(f"/location-predictor?item_id={item_id}").status_code == 403
            with client.session_transaction() as session:
                session.update(user_id=other_id, name="Prefill Other", role="user")
            assert client.get(f"/location-predictor?item_id={item_id}").status_code == 404
            with client.session_transaction() as session:
                session.update(user_id=owner_id, name="Prefill Owner", role="user")
            response = client.get(f"/location-predictor?item_id={item_id}")
            page = response.get_data(as_text=True)
            assert response.status_code == 200
            assert "Prefilled from your own lost report" in page
            assert "My lost phone" in page
            assert "Pune Junction" in page
            assert 'value="Pune"' in page
    finally:
        with app.app_context():
            cur = mysql.connection.cursor()
            cur.execute("DELETE FROM journey_places WHERE item_id=%s", (item_id,))
            cur.execute("DELETE FROM items WHERE id=%s", (item_id,))
            cur.execute("DELETE FROM users WHERE id IN (%s,%s)", (owner_id, other_id))
            mysql.connection.commit()
            cur.close()


@pytest.mark.parametrize(
    ("data", "expected_message"),
    [
        ({"category": "Invented"}, b"Choose a valid item category."),
        ({"travel_method": "Secret GPS tracker"}, b"Choose a listed travel method."),
        ({"places": [f"place {index}" for index in range(9)]}, b"no more than 8 visited places"),
    ],
)
def test_location_predictor_validates_form_inputs(data, expected_message):
    with app.test_client() as client:
        client.get("/location-predictor")
        with client.session_transaction() as session:
            data["_csrf_token"] = session["_csrf_token"]
        response = client.post("/location-predictor", data=data)
    assert response.status_code == 200
    assert expected_message in response.data
