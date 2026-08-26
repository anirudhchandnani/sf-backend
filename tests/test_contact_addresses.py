import pytest
from sqlalchemy import inspect, select, text

from app.database import SessionLocal, engine
from app.models import Address, Contact

BASE = "/api/v1/contacts"

HOME = {
    "type": "Home",
    "street": "12 Ockham Rd",
    "city": "London",
    "postal_code": "SW1A 1AA",
    "country": "UK",
}
WORK = {
    "type": "Work",
    "street": "1 Market St, Suite 400",
    "city": "San Francisco",
    "state": "CA",
    "postal_code": "94105",
    "country": "USA",
}
OTHER = {"type": "Other", "street": "Sea Ranch", "city": "Sonoma", "country": "USA"}


def _create(client, payload, **overrides):
    response = client.post(BASE, json={**payload, **overrides})
    assert response.status_code == 201, response.text
    return response.json()


# --------------------------------------------------------------------------
# Schema shape — a real table with a real foreign key, not bolted-on columns
# --------------------------------------------------------------------------


def test_addresses_live_in_their_own_table(client):
    assert "addresses" in inspect(engine).get_table_names()


def test_addresses_table_has_a_foreign_key_back_to_contacts(client):
    keys = inspect(engine).get_foreign_keys("addresses")

    assert len(keys) == 1
    assert keys[0]["referred_table"] == "contacts"
    assert keys[0]["constrained_columns"] == ["contact_id"]
    assert keys[0]["referred_columns"] == ["id"]


def test_contacts_table_has_no_numbered_address_columns(client):
    """The wrong answer this challenge warns about: address2, address3, ..."""
    columns = {column["name"] for column in inspect(engine).get_columns("contacts")}

    assert not any(name.startswith("address") for name in columns)
    for leaked in ("city", "state", "postal_code", "country"):
        assert leaked not in columns


def test_address_type_is_constrained_to_the_three_labels(client):
    """
    SQLAlchemy renders Enum on SQLite as VARCHAR plus a CHECK constraint, so
    reflection reports a plain VARCHAR. Read the DDL to prove the constraint is
    enforced by the database and not only by Pydantic.
    """
    columns = {column["name"] for column in inspect(engine).get_columns("addresses")}
    assert "type" in columns

    with SessionLocal() as db:
        ddl = db.execute(
            text("SELECT sql FROM sqlite_master WHERE type='table' AND name='addresses'")
        ).scalar_one()

    assert "CHECK" in ddl.upper()
    for label in ("Home", "Work", "Other"):
        assert f"'{label}'" in ddl


# --------------------------------------------------------------------------
# One-to-many behaviour
# --------------------------------------------------------------------------


def test_contact_starts_with_no_addresses(client, payload):
    assert _create(client, payload)["addresses"] == []


def test_contact_can_hold_many_addresses(client, payload):
    created = _create(client, payload, addresses=[HOME, WORK, OTHER])

    assert [address["type"] for address in created["addresses"]] == ["Home", "Work", "Other"]
    assert created["addresses"][1]["city"] == "San Francisco"


def test_every_address_is_linked_to_its_contact(client, payload):
    created = _create(client, payload, addresses=[HOME, WORK])

    for address in created["addresses"]:
        assert address["contact_id"] == created["id"]
        assert address["id"] > 0


def test_a_contact_can_hold_several_addresses_of_the_same_type(client, payload):
    """Nothing says one Home per person — two holiday homes is valid."""
    created = _create(client, payload, addresses=[HOME, {**HOME, "city": "Bath"}])

    assert len(created["addresses"]) == 2


def test_addresses_survive_a_round_trip(client, payload):
    created = _create(client, payload, addresses=[HOME, WORK])

    fetched = client.get(f"{BASE}/{created['id']}").json()

    assert fetched["addresses"] == created["addresses"]


def test_addresses_appear_in_the_listing(client, payload):
    _create(client, payload, addresses=[HOME])

    items = client.get(BASE).json()["items"]

    assert items[0]["addresses"][0]["street"] == "12 Ockham Rd"


@pytest.mark.parametrize("bad_type", ["home", "HOME", "Office", "", "Home ", None])
def test_rejects_an_address_type_outside_the_allow_list(client, payload, bad_type):
    response = client.post(BASE, json={**payload, "addresses": [{**HOME, "type": bad_type}]})
    assert response.status_code == 422


def test_address_type_defaults_to_home(client, payload):
    created = _create(client, payload, addresses=[{"street": "Somewhere"}])
    assert created["addresses"][0]["type"] == "Home"


# --------------------------------------------------------------------------
# Replace and update semantics
# --------------------------------------------------------------------------


def test_put_replaces_the_whole_address_set(client, payload):
    created = _create(client, payload, addresses=[HOME, WORK])

    response = client.put(f"{BASE}/{created['id']}", json={**payload, "addresses": [OTHER]})

    assert response.status_code == 200
    assert [address["type"] for address in response.json()["addresses"]] == ["Other"]


def test_put_without_addresses_clears_them(client, payload):
    created = _create(client, payload, addresses=[HOME, WORK])

    response = client.put(f"{BASE}/{created['id']}", json=payload)

    assert response.json()["addresses"] == []


def test_replaced_addresses_leave_no_orphan_rows(client, payload):
    """delete-orphan must actually delete, not just detach."""
    created = _create(client, payload, addresses=[HOME, WORK])

    client.put(f"{BASE}/{created['id']}", json={**payload, "addresses": [OTHER]})

    with SessionLocal() as db:
        assert db.execute(select(Address).where(Address.contact_id.is_(None))).scalars().all() == []
        assert len(db.execute(select(Address)).scalars().all()) == 1


def test_patch_without_addresses_preserves_them(client, payload):
    created = _create(client, payload, addresses=[HOME, WORK])

    response = client.patch(f"{BASE}/{created['id']}", json={"first_name": "Augusta"})

    assert response.status_code == 200
    assert len(response.json()["addresses"]) == 2


def test_patch_can_replace_the_address_set(client, payload):
    created = _create(client, payload, addresses=[HOME, WORK])

    response = client.patch(f"{BASE}/{created['id']}", json={"addresses": [OTHER]})

    assert [address["type"] for address in response.json()["addresses"]] == ["Other"]


def test_patch_can_clear_addresses_with_an_empty_list(client, payload):
    created = _create(client, payload, addresses=[HOME])

    response = client.patch(f"{BASE}/{created['id']}", json={"addresses": []})

    assert response.json()["addresses"] == []


# --------------------------------------------------------------------------
# Cascade
# --------------------------------------------------------------------------


def test_deleting_a_contact_deletes_its_addresses(client, payload):
    created = _create(client, payload, addresses=[HOME, WORK, OTHER])

    assert client.delete(f"{BASE}/{created['id']}").status_code == 204

    with SessionLocal() as db:
        remaining = db.execute(select(Address).where(Address.contact_id == created["id"])).scalars().all()
        assert remaining == []


def test_deleting_one_contact_leaves_another_contacts_addresses_alone(client, payload):
    keep = _create(client, payload, addresses=[HOME])
    drop = _create(client, payload, email="other@example.com", addresses=[WORK])

    client.delete(f"{BASE}/{drop['id']}")

    with SessionLocal() as db:
        survivors = db.execute(select(Address).where(Address.contact_id == keep["id"])).scalars().all()
        assert len(survivors) == 1


def test_the_orm_relationship_navigates_both_ways(client, payload):
    created = _create(client, payload, addresses=[HOME])

    with SessionLocal() as db:
        contact = db.get(Contact, created["id"])
        assert len(contact.addresses) == 1
        # back_populates: the child knows its parent too
        assert contact.addresses[0].contact.id == contact.id


def test_openapi_advertises_the_nested_addresses(client):
    schema = client.get("/openapi.json").json()

    assert "AddressRead" in schema["components"]["schemas"]
    assert "addresses" in schema["components"]["schemas"]["ContactRead"]["properties"]
