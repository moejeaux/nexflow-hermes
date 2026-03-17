import json
with open('/tmp/fifo_sample.json') as f:
    d = json.load(f)
print('top keys:', list(d.keys())[:10])
w = d.get('wallets')
if w is not None:
    print('wallets type:', type(w).__name__, 'len:', len(w))
    if isinstance(w, list) and len(w) > 0:
        print('first item type:', type(w[0]).__name__)
        if isinstance(w[0], dict):
            print('first item keys:', list(w[0].keys())[:8])
    elif isinstance(w, dict):
        fk = list(w.keys())[0]
        print('first key:', fk)
else:
    print('no wallets key')
