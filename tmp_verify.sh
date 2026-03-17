#!/bin/bash
set -a; source /Users/nexflow/.hermes/.env 2>/dev/null; set +a

PSQL="/opt/homebrew/opt/postgresql@16/bin/psql"
DB="postgresql://postgres.wposowmcdomdtvicnqsg:FpH68xSYnxzm1oAZ@aws-1-us-east-1.pooler.supabase.com:5432/postgres"

echo "=== Reset 1 launch to pending_initial for re-scoring ==="
PGPASSWORD=FpH68xSYnxzm1oAZ $PSQL "$DB" -c "
UPDATE launches 
SET status = 'pending_initial', 
    deployer_reputation = NULL, 
    funding_risk = NULL, 
    overall_safety_initial = NULL, 
    action_initial = NULL, 
    initial_scored_at = NULL,
    launchpad_trust_level = 'NONE'
WHERE launch_id = (SELECT launch_id FROM launches ORDER BY detected_at DESC LIMIT 1)
RETURNING launch_id, token_address;
"

echo ""
echo "=== Trigger run-cycle ==="
curl -s -X POST http://localhost:8100/ops/run-cycle -H "x-api-key: $NXFX01_API_KEY" | python3 -m json.tool

echo ""
echo "=== Check resulting scores ==="
PGPASSWORD=FpH68xSYnxzm1oAZ $PSQL "$DB" -c "
SELECT launch_id, contract_safety, deployer_reputation, funding_risk, overall_safety_initial, action_initial, launchpad_trust_level 
FROM launches ORDER BY detected_at DESC LIMIT 3;
"
