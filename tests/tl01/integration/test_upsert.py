import os

import pytest

from services.database_api.scripts.rollback_integration import main


@pytest.mark.skipif(not os.getenv("DB_PASSWORD"), reason="DB_PASSWORD required for live PostgreSQL test")
def test_repository_upsert_and_revisions_with_rollback():
    main()
