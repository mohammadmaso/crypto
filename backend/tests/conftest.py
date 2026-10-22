import os
import tempfile

os.environ["NOBI_DATA_DIR"] = tempfile.mkdtemp(prefix="nobi-test-")

import pytest  # noqa: E402

from app import db  # noqa: E402


@pytest.fixture(autouse=True)
def fresh_db():
    from sqlmodel import SQLModel
    SQLModel.metadata.drop_all(db.engine)
    db.init_db()
    yield
