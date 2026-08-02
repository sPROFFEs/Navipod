import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

CONCIERGE_ROOT = Path(__file__).resolve().parents[1]
if str(CONCIERGE_ROOT) not in sys.path:
    sys.path.insert(0, str(CONCIERGE_ROOT))


@pytest.fixture
def db_session():
    import database

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    database.Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()
        database.Base.metadata.drop_all(engine)
        engine.dispose()
