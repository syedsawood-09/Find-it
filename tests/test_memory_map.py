from uuid import uuid4

import app as findit
from app import app, mysql


def _create_user(cur, label):
    cur.execute(
        "INSERT INTO users(name,email,password,role) VALUES(%s,%s,'x','user')",
        (label, f"memory-map-{label}-{uuid4().hex}@example.test"),
    )
    return cur.lastrowid


def test_lost_report_owner_can_create_edit_sort_and_delete_private_places():
    with app.app_context():
        cur = mysql.connection.cursor()
        owner_id = _create_user(cur, "Owner")
        other_id = _create_user(cur, "Other")
        cur.execute(
            """INSERT INTO items(user_id,item_name,category,description,location,
                      event_date,status,city,state)
               VALUES(%s,'Memory test item','Other','Missing item','Central area',
                      '2026-10-08','Lost','Pune','Maharashtra')""",
            (owner_id,),
        )
        item_id = cur.lastrowid
        mysql.connection.commit()
        cur.close()

    first_place_id = None
    second_place_id = None
    try:
        with app.test_client() as client:
            with client.session_transaction() as session:
                session.update(
                    user_id=owner_id, name="Owner", role="user",
                    _csrf_token="memory-owner-token",
                )
            response = client.post(
                f"/item/{item_id}/memory-map",
                data={
                    "place_name": "Pune Junction",
                    "city": "Pune",
                    "state": "Maharashtra",
                    "visited_at": "2026-10-08T10:30",
                    "approx_lat": "18.5284",
                    "approx_lng": "73.8742",
                    "notes": "Stopped before taking the bus",
                    "_csrf_token": "memory-owner-token",
                },
            )
            assert response.status_code == 302
            with app.app_context():
                cur = mysql.connection.cursor()
                cur.execute(
                    "SELECT id FROM journey_places WHERE item_id=%s AND place_name='Pune Junction'",
                    (item_id,),
                )
                first_place_id = cur.fetchone()[0]
                cur.close()

            response = client.post(
                f"/item/{item_id}/memory-map",
                data={
                    "place_name": "Aundh market",
                    "city": "Pune",
                    "state": "Maharashtra",
                    "visited_at": "2026-10-08T08:15",
                    "_csrf_token": "memory-owner-token",
                },
            )
            assert response.status_code == 302
            with app.app_context():
                cur = mysql.connection.cursor()
                cur.execute(
                    "SELECT id FROM journey_places WHERE item_id=%s AND place_name='Aundh market'",
                    (item_id,),
                )
                second_place_id = cur.fetchone()[0]
                cur.close()

            response = client.get(f"/item/{item_id}")
            page = response.get_data(as_text=True)
            assert response.status_code == 200
            assert "FindIt Memory Map" in page
            assert "user-provided possibilities" in page
            timeline = page.split("Your visited-place timeline", 1)[1].split(
                "Possible found reports", 1
            )[0]
            assert timeline.index("Aundh market") < timeline.index("Pune Junction")
            assert "memoryJourneyMap" in page

            response = client.post(
                f"/item/{item_id}/memory-map/{first_place_id}/edit",
                data={
                    "place_name": "Pune Railway Station",
                    "city": "Pune",
                    "state": "Maharashtra",
                    "visited_at": "2026-10-08T10:45",
                    "_csrf_token": "memory-owner-token",
                },
            )
            assert response.status_code == 302
            with app.app_context():
                cur = mysql.connection.cursor()
                cur.execute(
                    "SELECT place_name,visited_at FROM journey_places WHERE id=%s",
                    (first_place_id,),
                )
                assert tuple(cur.fetchone()) == ("Pune Railway Station", "2026-10-08 10:45")
                cur.close()

            response = client.post(
                f"/item/{item_id}/memory-map/{second_place_id}/delete",
                data={"_csrf_token": "memory-owner-token"},
            )
            assert response.status_code == 302
            with app.app_context():
                cur = mysql.connection.cursor()
                cur.execute("SELECT COUNT(*) FROM journey_places WHERE item_id=%s", (item_id,))
                assert cur.fetchone()[0] == 1
                cur.close()
    finally:
        with app.app_context():
            cur = mysql.connection.cursor()
            cur.execute("DELETE FROM journey_places WHERE item_id=%s", (item_id,))
            cur.execute("DELETE FROM items WHERE id=%s", (item_id,))
            cur.execute("DELETE FROM users WHERE id IN (%s,%s)", (owner_id, other_id))
            mysql.connection.commit()
            cur.close()


def test_memory_map_places_are_private_and_cross_user_mutations_are_denied():
    with app.app_context():
        cur = mysql.connection.cursor()
        owner_id = _create_user(cur, "PrivateOwner")
        other_id = _create_user(cur, "PrivateOther")
        cur.execute(
            """INSERT INTO items(user_id,item_name,category,description,location,
                      event_date,status)
               VALUES(%s,'Private journey','Other','Missing item','Public area',
                      '2026-10-08','Lost')""",
            (owner_id,),
        )
        item_id = cur.lastrowid
        cur.execute(
            """INSERT INTO journey_places(item_id,user_id,place_name,city,visited_at)
               VALUES(%s,%s,'Private bus stop','Pune','2026-10-08 09:00')""",
            (item_id, owner_id),
        )
        place_id = cur.lastrowid
        mysql.connection.commit()
        cur.close()

    try:
        with app.test_client() as client:
            response = client.get(f"/item/{item_id}")
            assert response.status_code == 200
            assert b"Private bus stop" not in response.data

            with client.session_transaction() as session:
                session.update(
                    user_id=other_id, name="PrivateOther", role="user",
                    _csrf_token="other-memory-token",
                )
            response = client.get(f"/item/{item_id}")
            assert response.status_code == 200
            assert b"FindIt Memory Map" not in response.data
            response = client.post(
                f"/item/{item_id}/memory-map/{place_id}/delete",
                data={"_csrf_token": "other-memory-token"},
            )
            assert response.status_code == 404

            response = client.post(
                f"/item/{item_id}/memory-map",
                data={
                    "place_name": "Unauthorized stop",
                    "visited_at": "2026-10-08T09:00",
                    "_csrf_token": "other-memory-token",
                },
            )
            assert response.status_code == 404
        with app.app_context():
            cur = mysql.connection.cursor()
            cur.execute("SELECT place_name FROM journey_places WHERE id=%s", (place_id,))
            assert cur.fetchone()[0] == "Private bus stop"
            cur.close()
    finally:
        with app.app_context():
            cur = mysql.connection.cursor()
            cur.execute("DELETE FROM journey_places WHERE item_id=%s", (item_id,))
            cur.execute("DELETE FROM items WHERE id=%s", (item_id,))
            cur.execute("DELETE FROM users WHERE id IN (%s,%s)", (owner_id, other_id))
            mysql.connection.commit()
            cur.close()


def test_memory_map_candidate_score_combines_distance_and_time():
    visit = {
        "place_name": "Pune Junction",
        "city": "Pune",
        "state": "Maharashtra",
        "visited_at": "2026-10-08 10:00",
        "approx_lat": 18.5284,
        "approx_lng": 73.8742,
    }
    candidate = {
        "item_name": "Found phone",
        "location": "Pune Junction",
        "city": "Pune",
        "state": "Maharashtra",
        "event_date": "2026-10-08",
        "event_time": "10:15",
        "approx_lat": 18.529,
        "approx_lng": 73.875,
    }
    score, reasons = findit.score_memory_map_candidate(visit, candidate)
    assert score >= 95
    assert any("km apart" in reason for reason in reasons)
    assert any("hours apart" in reason for reason in reasons)


def test_memory_map_rejects_invalid_and_incomplete_coordinates():
    with app.test_request_context(
        "/",
        method="POST",
        data={
            "place_name": "Train station",
            "visited_at": "2026-10-08T10:00",
            "approx_lat": "18.5",
        },
    ):
        entry, error = findit.validate_memory_place_form()
    assert entry is None
    assert "both approximate coordinates" in error

    with app.test_request_context(
        "/",
        method="POST",
        data={
            "place_name": "Train station",
            "visited_at": "2026-10-08T10:00",
            "approx_lat": "200",
            "approx_lng": "73.8",
        },
    ):
        entry, error = findit.validate_memory_place_form()
    assert entry is None
    assert "valid approximate coordinates" in error
