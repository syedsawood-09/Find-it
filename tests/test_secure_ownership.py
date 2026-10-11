from uuid import uuid4
import sqlite3
from pathlib import Path

import app as findit
from app import app, mysql


def _create_user(cur, label, role="user"):
    email = f"secure-ownership-{label}-{uuid4().hex}@example.test"
    cur.execute(
        "INSERT INTO users(name,email,password,role) VALUES(%s,%s,'x',%s)",
        (label, email, role),
    )
    return cur.lastrowid


def test_finder_private_details_are_hidden_from_public_and_other_users():
    with app.app_context():
        cur = mysql.connection.cursor()
        finder_id = _create_user(cur, "Finder")
        other_id = _create_user(cur, "Other")
        cur.execute(
            """INSERT INTO items
               (user_id,item_name,category,description,location,event_date,status,private_details)
               VALUES(%s,'Test blue wallet','Wallet','A blue wallet','Library',
                      '2026-10-08','Found','Hidden detail: small star stitched inside')""",
            (finder_id,),
        )
        item_id = cur.lastrowid
        mysql.connection.commit()
        cur.close()

    try:
        with app.test_client() as client:
            response = client.get(f"/item/{item_id}")
            assert response.status_code == 200
            assert b"Hidden detail" not in response.data

            with client.session_transaction() as session:
                session.update(user_id=other_id, name="Other", role="user")
            response = client.get(f"/item/{item_id}")
            assert response.status_code == 200
            assert b"Hidden detail" not in response.data
            assert b'name="_csrf_token"' in response.data

            with client.session_transaction() as session:
                session.update(user_id=finder_id, name="Finder", role="user")
            response = client.get(f"/item/{item_id}")
            assert response.status_code == 200
            assert b"Hidden detail: small star stitched inside" in response.data
    finally:
        with app.app_context():
            cur = mysql.connection.cursor()
            cur.execute("DELETE FROM items WHERE id=%s", (item_id,))
            cur.execute("DELETE FROM users WHERE id IN (%s,%s)", (finder_id, other_id))
            mysql.connection.commit()
            cur.close()


def test_claimant_cannot_review_own_claim_or_modify_another_claim():
    with app.app_context():
        cur = mysql.connection.cursor()
        finder_id = _create_user(cur, "Owner")
        claimant_id = _create_user(cur, "StaffClaimant", "staff")
        reviewer_id = _create_user(cur, "Reviewer", "staff")
        other_id = _create_user(cur, "Unauthorized")
        cur.execute(
            """INSERT INTO items
               (user_id,item_name,category,description,location,event_date,status)
               VALUES(%s,'Test camera','Electronics','Camera','Library',
                      '2026-10-08','Claim Submitted')""",
            (finder_id,),
        )
        item_id = cur.lastrowid
        cur.execute(
            """INSERT INTO claims(item_id,user_id,proof,status)
               VALUES(%s,%s,'Private claimant details','Pending')""",
            (item_id, claimant_id),
        )
        claim_id = cur.lastrowid
        mysql.connection.commit()
        cur.close()

    try:
        with app.test_client() as client:
            with client.session_transaction() as session:
                session.update(
                    user_id=claimant_id, name="StaffClaimant", role="staff",
                    _csrf_token="claim-security-test-token",
                )
            response = client.post(
                f"/admin/claim/{claim_id}/approve",
                data={"_csrf_token": "claim-security-test-token"},
            )
            assert response.status_code == 403

            with client.session_transaction() as session:
                session.update(
                    user_id=other_id, name="Unauthorized", role="user",
                    _csrf_token="claim-security-test-token",
                )
            response = client.post(
                f"/admin/claim/{claim_id}/reject",
                data={"_csrf_token": "claim-security-test-token"},
            )
            assert response.status_code == 403

            with app.app_context():
                cur = mysql.connection.cursor()
                cur.execute("SELECT status FROM claims WHERE id=%s", (claim_id,))
                assert cur.fetchone()[0] == "Pending"
                cur.close()
    finally:
        with app.app_context():
            cur = mysql.connection.cursor()
            cur.execute("DELETE FROM notifications WHERE claim_id=%s", (claim_id,))
            cur.execute("DELETE FROM items WHERE id=%s", (item_id,))
            cur.execute(
                "DELETE FROM users WHERE id IN (%s,%s,%s,%s)",
                (finder_id, claimant_id, reviewer_id, other_id),
            )
            mysql.connection.commit()
            cur.close()


def test_authorized_staff_can_move_claim_to_review_and_notify_parties():
    with app.app_context():
        cur = mysql.connection.cursor()
        finder_id = _create_user(cur, "ReviewOwner")
        claimant_id = _create_user(cur, "ReviewClaimant")
        reviewer_id = _create_user(cur, "ReviewStaff", "security")
        cur.execute(
            """INSERT INTO items
               (user_id,item_name,category,description,location,event_date,status,private_details)
               VALUES(%s,'Review test bag','Bag','Red bag','Station',
                      '2026-10-08','Claim Submitted','Secret green stitch inside pocket')""",
            (finder_id,),
        )
        item_id = cur.lastrowid
        cur.execute(
            """INSERT INTO claims(item_id,user_id,proof,status)
               VALUES(%s,%s,'Private color and contents','Pending')""",
            (item_id, claimant_id),
        )
        claim_id = cur.lastrowid
        mysql.connection.commit()
        cur.close()

    try:
        with app.test_client() as client:
            with client.session_transaction() as session:
                session.update(
                    user_id=reviewer_id, name="ReviewStaff", role="security",
                    _csrf_token="review-test-token",
                )
            response = client.post(
                f"/admin/claim/{claim_id}/review",
                data={"_csrf_token": "review-test-token"},
            )
            assert response.status_code == 302
            response = client.get("/admin")
            assert response.status_code == 200
            assert b"Secret green stitch inside pocket" in response.data
            with client.session_transaction() as session:
                session.update(user_id=claimant_id, name="ReviewClaimant", role="user")
            response = client.get(f"/item/{item_id}")
            assert response.status_code == 200
            assert b"Secret green stitch inside pocket" not in response.data
            assert b"Private color and contents" not in response.data

        with app.app_context():
            cur = mysql.connection.cursor()
            cur.execute("SELECT status FROM claims WHERE id=%s", (claim_id,))
            assert cur.fetchone()[0] == "Under Review"
            cur.execute(
                """SELECT COUNT(*) FROM notifications
                   WHERE claim_id=%s AND notification_type='claim' AND user_id IN (%s,%s)""",
                (claim_id, claimant_id, finder_id),
            )
            assert cur.fetchone()[0] == 2
            cur.close()
    finally:
        with app.app_context():
            cur = mysql.connection.cursor()
            cur.execute("DELETE FROM notifications WHERE claim_id=%s", (claim_id,))
            cur.execute("DELETE FROM audit_logs WHERE entity_id=%s", (claim_id,))
            cur.execute("DELETE FROM items WHERE id=%s", (item_id,))
            cur.execute(
                "DELETE FROM users WHERE id IN (%s,%s,%s)",
                (finder_id, claimant_id, reviewer_id),
            )
            mysql.connection.commit()
            cur.close()


def test_sensitive_claim_and_report_posts_reject_missing_csrf_tokens():
    with app.test_client() as client:
        with client.session_transaction() as session:
            session.update(user_id=456789, role="user")
        claim_response = client.post("/claim/456789", data={"proof": "My item"})
        report_response = client.post("/report", data={"item_type": "Found"})

    assert claim_response.status_code == 400
    assert report_response.status_code == 400


def test_claim_status_schema_supports_secure_workflow_states():
    with app.app_context():
        cur = mysql.connection.cursor()
        cur.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='claims'")
        schema = cur.fetchone()[0]
        cur.close()

    for status in ("Pending", "Under Review", "Approved", "Rejected", "Returned"):
        assert status in schema


def test_legacy_claim_migration_preserves_claims_and_receipts(tmp_path):
    legacy_database = tmp_path / "findit-legacy.sqlite3"
    schema = Path(findit.__file__).with_name("database.sql").read_text(encoding="utf-8")
    schema = schema.replace(
        "'Pending','Under Review','Approved','Rejected','Returned'",
        "'Pending','Approved','Rejected'",
    )
    schema = schema.replace(" private_details TEXT DEFAULT NULL,\n", "")
    connection = sqlite3.connect(legacy_database)
    connection.executescript(schema)
    connection.execute(
        "INSERT INTO users(name,email,password,role) VALUES('Legacy finder','legacy-f@example.test','x','user')"
    )
    finder_id = connection.execute("SELECT id FROM users WHERE email='legacy-f@example.test'").fetchone()[0]
    connection.execute(
        "INSERT INTO users(name,email,password,role) VALUES('Legacy claimant','legacy-c@example.test','x','user')"
    )
    claimant_id = connection.execute("SELECT id FROM users WHERE email='legacy-c@example.test'").fetchone()[0]
    connection.execute(
        "INSERT INTO users(name,email,password,role) VALUES('Legacy staff','legacy-s@example.test','x','security')"
    )
    staff_id = connection.execute("SELECT id FROM users WHERE email='legacy-s@example.test'").fetchone()[0]
    connection.execute(
        """INSERT INTO items(user_id,item_name,category,description,location,event_date,status)
           VALUES(?,'Legacy item','Other','Legacy description','Library','2026-10-08','Returned')""",
        (finder_id,),
    )
    item_id = connection.execute("SELECT id FROM items WHERE item_name='Legacy item'").fetchone()[0]
    connection.execute(
        "INSERT INTO claims(item_id,user_id,proof,status) VALUES(?,?,'Legacy private proof','Approved')",
        (item_id, claimant_id),
    )
    claim_id = connection.execute("SELECT id FROM claims WHERE item_id=?", (item_id,)).fetchone()[0]
    connection.execute(
        """INSERT INTO handover_receipts
           (item_id,claim_id,receiver_id,receiver_name,receiver_confirmation,
            authorized_staff_id,authorized_staff_name)
           VALUES(?,?,?,'Legacy claimant',1,?,'Legacy staff')""",
        (item_id, claim_id, claimant_id, staff_id),
    )
    connection.commit()
    connection.close()

    original_database = app.config["DATABASE"]
    app.config["DATABASE"] = str(legacy_database)
    try:
        findit.initialize_database()
        with app.app_context():
            findit.ensure_claim_image_column()
            cur = mysql.connection.cursor()
            cur.execute("SELECT status,proof FROM claims WHERE id=%s", (claim_id,))
            claim = cur.fetchone()
            cur.execute("SELECT claim_id FROM handover_receipts WHERE item_id=%s", (item_id,))
            receipt = cur.fetchone()
            cur.execute("PRAGMA foreign_key_check")
            foreign_key_violations = cur.fetchall()
            cur.execute("SELECT private_details FROM items WHERE id=%s", (item_id,))
            private_column = cur.fetchone()[0]
            cur.close()
        assert tuple(claim) == ("Approved", "Legacy private proof")
        assert receipt[0] == claim_id
        assert not foreign_key_violations
        assert private_column is None
    finally:
        app.config["DATABASE"] = original_database
