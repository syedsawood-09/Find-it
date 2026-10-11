from uuid import uuid4

import app as findit
from app import app, mysql


def _create_user(cur, label, role="user"):
    cur.execute(
        "INSERT INTO users(name,email,password,role) VALUES(%s,%s,'x',%s)",
        (label, f"recovery-{label}-{uuid4().hex}@example.test", role),
    )
    return cur.lastrowid


def test_report_to_return_journey_is_tracked_and_notified(monkeypatch):
    with app.app_context():
        cur = mysql.connection.cursor()
        finder_id = _create_user(cur, "JourneyFinder")
        claimant_id = _create_user(cur, "JourneyClaimant")
        staff_id = _create_user(cur, "JourneySecurity", "security")
        mysql.connection.commit()
        cur.close()

    monkeypatch.setattr(findit, "rank_item_matches", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(findit, "rank_smart_matches", lambda *_args, **_kwargs: [])

    item_id = None
    claim_id = None
    try:
        with app.test_client() as finder_client:
            with finder_client.session_transaction() as session:
                session.update(
                    user_id=finder_id, name="JourneyFinder", role="user",
                    _csrf_token="journey-finder-token",
                )
            response = finder_client.post(
                "/report",
                data={
                    "item_type": "Found",
                    "item_name": "Journey test phone",
                    "category": "Electronics",
                    "description": "A phone with a unique case",
                    "location": "Central library",
                    "city": "Pune",
                    "state": "Maharashtra",
                    "event_date": "2026-10-08",
                    "private_details": "A tiny silver star under the case",
                    "_csrf_token": "journey-finder-token",
                },
            )
            assert response.status_code == 302
            with app.app_context():
                cur = mysql.connection.cursor()
                cur.execute(
                    "SELECT id FROM items WHERE user_id=%s AND item_name=%s",
                    (finder_id, "Journey test phone"),
                )
                item_id = cur.fetchone()[0]
                cur.close()

        with app.test_client() as claimant_client:
            with claimant_client.session_transaction() as session:
                session.update(
                    user_id=claimant_id, name="JourneyClaimant", role="user",
                    _csrf_token="journey-claimant-token",
                )
            response = claimant_client.post(
                f"/claim/{item_id}",
                data={
                    "proof": "I can identify its private marks",
                    "_csrf_token": "journey-claimant-token",
                },
            )
            assert response.status_code == 302
            with app.app_context():
                cur = mysql.connection.cursor()
                cur.execute("SELECT id FROM claims WHERE item_id=%s", (item_id,))
                claim_id = cur.fetchone()[0]
                cur.close()

        with app.test_client() as staff_client:
            with staff_client.session_transaction() as session:
                session.update(
                    user_id=staff_id, name="JourneySecurity", role="security",
                    _csrf_token="journey-staff-token",
                )
            response = staff_client.post(
                f"/admin/claim/{claim_id}/review",
                data={"_csrf_token": "journey-staff-token"},
            )
            assert response.status_code == 302
            response = staff_client.post(
                f"/admin/claim/{claim_id}/approve",
                data={"_csrf_token": "journey-staff-token"},
            )
            assert response.status_code == 302
            response = staff_client.post(
                f"/admin/item/{item_id}/handover",
                data={
                    "receiver_name": "JourneyClaimant",
                    "receiver_confirmed": "on",
                    "_csrf_token": "journey-staff-token",
                },
            )
            assert response.status_code == 302

        with app.test_client() as finder_client:
            with finder_client.session_transaction() as session:
                session.update(user_id=finder_id, name="JourneyFinder", role="user")
            response = finder_client.get(f"/item/{item_id}")
            page = response.get_data(as_text=True)
            assert response.status_code == 200
            assert "Recovery journey" in page
            assert "Reported" in page
            assert "Claim Submitted" in page
            assert "Under Review" in page
            assert "Ownership Verified" in page
            assert "Returned" in page
            assert "A tiny silver star under the case" in page

        with app.test_client() as claimant_client:
            with claimant_client.session_transaction() as session:
                session.update(user_id=claimant_id, name="JourneyClaimant", role="user")
            response = claimant_client.get(f"/item/{item_id}")
            page = response.get_data(as_text=True)
            assert response.status_code == 200
            assert "Under Review" in page
            assert "Ownership Verified" in page
            assert "Returned" in page
            assert "A tiny silver star under the case" not in page

        with app.test_client() as public_client:
            response = public_client.get(f"/item/{item_id}")
            assert response.status_code == 200
            assert b"Recovery journey" not in response.data
            assert b"A tiny silver star under the case" not in response.data

        with app.app_context():
            cur = mysql.connection.cursor()
            cur.execute("SELECT status FROM items WHERE id=%s", (item_id,))
            assert cur.fetchone()[0] == "Returned"
            cur.execute("SELECT status FROM claims WHERE id=%s", (claim_id,))
            assert cur.fetchone()[0] == "Returned"
            cur.execute(
                "SELECT COUNT(*) FROM notifications WHERE claim_id=%s AND user_id IN (%s,%s)",
                (claim_id, claimant_id, finder_id),
            )
            assert cur.fetchone()[0] >= 4
            cur.close()
    finally:
        with app.app_context():
            cur = mysql.connection.cursor()
            if item_id:
                cur.execute("DELETE FROM notifications WHERE item_id=%s OR claim_id=%s", (item_id, claim_id))
                cur.execute("DELETE FROM audit_logs WHERE entity_id=%s", (item_id,))
                cur.execute("DELETE FROM items WHERE id=%s", (item_id,))
            cur.execute(
                "DELETE FROM users WHERE id IN (%s,%s,%s)",
                (finder_id, claimant_id, staff_id),
            )
            mysql.connection.commit()
            cur.close()


def test_recovery_status_route_rejects_invalid_transitions_and_unauthorized_users():
    with app.app_context():
        cur = mysql.connection.cursor()
        owner_id = _create_user(cur, "TransitionOwner")
        staff_id = _create_user(cur, "TransitionStaff", "staff")
        cur.execute(
            """INSERT INTO items
               (user_id,item_name,category,description,location,event_date,status)
               VALUES(%s,'Transition test','Other','Test item','Library','2026-10-08','Found')""",
            (owner_id,),
        )
        item_id = cur.lastrowid
        mysql.connection.commit()
        cur.close()
    try:
        with app.test_client() as client:
            with client.session_transaction() as session:
                session.update(user_id=owner_id, role="user")
            response = client.post(
                f"/admin/item/{item_id}/status",
                data={"status": "Returned"},
            )
            assert response.status_code == 403

            with client.session_transaction() as session:
                session.update(
                    user_id=staff_id, role="staff",
                    _csrf_token="transition-test-token",
                )
            response = client.post(
                f"/admin/item/{item_id}/status",
                data={
                    "status": "Returned",
                    "_csrf_token": "transition-test-token",
                },
            )
            assert response.status_code == 400

        with app.app_context():
            cur = mysql.connection.cursor()
            cur.execute("SELECT status FROM items WHERE id=%s", (item_id,))
            assert cur.fetchone()[0] == "Found"
            cur.close()
    finally:
        with app.app_context():
            cur = mysql.connection.cursor()
            cur.execute("DELETE FROM items WHERE id=%s", (item_id,))
            cur.execute("DELETE FROM users WHERE id IN (%s,%s)", (owner_id, staff_id))
            mysql.connection.commit()
            cur.close()


def test_possible_match_appears_only_in_lost_report_owner_journey():
    with app.app_context():
        cur = mysql.connection.cursor()
        lost_owner_id = _create_user(cur, "MatchJourneyOwner")
        found_owner_id = _create_user(cur, "MatchJourneyFinder")
        cur.execute(
            """INSERT INTO items
               (user_id,item_name,category,description,location,event_date,status)
               VALUES(%s,'Journey lost watch','Other','Watch with engraved star','Station',
                      '2026-10-08','Lost')""",
            (lost_owner_id,),
        )
        lost_item_id = cur.lastrowid
        cur.execute(
            """INSERT INTO items
               (user_id,item_name,category,description,location,event_date,status)
               VALUES(%s,'Journey candidate watch','Other','Found watch','Station',
                      '2026-10-08','Found')""",
            (found_owner_id,),
        )
        found_item_id = cur.lastrowid
        created = findit.store_match_notification(
            cur, lost_owner_id, lost_item_id, found_item_id, 87,
            "Description and category details are similar.",
        )
        assert created
        mysql.connection.commit()
        cur.close()

    try:
        with app.test_client() as client:
            with client.session_transaction() as session:
                session.update(user_id=lost_owner_id, name="MatchJourneyOwner", role="user")
            response = client.get(f"/item/{lost_item_id}")
            assert response.status_code == 200
            assert b"Possible Match" in response.data
            assert b"87% estimated match" in response.data
            assert b"not proof of ownership" in response.data
            assert b"Journey candidate watch" in response.data

            with client.session_transaction() as session:
                session.update(user_id=found_owner_id, name="MatchJourneyFinder", role="user")
            response = client.get(f"/item/{found_item_id}")
            assert response.status_code == 200
            assert b"Possible Match" not in response.data
            assert b"87% estimated match" not in response.data
    finally:
        with app.app_context():
            cur = mysql.connection.cursor()
            cur.execute(
                "DELETE FROM notifications WHERE item_id=%s OR matched_item_id=%s",
                (lost_item_id, found_item_id),
            )
            cur.execute("DELETE FROM items WHERE id IN (%s,%s)", (lost_item_id, found_item_id))
            cur.execute(
                "DELETE FROM users WHERE id IN (%s,%s)",
                (lost_owner_id, found_owner_id),
            )
            mysql.connection.commit()
            cur.close()
