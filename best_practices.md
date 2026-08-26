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

## Validate data-URL images on the schema, with a MIME allow-list and a decoded size cap

Accepting arbitrary strings into an image field lets a caller store megabytes of junk or a
`data:text/html` payload that a browser may later render. Validate the prefix against an
allow-list and bound the *decoded* size — base64 inflates by roughly a third, so the
encoded length is not the number you want to limit on.

Example code before:

```python
class ContactBase(BaseModel):
    photo: str | None = None
```

Example code after:

```python
ALLOWED_IMAGE_TYPES = {"image/png", "image/jpeg", "image/gif", "image/webp"}
MAX_PHOTO_BYTES = 2 * 1024 * 1024


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
        return value
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

## Replace child collections wholesale on PUT, honouring delete-orphan

A full replace must reconcile children too. Assigning a fresh list lets `delete-orphan` remove
the rows that are gone; mutating in place or appending leaves stale rows behind.

Example code before:

```python
def replace_contact(db: Session, contact: Contact, payload: ContactReplace) -> Contact:
    for field, value in payload.model_dump().items():
        setattr(contact, field, value)
    db.commit()
    return contact
```

Example code after:

```python
def replace_contact(db: Session, contact: Contact, payload: ContactReplace) -> Contact:
    data = payload.model_dump()
    addresses = data.pop("addresses", None) or []
    for field, value in data.items():
        setattr(contact, field, value)
    contact.addresses = [Address(**address) for address in addresses]
    db.commit()
    db.refresh(contact)
    return contact
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
