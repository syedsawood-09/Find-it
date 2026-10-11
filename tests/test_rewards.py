from uuid import uuid4

from app import app, grant_successful_return_reward, mysql


def _create_user(cur, label, role="user"):
    email = f"rewards-{label.lower().replace(' ', '-')}-{uuid4().hex}@example.test"
    cur.execute(
        "INSERT INTO users(name,email,password,role) VALUES(%s,%s,'x',%s)",
        (label, email, role),
    )
    return cur.lastrowid


def test_confirmed_handover_awards_finder_once_and_unlocks_badge():
    with app.app_context():
        cur = mysql.connection.cursor()
        finder_id = _create_user(cur, "Reward Finder")
        claimant_id = _create_user(cur, "Reward Claimant")
        staff_id = _create_user(cur, "Reward Security", "security")
        admin_id = _create_user(cur, "Reward Admin", "admin")
        cur.execute(
            """INSERT INTO items
               (user_id,item_name,category,description,location,event_date,status)
               VALUES(%s,'Reward test wallet','Wallet','Blue wallet','Jaipur',
                      '2026-10-08','Verification')""",
            (finder_id,),
        )
        item_id = cur.lastrowid
        cur.execute(
            """INSERT INTO claims(item_id,user_id,proof,status)
               VALUES(%s,%s,'Private ownership evidence','Approved')""",
            (item_id, claimant_id),
        )
        mysql.connection.commit()
        cur.close()

    try:
        with app.test_client() as client:
            with client.session_transaction() as session:
                session.update(
                    user_id=staff_id, name="Reward Security", role="security",
                    _csrf_token="reward-handover-token",
                )
            response = client.post(
                f"/admin/item/{item_id}/handover",
                data={
                    "receiver_name": "Reward Claimant",
                    "receiver_confirmed": "on",
                    "_csrf_token": "reward-handover-token",
                },
            )
            assert response.status_code == 302

        with app.app_context():
            cur = mysql.connection.cursor()
            cur.execute(
                """SELECT id,contribution_type,points,recovery_id FROM reward_transactions
                   WHERE user_id=%s""",
                (finder_id,),
            )
            entries = cur.fetchall()
            assert len(entries) == 1
            assert tuple(entries[0][1:]) == ("successful_return", 100, item_id)
            award_id = entries[0][0]
            cur.execute(
                """SELECT b.name FROM user_reward_badges ub
                   JOIN reward_badges b ON b.id=ub.badge_id WHERE ub.user_id=%s""",
                (finder_id,),
            )
            assert cur.fetchone()[0] == "Helpful Member"
            cur.execute(
                "SELECT COUNT(*) FROM notifications WHERE user_id=%s AND notification_type='reward'",
                (finder_id,),
            )
            assert cur.fetchone()[0] == 2

            assert grant_successful_return_reward(cur, finder_id, item_id, staff_id) is False
            mysql.connection.commit()
            cur.execute(
                "SELECT COUNT(*) FROM reward_transactions WHERE user_id=%s",
                (finder_id,),
            )
            assert cur.fetchone()[0] == 1
            cur.close()

        with app.test_client() as client:
            with client.session_transaction() as session:
                session.update(
                    user_id=finder_id, name="Reward Finder", role="user",
                    _csrf_token="reward-privacy-token",
                )
            response = client.post(
                "/rewards",
                data={
                    "reward_public": "on",
                    "reward_public_name": "Helpful Jaipur Helper",
                    "_csrf_token": "reward-privacy-token",
                },
            )
            assert response.status_code == 302
            response = client.get("/rewards")
            assert b"Helpful Jaipur Helper" in response.data
            with app.app_context():
                cur = mysql.connection.cursor()
                cur.execute("SELECT email FROM users WHERE id=%s", (finder_id,))
                private_email = cur.fetchone()[0].encode()
                cur.close()
            assert private_email not in response.data

        with app.test_client() as client:
            with client.session_transaction() as session:
                session.update(
                    user_id=admin_id, name="Reward Admin", role="admin",
                    _csrf_token="reward-reversal-token",
                )
            post_data = {
                "reason": "Verified handover was recorded in error",
                "_csrf_token": "reward-reversal-token",
            }
            response = client.post(f"/admin/rewards/reverse/{award_id}", data=post_data)
            assert response.status_code == 302
            response = client.post(f"/admin/rewards/reverse/{award_id}", data=post_data)
            assert response.status_code == 409

        with app.app_context():
            cur = mysql.connection.cursor()
            cur.execute(
                """SELECT COALESCE(SUM(points),0) FROM reward_transactions
                   WHERE user_id=%s""",
                (finder_id,),
            )
            assert cur.fetchone()[0] == 0
            cur.execute(
                "SELECT COUNT(*) FROM user_reward_badges WHERE user_id=%s",
                (finder_id,),
            )
            assert cur.fetchone()[0] == 0
            cur.execute(
                """SELECT COUNT(*) FROM audit_logs
                   WHERE action='reward_return_reversed' AND entity_id IN (
                     SELECT id FROM reward_transactions WHERE user_id=%s AND contribution_type='reversal'
                   )""",
                (finder_id,),
            )
            assert cur.fetchone()[0] == 1
            cur.close()
    finally:
        with app.app_context():
            cur = mysql.connection.cursor()
            cur.execute("DELETE FROM notifications WHERE user_id IN (%s,%s,%s)", (finder_id, claimant_id, admin_id))
            cur.execute("DELETE FROM audit_logs WHERE entity_id=%s", (item_id,))
            cur.execute(
                """DELETE FROM audit_logs WHERE action='reward_return_reversed'
                   AND entity_id IN (SELECT id FROM reward_transactions
                                     WHERE user_id=%s AND contribution_type='reversal')""",
                (finder_id,),
            )
            cur.execute("DELETE FROM items WHERE id=%s", (item_id,))
            cur.execute(
                "DELETE FROM users WHERE id IN (%s,%s,%s,%s)",
                (finder_id, claimant_id, staff_id, admin_id),
            )
            mysql.connection.commit()
            cur.close()


def test_helpful_clue_requires_moderation_and_cannot_be_self_approved():
    with app.app_context():
        cur = mysql.connection.cursor()
        finder_id = _create_user(cur, "Clue Finder")
        contributor_id = _create_user(cur, "Clue Contributor", "admin")
        admin_id = _create_user(cur, "Clue Admin", "admin")
        cur.execute(
            """INSERT INTO items
               (user_id,item_name,category,description,location,event_date,status)
               VALUES(%s,'Clue test bag','Bag','Bag description','Surat',
                      '2026-10-08','Found')""",
            (finder_id,),
        )
        item_id = cur.lastrowid
        mysql.connection.commit()
        cur.close()

    try:
        with app.test_client() as client:
            with client.session_transaction() as session:
                session.update(
                    user_id=contributor_id, name="Clue Contributor", role="user",
                    _csrf_token="reward-clue-token",
                )
            response = client.post(
                f"/item/{item_id}/helpful-clue",
                data={"details": "A possible lead from the station desk.", "_csrf_token": "reward-clue-token"},
            )
            assert response.status_code == 302
            response = client.post(
                f"/item/{item_id}/helpful-clue",
                data={"details": "A duplicate clue must not earn more points.", "_csrf_token": "reward-clue-token"},
            )
            assert response.status_code == 302
            with app.app_context():
                cur = mysql.connection.cursor()
                cur.execute(
                    "SELECT id,status FROM reward_contributions WHERE user_id=%s AND item_id=%s",
                    (contributor_id, item_id),
                )
                contribution_id, status = cur.fetchone()
                assert status == "Pending"
                cur.execute(
                    "SELECT COUNT(*) FROM reward_transactions WHERE user_id=%s",
                    (contributor_id,),
                )
                assert cur.fetchone()[0] == 0
                cur.close()

            with client.session_transaction() as session:
                session.update(user_id=contributor_id, name="Clue Contributor", role="admin")
            response = client.post(
                f"/admin/rewards/clue/{contribution_id}/approve",
                data={"_csrf_token": "reward-clue-token"},
            )
            assert response.status_code == 403

            with client.session_transaction() as session:
                session.update(user_id=admin_id, name="Clue Admin", role="admin")
            response = client.post(
                f"/admin/rewards/clue/{contribution_id}/approve",
                data={"_csrf_token": "reward-clue-token"},
            )
            assert response.status_code == 302

            response = client.get("/admin/rewards")
            assert response.status_code == 200
            assert b"Recent reward ledger" in response.data

            with app.app_context():
                cur = mysql.connection.cursor()
                cur.execute(
                    """SELECT status FROM reward_contributions WHERE id=%s""",
                    (contribution_id,),
                )
                assert cur.fetchone()[0] == "Approved"
                cur.execute(
                    """SELECT points FROM reward_transactions
                       WHERE user_id=%s AND contribution_id=%s""",
                    (contributor_id, contribution_id),
                )
                assert cur.fetchone()[0] == 10
                cur.close()

        with app.test_client() as client:
            with client.session_transaction() as session:
                session.update(
                    user_id=contributor_id, name="Clue Contributor", role="user",
                    _csrf_token="reward-clue-token",
                )
            response = client.post(
                "/rewards",
                data={
                    "reward_public": "on",
                    "reward_public_name": "Helpful Clue Contributor",
                    "_csrf_token": "reward-clue-token",
                },
            )
            assert response.status_code == 302
            response = client.get("/rewards")
            assert response.status_code == 200
            assert b"10 points" in response.data
            assert b"Helpful Clue Contributor" in response.data
            assert b"Approved clues" in response.data
            with app.app_context():
                cur = mysql.connection.cursor()
                cur.execute("SELECT email FROM users WHERE id=%s", (contributor_id,))
                private_email = cur.fetchone()[0].encode()
                cur.close()
            assert private_email not in response.data

            with client.session_transaction() as session:
                session.update(user_id=admin_id, name="Clue Admin", role="admin")
            response = client.post(
                f"/admin/rewards/adjust",
                data={
                    "user_id": admin_id,
                    "points": "5",
                    "reason": "Self adjustment should be forbidden",
                    "_csrf_token": "reward-clue-token",
                },
            )
            assert response.status_code == 403

        with app.app_context():
            cur = mysql.connection.cursor()
            cur.execute(
                "SELECT reward_public,reward_public_name FROM users WHERE id=%s",
                (contributor_id,),
            )
            assert tuple(cur.fetchone()) == (1, "Helpful Clue Contributor")
            cur.close()
    finally:
        with app.app_context():
            cur = mysql.connection.cursor()
            cur.execute("DELETE FROM notifications WHERE user_id IN (%s,%s,%s)", (finder_id, contributor_id, admin_id))
            cur.execute("DELETE FROM items WHERE id=%s", (item_id,))
            cur.execute(
                "DELETE FROM users WHERE id IN (%s,%s,%s)",
                (finder_id, contributor_id, admin_id),
            )
            mysql.connection.commit()
            cur.close()


def test_non_admin_cannot_review_or_adjust_rewards():
    with app.app_context():
        cur = mysql.connection.cursor()
        finder_id = _create_user(cur, "Authorization Finder")
        contributor_id = _create_user(cur, "Authorization Contributor")
        cur.execute(
            """INSERT INTO items
               (user_id,item_name,category,description,location,event_date,status)
               VALUES(%s,'Authorization item','Other','Description','Indore',
                      '2026-10-08','Found')""",
            (finder_id,),
        )
        item_id = cur.lastrowid
        cur.execute(
            """INSERT INTO reward_contributions(user_id,item_id,contribution_type,details)
               VALUES(%s,%s,'helpful_clue','A useful clue for the owner.')""",
            (contributor_id, item_id),
        )
        contribution_id = cur.lastrowid
        mysql.connection.commit()
        cur.close()

    try:
        with app.test_client() as client:
            with client.session_transaction() as session:
                session.update(
                    user_id=contributor_id, name="Authorization Contributor", role="user",
                    _csrf_token="reward-auth-token",
                )
            response = client.post(
                f"/admin/rewards/clue/{contribution_id}/approve",
                data={"_csrf_token": "reward-auth-token"},
            )
            assert response.status_code == 403
            response = client.post(
                "/admin/rewards/adjust",
                data={
                    "user_id": finder_id,
                    "points": "100",
                    "reason": "Unauthorized manual points",
                    "_csrf_token": "reward-auth-token",
                },
            )
            assert response.status_code == 403
    finally:
        with app.app_context():
            cur = mysql.connection.cursor()
            cur.execute("DELETE FROM items WHERE id=%s", (item_id,))
            cur.execute(
                "DELETE FROM users WHERE id IN (%s,%s)",
                (finder_id, contributor_id),
            )
            mysql.connection.commit()
            cur.close()


def test_closed_report_and_rejected_claim_do_not_award_return_points():
    with app.app_context():
        cur = mysql.connection.cursor()
        finder_id = _create_user(cur, "Closed Report Finder")
        claimant_id = _create_user(cur, "Rejected Claimant")
        staff_id = _create_user(cur, "Closed Report Security", "security")
        cur.execute(
            """INSERT INTO items
               (user_id,item_name,category,description,location,event_date,status)
               VALUES(%s,'Closed reward report','Other','Closed report','Bhopal',
                      '2026-10-08','Closed')""",
            (finder_id,),
        )
        item_id = cur.lastrowid
        cur.execute(
            """INSERT INTO claims(item_id,user_id,proof,status)
               VALUES(%s,%s,'Claim was rejected','Rejected')""",
            (item_id, claimant_id),
        )
        mysql.connection.commit()
        cur.close()

    try:
        with app.test_client() as client:
            with client.session_transaction() as session:
                session.update(
                    user_id=staff_id, name="Closed Report Security", role="security",
                    _csrf_token="closed-reward-token",
                )
            response = client.post(
                f"/admin/item/{item_id}/handover",
                data={
                    "receiver_name": "Rejected Claimant",
                    "receiver_confirmed": "on",
                    "_csrf_token": "closed-reward-token",
                },
            )
            assert response.status_code == 400

        with app.app_context():
            cur = mysql.connection.cursor()
            cur.execute(
                "SELECT COUNT(*) FROM reward_transactions WHERE user_id=%s",
                (finder_id,),
            )
            assert cur.fetchone()[0] == 0
            cur.close()
    finally:
        with app.app_context():
            cur = mysql.connection.cursor()
            cur.execute("DELETE FROM items WHERE id=%s", (item_id,))
            cur.execute(
                "DELETE FROM users WHERE id IN (%s,%s,%s)",
                (finder_id, claimant_id, staff_id),
            )
            mysql.connection.commit()
            cur.close()
