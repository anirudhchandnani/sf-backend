import base64

BASE = "/api/v1/contacts"

# Smallest valid PNG: a single transparent pixel.
PIXEL_PNG = (
    "data:image/png;base64,"
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


def _create(client, payload, **overrides):
    response = client.post(BASE, json={**payload, **overrides})
    assert response.status_code == 201, response.text
    return response.json()


def test_contact_defaults_to_no_photo(client, payload):
    """A contact without a photo reports null, so the UI can fall back to initials."""
    assert _create(client, payload)["photo"] is None


def test_create_and_read_back_a_photo(client, payload):
    created = _create(client, payload, photo=PIXEL_PNG)
    assert created["photo"] == PIXEL_PNG

    fetched = client.get(f"{BASE}/{created['id']}")
    assert fetched.status_code == 200
    assert fetched.json()["photo"] == PIXEL_PNG


def test_photo_survives_a_full_replace_that_includes_it(client, payload):
    """The happy path the edit form must produce: PUT carrying the photo through."""
    created = _create(client, payload, photo=PIXEL_PNG)

    response = client.put(
        f"{BASE}/{created['id']}",
        json={**payload, "first_name": "Augusta", "photo": PIXEL_PNG},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["first_name"] == "Augusta"
    assert body["photo"] == PIXEL_PNG


def test_put_without_photo_clears_it(client, payload):
    """
    PUT is a full replace, so an omitted photo is intentionally nulled.

    This is the trap behind the edit form: if the form does not carry the existing
    photo through, renaming a contact silently wipes their picture. Pinned here so
    the behaviour is deliberate rather than discovered in the UI.
    """
    created = _create(client, payload, photo=PIXEL_PNG)

    response = client.put(f"{BASE}/{created['id']}", json=payload)

    assert response.status_code == 200
    assert response.json()["photo"] is None


def test_patch_without_photo_preserves_it(client, payload):
    """PATCH only writes what was sent, so the photo must survive untouched."""
    created = _create(client, payload, photo=PIXEL_PNG)

    response = client.patch(f"{BASE}/{created['id']}", json={"first_name": "Augusta"})

    assert response.status_code == 200
    body = response.json()
    assert body["first_name"] == "Augusta"
    assert body["photo"] == PIXEL_PNG


def test_patch_can_clear_a_photo_explicitly(client, payload):
    created = _create(client, payload, photo=PIXEL_PNG)

    response = client.patch(f"{BASE}/{created['id']}", json={"photo": None})

    assert response.status_code == 200
    assert response.json()["photo"] is None


def test_rejects_a_photo_that_is_not_a_data_url(client, payload):
    response = client.post(BASE, json={**payload, "photo": "https://example.com/ada.png"})
    assert response.status_code == 422


def test_rejects_a_non_image_data_url(client, payload):
    """`text/html` in an <img> src is a stored-XSS vector in some renderers."""
    html = base64.b64encode(b"<script>alert(1)</script>").decode()
    response = client.post(BASE, json={**payload, "photo": f"data:text/html;base64,{html}"})
    assert response.status_code == 422


def test_rejects_malformed_base64(client, payload):
    response = client.post(BASE, json={**payload, "photo": "data:image/png;base64,not!valid!base64!"})
    assert response.status_code == 422


def test_rejects_a_photo_over_the_size_limit(client, payload):
    """The cap applies to decoded bytes, so 3 MB of payload must fail."""
    oversized = base64.b64encode(b"\x00" * (3 * 1024 * 1024)).decode()
    response = client.post(BASE, json={**payload, "photo": f"data:image/png;base64,{oversized}"})
    assert response.status_code == 422


def test_photo_is_advertised_in_the_openapi_schema(client):
    schema = client.get("/openapi.json").json()
    assert "photo" in schema["components"]["schemas"]["ContactRead"]["properties"]
    assert "photo" in schema["components"]["schemas"]["ContactCreate"]["properties"]
