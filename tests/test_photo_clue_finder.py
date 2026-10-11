import os
from io import BytesIO
from uuid import uuid4

import pytest
from PIL import Image, ImageColor

import app as findit
from app import app, mysql


def _image_bytes(color="navy"):
    image = Image.new("RGB", (72, 72), "white")
    rgb = ImageColor.getrgb(color)
    for x in range(12, 60):
        for y in range(12, 60):
            image.putpixel((x, y), rgb)
    output = BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def _create_user(cur, suffix, label):
    cur.execute(
        "INSERT INTO users(name,email,password,role) VALUES(%s,%s,'x','user')",
        (label, f"photo-clue-{suffix}-{label.lower().replace(' ', '-')}@example.test"),
    )
    return cur.lastrowid


def test_photo_upload_validation_accepts_and_sanitizes_real_image():
    photo_name, photo_bytes = findit.sanitize_item_photo(
        findit.FileStorage(stream=BytesIO(_image_bytes()), filename="reference.png")
    )
    assert photo_name.endswith(".jpg")
    with Image.open(BytesIO(photo_bytes)) as sanitized:
        assert sanitized.format == "JPEG"
        assert sanitized.size == (72, 72)


@pytest.mark.parametrize(
    ("filename", "data", "error"),
    [
        ("photo.gif", b"GIF89a", "JPEG, PNG, or WEBP"),
        ("photo.png", b"not an image", "not a valid JPEG"),
        ("photo.jpg", _image_bytes(), "contents do not match"),
    ],
)
def test_photo_upload_validation_rejects_invalid_types_and_contents(filename, data, error):
    with pytest.raises(ValueError, match=error):
        findit.sanitize_item_photo(
            findit.FileStorage(stream=BytesIO(data), filename=filename)
        )


def test_opposite_type_photo_matching_combines_visual_and_smart_signals_without_private_data(
    monkeypatch, tmp_path
):
    suffix = uuid4().hex
    candidate_photo = tmp_path / f"candidate-{suffix}.jpg"
    with Image.open(BytesIO(_image_bytes())) as image:
        image.convert("RGB").save(candidate_photo, format="JPEG")
    monkeypatch.setitem(app.config, "UPLOAD_FOLDER", str(tmp_path))
    monkeypatch.setitem(app.config, "OPENAI_API_KEY", "")
    monkeypatch.setitem(app.config, "AI_IMAGE_MATCHING", True)

    with app.app_context():
        cur = mysql.connection.cursor()
        finder_id = _create_user(cur, suffix, "Photo Finder")
        owner_id = _create_user(cur, suffix, "Photo Owner")
        cur.execute(
            """INSERT INTO items
               (user_id,item_name,category,description,location,event_date,status,image,
                private_details,color)
               VALUES(%s,%s,'Electronics','Navy headphones in a case','Pune campus',
                      '2026-10-09','Found',%s,%s,'navy')""",
            (
                owner_id,
                f"Found headphones {suffix}",
                candidate_photo.name,
                f"never expose hidden maker mark {suffix}",
            ),
        )
        found_item_id = cur.lastrowid
        mysql.connection.commit()
        cur.close()

    semantic_calls = []

    def fake_semantic_scores(_cur, _query, candidates):
        semantic_calls.append(candidates)
        return {row[0]: 0.72 for row in candidates}, "Test semantic model"

    monkeypatch.setattr(findit, "semantic_description_scores", fake_semantic_scores)
    report_data = {
        "item_type": "Lost",
        "item_name": f"Wireless headphones {suffix}",
        "category": "Electronics",
        "description": "Navy headphones in a case",
        "location": "Pune campus",
        "city": "Pune",
        "event_date": "2026-10-09",
        "color": "navy",
        "photo_search_only": "yes",
        "_csrf_token": "photo-clue-test-token",
        "image": (BytesIO(_image_bytes()), "reference.png", "image/png"),
    }
    saved_photo_path = None
    try:
        with app.test_client() as client:
            with client.session_transaction() as session:
                session.update(
                    user_id=finder_id,
                    name="Photo Finder",
                    role="user",
                    _csrf_token="photo-clue-test-token",
                )
            response = client.post(
                "/report",
                data=report_data,
                content_type="multipart/form-data",
            )
        page = response.get_data(as_text=True)
        assert response.status_code == 200
        assert f"Found headphones {suffix}" in page
        assert "Photo search complete." in page
        assert "No new report was published." in page
        assert "Photo clue:" in page
        assert "overall appearance, including shape, color, and visible patterns" in page
        assert "not proof of ownership" in page.lower()
        assert f"never expose hidden maker mark {suffix}" not in page
        assert len(semantic_calls) == 1
        assert str(found_item_id) in page or "Found headphones" in page
        assert "AI visual comparison" not in page
        with app.app_context():
            cur = mysql.connection.cursor()
            cur.execute(
                "SELECT COUNT(*) FROM items WHERE user_id=%s AND item_name=%s",
                (finder_id, f"Wireless headphones {suffix}"),
            )
            assert cur.fetchone()[0] == 0
            cur.close()

        report_data.pop("photo_search_only")
        report_data["confirm_report"] = "yes"
        report_data["image"] = (
            BytesIO(_image_bytes()), "reference.png", "image/png"
        )
        with app.test_client() as client:
            with client.session_transaction() as session:
                session.update(
                    user_id=finder_id,
                    name="Photo Finder",
                    role="user",
                    _csrf_token="photo-clue-test-token",
                )
            response = client.post(
                "/report", data=report_data, content_type="multipart/form-data"
            )
        assert response.status_code == 302
        with app.app_context():
            cur = mysql.connection.cursor()
            cur.execute(
                """SELECT id,image FROM items
                   WHERE user_id=%s AND item_name=%s AND status='Lost'""",
                (finder_id, f"Wireless headphones {suffix}"),
            )
            reported_id, saved_image = cur.fetchone()
            cur.close()
        assert saved_image.endswith(".jpg")
        saved_photo_path = tmp_path / saved_image
        with Image.open(saved_photo_path) as saved:
            assert saved.format == "JPEG"
            assert not saved.getexif()
    finally:
        with app.app_context():
            cur = mysql.connection.cursor()
            cur.execute(
                "SELECT id,image FROM items WHERE user_id=%s AND item_name=%s",
                (finder_id, f"Wireless headphones {suffix}"),
            )
            created_report = cur.fetchone()
            if created_report:
                cur.execute(
                    "DELETE FROM notifications WHERE item_id=%s OR matched_item_id=%s",
                    (created_report[0], created_report[0]),
                )
                cur.execute(
                    "DELETE FROM audit_logs WHERE entity_type='item' AND entity_id=%s",
                    (created_report[0],),
                )
                cur.execute("DELETE FROM items WHERE id=%s", (created_report[0],))
            cur.execute(
                """DELETE FROM notifications WHERE item_id=%s OR matched_item_id=%s""",
                (found_item_id, found_item_id),
            )
            cur.execute(
                "DELETE FROM audit_logs WHERE entity_type='item' AND entity_id=%s",
                (found_item_id,),
            )
            cur.execute("DELETE FROM items WHERE id=%s", (found_item_id,))
            cur.execute("DELETE FROM users WHERE id IN (%s,%s)", (finder_id, owner_id))
            mysql.connection.commit()
            cur.close()
        if saved_photo_path and saved_photo_path.is_file():
            os.remove(saved_photo_path)


def test_external_visual_matching_requires_explicit_consent_and_omits_reporter_data(
    monkeypatch,
):
    monkeypatch.setitem(app.config, "OPENAI_API_KEY", "configured-test-key")
    monkeypatch.setitem(app.config, "AI_IMAGE_MATCHING", True)
    calls = []

    def fake_ai_similarity(upload, candidates, include_explanations=False):
        calls.append((upload.filename, candidates))
        return {
            candidate["id"]: (
                {"score": 0.91, "explanation": "Navy color and over-ear shape appear similar."}
                if include_explanations else 0.91
            )
            for candidate in candidates
        }

    monkeypatch.setattr(findit, "ai_image_similarity", fake_ai_similarity)
    matches = [{
        "id": 9,
        "name": "Found bag",
        "image": "candidate.jpg",
        "score": 70,
        "reasons": ["Category agrees"],
        "private_details": "not passed to external AI",
    }]
    upload = findit.FileStorage(stream=BytesIO(b"encoded-photo"), filename="query.jpg")

    findit.add_visual_scores(upload, matches, allow_ai_visual=False)
    assert calls == []

    findit.add_visual_scores(upload, matches, allow_ai_visual=True)
    assert len(calls) == 1
    assert "private_details" not in calls[0][1][0]
    assert matches[0]["image_score"] == 91
    assert matches[0]["match_method"] == "AI visual + metadata"
    assert matches[0]["visual_explanation"] == "Navy color and over-ear shape appear similar."


def test_visual_score_ranks_the_closest_candidate_and_provider_failure_uses_local_fallback(
    monkeypatch,
):
    monkeypatch.setitem(app.config, "OPENAI_API_KEY", "configured-test-key")
    monkeypatch.setitem(app.config, "AI_IMAGE_MATCHING", True)
    monkeypatch.setattr(
        findit, "photo_similarity",
        lambda _upload, filename: {"closest.jpg": 0.95, "other.jpg": 0.1}[filename],
    )
    monkeypatch.setattr(
        findit, "ai_image_similarity", lambda *_args, **_kwargs: {}
    )
    matches = [
        {"id": 1, "image": "other.jpg", "score": 75, "reasons": []},
        {"id": 2, "image": "closest.jpg", "score": 75, "reasons": []},
    ]
    upload = findit.FileStorage(stream=BytesIO(b"test-image"), filename="query.jpg")
    ranked = findit.add_visual_scores(upload, matches, allow_ai_visual=True)
    assert [match["id"] for match in ranked] == [2, 1]
    assert ranked[0]["image_score"] == 95
    assert ranked[0]["match_method"] == "Photo similarity + metadata"
    assert "local perceptual-image similarity" in ranked[0]["reasons"][0]


def test_openai_visual_explanation_is_optional_and_limited_to_visible_details(
    monkeypatch, tmp_path
):
    import json

    monkeypatch.setitem(app.config, "OPENAI_API_KEY", "photo-test-key")
    image = BytesIO()
    Image.new("RGB", (16, 16), "navy").save(image, format="PNG")
    image.seek(0)
    Image.new("RGB", (16, 16), "navy").save(tmp_path / "candidate.jpg", format="JPEG")
    monkeypatch.setitem(app.config, "UPLOAD_FOLDER", str(tmp_path))
    request_payload = {}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            content = json.dumps({
                "matches": [{
                    "index": 1,
                    "score": 84,
                    "explanation": "Navy color and rounded ear cups look similar.",
                }],
            })
            return json.dumps({
                "choices": [{"message": {"content": content}}],
            }).encode()

    def fake_urlopen(request, **_kwargs):
        request_payload.update(json.loads(request.data))
        return FakeResponse()

    monkeypatch.setattr(findit.urllib.request, "urlopen", fake_urlopen)
    scores = findit.ai_image_similarity(
        findit.FileStorage(stream=image, filename="reference.png"),
        [{"id": 55, "image": "candidate.jpg"}],
        include_explanations=True,
    )
    assert scores == {
        55: {
            "score": 0.84,
            "explanation": "Navy color and rounded ear cups look similar.",
        },
    }
    prompt = request_payload["messages"][0]["content"][0]["text"]
    assert "readable brand/model" in prompt
    assert "not proof the objects are identical" in prompt


def test_same_type_duplicate_matching_includes_a_separate_photo_clue_score(
    monkeypatch, tmp_path
):
    candidate_path = tmp_path / "duplicate.jpg"
    with Image.open(BytesIO(_image_bytes())) as image:
        image.save(candidate_path, format="JPEG")
    monkeypatch.setitem(app.config, "UPLOAD_FOLDER", str(tmp_path))

    class Cursor:
        def execute(self, *_args, **_kwargs):
            return self

        def fetchall(self):
            return [(
                71, "Lost navy headphones", "Electronics",
                "Navy headphones in a case", "Pune campus", "2026-10-09",
                "Lost", None, "navy", candidate_path.name, 12, 0,
                "10:00", "Pune", "Maharashtra",
            )]

    matches = findit.rank_item_matches(
        Cursor(),
        {
            "item_name": "Navy headphones",
            "category": "Electronics",
            "description": "Navy headphones in a case",
            "location": "Pune campus",
            "event_date": "2026-10-09",
            "event_time": "10:00",
            "color": "navy",
        },
        ["Lost"],
        image_upload=findit.FileStorage(
            stream=BytesIO(_image_bytes()), filename="reference.png"
        ),
    )
    assert len(matches) == 1
    assert matches[0]["image_score"] is not None
    assert matches[0]["score"] > 0


def test_report_displays_photo_clue_consent_and_server_validates_uploaded_image():
    with app.test_client() as client:
        with client.session_transaction() as session:
            session.update(user_id=1, name="Photo Form User", role="user")
        response = client.get("/report")
    page = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "FindIt AI Photo Clue Finder" in page
    assert "I consent to send this photo" in page
    assert 'accept="image/jpeg,image/png,image/webp"' in page
    assert "Do not upload faces, private documents" in page
    assert "Find possible reports using this photo" in page


def test_report_post_rejects_forged_image_payload():
    with app.test_client() as client:
        with client.session_transaction() as session:
            session.update(
                user_id=1, name="Photo Form User", role="user",
                _csrf_token="invalid-photo-test-token",
            )
        response = client.post(
            "/report",
            data={
                "item_type": "Lost",
                "item_name": "Test backpack",
                "category": "Bag",
                "description": "Blue backpack",
                "location": "Campus",
                "event_date": "2026-10-09",
                "_csrf_token": "invalid-photo-test-token",
                "image": (BytesIO(b"not an image"), "backpack.png", "image/png"),
            },
            content_type="multipart/form-data",
        )
    assert response.status_code == 200
    assert b"not a valid JPEG, PNG, or WEBP" in response.data


def test_report_upload_limit_returns_payload_too_large():
    with app.test_client() as client:
        with client.session_transaction() as session:
            session.update(
                user_id=1, name="Photo Form User", role="user",
                _csrf_token="oversized-photo-test-token",
            )
        response = client.post(
            "/report",
            data={
                "item_type": "Lost",
                "item_name": "Test backpack",
                "category": "Bag",
                "description": "Blue backpack",
                "location": "Campus",
                "event_date": "2026-10-09",
                "_csrf_token": "oversized-photo-test-token",
                "image": (BytesIO(b"x" * (5 * 1024 * 1024)), "large.jpg", "image/jpeg"),
            },
            content_type="multipart/form-data",
        )
    assert response.status_code == 413
