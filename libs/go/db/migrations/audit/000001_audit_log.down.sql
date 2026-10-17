-- Intentionally refuses: the audit log is never dropped by a migration (SE-020).
DO $$ BEGIN RAISE EXCEPTION 'audit log migrations are irreversible'; END $$;
