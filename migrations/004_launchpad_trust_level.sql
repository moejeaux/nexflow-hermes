-- 004: Add launchpad_trust_level to launches
-- Tracks how much trust we assign to the launchpad that deployed a token.
-- Values: NONE, LOW, MEDIUM, HIGH (stored as TEXT, not enum, for easy extension).

ALTER TABLE launches
ADD COLUMN IF NOT EXISTS launchpad_trust_level TEXT NOT NULL DEFAULT 'NONE';

-- Backfill: any existing launch_type='launchpad' gets at least LOW
UPDATE launches
SET launchpad_trust_level = 'LOW'
WHERE launch_type = 'launchpad'
  AND launchpad_trust_level = 'NONE';
