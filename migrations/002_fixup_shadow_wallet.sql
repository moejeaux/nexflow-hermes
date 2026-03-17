-- Fixup: add missing columns expected by nxfx01-api
ALTER TABLE launches ADD COLUMN IF NOT EXISTS shadow BOOLEAN NOT NULL DEFAULT TRUE;
ALTER TABLE launches ADD COLUMN IF NOT EXISTS wallet_summary JSONB NOT NULL DEFAULT '{}'::jsonb;
