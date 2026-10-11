from uuid import uuid4

from app import app, mysql, store_match_notification


def _create_user(cur, name, role="user"):
    email = f"{name.lower().replace(' ', '-')}-{uuid4().hex}@example.test"
    cur.execute(
        "INSERT INTO users(name,email,password,role) VALUES(%s,%s,'x',%s)",
        (name, email, role),
    )
    return cur.lastrowid


def test_match_notifications_are_deduplicated_private_and_markable_as_read():
    with app.app_context():
        cur = mysql.connection.cursor()
        lost_owner_id = _create_user(cur, "Lost Report Owner")
        found_owner_id = _create_user(cur, "Found Report Owner")
        cur.execute(
            """INSERT INTO items
               (user_id,item_name,category,description,location,event_date,status)
               VALUES(%s,'Lost blue phone','Electronics','Blue phone','Delhi','2026-10-08','Lost')""",
            (lost_owner_id,),
        )
        lost_item_id = cur.lastrowid
        cur.execute(
            """INSERT INTO items
               (user_id,item_name,category,description,location,event_date,status)
               VALUES(%s,'Found blue phone','Electronics','Blue phone','Delhi','2026-10-08','Found')""",
            (found_owner_id,),
        )
        found_item_id = cur.lastrowid
        first_insert = store_match_notification(
            cur, lost_owner_id, lost_item_id, found_item_id, 84,
            "Description is semantically similar; Category matches",
        )
        duplicate_insert = store_match_notification(
            cur, lost_owner_id, lost_item_id, found_item_id, 91,
            "Duplicate candidate notification",
        )
        mysql.connection.commit()
        cur.execute(
            """SELECT id,user_id,match_score,explanation FROM notifications
               WHERE notification_type='match' AND item_id=%s AND matched_item_id=%s""",
            (lost_item_id, found_item_id),
        )
        notification = cur.fetchone()
        cur.close()

    try:
        assert first_insert is True
        assert duplicate_insert is False
        assert tuple(notification)[1:] == (
            lost_owner_id,
            84,
            "Description is semantically similar; Category matches",
        )

        with app.test_client() as client:
            with client.session_transaction() as session:
                session.update(user_id=lost_owner_id, name="Lost Report Owner", role="user")
            response = client.get("/notifications")
            page = response.get_data(as_text=True)
            assert response.status_code == 200
            assert "Found blue phone" in page
            assert "84% estimated match" in page
            assert "Category matches" in page
            assert "View candidate report" in page
            assert "found-report-owner-" not in page
            assert "not probabilities or proof of ownership" in page

            with client.session_transaction() as session:
                session["user_id"] = found_owner_id
            response = client.post(
                f"/notifications/{notification[0]}/read",
                follow_redirects=False,
            )
            assert response.status_code == 302

            with app.app_context():
                cur = mysql.connection.cursor()
                cur.execute("SELECT is_read FROM notifications WHERE id=%s", (notification[0],))
                assert cur.fetchone()[0] == 0
                cur.close()

            with client.session_transaction() as session:
                session["user_id"] = lost_owner_id
            response = client.post(
                f"/notifications/{notification[0]}/read",
                follow_redirects=False,
            )
            assert response.status_code == 302

        with app.app_context():
            cur = mysql.connection.cursor()
            cur.execute("SELECT is_read FROM notifications WHERE id=%s", (notification[0],))
            assert cur.fetchone()[0] == 1
            cur.close()
    finally:
        with app.app_context():
            cur = mysql.connection.cursor()
            cur.execute(
                "DELETE FROM notifications WHERE user_id IN (%s,%s)",
                (lost_owner_id, found_owner_id),
            )
            cur.execute("DELETE FROM items WHERE id IN (%s,%s)", (lost_item_id, found_item_id))
            cur.execute(
                "DELETE FROM users WHERE id IN (%s,%s)",
                (lost_owner_id, found_owner_id),
            )
            mysql.connection.commit()
            cur.close()


def test_claim_status_updates_create_private_claim_notification():
    with app.app_context():
        cur = mysql.connection.cursor()
        reporter_id = _create_user(cur, "Claim Report Owner")
        claimant_id = _create_user(cur, "Claimant")
        staff_id = _create_user(cur, "Claim Review Staff", "staff")
        cur.execute(
            """INSERT INTO items
               (user_id,item_name,category,description,location,event_date,status)
               VALUES(%s,'Claimed backpack','Bag','Private description','Pune',
                      '2026-10-08','Claim Submitted')""",
            (reporter_id,),
        )
        item_id = cur.lastrowid
        cur.execute(
            """INSERT INTO claims(item_id,user_id,proof,status)
               VALUES(%s,%s,'Secret ownership evidence','Pending')""",
            (item_id, claimant_id),
        )
        claim_id = cur.lastrowid
        mysql.connection.commit()
        cur.close()

    try:
        with app.test_client() as client:
            with client.session_transaction() as session:
                session.update(
                    user_id=staff_id, name="Claim Review Staff", role="staff",
                    _csrf_token="claim-review-test-token",
                )
            response = client.post(
                f"/admin/claim/{claim_id}/reject",
                data={"_csrf_token": "claim-review-test-token"},
            )
            assert response.status_code == 302

            with client.session_transaction() as session:
                session.update(user_id=claimant_id, name="Claimant", role="user")
            response = client.get("/notifications")
            page = response.get_data(as_text=True)
            assert response.status_code == 200
            assert "Your claim status is now rejected." in page
            assert "Claimed backpack" in page
            assert "Secret ownership evidence" not in page

        with app.app_context():
            cur = mysql.connection.cursor()
            cur.execute(
                """SELECT notification_type,claim_id,item_id FROM notifications
                   WHERE user_id=%s AND claim_id=%s""",
                (claimant_id, claim_id),
            )
            assert tuple(cur.fetchone()) == ("claim", claim_id, item_id)
            cur.close()
    finally:
        with app.app_context():
            cur = mysql.connection.cursor()
            cur.execute("DELETE FROM notifications WHERE user_id=%s", (claimant_id,))
            cur.execute("DELETE FROM items WHERE id=%s", (item_id,))
            cur.execute(
                "DELETE FROM users WHERE id IN (%s,%s,%s)",
                (reporter_id, claimant_id, staff_id),
            )
            mysql.connection.commit()
            cur.close()
