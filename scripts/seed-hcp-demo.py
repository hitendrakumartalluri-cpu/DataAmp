#!/usr/bin/env python3
"""Upload synthetic records to a dedicated HCP namespace, using local credentials."""
import argparse
import os
import sys
from pathlib import Path
from urllib.parse import quote,urlparse
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'services/control-plane'))
import httpx
from app.demo.fixtures import documents

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--endpoint',required=True,help='https://dedicated-demo-namespace.tenant.hcp-domain')
parser.add_argument('--ca-bundle')
parser.add_argument('--upload',action='store_true',help='Explicitly upload into the dedicated namespace; default lists planned keys')
args=parser.parse_args()
parsed=urlparse(args.endpoint)
if parsed.scheme!='https' or parsed.path not in {'','/'} or parsed.username or parsed.query:
    parser.error('supply an HTTPS namespace root without credentials')
records=documents()
if not args.upload:
    for key in records:print(key)
    print('Dry run. Use --upload only against a dedicated demo namespace.')
    sys.exit(0)
token=os.environ.get('AMP_HCP_AUTH_TOKEN','')
if not token:parser.error('set AMP_HCP_AUTH_TOKEN locally')
header={'Authorization':token if token.startswith('HCP ') else 'HCP '+token}
with httpx.Client(verify=args.ca_bundle or True,timeout=60,follow_redirects=False,trust_env=False) as client:
    for key,item in records.items():
        url=args.endpoint.rstrip('/')+'/rest/'+quote(key,safe='/')
        existing=client.head(url,headers=header)
        if existing.status_code not in {404}:
            raise RuntimeError('Refusing to replace an existing or inaccessible object: '+key)
        response=client.put(url,headers={**header,'Content-Type':'application/octet-stream'},content=item['data'])
        response.raise_for_status()
        for name,value in item['annotations'].items():
            response=client.put(url,headers={**header,'Content-Type':'application/xml'},params={'type':'custom-metadata','annotation':name},content=value)
            response.raise_for_status()
        print('Uploaded '+key)
print('Payloads and annotations uploaded. Native holds/retention were not modified.')
