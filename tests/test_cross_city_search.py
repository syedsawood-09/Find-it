from uuid import uuid4

import app as findit
from app import app, mysql


def _user(cur, suffix):
    cur.execute(
        "INSERT INTO users(name,email,password,role) VALUES(%s,%s,'x','user')",
        ("Cross City Test", f"cross-city-{suffix}@example.test"),
    )
    return cur.lastrowid


def test_city_alias_state_date_filters_and_pagination():
    suffix = uuid4().hex
    with app.app_context():
        cur = mysql.connection.cursor()
        user_id = _user(cur, suffix)
        item_ids = []
        for index in range(22):
            cur.execute(
                """INSERT INTO items
                   (user_id,item_name,category,description,location,event_date,status,city,state)
                   VALUES(%s,%s,'Electronics','Blue phone found near a public station',
                          'Private address must not appear','2026-10-08','Found','Bengaluru','Karnataka')""",
                (user_id, f"Blue Phone {suffix} {index:02}",),
            )
            item_ids.append(cur.lastrowid)
        cur.execute(
            """INSERT INTO items
               (user_id,item_name,category,description,location,event_date,status,city,state)
               VALUES(%s,%s,'Electronics','Blue phone found in another state',
                      'Sensitive location','2026-10-08','Found','Mumbai','Maharashtra')""",
            (user_id, f"Blue Phone Mumbai {suffix}"),
        )
        other_city_id = cur.lastrowid
        cur.execute(
            """INSERT INTO items
               (user_id,item_name,category,description,location,event_date,status,city,state)
               VALUES(%s,%s,'Wallet','Brown wallet','Private','2026-09-01','Lost','Bengaluru','Karnataka')""",
            (user_id, f"Old wallet {suffix}"),
        )
        old_item_id = cur.lastrowid
        mysql.connection.commit()
        cur.close()

    try:
        with app.test_client() as client:
            response = client.get(
                "/items?q=blue%20phone&city=Bangalore&state=Karnataka"
                "&date_from=2026-10-01&date_to=2026-10-10&category=Electronics"
            )
            page = response.get_data(as_text=True)
            assert response.status_code == 200
            assert "Blue Phone" in page
            assert "Bengaluru, Karnataka" in page
            assert "Private address must not appear" not in page
            assert "Mumbai" not in page
            assert "Old wallet" not in page
            assert "Page 1 of 2" in page

            detail = client.get(f"/item/{item_ids[0]}").get_data(as_text=True)
            assert "Private address must not appear" not in detail
            assert "Bengaluru, Karnataka" in detail

            response = client.get(
                "/items?q=blue%20phone&city=Bengaluru,Mumbai"
                "&date_from=2026-10-01&date_to=2026-10-10"
                "&category=Electronics"
            )
            multi_city_page = response.get_data(as_text=True)
            assert response.status_code == 200
            assert f"Blue Phone Mumbai {suffix}" in multi_city_page

            response = client.get(
                "/items?q=blue%20phone&city=Bengaluru&state=Karnataka"
                "&date_from=2026-10-01&date_to=2026-10-10"
                "&category=Electronics&page=2"
            )
            page_two = response.get_data(as_text=True)
            assert response.status_code == 200
            assert "Page 2 of 2" in page_two
            assert "Next" not in page_two
            assert "Bengaluru, Karnataka" in page_two
    finally:
        with app.app_context():
            cur = mysql.connection.cursor()
            cur.execute(
                "DELETE FROM items WHERE id IN (%s)",
                (other_city_id,),
            )
            cur.execute("DELETE FROM items WHERE id=%s", (old_item_id,))
            cur.execute(
                "DELETE FROM items WHERE id IN (%s)" % ",".join(["%s"] * len(item_ids)),
                tuple(item_ids),
            )
            cur.execute("DELETE FROM users WHERE id=%s", (user_id,))
            mysql.connection.commit()
            cur.close()


def test_smart_cross_city_search_uses_location_signal_and_explains_distance(monkeypatch):
    query = {
        "description": "Blue phone",
        "city": "Bengaluru",
        "state": "Karnataka",
        "approx_lat": 12.9716,
        "approx_lng": 77.5946,
    }
    candidate = (
        1, "Blue phone", "Electronics", "Blue phone with a cracked case",
        "Public station", "2026-10-08", "Found", None, "Blue", None, 2, 0,
        None, "Bengaluru", "Karnataka", 12.98, 77.60,
    )
    score, reasons = findit._smart_match_score(query, candidate, 0.9)
    assert score > 75
    assert any("km apart" in reason for reason in reasons)

    monkeypatch.setattr(
        findit,
        "semantic_description_scores",
        lambda _cur, _query, rows: ({row[0]: 0.9 for row in rows}, "Test model"),
    )
    with app.app_context():
        cur = mysql.connection.cursor()
        user_id = _user(cur, uuid4().hex)
        cur.execute(
            """INSERT INTO items
               (user_id,item_name,category,description,location,event_date,status,city,state)
               VALUES(%s,'Blue phone','Electronics','Blue phone','Downtown',
                      '2026-10-08','Found','Bengaluru','Karnataka')""",
            (user_id,),
        )
        item_id = cur.lastrowid
        mysql.connection.commit()
        cur.close()
    try:
        with app.test_client() as client:
            response = client.get(
                "/items?q=blue%20phone&smart=1&city=Bengaluru&state=Karnataka"
            )
            page = response.get_data(as_text=True)
            assert response.status_code == 200
            assert "estimated match" in page
            assert "Bengaluru, Karnataka" in page
            assert "Report date:" in page
            assert "Why suggested:" in page
    finally:
        with app.app_context():
            cur = mysql.connection.cursor()
            cur.execute("DELETE FROM items WHERE id=%s", (item_id,))
            cur.execute("DELETE FROM users WHERE id=%s", (user_id,))
            mysql.connection.commit()
            cur.close()
