# Best Practices — Contacts API

Project-specific review guidance for this FastAPI + SQLAlchemy service.
Applies to `app/models.py`, `app/schemas.py`, `app/crud.py`, `app/routers/`.

## Model one-to-many relationships as a child table, never as numbered columns

A record that can hold several of something gets its own table with a foreign key
back to the parent and a discriminating type column. Numbered columns cap the count
arbitrarily, waste storage on nulls, cannot be labelled, and cannot be queried across
records. A JSON blob is equally wrong: it defeats foreign keys, constraints, and indexes.

Example code before:

```python
class Contact(Base):
    __tablename__ = "contacts"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    address: Mapped[str | None] = mapped_column(String(300))
    address2: Mapped[str | None] = mapped_column(String(300))
    address3: Mapped[str | None] = mapped_column(String(300))
```

Example code after:

```python
class Address(Base):
    __tablename__ = "addresses"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    contact_id: Mapped[int] = mapped_column(
        ForeignKey("contacts.id", ondelete="CASCADE"), index=True, nullable=False
    )
    type: Mapped[AddressType] = mapped_column(SAEnum(AddressType), nullable=False)
    street: Mapped[str | None] = mapped_column(String(300))


class Contact(Base):
    addresses: Mapped[list["Address"]] = relationship(
        back_populates="contact",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
```

## Give child collections a cascade and an explicit loading strategy

A child relationship without `cascade="all, delete-orphan"` leaves orphan rows when the
parent is deleted or an item is removed from the list. A relationship without an explicit
`lazy=` triggers one query per parent row when serializing a list endpoint (N+1).

Example code before:

```python
addresses: Mapped[list["Address"]] = relationship(back_populates="contact")
```

Example code after:

```python
addresses: Mapped[list["Address"]] = relationship(
    back_populates="contact",
    cascade="all, delete-orphan",
    lazy="selectin",
)
```

## Store unbounded user text as Text, not String(n)

Base64 image payloads, notes, and other free-form content overflow any varchar cap.
Pick `Text` when there is no natural length limit, and enforce real limits in the
Pydantic layer where the error can be returned to the caller as a 422.

Example code before:

```python
photo: Mapped[str | None] = mapped_column(String(255))
```

Example code after:

```python
photo: Mapped[str | None] = mapped_column(Text)
```

## Validate data-URL images against their content, not their declared type

Accepting arbitrary strings into an image field lets a caller store megabytes of junk or a
`data:text/html` payload that a browser may later render. Three checks, all necessary:

1. The declared MIME against an allow-list.
2. The *decoded* size — base64 inflates by roughly a third, so the encoded length is not the
   number to limit on.
3. **The leading bytes against that declared type.** A data URL's MIME is caller-supplied and
   worth nothing on its own; without a magic-byte check any payload can be stored as
   `image/png`, and the UI is left rendering a broken image.

Example code before:

```python
class ContactBase(BaseModel):
    photo: str | None = None
```

Example code after:

```python
ALLOWED_IMAGE_TYPES = {"image/png", "image/jpeg", "image/gif", "image/webp"}
MAX_PHOTO_BYTES = 2 * 1024 * 1024


def _sniff_image_type(data: bytes) -> str | None:
    """Identify an image from its leading bytes."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"GIF87a") or data.startswith(b"GIF89a"):
        return "image/gif"
    if len(data) >= 12 and data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "image/webp"
    return None


class ContactBase(BaseModel):
    photo: str | None = None

    @field_validator("photo")
    @classmethod
    def _validate_photo(cls, value: str | None) -> str | None:
        if value is None:
            return None
        match = re.fullmatch(r"data:([\w/+.-]+);base64,(.+)", value, re.DOTALL)
        if not match:
            raise ValueError("photo must be a data URL, e.g. data:image/png;base64,...")
        mime, payload = match.group(1), match.group(2)
        if mime not in ALLOWED_IMAGE_TYPES:
            raise ValueError(f"unsupported image type {mime!r}")
        try:
            decoded = base64.b64decode(payload, validate=True)
        except binascii.Error as exc:
            raise ValueError("photo is not valid base64") from exc
        if len(decoded) > MAX_PHOTO_BYTES:
            raise ValueError("photo exceeds the 2 MB limit")

        sniffed = _sniff_image_type(decoded)
        if sniffed is None:
            raise ValueError("photo does not contain a recognisable image")
        if sniffed != mime:
            raise ValueError(f"photo is declared {mime!r} but its bytes are {sniffed!r}")
        return value
```

## Never inline a large field in a collection response

A field that is bounded per record is still unbounded per page. A 2 MB base64 image is
acceptable on one contact and catastrophic on a page of two hundred — the response grows to
hundreds of megabytes to render avatars a few dozen pixels wide. Give the list a lightweight
flag and serve the payload from its own cacheable endpoint.

Example code before:

```python
class ContactPage(BaseModel):
    items: list[ContactRead]  # every photo inlined, unbounded page size
```

Example code after:

```python
class ContactListItem(ContactRead):
    """Like ContactRead, but the photo is a flag rather than the bytes."""

    photo: str | None = Field(default=None, exclude=True)

    @computed_field
    @property
    def has_photo(self) -> bool:
        return self.photo is not None


class ContactPage(BaseModel):
    items: list[ContactListItem]
```

## Bound every collection a client can submit

A nested list with no `max_length` lets one request write an unbounded number of rows, and the
UI then renders every one of them. Cap it in the schema, where the error is a 422 rather than a
timeout.

Example code before:

```python
addresses: list[AddressCreate] = Field(default_factory=list)
```

Example code after:

```python
MAX_ADDRESSES_PER_CONTACT = 20

addresses: list[AddressCreate] = Field(
    default_factory=list,
    max_length=MAX_ADDRESSES_PER_CONTACT,
)
```

## Keep full-replace (PUT) schemas complete and partial-update (PATCH) schemas optional

`ContactReplace` defines the whole resource: any persisted field missing from it is set to
null on every PUT, silently destroying data the caller never intended to touch. `ContactUpdate`
is the opposite — every field defaults to unset so `exclude_unset=True` only writes what was sent.
Whenever a column is added to the model, both schemas need a deliberate decision.

Example code before:

```python
class ContactBase(BaseModel):
    first_name: str
    email: EmailStr
    # photo added to the model but not here -> every PUT wipes it


class ContactUpdate(BaseModel):
    first_name: str | None = None
    # photo missing -> PATCH can never set it
```

Example code after:

```python
class ContactBase(BaseModel):
    first_name: str
    email: EmailStr
    photo: str | None = None


class ContactUpdate(BaseModel):
    first_name: str | None = None
    photo: str | None = None
```

## Pop nested collections before splatting a payload into a model constructor

`Contact(**payload.model_dump())` works only while every field is a scalar column. The moment
the schema carries a nested list, the splat passes a list of dicts where the ORM expects model
instances and raises at runtime. Separate the collection from the scalars explicitly.

Example code before:

```python
def create_contact(db: Session, payload: ContactCreate) -> Contact:
    contact = Contact(**payload.model_dump())
    db.add(contact)
    db.commit()
    return contact
```

Example code after:

```python
def create_contact(db: Session, payload: ContactCreate) -> Contact:
    data = payload.model_dump()
    addresses = data.pop("addresses", None) or []
    contact = Contact(**data)
    contact.addresses = [Address(**address) for address in addresses]
    db.add(contact)
    db.commit()
    db.refresh(contact)
    return contact
```

## Reconcile child collections in place; do not rebuild them on every save

A full replace must reconcile children too, but assigning a fresh list makes
`delete-orphan` drop every existing row and insert replacements — so an edit that touched no
child at all still rewrites each one and hands it a new primary key. Update the rows that
survive, delete only what the payload dropped, and insert only what is new.

Example code before:

```python
def replace_contact(db: Session, contact: Contact, payload: ContactReplace) -> Contact:
    data = payload.model_dump()
    addresses = data.pop("addresses", None) or []
    for field, value in data.items():
        setattr(contact, field, value)
    # Rewrites every row: renaming the contact churns all its address ids.
    contact.addresses = [Address(**address) for address in addresses]
    db.commit()
    return contact
```

Example code after:

```python
def _reconcile_addresses(contact: Contact, rows: list[dict]) -> None:
    existing = list(contact.addresses)

    for address, data in zip(existing, rows):
        for field, value in data.items():
            if getattr(address, field) != value:
                setattr(address, field, value)

    for address in existing[len(rows):]:
        contact.addresses.remove(address)  # delete-orphan removes it on flush

    for data in rows[len(existing):]:
        contact.addresses.append(Address(**data))
```

## Distinguish an absent key from a null value on PATCH

`model_dump(exclude_unset=True)` reports which keys the caller actually sent. For a collection,
omitting the key means "leave it alone" while an explicit `null` means "clear it" — testing the
*value* for None collapses those two into one and silently ignores a deliberate clear.

Example code before:

```python
addresses = data.pop("addresses", None)
if addresses is not None:          # an explicit null is silently ignored
    contact.addresses = ...
```

Example code after:

```python
replace_addresses = "addresses" in data     # presence, not truthiness
addresses = data.pop("addresses", None) or []
if replace_addresses:
    _reconcile_addresses(contact, addresses)
```

## Cover the destructive paths in tests, not just the happy path

The bugs that matter in a CRUD service are the silent ones: a PUT that nulls a field the client
omitted, a delete that leaves orphans. Assert those explicitly.

Example code before:

```python
def test_update_contact(client):
    response = client.put("/api/v1/contacts/1", json={"first_name": "Ada", ...})
    assert response.status_code == 200
```

Example code after:

```python
def test_put_without_photo_clears_it(client, contact_with_photo):
    payload = {k: v for k, v in contact_with_photo.items() if k != "photo"}
    response = client.put(f"/api/v1/contacts/{contact_with_photo['id']}", json=payload)
    assert response.status_code == 200
    assert response.json()["photo"] is None


def test_patch_without_photo_preserves_it(client, contact_with_photo):
    response = client.patch(
        f"/api/v1/contacts/{contact_with_photo['id']}", json={"first_name": "Ada"}
    )
    assert response.json()["photo"] == contact_with_photo["photo"]


def test_deleting_contact_removes_its_addresses(client, db_session):
    client.delete("/api/v1/contacts/1")
    assert db_session.query(Address).filter_by(contact_id=1).count() == 0
```
