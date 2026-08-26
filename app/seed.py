from app.crud import count_contacts, create_contact
from app.models import AddressType
from app.schemas import AddressCreate, ContactCreate
from app.database import SessionLocal

# Ada carries two addresses so the one-to-many relationship and the grouped
# detail view are both visible on a fresh database.
SAMPLE_CONTACTS = [
    ContactCreate(
        first_name="Ada",
        last_name="Lovelace",
        email="ada@example.com",
        phone="+1-415-555-0101",
        company="Analytical Engines",
        job_title="Mathematician",
        notes="First programmer.",
        addresses=[
            AddressCreate(
                type=AddressType.WORK,
                street="1 Market St, Suite 400",
                city="San Francisco",
                state="CA",
                postal_code="94105",
                country="USA",
            ),
            AddressCreate(
                type=AddressType.HOME,
                street="12 Ockham Rd",
                city="London",
                postal_code="SW1A 1AA",
                country="UK",
            ),
        ],
    ),
    ContactCreate(
        first_name="Grace",
        last_name="Hopper",
        email="grace@example.com",
        phone="+1-415-555-0102",
        company="US Navy",
        job_title="Rear Admiral",
        addresses=[
            AddressCreate(
                type=AddressType.WORK,
                street="701 S Courthouse Rd",
                city="Arlington",
                state="VA",
                postal_code="22204",
                country="USA",
            )
        ],
    ),
    # No addresses and no photo: the empty states have something to render.
    ContactCreate(
        first_name="Alan",
        last_name="Turing",
        email="alan@example.com",
        phone="+44-20-5555-0103",
        company="Bletchley Park",
        job_title="Cryptanalyst",
    ),
]


def seed_if_empty() -> int:
    """Insert sample contacts when the database has none. Returns rows added."""
    with SessionLocal() as db:
        if count_contacts(db) > 0:
            return 0
        for contact in SAMPLE_CONTACTS:
            create_contact(db, contact)
        return len(SAMPLE_CONTACTS)
