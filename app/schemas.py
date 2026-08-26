import base64
import binascii
import re
from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict, EmailStr, Field, computed_field, field_validator

from app.models import AddressType

#: Image types accepted in the `photo` field. Anything else — notably `text/html`,
#: which a browser would happily render — is rejected.
ALLOWED_PHOTO_TYPES = frozenset({"image/png", "image/jpeg", "image/gif", "image/webp"})

#: Cap on the *decoded* image, so the limit means what a user expects. Base64
#: inflates by roughly a third, so the encoded string may be ~2.7 MB.
MAX_PHOTO_BYTES = 2 * 1024 * 1024

_DATA_URL_RE = re.compile(r"data:([\w/+.-]+);base64,(.+)", re.DOTALL)

_PHOTO_DESCRIPTION = (
    "Profile picture as a base64 data URL, e.g. `data:image/png;base64,iVBOR...`. "
    f"Must be one of {', '.join(sorted(ALLOWED_PHOTO_TYPES))} and at most 2 MB decoded. "
    "`null` means the contact has no photo and the UI falls back to their initials."
)
_PHOTO_EXAMPLE = (
    "data:image/png;base64,"
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


def _validate_photo(value: str | None) -> str | None:
    """Reject anything that is not a reasonably sized image data URL."""
    if value is None:
        return None

    match = _DATA_URL_RE.fullmatch(value)
    if match is None:
        raise ValueError("photo must be a base64 data URL, e.g. data:image/png;base64,...")

    mime, payload = match.group(1), match.group(2)
    if mime not in ALLOWED_PHOTO_TYPES:
        raise ValueError(f"unsupported image type {mime!r}; allowed: {', '.join(sorted(ALLOWED_PHOTO_TYPES))}")

    try:
        decoded = base64.b64decode(payload, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("photo is not valid base64") from exc

    if not decoded:
        raise ValueError("photo is empty")
    if len(decoded) > MAX_PHOTO_BYTES:
        raise ValueError(f"photo is {len(decoded)} bytes decoded, over the {MAX_PHOTO_BYTES} byte limit")

    return value


class AddressBase(BaseModel):
    """Fields shared by every address request and response."""

    type: AddressType = Field(
        default=AddressType.HOME,
        description="What this address is for: Home, Work, or Other.",
        examples=["Home"],
    )
    street: str | None = Field(
        default=None,
        max_length=300,
        description="Street address, including unit or suite.",
        examples=["1 Market St, Suite 400"],
    )
    city: str | None = Field(default=None, max_length=120, description="City or locality.", examples=["San Francisco"])
    state: str | None = Field(default=None, max_length=120, description="State, province, or region.", examples=["CA"])
    postal_code: str | None = Field(default=None, max_length=20, description="Postal or ZIP code.", examples=["94105"])
    country: str | None = Field(default=None, max_length=120, description="Country name.", examples=["USA"])


class AddressCreate(AddressBase):
    """One address in a contact create or replace payload."""


class AddressRead(AddressBase):
    """A stored address, always returned nested inside its contact."""

    model_config = ConfigDict(from_attributes=True)

    id: int = Field(description="Server-assigned identifier.", examples=[1])
    contact_id: int = Field(description="Owning contact's id.", examples=[1])


class ContactBase(BaseModel):
    """Fields shared by every contact request and response."""

    first_name: str = Field(
        min_length=1,
        max_length=100,
        description="Given name. Required, must not be blank.",
        examples=["Ada"],
    )
    last_name: str = Field(
        min_length=1,
        max_length=100,
        description="Family name. Required, must not be blank.",
        examples=["Lovelace"],
    )
    email: EmailStr = Field(
        max_length=320,
        description=(
            "Primary email address. Required and unique across all contacts; "
            "compared case-insensitively and stored lowercased."
        ),
        examples=["ada@example.com"],
    )
    phone: str | None = Field(
        default=None,
        max_length=40,
        description="Phone number. Stored verbatim — any format is accepted.",
        examples=["+1-415-555-0101"],
    )
    company: str | None = Field(
        default=None,
        max_length=200,
        description="Employer or organisation name.",
        examples=["Analytical Engines"],
    )
    job_title: str | None = Field(
        default=None,
        max_length=200,
        description="Role held at the company.",
        examples=["Mathematician"],
    )
    notes: str | None = Field(
        default=None,
        description="Free-form notes about the contact. No length limit.",
        examples=["Met at the SF hackathon."],
    )
    photo: str | None = Field(default=None, description=_PHOTO_DESCRIPTION, examples=[_PHOTO_EXAMPLE])
    addresses: list[AddressCreate] = Field(
        default_factory=list,
        description=(
            "All addresses for this contact, each labelled Home, Work, or Other. "
            "On PUT this replaces the whole set: addresses you omit are deleted."
        ),
    )

    @field_validator("photo")
    @classmethod
    def _check_photo(cls, value: str | None) -> str | None:
        return _validate_photo(value)


_FULL_EXAMPLE = {
    "first_name": "Ada",
    "last_name": "Lovelace",
    "email": "ada@example.com",
    "phone": "+1-415-555-0101",
    "company": "Analytical Engines",
    "job_title": "Mathematician",
    "notes": "Met at the SF hackathon.",
    "addresses": [
        {
            "type": "Home",
            "street": "12 Ockham Rd",
            "city": "London",
            "postal_code": "SW1A 1AA",
            "country": "UK",
        },
        {
            "type": "Work",
            "street": "1 Market St, Suite 400",
            "city": "San Francisco",
            "state": "CA",
            "postal_code": "94105",
            "country": "USA",
        },
    ],
}
_MINIMAL_EXAMPLE = {"first_name": "Grace", "last_name": "Hopper", "email": "grace@example.com"}


class ContactCreate(ContactBase):
    """Body of `POST /api/v1/contacts`. Only the two names and email are required."""

    model_config = ConfigDict(json_schema_extra={"examples": [_FULL_EXAMPLE, _MINIMAL_EXAMPLE]})


class ContactReplace(ContactBase):
    """
    Body of `PUT /api/v1/contacts/{contact_id}`.

    This is a full replacement: any optional field you omit is set back to `null`.
    Use `PATCH` if you only want to change some fields.
    """

    model_config = ConfigDict(json_schema_extra={"examples": [_FULL_EXAMPLE]})


class ContactUpdate(BaseModel):
    """
    Body of `PATCH /api/v1/contacts/{contact_id}`.

    Every field is optional. Only the fields actually present in the request are
    written; omitted fields keep their current value. Sending an explicit `null`
    clears that field.
    """

    model_config = ConfigDict(
        json_schema_extra={"examples": [{"phone": "+1-415-555-0199", "job_title": "Chief Engineer"}]}
    )

    first_name: str | None = Field(default=None, min_length=1, max_length=100, description="New given name.")
    last_name: str | None = Field(default=None, min_length=1, max_length=100, description="New family name.")
    email: EmailStr | None = Field(
        default=None,
        max_length=320,
        description="New email address. Must not belong to another contact.",
    )
    phone: str | None = Field(default=None, max_length=40, description="New phone number.")
    company: str | None = Field(default=None, max_length=200, description="New company.")
    job_title: str | None = Field(default=None, max_length=200, description="New job title.")
    notes: str | None = Field(default=None, description="New notes; replaces the existing text.")
    addresses: list[AddressCreate] | None = Field(
        default=None,
        description="Replaces the whole address set. Omit to leave the existing addresses alone.",
    )
    photo: str | None = Field(default=None, description=f"New photo. {_PHOTO_DESCRIPTION}")

    @field_validator("photo")
    @classmethod
    def _check_photo(cls, value: str | None) -> str | None:
        return _validate_photo(value)


class ContactRead(ContactBase):
    """A stored contact, as returned by every contact endpoint."""

    model_config = ConfigDict(
        from_attributes=True,
        json_schema_extra={
            "examples": [
                {
                    **_FULL_EXAMPLE,
                    "id": 1,
                    "full_name": "Ada Lovelace",
                    "created_at": "2026-08-19T16:22:58.189507Z",
                    "updated_at": "2026-08-19T16:22:58.189511Z",
                }
            ]
        },
    )

    id: int = Field(description="Server-assigned identifier.", examples=[1])
    # Narrows ContactBase's list[AddressCreate]: reads carry ids, writes do not.
    addresses: list[AddressRead] = Field(
        default_factory=list,
        description="All addresses for this contact, ordered by id.",
    )
    created_at: datetime = Field(
        description="UTC timestamp of when the contact was created.",
        examples=["2026-08-19T16:22:58.189507Z"],
    )
    updated_at: datetime = Field(
        description="UTC timestamp of the last modification.",
        examples=["2026-08-19T16:22:58.189511Z"],
    )

    @field_validator("created_at", "updated_at")
    @classmethod
    def _as_utc(cls, value: datetime) -> datetime:
        # SQLite discards tzinfo on write; the stored values are UTC, so label
        # them as such rather than emitting an ambiguous naive timestamp.
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value

    @computed_field(description="Convenience concatenation of first and last name.", examples=["Ada Lovelace"])
    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}".strip()


class ContactPage(BaseModel):
    """One page of contacts plus the totals a client needs to paginate."""

    items: list[ContactRead] = Field(description="Contacts on this page, ordered by the requested sort.")
    total: int = Field(
        description="Total contacts matching the query, ignoring `limit` and `offset`.",
        examples=[42],
    )
    limit: int = Field(description="Page size that was applied.", examples=[50])
    offset: int = Field(description="Number of records skipped.", examples=[0])


class HealthResponse(BaseModel):
    """Result of the liveness probe."""

    status: str = Field(description="Always `ok` when the service can serve traffic.", examples=["ok"])
    database: str = Field(description="Active SQLAlchemy dialect.", examples=["sqlite"])
    contacts: int = Field(description="Number of contacts currently stored.", examples=[3])


class RootResponse(BaseModel):
    """Discovery document listing the API's entry points."""

    name: str = Field(description="Human-readable service name.", examples=["Contacts API"])
    version: str = Field(description="Service version.", examples=["0.1.0"])
    docs: str = Field(description="Path to the Swagger UI.", examples=["/docs"])
    redoc: str = Field(description="Path to the ReDoc UI.", examples=["/redoc"])
    openapi: str = Field(description="Path to the OpenAPI 3.1 document.", examples=["/openapi.json"])
    contacts: str = Field(description="Base path of the contacts collection.", examples=["/api/v1/contacts"])
    health: str = Field(description="Path to the liveness probe.", examples=["/health"])


class ErrorResponse(BaseModel):
    """Shape of every non-validation error returned by the API."""

    detail: str = Field(
        description="Human-readable explanation of the failure.",
        examples=["Contact 42 not found"],
    )
