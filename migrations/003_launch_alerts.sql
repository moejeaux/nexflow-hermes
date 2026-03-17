-- Migration 003: Launch alerts table for score-triggered agent notifications
-- Applied to: Supabase (NXFX01 schema)

CREATE TYPE alert_type AS ENUM ('BUY_TRIGGER', 'EVALUATE', 'UPGRADE', 'DOWNGRADE', 'RUG_WARNING');
CREATE TYPE alert_status AS ENUM ('pending', 'sent', 'acknowledged', 'expired');

CREATE TABLE launch_alerts (
    alert_id        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    launch_id       UUID NOT NULL REFERENCES launches(launch_id),
    alert_type      alert_type NOT NULL,
    alert_status    alert_status NOT NULL DEFAULT 'pending',
    score           INT NOT NULL,
    action_mode     TEXT NOT NULL,
    message         TEXT NOT NULL,
    context         JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    sent_at         TIMESTAMPTZ,
    acknowledged_at TIMESTAMPTZ
);

CREATE INDEX idx_alerts_pending ON launch_alerts (alert_status) WHERE alert_status = 'pending';
CREATE INDEX idx_alerts_launch ON launch_alerts (launch_id);
CREATE INDEX idx_alerts_created ON launch_alerts (created_at DESC);

-- Config entries for alert thresholds
INSERT INTO nxfx01_config (key, value) VALUES
    ('buy_trigger_threshold', '85'),
    ('evaluate_threshold', '60'),
    ('buy_threshold_final', '85')
ON CONFLICT (key) DO NOTHING;
