from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.models import Address, Contact
from app.schemas import ContactCreate, ContactReplace, ContactUpdate

SORTABLE_FIELDS = ("id", "first_name", "last_name", "email", "company", "created_at", "updated_at")


def _normalize_email(email: str) -> str:
    return email.strip().lower()


def get_contact(db: Session, contact_id: int) -> Contact | None:
    return db.get(Contact, contact_id)


def get_contact_by_email(db: Session, email: str) -> Contact | None:
    stmt = select(Contact).where(func.lower(Contact.email) == _normalize_email(email))
    return db.execute(stmt).scalar_one_or_none()


def count_contacts(db: Session) -> int:
    return db.execute(select(func.count()).select_from(Contact)).scalar_one()


def list_contacts(
    db: Session,
    *,
    search: str | None = None,
    limit: int = 50,
    offset: int = 0,
    sort_by: str = "id",
    order: str = "asc",
) -> tuple[list[Contact], int]:
    """Return (page of contacts, total matching count)."""
    stmt = select(Contact)

    if search:
        pattern = f"%{search.strip().lower()}%"
        stmt = stmt.where(
            or_(
                func.lower(Contact.first_name).like(pattern),
                func.lower(Contact.last_name).like(pattern),
                func.lower(Contact.email).like(pattern),
                func.lower(func.coalesce(Contact.company, "")).like(pattern),
                func.lower(func.coalesce(Contact.phone, "")).like(pattern),
            )
        )

    total = db.execute(select(func.count()).select_from(stmt.subquery())).scalar_one()

    if sort_by not in SORTABLE_FIELDS:
        sort_by = "id"
    column = getattr(Contact, sort_by)
    stmt = stmt.order_by(column.desc() if order == "desc" else column.asc())

    items = db.execute(stmt.limit(limit).offset(offset)).scalars().all()
    return list(items), total


def _build_addresses(rows: list[dict] | None) -> list[Address]:
    """Turn the payload's plain dicts into ORM rows."""
    return [Address(**row) for row in rows or []]


def _reconcile_addresses(contact: Contact, rows: list[dict]) -> None:
    """
    Update the contact's addresses in place to match `rows`.

    Reassigning a fresh list would be simpler, but delete-orphan would then drop
    every existing row and insert replacements — so a change that touched no
    address at all (renaming the contact) still rewrote each one and handed it a
    new id. Existing rows are reused positionally instead: only genuine additions
    insert, only genuine removals delete, and an unchanged address keeps its id.
    """
    existing = list(contact.addresses)

    for address, data in zip(existing, rows):
        for field, value in data.items():
            if getattr(address, field) != value:
                setattr(address, field, value)

    # Trim rows the payload no longer has; delete-orphan removes them on flush.
    for address in existing[len(rows) :]:
        contact.addresses.remove(address)

    # Append the ones beyond what already existed.
    for data in rows[len(existing) :]:
        contact.addresses.append(Address(**data))


def create_contact(db: Session, payload: ContactCreate) -> Contact:
    data = payload.model_dump()
    # addresses is a list of dicts; Contact(**data) would hand the relationship
    # raw dicts where it expects Address instances, so split it off first.
    addresses = data.pop("addresses", None)
    data["email"] = _normalize_email(data["email"])

    contact = Contact(**data)
    contact.addresses = _build_addresses(addresses)

    db.add(contact)
    db.commit()
    db.refresh(contact)
    return contact


def replace_contact(db: Session, contact: Contact, payload: ContactReplace) -> Contact:
    data = payload.model_dump()
    addresses = data.pop("addresses", None) or []

    for field, value in data.items():
        setattr(contact, field, _normalize_email(value) if field == "email" else value)

    _reconcile_addresses(contact, addresses)

    db.commit()
    db.refresh(contact)
    return contact


def update_contact(db: Session, contact: Contact, payload: ContactUpdate) -> Contact:
    data = payload.model_dump(exclude_unset=True)

    # Presence of the key is what matters, not its value: omitting `addresses`
    # leaves the set alone, while an explicit null or [] clears it — matching
    # how an explicit `photo: null` clears the photo.
    replace_addresses = "addresses" in data
    addresses = data.pop("addresses", None) or []

    for field, value in data.items():
        setattr(contact, field, _normalize_email(value) if field == "email" else value)

    if replace_addresses:
        _reconcile_addresses(contact, addresses)

    db.commit()
    db.refresh(contact)
    return contact


def delete_contact(db: Session, contact: Contact) -> None:
    db.delete(contact)
    db.commit()
