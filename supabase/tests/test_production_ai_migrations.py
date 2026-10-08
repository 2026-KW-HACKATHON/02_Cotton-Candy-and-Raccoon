"""Issue #76: actual baseline upgrade, drift refusal and transactional rollback."""

import importlib.util
from pathlib import Path

import psycopg
import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "production_ai_migrations", ROOT / "services/pipeline/scripts/production_ai_migrations.py",
)
upgrade = importlib.util.module_from_spec(spec)
spec.loader.exec_module(upgrade)

pytestmark = pytest.mark.parametrize(
    "database", ["20261008160000_standard_dictionary_cache.sql"], indirect=True,
)


@pytest.fixture
def baseline(db):
    db.execute("create schema supabase_migrations")
    db.execute(
        "create table supabase_migrations.schema_migrations "
        "(version text primary key,name text,statements text[])"
    )
    for version in upgrade.BASE:
        db.execute("insert into supabase_migrations.schema_migrations(version) values (%s)", (
            version,
        ))
    return db


def saved_state(db):
    columns = upgrade.layout(db)
    return {
        "versions": list(upgrade.inspect(db)), "columns": columns,
        "rows": upgrade.snapshot(db, columns), "public": upgrade.public_snapshot(db),
        "migration_hashes": {v: upgrade.digest(p.read_text("utf-8"))
                             for v, p in upgrade.migration_files().items()},
    }


def test_apply_preserves_all_rows_public_reads_and_private_grants(baseline):
    saved = saved_state(baseline)
    result = upgrade.apply(baseline, saved)
    assert result["applied"] == list(upgrade.UPGRADES)
    assert result["data_preserved"] and result["public_reads_preserved"]
    assert upgrade.inspect(baseline) == upgrade.BASE + upgrade.UPGRADES
    # Re-running on the final schema does not replay CREATE TABLE migrations.
    assert upgrade.apply(baseline, saved_state(baseline))["applied"] == []


def test_refuse_data_drift_between_backup_and_apply(baseline):
    saved = saved_state(baseline)
    baseline.execute("update notices set title=title || ' changed'")
    with pytest.raises(upgrade.UpgradeError, match="database_changed_since_backup"):
        upgrade.apply(baseline, saved)
    assert upgrade.inspect(baseline) == upgrade.BASE


def test_refuse_branch_collision_without_repairing_history(baseline):
    baseline.execute("create table standard_dictionary_cache(dummy int)")
    with pytest.raises(upgrade.UpgradeError, match="history_schema_mismatch"):
        upgrade.inspect(baseline)


def test_late_validation_failure_rolls_back_schema_and_history(baseline, monkeypatch):
    saved = saved_state(baseline)

    def reject(_):
        raise upgrade.UpgradeError("private_table_exposed")

    monkeypatch.setattr(upgrade, "verify_permissions", reject)
    with pytest.raises(upgrade.UpgradeError, match="private_table_exposed"):
        with baseline.transaction():
            upgrade.apply(baseline, saved)
    assert upgrade.inspect(baseline) == upgrade.BASE
    assert upgrade.snapshot(baseline, saved["columns"]) == saved["rows"]


def test_reject_modified_migration_after_backup(baseline):
    saved = saved_state(baseline)
    saved["migration_hashes"][upgrade.UPGRADES[0]] = "incorrect"
    with pytest.raises(upgrade.UpgradeError, match="migration_changed_since_backup"):
        upgrade.apply(baseline, saved)


def test_missing_baseline_view_is_not_silently_repaired(baseline):
    baseline.execute("drop view app_notice_detail")
    with pytest.raises(upgrade.UpgradeError, match="baseline_app_views_missing"):
        upgrade.inspect(baseline)


def test_history_insert_failure_rolls_back_ddl(baseline):
    saved = saved_state(baseline)
    baseline.execute(
        "alter table supabase_migrations.schema_migrations add constraint reject_new "
        "check (version <= '20261008150000')"
    )
    with pytest.raises(psycopg.errors.CheckViolation):
        with baseline.transaction():
            upgrade.apply(baseline, saved)
    assert upgrade.inspect(baseline) == upgrade.BASE


def test_restore_refuses_remote_database(baseline, monkeypatch):
    monkeypatch.setenv("RESTORE_DATABASE_URL", "postgresql://user@example.com/production")
    with pytest.raises(upgrade.UpgradeError, match="restore_requires_disposable"):
        upgrade.restore_check({})
