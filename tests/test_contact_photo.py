import base64

import pytest

from app.schemas import MAX_PHOTO_BYTES

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


# --------------------------------------------------------------------------
# Format matrix — every type the UI can produce, and the ones it must not.
# --------------------------------------------------------------------------

#: Leading bytes that identify each accepted format. The validator sniffs these,
#: so a payload must actually start with them — a declared MIME is not enough.
SIGNATURES = {
    "image/png": b"\x89PNG\r\n\x1a\n",
    "image/jpeg": b"\xff\xd8\xff",
    "image/gif": b"GIF89a",
    "image/webp": b"RIFF\x00\x00\x00\x00WEBP",
}


def data_url(mime: str, *, padding: int = 8) -> str:
    """A minimal payload that sniffs as `mime`, optionally padded to a size."""
    raw = SIGNATURES[mime] + b"\x00" * padding
    return f"data:{mime};base64,{base64.b64encode(raw).decode()}"


@pytest.mark.parametrize("mime", sorted(SIGNATURES))
def test_accepts_every_allowed_image_type(client, payload, mime):
    response = client.post(BASE, json={**payload, "photo": data_url(mime)})
    assert response.status_code == 201, response.text


@pytest.mark.parametrize("mime", sorted(SIGNATURES))
def test_rejects_bytes_that_do_not_match_the_declared_type(client, payload, mime):
    """A PNG label on JPEG bytes is a lie, and the validator must catch it."""
    other = next(m for m in SIGNATURES if m != mime)
    lying = f"data:{mime};base64,{base64.b64encode(SIGNATURES[other] + b'\\x00' * 8).decode()}"

    response = client.post(BASE, json={**payload, "photo": lying})
    assert response.status_code == 422


def test_rejects_a_payload_with_no_recognisable_image_header(client, payload):
    """The finding that made a broken avatar reachable: content was never sniffed."""
    junk = base64.b64encode(b"<html>not a png at all</html>").decode()
    response = client.post(BASE, json={**payload, "photo": f"data:image/png;base64,{junk}"})
    assert response.status_code == 422


@pytest.mark.parametrize(
    "mime",
    [
        "image/svg+xml",  # SVG can carry <script>; never render user SVG from an <img> we trust
        "image/bmp",
        "image/tiff",
        "text/html",
        "text/plain",
        "application/pdf",
        "application/octet-stream",
    ],
)
def test_rejects_types_outside_the_allow_list(client, payload, mime):
    # Real PNG bytes, but a type the allow-list does not contain: still rejected.
    body = base64.b64encode(SIGNATURES["image/png"] + b"\x00" * 8).decode()
    response = client.post(BASE, json={**payload, "photo": f"data:{mime};base64,{body}"})
    assert response.status_code == 422


@pytest.mark.parametrize(
    "photo",
    [
        "",  # blank string is not a data URL
        "   ",
        "data:image/png;base64,",  # declared but empty
        "data:image/png,notbase64",  # missing the ;base64 marker
        "data:;base64,AAAA",  # no MIME type
        "base64,AAAA",  # no scheme
        "javascript:alert(1)",
        "https://example.com/a.png",
        "/uploads/a.png",
        "DATA:IMAGE/PNG;BASE64,AAAA",  # browsers emit lowercase; stay strict
    ],
)
def test_rejects_malformed_photo_values(client, payload, photo):
    response = client.post(BASE, json={**payload, "photo": photo})
    assert response.status_code == 422


def test_accepts_a_photo_exactly_at_the_size_limit(client, payload):
    sig = SIGNATURES["image/png"]
    at_limit = base64.b64encode(sig + b"\x00" * (MAX_PHOTO_BYTES - len(sig))).decode()
    response = client.post(BASE, json={**payload, "photo": f"data:image/png;base64,{at_limit}"})
    assert response.status_code == 201, response.text


def test_rejects_a_photo_one_byte_over_the_limit(client, payload):
    sig = SIGNATURES["image/png"]
    over = base64.b64encode(sig + b"\x00" * (MAX_PHOTO_BYTES - len(sig) + 1)).decode()
    response = client.post(BASE, json={**payload, "photo": f"data:image/png;base64,{over}"})
    assert response.status_code == 422


def test_size_limit_applies_to_decoded_bytes_not_the_encoded_string(client, payload):
    """
    1.6 MB of image encodes to ~2.13 MB of base64. Limiting the encoded length
    would wrongly reject it, so the check must decode first.
    """
    raw = SIGNATURES["image/png"] + b"\x00" * 1_600_000
    encoded = base64.b64encode(raw).decode()
    assert len(encoded) > MAX_PHOTO_BYTES  # the encoded form is over the cap...

    response = client.post(BASE, json={**payload, "photo": f"data:image/png;base64,{encoded}"})
    assert response.status_code == 201, response.text  # ...but the decoded image is not


def test_a_large_but_valid_photo_round_trips_unchanged(client, payload):
    """Nothing re-encodes or truncates the value on the way through."""
    photo = data_url("image/png", padding=800_000)

    created = _create(client, payload, photo=photo)
    fetched = client.get(f"{BASE}/{created['id']}").json()

    assert fetched["photo"] == photo


def test_the_listing_reports_has_photo_instead_of_inlining_it(client, payload):
    """
    A page of contacts must never carry base64 images.

    With a 2 MB cap and a 200-row page, inlining them makes a single unauthenticated
    GET worth ~500 MB. The list advertises `has_photo`; the bytes come from
    /contacts/{id}/photo.
    """
    _create(client, payload, photo=PIXEL_PNG)

    items = client.get(BASE).json()["items"]

    assert "photo" not in items[0]
    assert items[0]["has_photo"] is True


def test_the_listing_reports_has_photo_false_without_one(client, payload):
    _create(client, payload)

    items = client.get(BASE).json()["items"]

    assert items[0]["has_photo"] is False


def test_a_page_of_photo_contacts_stays_small(client, payload):
    """Regression guard for the amplification finding."""
    big = data_url("image/png", padding=500_000)
    for i in range(5):
        _create(client, payload, email=f"big{i}@example.com", photo=big)

    body = client.get(f"{BASE}?limit=200").content

    # 5 x ~500 KB inlined would be megabytes; without photos it is a few KB.
    assert len(body) < 50_000, f"list payload grew to {len(body)} bytes"


def test_the_detail_endpoint_still_returns_the_photo(client, payload):
    """The edit form needs the actual value to round-trip it through a PUT."""
    created = _create(client, payload, photo=PIXEL_PNG)

    assert client.get(f"{BASE}/{created['id']}").json()["photo"] == PIXEL_PNG


# --------------------------------------------------------------------------
# The dedicated photo endpoint
# --------------------------------------------------------------------------


def test_photo_endpoint_serves_the_decoded_image(client, payload):
    created = _create(client, payload, photo=data_url("image/png"))

    response = client.get(f"{BASE}/{created['id']}/photo")

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.content.startswith(SIGNATURES["image/png"])


@pytest.mark.parametrize("mime", sorted(SIGNATURES))
def test_photo_endpoint_sends_the_real_content_type(client, payload, mime):
    created = _create(client, payload, photo=data_url(mime))

    assert client.get(f"{BASE}/{created['id']}/photo").headers["content-type"] == mime


def test_photo_endpoint_sets_a_validator_and_cache_policy(client, payload):
    created = _create(client, payload, photo=data_url("image/png"))

    headers = client.get(f"{BASE}/{created['id']}/photo").headers

    assert headers["etag"]
    assert "private" in headers["cache-control"]


def test_photo_endpoint_returns_304_when_the_etag_matches(client, payload):
    """An advertised ETag that is never compared is decorative."""
    created = _create(client, payload, photo=data_url("image/png"))
    etag = client.get(f"{BASE}/{created['id']}/photo").headers["etag"]

    revalidated = client.get(f"{BASE}/{created['id']}/photo", headers={"If-None-Match": etag})

    assert revalidated.status_code == 304
    assert revalidated.content == b""


def test_photo_endpoint_handles_a_weak_etag_and_a_list(client, payload):
    created = _create(client, payload, photo=data_url("image/png"))
    etag = client.get(f"{BASE}/{created['id']}/photo").headers["etag"]

    header = f'"something-else", W/{etag}'
    assert client.get(f"{BASE}/{created['id']}/photo", headers={"If-None-Match": header}).status_code == 304


def test_photo_endpoint_sends_the_body_when_the_etag_differs(client, payload):
    created = _create(client, payload, photo=data_url("image/png"))

    response = client.get(f"{BASE}/{created['id']}/photo", headers={"If-None-Match": '"stale"'})

    assert response.status_code == 200
    assert response.content


def test_editing_the_photo_changes_the_etag(client, payload):
    """Otherwise a stale image would be served after an edit."""
    created = _create(client, payload, photo=data_url("image/png", padding=8))
    first = client.get(f"{BASE}/{created['id']}/photo").headers["etag"]

    client.patch(f"{BASE}/{created['id']}", json={"photo": data_url("image/png", padding=64)})
    second = client.get(f"{BASE}/{created['id']}/photo").headers["etag"]

    assert first != second


def test_photo_endpoint_404s_when_the_contact_has_no_photo(client, payload):
    created = _create(client, payload)

    assert client.get(f"{BASE}/{created['id']}/photo").status_code == 404


def test_photo_endpoint_404s_for_an_unknown_contact(client):
    assert client.get(f"{BASE}/999999/photo").status_code == 404
