#!/usr/bin/env bash
# Check actual event topics from Aerodrome and Uniswap V2 factories on Base
set -euo pipefail

echo "=== Aerodrome PoolFactory events ==="
curl -s 'https://base.blockscout.com/api/v2/addresses/0x420DD381b31aEf6683db6B902084cB0FFECe40Da/logs' | python3 -c '
import json, sys
d = json.load(sys.stdin)
items = d.get("items", [])
for i in items[:3]:
    topics = i.get("topics", [])
    d2 = i.get("data", "")
    print(f"topic0: {topics[0] if topics else None}")
    print(f"topics: {topics}")
    print(f"topics count: {len(topics)}")
    print(f"data length: {len(d2)}")
    print(f"data: {d2[:200]}")
    print()
'

echo "=== Uniswap V2 Factory events ==="
curl -s 'https://base.blockscout.com/api/v2/addresses/0x8909Dc15e40173Ff4699343b6eB8132c65e18eC6/logs' | python3 -c '
import json, sys
d = json.load(sys.stdin)
items = d.get("items", [])
for i in items[:3]:
    topics = i.get("topics", [])
    d2 = i.get("data", "")
    print(f"topic0: {topics[0] if topics else None}")
    print(f"topics: {topics}")
    print(f"topics count: {len(topics)}")
    print(f"data length: {len(d2)}")
    print(f"data: {d2[:200]}")
    print()
'
