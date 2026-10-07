import os
import sys
from io import BytesIO

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app import app, mysql


def test_claim_accepts_proof_image():
    with app.test_client() as client:
        with app.app_context():
            cur = mysql.connection.cursor()
            cur.execute("SELECT id FROM users WHERE email=%s", ("claimuser@example.com",))
            owner_id = cur.fetchone()
            if owner_id is None:
                cur.execute(
                    "INSERT INTO users(name,email,password,role) VALUES(%s,%s,%s,'user')",
                    ("Claim User", "claimuser@example.com", "hashedpass"),
                )
                mysql.connection.commit()
                cur.execute("SELECT id FROM users WHERE email=%s", ("claimuser@example.com",))
                owner_id = cur.fetchone()[0]
            else:
                owner_id = owner_id[0]

            cur.execute(
                "SELECT id FROM items WHERE item_name=%s AND user_id=%s",
                ("Test proof item", owner_id),
            )
            item_row = cur.fetchone()
            if item_row is None:
                cur.execute(
                    "INSERT INTO items(user_id,item_name,category,description,location,event_date,image,status,icon) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (owner_id, "Test proof item", "Wallet", "for test", "Main St", "2026-09-30", None, "Found", "fa-box"),
                )
                mysql.connection.commit()
                cur.execute("SELECT id FROM items WHERE item_name=%s AND user_id=%s", ("Test proof item", owner_id))
                item_id = cur.fetchone()[0]
            else:
                item_id = item_row[0]

            cur.execute("SELECT id FROM users WHERE email=%s", ("testclaimer@example.com",))
            claim_user = cur.fetchone()
            if claim_user is None:
                cur.execute(
                    "INSERT INTO users(name,email,password,role) VALUES(%s,%s,%s,'user')",
                    ("Test Claimer", "testclaimer@example.com", "hashedpass"),
                )
                mysql.connection.commit()
                cur.execute("SELECT id FROM users WHERE email=%s", ("testclaimer@example.com",))
                claim_user_id = cur.fetchone()[0]
            else:
                claim_user_id = claim_user[0]
            cur.close()

        with client.session_transaction() as sess:
            sess["user_id"] = claim_user_id
            sess["name"] = "Tester"
            sess["role"] = "user"

        response = client.post(
            f"/claim/{item_id}",
            data={
                "proof": "I own this item because of the engraved initials.",
                "proof_image": (BytesIO(b"fake-image-content"), "proof.png"),
            },
            content_type="multipart/form-data",
            follow_redirects=False,
        )

        with app.app_context():
            cur = mysql.connection.cursor()
            cur.execute(
                "SELECT proof_image FROM claims WHERE item_id=%s AND user_id=%s ORDER BY id DESC LIMIT 1",
                (item_id, claim_user_id),
            )
            row = cur.fetchone()
            cur.close()

        assert response.status_code == 302
        assert row is not None and row[0] is not None
