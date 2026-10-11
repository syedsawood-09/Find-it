from uuid import uuid4

import pytest

from app import app, mysql


def _create_user(cur, label):
    cur.execute(
        "INSERT INTO users(name,email,password,role) VALUES(%s,%s,'x','user')",
        (label, f"mission-{label}-{uuid4().hex}@example.test"),
    )
    return cur.lastrowid


def _create_item(cur, user_id, status="Lost"):
    cur.execute(
        """INSERT INTO items(user_id,item_name,category,description,location,
                  event_date,event_time,status,city,state)
           VALUES(%s,'Blue headphones','Electronics','Blue wireless headphones',
                  'College library','2026-04-02','10:30',%s,'Pune','Maharashtra')""",
        (user_id, status),
    )
    return cur.lastrowid


def _login(client, user_id, label):
    with client.session_transaction() as session:
        session.update(
            user_id=user_id, name=label, role="user",
            _csrf_token=f"token-{label}",
        )
    return f"token-{label}"


def _post(client, path, token, data=None):
    return client.post(
        path,
        data={**(data or {}), "_csrf_token": token},
        follow_redirects=False,
    )


@pytest.fixture
def mission_data():
    with app.app_context():
        cur = mysql.connection.cursor()
        owner_id = _create_user(cur, "Owner")
        other_id = _create_user(cur, "Other")
        item_id = _create_item(cur, owner_id)
        other_item_id = _create_item(cur, other_id)
        found_item_id = _create_item(cur, owner_id, "Found")
        cur.execute(
            """INSERT INTO journey_places(item_id,user_id,place_name,city,state,visited_at)
               VALUES(%s,%s,'Pune Junction','Pune','Maharashtra','2026-04-02 11:15')""",
            (item_id, owner_id),
        )
        mysql.connection.commit()
        cur.close()
    yield owner_id, other_id, item_id, other_item_id, found_item_id
    with app.app_context():
        cur = mysql.connection.cursor()
        cur.execute("DELETE FROM users WHERE id IN (%s,%s)", (owner_id, other_id))
        mysql.connection.commit()
        cur.close()


def test_mission_creation_generates_predictor_and_memory_map_tasks_and_is_private(mission_data):
    owner_id, other_id, item_id, other_item_id, _ = mission_data
    with app.test_client() as client:
        owner_token = _login(client, owner_id, "Owner")
        response = client.get(f"/missions/new/{item_id}")
        assert response.status_code == 200
        assert b"Create a search mission" in response.data

        response = _post(client, f"/missions/new/{item_id}", owner_token)
        assert response.status_code == 302
        mission_id = int(response.headers["Location"].rsplit("/", 1)[1])
        response = client.get(f"/missions/{mission_id}")
        assert response.status_code == 200
        body = response.get_data(as_text=True)
        assert "Pune Junction" in body
        assert "Review possible matches on FindIt!" in body
        assert "not automatically marked complete" not in body
        assert "Not Started" in body

        response = _post(client, f"/missions/new/{item_id}", owner_token)
        assert response.status_code == 302
        assert response.headers["Location"] == f"/missions/{mission_id}"

        _login(client, other_id, "Other")
        assert client.get(f"/missions/{mission_id}").status_code == 404
        assert client.get(f"/missions/new/{item_id}").status_code == 404
        assert client.get(f"/missions/new/{other_item_id}").status_code == 200


def test_task_updates_progress_custom_tasks_and_mission_state(mission_data):
    owner_id, _, item_id, _, _ = mission_data
    with app.test_client() as client:
        token = _login(client, owner_id, "Owner")
        response = _post(client, f"/missions/new/{item_id}", token)
        mission_id = int(response.headers["Location"].rsplit("/", 1)[1])
        with app.app_context():
            cur = mysql.connection.cursor()
            cur.execute(
                "SELECT id,title,details,priority FROM search_mission_tasks WHERE mission_id=%s ORDER BY id LIMIT 1",
                (mission_id,),
            )
            task_id, title, details, priority = cur.fetchone()
            cur.execute(
                "SELECT id,title,details,priority FROM search_mission_tasks WHERE mission_id=%s ORDER BY id LIMIT 1 OFFSET 1",
                (mission_id,),
            )
            not_applicable_task = cur.fetchone()
            cur.close()

        response = _post(
            client, f"/missions/{mission_id}/tasks/{task_id}", token,
            {
                "title": title, "details": details or "",
                "priority": priority, "status": "Completed",
            },
        )
        assert response.status_code == 302
        response = client.get(f"/missions/{mission_id}")
        assert b"1 completed" in response.data
        na_id, na_title, na_details, na_priority = not_applicable_task
        response = _post(
            client, f"/missions/{mission_id}/tasks/{na_id}", token,
            {
                "title": na_title, "details": na_details or "",
                "priority": na_priority, "status": "Not Applicable",
            },
        )
        assert response.status_code == 302
        response = client.get(f"/missions/{mission_id}")
        assert b"1 completed" in response.data
        assert b"1 not applicable" in response.data
        response = client.get("/dashboard")
        assert response.status_code == 200
        assert b"My Search Missions" in response.data
        response = client.get("/missions")
        assert response.status_code == 200

        response = _post(
            client, f"/missions/{mission_id}/tasks/add", token,
            {"title": "Call the library", "details": "Ask reception", "priority": "High"},
        )
        assert response.status_code == 302
        with app.app_context():
            cur = mysql.connection.cursor()
            cur.execute(
                "SELECT id FROM search_mission_tasks WHERE mission_id=%s AND is_custom=1",
                (mission_id,),
            )
            custom_task_id = cur.fetchone()[0]
            cur.close()
        response = _post(
            client, f"/missions/{mission_id}/tasks/{custom_task_id}/delete", token,
        )
        assert response.status_code == 302
        assert _post(
            client, f"/missions/{mission_id}/tasks/{task_id}", token,
            {
                "title": "", "details": "", "priority": "High",
                "status": "Completed",
            },
        ).status_code == 400

        assert _post(
            client, f"/missions/{mission_id}/action", token, {"action": "pause"},
        ).status_code == 302
        assert _post(
            client, f"/missions/{mission_id}/action", token, {"action": "resume"},
        ).status_code == 302
        assert _post(
            client, f"/missions/{mission_id}/action", token, {"action": "complete"},
        ).status_code == 302
        assert _post(
            client, f"/missions/{mission_id}/tasks/{task_id}", token,
            {
                "title": title, "details": details or "",
                "priority": priority, "status": "In Progress",
            },
        ).status_code == 400
        assert _post(
            client, f"/missions/{mission_id}/action", token, {"action": "archive"},
        ).status_code == 302
        response = client.get(f"/missions/{mission_id}")
        assert b"Archived" in response.data


def test_mission_success_requires_existing_verified_recovery_and_reminder_is_limited(mission_data):
    owner_id, _, item_id, _, _ = mission_data
    with app.test_client() as client:
        token = _login(client, owner_id, "Owner")
        response = _post(client, f"/missions/new/{item_id}", token)
        mission_id = int(response.headers["Location"].rsplit("/", 1)[1])
        response = _post(
            client, f"/missions/{mission_id}/action", token, {"action": "success"},
        )
        assert response.status_code == 302
        with app.app_context():
            cur = mysql.connection.cursor()
            cur.execute("SELECT status FROM items WHERE id=%s", (item_id,))
            assert cur.fetchone()[0] == "Lost"
            cur.execute(
                "SELECT status FROM search_missions WHERE id=%s", (mission_id,),
            )
            assert cur.fetchone()[0] == "Active"
            cur.execute(
                "UPDATE items SET status='Returned' WHERE id=%s", (item_id,),
            )
            mysql.connection.commit()
            cur.close()
        response = _post(
            client, f"/missions/{mission_id}/action", token, {"action": "success"},
        )
        assert response.status_code == 302
        with app.app_context():
            cur = mysql.connection.cursor()
            cur.execute(
                "SELECT status FROM search_missions WHERE id=%s", (mission_id,),
            )
            assert cur.fetchone()[0] == "Successful"
            cur.execute(
                "SELECT id FROM search_missions WHERE id=%s", (mission_id,),
            )
            cur.close()

        response = _post(
            client, f"/missions/{mission_id}/remind", token,
        )
        assert response.status_code == 400


def test_active_mission_reminder_is_opt_in_and_rate_limited(mission_data):
    owner_id, _, item_id, _, _ = mission_data
    with app.test_client() as client:
        token = _login(client, owner_id, "Owner")
        response = _post(client, f"/missions/new/{item_id}", token)
        mission_id = int(response.headers["Location"].rsplit("/", 1)[1])
        response = _post(client, f"/missions/{mission_id}/remind", token)
        assert response.status_code == 302
        response = client.post(
            f"/missions/{mission_id}/remind",
            data={"_csrf_token": token},
            follow_redirects=True,
        )
        assert response.status_code == 200
        assert b"tomorrow" in response.data
        with app.app_context():
            cur = mysql.connection.cursor()
            cur.execute(
                """SELECT COUNT(*) FROM notifications
                   WHERE user_id=%s AND item_id=%s AND message LIKE 'Search mission reminder:%'""",
                (owner_id, item_id),
            )
            assert cur.fetchone()[0] == 1
            cur.close()


def test_mission_creation_rejects_found_reports_and_csrf(mission_data):
    owner_id, _, lost_item_id, _, found_item_id = mission_data
    with app.test_client() as client:
        token = _login(client, owner_id, "Owner")
        assert client.get(f"/missions/new/{found_item_id}").status_code == 400
        response = client.post(
            f"/missions/new/{lost_item_id}",
            data={"_csrf_token": "wrong-token"},
        )
        assert response.status_code == 400
