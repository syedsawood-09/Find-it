from uuid import uuid4

import app as findit
from app import app, mysql


def _candidate(
    category="Electronics",
    description="A phone with a cracked screen",
    location="Connaught Place, Delhi",
    event_date="2026-10-08",
    brand=None,
    color=None,
):
    return (
        1, "Phone", category, description, location, event_date, "Found",
        brand, color, None, 2, 0, None,
    )


def test_smart_match_weights_and_missing_signals_are_normalized():
    query = {"description": "A phone with a cracked screen"}
    score, _ = findit._smart_match_score(query, _candidate(), 0.8)
    assert score == 80

    query_with_optional_signals = {
        **query, "category": "Electronics", "color": "Blue",
    }
    without_color = _candidate(color=None)
    with_color = _candidate(color="Blue")
    missing_score, _ = findit._smart_match_score(
        query_with_optional_signals, without_color, 0.8
    )
    baseline_score, _ = findit._smart_match_score(
        {**query_with_optional_signals, "color": None}, without_color, 0.8
    )
    matching_score, _ = findit._smart_match_score(
        query_with_optional_signals, with_color, 0.8
    )
    assert missing_score == baseline_score
    assert matching_score > missing_score


def test_smart_matching_uses_lexical_fallback_when_transformers_are_missing(monkeypatch):
    import builtins

    original_import = builtins.__import__

    def import_without_transformers(name, *args, **kwargs):
        if name == "sentence_transformers":
            raise ImportError("Sentence Transformers disabled for fallback test")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", import_without_transformers)
    monkeypatch.setattr(findit, "_smart_match_model", None)
    scores, method = findit.semantic_description_scores(
        None,
        "Black umbrella left on the train",
        [(9, "Umbrella", "Other", "Black umbrella found on the train")],
    )
    assert method == "Lexical fallback"
    assert 0 < scores[9] <= 1


def test_smart_search_ranks_active_matches_and_excludes_ineligible_reports(monkeypatch):
    suffix = uuid4().hex
    with app.app_context():
        cur = mysql.connection.cursor()
        cur.execute(
            "INSERT INTO users(name,email,password,role) VALUES(%s,%s,%s,'user')",
            ("Smart Match Test", f"smart-match-{suffix}@example.test", "x"),
        )
        user_id = cur.lastrowid
        cur.execute(
            "INSERT INTO users(name,email,password,role) VALUES(%s,%s,%s,'user')",
            ("Smart Match Reporter", f"smart-reporter-{suffix}@example.test", "x"),
        )
        reporter_id = cur.lastrowid
        item_ids = []
        for name, status in (
            ("Best semantic match", "Found"),
            ("Second semantic match", "Claim Submitted"),
            ("Returned report", "Returned"),
            ("Closed report", "Closed"),
        ):
            cur.execute(
                """INSERT INTO items
                   (user_id,item_name,category,description,location,event_date,status)
                   VALUES(%s,%s,'Electronics','A phone with a cracked screen',
                          'Connaught Place, Delhi','2026-10-08',%s)""",
                (user_id, f"{name} {suffix}", status),
            )
            item_ids.append(cur.lastrowid)
        mysql.connection.commit()
        cur.close()

    def fake_semantic_scores(_cur, _query, candidates):
        return {
            row[0]: (
                0.95 if row[1].startswith("Best semantic")
                else 0.55 if row[1].startswith("Second semantic")
                else 0.0
            )
            for row in candidates
        }, "Test semantic model"

    monkeypatch.setattr(findit, "semantic_description_scores", fake_semantic_scores)
    try:
        with app.test_client() as client:
            response = client.get(
                "/items?q=phone%20with%20a%20cracked%20screen&smart=1&status=Lost"
            )
            page = response.get_data(as_text=True)
            assert response.status_code == 200
            assert page.index(f"Best semantic match {suffix}") < page.index(
                f"Second semantic match {suffix}"
            )
            assert "Returned report" not in page
            assert "Closed report" not in page
            assert "Why suggested:" in page
            assert "not a probability or proof of ownership" in page

            with client.session_transaction() as session:
                session.update(
                    user_id=reporter_id, name="Smart Match Reporter", role="user",
                    _csrf_token="smart-match-test-token",
                )
            report_data = {
                "item_type": "Lost",
                "item_name": f"Test lost report {suffix}",
                "category": "Electronics",
                "description": "A phone with a cracked screen",
                "location": "Connaught Place, Delhi",
                "event_date": "2026-10-08",
                "_csrf_token": "smart-match-test-token",
            }
            response = client.post("/report", data=report_data)
            report_page = response.get_data(as_text=True)
            assert response.status_code == 200
            assert "Smart possible matches" in report_page
            assert "Best semantic match" in report_page

            response = client.post(
                "/report", data={**report_data, "confirm_report": "yes"}
            )
            assert response.status_code == 302
            assert response.location.endswith("/dashboard")
            with app.app_context():
                cur = mysql.connection.cursor()
                cur.execute(
                    """SELECT notification_type,item_id,matched_item_id,match_score,explanation
                       FROM notifications WHERE user_id=%s AND notification_type='match'
                         AND item_id=(SELECT id FROM items WHERE user_id=%s AND item_name=%s)""",
                    (reporter_id, reporter_id, f"Test lost report {suffix}"),
                )
                match_notifications = cur.fetchall()
                cur.close()
            assert len(match_notifications) == 2
            assert all(row[0] == "match" and row[1] is not None for row in match_notifications)
            assert all(row[2] in item_ids and row[3] >= 50 and row[4] for row in match_notifications)
    finally:
        with app.app_context():
            cur = mysql.connection.cursor()
            cur.execute("DELETE FROM notifications WHERE user_id=%s", (reporter_id,))
            cur.execute(
                "SELECT id FROM items WHERE item_name=%s AND user_id=%s",
                (f"Test lost report {suffix}", reporter_id),
            )
            reported_item = cur.fetchone()
            if reported_item:
                cur.execute("DELETE FROM audit_logs WHERE entity_id=%s", (reported_item[0],))
                cur.execute("DELETE FROM items WHERE id=%s", (reported_item[0],))
            cur.execute(
                "DELETE FROM notifications WHERE message LIKE %s",
                (f"Possible match: Test lost report {suffix}%",),
            )
            cur.execute(
                "DELETE FROM items WHERE id IN (%s,%s,%s,%s)",
                tuple(item_ids),
            )
            cur.execute("DELETE FROM users WHERE id IN (%s,%s)", (user_id, reporter_id))
            mysql.connection.commit()
            cur.close()
