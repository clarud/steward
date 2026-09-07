from datetime import UTC, datetime
from pathlib import Path
import sqlite3
import pytest
from steward.privacy import PrivacyRule, PrivacyService
from steward.sources import Source, SourceRepository, SourceType
from steward.storage import initialize_database

def test_source_privacy_defaults_and_persists(tmp_path: Path) -> None:
    database=tmp_path/"db.sqlite"; initialize_database(database); now=datetime(2026,9,8,tzinfo=UTC)
    source=SourceRepository(database).add(Source(None,tmp_path/"note.md","a"*64,SourceType.MARKDOWN,0,now,now,now))
    privacy=PrivacyService(database)
    assert privacy.rule_for(source.id or 0) is PrivacyRule.EXTERNAL_ALLOWED
    privacy.set_rule(source.id or 0, PrivacyRule.LOCAL_MODEL_ONLY)
    assert privacy.permits_external_model(source.id or 0) is False
    with pytest.raises(sqlite3.IntegrityError): privacy.set_rule(999, PrivacyRule.NO_MODEL)
