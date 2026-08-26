from datetime import datetime, timezone
from enum import Enum

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class AddressType(str, Enum):
    """What a given address is for. Stored by value, so the DB holds 'Home'."""

    HOME = "Home"
    WORK = "Work"
    OTHER = "Other"


class Contact(Base):
    __tablename__ = "contacts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    first_name: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    last_name: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    email: Mapped[str] = mapped_column(String(320), nullable=False, unique=True, index=True)
    phone: Mapped[str | None] = mapped_column(String(40))

    company: Mapped[str | None] = mapped_column(String(200))
    job_title: Mapped[str | None] = mapped_column(String(200))

    notes: Mapped[str | None] = mapped_column(Text)

    # Base64 data URL (`data:image/png;base64,...`). Text because an encoded image
    # overflows any varchar cap; the size limit is enforced in the schema layer so
    # an oversized upload comes back as a 422 rather than a database error.
    photo: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=_utcnow,
        onupdate=_utcnow,
        server_default=func.now(),
        nullable=False,
    )

    # A contact has many addresses. delete-orphan is what removes an address
    # dropped from the list on a full replace, and cascades the whole set when
    # the contact goes. selectin loads them in one extra query per page rather
    # than one per contact.
    addresses: Mapped[list["Address"]] = relationship(
        back_populates="contact",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="Address.id",
    )

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}".strip()

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Contact id={self.id} email={self.email!r}>"


class Address(Base):
    """
    One postal address belonging to a contact.

    A separate table rather than numbered columns on `contacts`: a contact can
    hold any number of these, each labelled, and they stay queryable — "every
    work address in CA" is a WHERE clause, not a scan across address2/address3.
    """

    __tablename__ = "addresses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    contact_id: Mapped[int] = mapped_column(
        ForeignKey("contacts.id", ondelete="CASCADE"), index=True, nullable=False
    )

    type: Mapped[AddressType] = mapped_column(
        SAEnum(
            AddressType,
            values_callable=lambda enum: [member.value for member in enum],
            # SQLAlchemy has defaulted this to False since 1.4, which leaves the
            # column a bare VARCHAR — the labels would then be enforced only by
            # Pydantic, and anything writing to the DB directly could store junk.
            create_constraint=True,
            name="address_type",
        ),
        nullable=False,
        default=AddressType.HOME,
    )

    street: Mapped[str | None] = mapped_column(String(300))
    city: Mapped[str | None] = mapped_column(String(120))
    state: Mapped[str | None] = mapped_column(String(120))
    postal_code: Mapped[str | None] = mapped_column(String(20))
    country: Mapped[str | None] = mapped_column(String(120))

    contact: Mapped["Contact"] = relationship(back_populates="addresses")

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Address id={self.id} contact_id={self.contact_id} type={self.type.value!r}>"
