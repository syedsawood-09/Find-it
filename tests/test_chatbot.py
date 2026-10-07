import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app import app, generate_chatbot_answer


def test_chatbot_guides_lost_item_reports():
    with app.test_client() as client:
        response = client.post(
            "/api/chatbot",
            json={"message": "I lost my ID card yesterday."},
        )

    data = response.get_json()
    assert response.status_code == 200
    assert "choose Lost" in data["answer"]
    assert "possible matches" in data["answer"]
    assert data["items"] == []


def test_chatbot_explains_claims():
    with app.test_client() as client:
        response = client.post(
            "/api/chatbot",
            json={"message": "How do I claim an item?"},
        )

    data = response.get_json()
    assert response.status_code == 200
    assert "Which found item" in data["answer"]
    assert data["next_step"] == "claim-search"


def test_chatbot_searches_for_found_phones():
    with app.test_client() as client:
        response = client.post(
            "/api/chatbot",
            json={"message": "Show found phones."},
        )

    data = response.get_json()
    assert response.status_code == 200
    assert "answer" in data
    assert isinstance(data["items"], list)
    assert all({"name", "category", "location", "date", "url"} <= item.keys() for item in data["items"])


def test_chatbot_returns_configured_office_location(monkeypatch):
    monkeypatch.setitem(app.config, "LOST_FOUND_OFFICE", "Student Center, Room 104")
    with app.test_client() as client:
        response = client.post(
            "/api/chatbot",
            json={"message": "Where is the lost-and-found office?"},
        )

    data = response.get_json()
    assert response.status_code == 200
    assert data["answer"] == "The lost-and-found office is at Student Center, Room 104."


def test_chatbot_uses_ai_for_general_questions(monkeypatch):
    captured = {}

    def fake_generate_answer(message, conversation_history, language):
        captured["message"] = message
        captured["history"] = conversation_history
        captured["language"] = language
        return "A found-item report records something you picked up and turned in."

    monkeypatch.setitem(app.config, "OPENAI_API_KEY", "test-key")
    monkeypatch.setattr("app.generate_chatbot_answer", fake_generate_answer)
    with app.test_client() as client:
        response = client.post(
            "/api/chatbot",
            json={
                "message": "What is a found-item report?",
                "history": [{"role": "user", "content": "How do I report an item?"}],
                "language": "kn",
            },
        )

    assert response.status_code == 200
    assert response.get_json()["answer"].startswith("A found-item report")
    assert captured["message"] == "What is a found-item report?"
    assert captured["history"] == [{"role": "user", "content": "How do I report an item?"}]
    assert captured["language"] == "kn"


def test_chatbot_explains_found_report_steps_without_ai():
    with app.test_client() as client:
        response = client.post(
            "/api/chatbot",
            json={"message": "How do I report a found item?"},
        )

    data = response.get_json()
    assert response.status_code == 200
    assert "choose Found" in data["answer"]
    assert "when and where you found it" in data["answer"]


def test_chatbot_returns_localized_guidance():
    with app.test_client() as client:
        response = client.post(
            "/api/chatbot",
            json={"message": "How do I report a found item?", "language": "kn"},
        )

    data = response.get_json()
    assert response.status_code == 200
    assert "ಸಿಕ್ಕಿದ ವಸ್ತುವನ್ನು ವರದಿ ಮಾಡಲು" in data["answer"]


def test_chatbot_guides_hindi_and_kannada_report_questions_without_ai():
    with app.test_client() as client:
        hindi = client.post(
            "/api/chatbot",
            json={"message": "खोई हुई वस्तु की रिपोर्ट कैसे करें?", "language": "hi"},
        )
        kannada = client.post(
            "/api/chatbot",
            json={"message": "ಸಿಕ್ಕಿದ ವಸ್ತುವನ್ನು ವರದಿ ಮಾಡುವುದು ಹೇಗೆ?", "language": "kn"},
        )

    assert hindi.status_code == 200
    assert "आपकी वस्तु खोने का दुख है" in hindi.get_json()["answer"]
    assert kannada.status_code == 200
    assert "ಸಿಕ್ಕಿದ ವಸ್ತುವನ್ನು ವರದಿ ಮಾಡಲು" in kannada.get_json()["answer"]


def test_chatbot_reports_missing_ai_configuration(monkeypatch):
    monkeypatch.setitem(app.config, "OPENAI_API_KEY", "")
    with app.test_client() as client:
        response = client.post(
            "/api/chatbot",
            json={"message": "Can you explain how match alerts work?"},
        )

    assert response.status_code == 503
    assert "OPENAI_API_KEY" in response.get_json()["answer"]


def test_openai_chatbot_request_uses_configured_model_and_prompt(monkeypatch):
    captured = {}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return b'{"choices":[{"message":{"content":"Here is a helpful answer."}}]}'

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["authorization"] = request.get_header("Authorization")
        captured["timeout"] = timeout
        captured["payload"] = request.data.decode("utf-8")
        return FakeResponse()

    monkeypatch.setitem(app.config, "OPENAI_API_KEY", "test-key")
    monkeypatch.setitem(app.config, "OPENAI_MODEL", "test-model")
    monkeypatch.setattr("app.urllib.request.urlopen", fake_urlopen)

    answer = generate_chatbot_answer(
        "What should I do next?",
        [{"role": "user", "content": "I found a backpack."}],
        "kn",
    )

    assert answer == "Here is a helpful answer."
    assert captured["url"] == "https://api.openai.com/v1/chat/completions"
    assert captured["authorization"] == "Bearer test-key"
    assert captured["timeout"] == 15
    assert '"model": "test-model"' in captured["payload"]
    assert "Respond in Kannada" in captured["payload"]
    assert "I found a backpack." in captured["payload"]


def test_ai_image_similarity_uses_candidate_photos(monkeypatch, tmp_path):
    import json
    from io import BytesIO
    from PIL import Image
    from werkzeug.datastructures import FileStorage
    from app import ai_image_similarity

    uploaded = BytesIO()
    Image.new("RGB", (16, 16), "red").save(uploaded, format="PNG")
    uploaded.seek(0)
    candidate = tmp_path / "candidate.jpg"
    Image.new("RGB", (16, 16), "blue").save(candidate, format="JPEG")
    monkeypatch.setitem(app.config, "OPENAI_API_KEY", "test-key")
    monkeypatch.setitem(app.config, "UPLOAD_FOLDER", str(tmp_path))

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return b'{"choices":[{"message":{"content":"{\\"matches\\":[{\\"index\\":1,\\"score\\":87}]}"}}]}'

    def fake_urlopen(request, timeout):
        payload = json.loads(request.data)
        assert timeout == 20
        assert payload["messages"][0]["content"][1]["image_url"]["url"].startswith("data:image/jpeg;base64,")
        return FakeResponse()

    monkeypatch.setattr("app.urllib.request.urlopen", fake_urlopen)
    scores = ai_image_similarity(
        FileStorage(stream=uploaded, filename="lost.png"),
        [{"id": 43, "image": candidate.name}],
    )

    assert scores == {43: 0.87}
