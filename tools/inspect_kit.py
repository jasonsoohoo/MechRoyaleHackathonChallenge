import gzip
import json
from pathlib import Path

root = Path(__file__).resolve().parents[1]
with gzip.open(root / 'kit/check/stream.jsonl.gz', 'rt') as stream:
    hello = json.loads(next(stream))
    print(json.dumps(hello['config'], indent=2))
    print(next(stream))
schema = json.loads((root / 'kit/mechfight.schema.json').read_text())
print('Schema definitions:', ', '.join(schema['definitions']))
