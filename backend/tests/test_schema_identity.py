"""The API reports its schema identity, so a stale stack is visible, not silent.

The second independent acceptance found an old API still running against an
un-migrated database: the new routes simply 404'd and the workbench had no way
to say why.  ``/health`` now states the database revision next to the head this
code ships with; an unknown revision is reported as unknown, never as current.
"""

import pytest
from sqlalchemy import text

from shine.main import _code_schema_head

pytestmark = pytest.mark.unit


def test_health_states_the_schema_head_this_code_expects(api):
    client, factory, settings = api
    head = _code_schema_head()
    assert head, "the shipped Alembic scripts must resolve to one head"
    body = client.get("/health").json()
    assert body["schema_head"] == head
    # The test database is built from the models, not by Alembic: unknown.
    assert body["schema_revision"] is None
    assert body["schema_up_to_date"] is None


def test_a_database_behind_the_code_is_reported_as_behind(api):
    client, factory, settings = api
    with factory.begin() as db:
        db.execute(text("CREATE TABLE IF NOT EXISTS alembic_version (version_num VARCHAR(32) NOT NULL)"))
        db.execute(text("DELETE FROM alembic_version"))
        db.execute(text("INSERT INTO alembic_version (version_num) VALUES ('f170a0030001')"))
    body = client.get("/health").json()
    assert body["schema_revision"] == "f170a0030001"
    assert body["schema_up_to_date"] is False
    with factory.begin() as db:
        db.execute(text("UPDATE alembic_version SET version_num = :head"), {"head": _code_schema_head()})
    assert client.get("/health").json()["schema_up_to_date"] is True
