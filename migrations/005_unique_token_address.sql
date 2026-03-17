-- Migration 005: Add UNIQUE constraint on token_address
-- Required for ON CONFLICT (token_address) DO NOTHING in scheduler.py and launch_scanner.py

-- Drop the existing non-unique index first
DROP INDEX IF EXISTS idx_launches_token_address;

-- Create a unique index (serves as both index and constraint)
CREATE UNIQUE INDEX idx_launches_token_address ON launches (token_address);
