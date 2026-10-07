import os
import sys
from uuid import uuid4

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app import app, mysql


def test_handover_requires_receiver_confirmation_and_creates_receipt():
    suffix = uuid4().hex
    with app.app_context():
        cur = mysql.connection.cursor()
        cur.execute(
            "INSERT INTO users(name,email,password,role) VALUES(%s,%s,%s,'user')",
            ("Receipt Reporter", f"receipt-reporter-{suffix}@example.test", "x"),
        )
        reporter_id = cur.lastrowid
        cur.execute(
            "INSERT INTO users(name,email,password,role) VALUES(%s,%s,%s,'user')",
            ("Receipt Receiver", f"receipt-receiver-{suffix}@example.test", "x"),
        )
        receiver_id = cur.lastrowid
        cur.execute(
            "INSERT INTO users(name,email,password,role) VALUES(%s,%s,%s,'security')",
            ("Receipt Security", f"receipt-security-{suffix}@example.test", "x"),
        )
        staff_id = cur.lastrowid
        cur.execute(
            """INSERT INTO items
               (user_id,item_name,category,description,location,event_date,status)
               VALUES(%s,%s,'Other','Receipt test item','Campus','2026-10-06','Verification')""",
            (reporter_id, f"Receipt Test {suffix}"),
        )
        item_id = cur.lastrowid
        cur.execute(
            """INSERT INTO claims(item_id,user_id,proof,status)
               VALUES(%s,%s,'Test ownership proof','Approved')""",
            (item_id, receiver_id),
        )
        claim_id = cur.lastrowid
        mysql.connection.commit()
        cur.close()

    try:
        with app.test_client() as client:
            with client.session_transaction() as session:
                session.update(user_id=staff_id, name="Receipt Security", role="security")
            response = client.post(
                f"/admin/item/{item_id}/handover",
                data={"receiver_name": "Receipt Receiver"},
            )
            assert response.status_code == 302
            assert response.location.endswith("/admin")

            response = client.post(
                f"/admin/item/{item_id}/handover",
                data={
                    "receiver_name": "Receipt Receiver",
                    "receiver_confirmed": "on",
                },
            )
            assert response.status_code == 302
            assert response.location.endswith(f"/item/{item_id}/handover-receipt")

            receipt_page = client.get(f"/item/{item_id}/handover-receipt")
            assert receipt_page.status_code == 200
            assert "Item Handover Receipt" in receipt_page.get_data(as_text=True)
            assert "Receipt Receiver" in receipt_page.get_data(as_text=True)
            assert "Receipt Security" in receipt_page.get_data(as_text=True)

        with app.app_context():
            cur = mysql.connection.cursor()
            cur.execute(
                """SELECT claim_id,receiver_id,receiver_name,receiver_confirmation,
                          authorized_staff_id,authorized_staff_name
                   FROM handover_receipts WHERE item_id=%s""",
                (item_id,),
            )
            receipt = cur.fetchone()
            cur.execute("SELECT status FROM items WHERE id=%s", (item_id,))
            item_status = cur.fetchone()[0]
            cur.close()
            assert tuple(receipt) == (
                claim_id, receiver_id, "Receipt Receiver", 1, staff_id, "Receipt Security"
            )
            assert item_status == "Returned"
    finally:
        with app.app_context():
            cur = mysql.connection.cursor()
            cur.execute("DELETE FROM audit_logs WHERE entity_id=%s", (item_id,))
            cur.execute("DELETE FROM notifications WHERE user_id=%s", (receiver_id,))
            cur.execute("DELETE FROM items WHERE id=%s", (item_id,))
            cur.execute(
                "DELETE FROM users WHERE id IN (%s,%s,%s)",
                (reporter_id, receiver_id, staff_id),
            )
            mysql.connection.commit()
            cur.close()
