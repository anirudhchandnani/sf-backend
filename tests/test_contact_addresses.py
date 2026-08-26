import pytest
from sqlalchemy import inspect, select, text

from app.database import SessionLocal, engine
from app.models import Address, Contact
from app.schemas import MAX_ADDRESSES_PER_CONTACT

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


# --------------------------------------------------------------------------
# Bounds and content rules
# --------------------------------------------------------------------------


def test_rejects_more_addresses_than_the_cap(client, payload):
    """Without a cap, one request could write an unbounded number of rows."""
    too_many = [{"type": "Home", "city": f"c{i}"} for i in range(MAX_ADDRESSES_PER_CONTACT + 1)]

    response = client.post(BASE, json={**payload, "addresses": too_many})

    assert response.status_code == 422


def test_accepts_exactly_the_cap(client, payload):
    at_cap = [{"type": "Home", "city": f"c{i}"} for i in range(MAX_ADDRESSES_PER_CONTACT)]

    response = client.post(BASE, json={**payload, "addresses": at_cap})

    assert response.status_code == 201, response.text
    assert len(response.json()["addresses"]) == MAX_ADDRESSES_PER_CONTACT


def test_rejects_an_address_with_no_content(client, payload):
    """A type alone is not an address; it renders as an empty dash on the page."""
    response = client.post(BASE, json={**payload, "addresses": [{"type": "Home"}]})

    assert response.status_code == 422


def test_rejects_an_address_that_is_only_whitespace(client, payload):
    response = client.post(BASE, json={**payload, "addresses": [{"type": "Home", "city": "   "}]})

    assert response.status_code == 422


def test_one_filled_part_is_enough(client, payload):
    response = client.post(BASE, json={**payload, "addresses": [{"type": "Home", "country": "UK"}]})

    assert response.status_code == 201, response.text


def test_rejects_duplicate_addresses(client, payload):
    response = client.post(BASE, json={**payload, "addresses": [HOME, HOME]})

    assert response.status_code == 422


def test_duplicate_detection_ignores_case_and_padding(client, payload):
    near = {**HOME, "city": "  LONDON  "}

    response = client.post(BASE, json={**payload, "addresses": [HOME, near]})

    assert response.status_code == 422


def test_same_street_under_different_types_is_not_a_duplicate(client, payload):
    """Working from home is legitimate: same address, two labels."""
    response = client.post(BASE, json={**payload, "addresses": [HOME, {**HOME, "type": "Work"}]})

    assert response.status_code == 201, response.text


# --------------------------------------------------------------------------
# Reconcile: ids must survive an edit that did not touch the addresses
# --------------------------------------------------------------------------


def test_renaming_a_contact_keeps_its_address_ids(client, payload):
    """
    Rebuilding the list wholesale gave every address a new id on every save.
    Rows are reconciled in place now, so an unrelated edit touches none of them.
    """
    created = _create(client, payload, addresses=[HOME, WORK])
    before = [address["id"] for address in created["addresses"]]

    response = client.put(
        f"{BASE}/{created['id']}",
        json={**payload, "first_name": "Augusta", "addresses": [HOME, WORK]},
    )

    assert [address["id"] for address in response.json()["addresses"]] == before


def test_editing_one_address_keeps_the_others_ids(client, payload):
    created = _create(client, payload, addresses=[HOME, WORK])
    before = [address["id"] for address in created["addresses"]]

    response = client.put(
        f"{BASE}/{created['id']}",
        json={**payload, "addresses": [HOME, {**WORK, "city": "Oakland"}]},
    )

    after = response.json()["addresses"]
    assert [address["id"] for address in after] == before
    assert after[1]["city"] == "Oakland"


def test_appending_an_address_leaves_existing_ids_alone(client, payload):
    created = _create(client, payload, addresses=[HOME])
    first_id = created["addresses"][0]["id"]

    response = client.put(f"{BASE}/{created['id']}", json={**payload, "addresses": [HOME, WORK]})

    after = response.json()["addresses"]
    assert after[0]["id"] == first_id
    assert len(after) == 2


def test_removing_an_address_leaves_no_orphans(client, payload):
    created = _create(client, payload, addresses=[HOME, WORK, OTHER])

    client.put(f"{BASE}/{created['id']}", json={**payload, "addresses": [HOME]})

    with SessionLocal() as db:
        rows = db.execute(select(Address).where(Address.contact_id == created["id"])).scalars().all()
        assert len(rows) == 1


def test_patch_with_an_explicit_null_clears_the_addresses(client, payload):
    """
    Consistent with `photo: null`. Omitting the key still leaves them alone —
    presence of the key is what distinguishes the two.
    """
    created = _create(client, payload, addresses=[HOME, WORK])

    response = client.patch(f"{BASE}/{created['id']}", json={"addresses": None})

    assert response.status_code == 200
    assert response.json()["addresses"] == []


def test_openapi_advertises_the_nested_addresses(client):
    schema = client.get("/openapi.json").json()

    assert "AddressRead" in schema["components"]["schemas"]
    assert "addresses" in schema["components"]["schemas"]["ContactRead"]["properties"]
