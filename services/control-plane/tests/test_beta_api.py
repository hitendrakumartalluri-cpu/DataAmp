"""HTTP contracts run in a fresh process to isolate import-time configuration."""
import os
import subprocess
import sys
from pathlib import Path


def test_beta_http_identity_archive_package_export_contract(tmp_path):
    program = r'''
import hashlib, io, json, zipfile, os
from fastapi.testclient import TestClient
identities = {
    hashlib.sha256(b'admin').hexdigest(): {'tenant':'demo','actor':'operator','roles':['admin'],'groups':[]},
    hashlib.sha256(b'reader').hexdigest(): {'tenant':'demo','actor':'reader','roles':[],'groups':[]},
    hashlib.sha256(b'foreign').hexdigest(): {'tenant':'other','actor':'operator','roles':['admin'],'groups':[]}}
os.environ['AMP_IDENTITIES_JSON'] = json.dumps(identities)
from app.main import app
with TestClient(app) as client:
    admin = {'Authorization':'Bearer admin'}
    reader = {'Authorization':'Bearer reader'}
    foreign = {'Authorization':'Bearer foreign'}
    assert client.get('/api/v1/beta/identity',headers={'Authorization':'Bearer unknown'}).status_code == 401
    assert client.get('/api/v1/beta/identity',headers=foreign).json()['tenant']=='other'
    assert client.get('/api/v1/storages',headers=foreign).status_code==403
    assert client.post('/api/v1/storages?tenant_id=other',headers=foreign,json={'name':'unsafe-default','kind':'LOCAL','root_path':'/tmp'}).status_code==403
    assert client.get('/api/v1/beta/archive/profiles',headers=reader).status_code==403
    profile = client.get('/api/v1/beta/archive/profiles',headers=admin).json()[0]
    data={'profile_id':profile['id'],'business_id':'API-1','revision':'1','idempotency_key':'api-1','metadata':'{"jurisdiction":"UK"}'}
    upload=client.post('/api/v1/beta/archive/upload',headers=admin,data=data,files={'file':('api.txt',b'HTTP invoice example','text/plain')})
    assert upload.status_code==202, upload.text
    iid=upload.json()['id']
    assert client.get('/api/v1/beta/archive/items/'+iid,headers=foreign).status_code==404
    receipt=client.post('/api/v1/beta/archive/items/'+iid+'/process',headers=admin)
    assert receipt.status_code==200 and receipt.json()['archive_status']=='ARCHIVED', receipt.text
    assert client.get('/api/v1/beta/archive/items/'+iid+'/download',headers=admin).content==b'HTTP invoice example'
    assert client.get('/api/v1/beta/archive/items/'+iid+'/download',headers=reader).status_code==403
    assert client.post('/api/v1/beta/search',headers=reader,json={'query':'HTTP'}).json()['total']==0
    manifest={'schema_version':1,'batch_id':'api-package','items':[{'path':'package.txt','business_id':'P1','revision':'1','idempotency_key':'P1','metadata':{}}]}
    buffer=io.BytesIO()
    with zipfile.ZipFile(buffer,'w') as package:
        package.writestr('manifest.json',json.dumps(manifest)); package.writestr('package.txt','package invoice')
    result=client.post('/api/v1/beta/archive/package',headers=admin,data={'profile_id':profile['id']},files={'file':('batch.zip',buffer.getvalue(),'application/zip')})
    assert result.status_code==202 and len(result.json()['items'])==1, result.text
    job=client.post('/api/v1/beta/jobs',headers=admin,json={'kind':'EXPORT','request':{'query':'HTTP'}})
    assert job.status_code==202, job.text
    jid=job.json()['id']
    result=client.post('/api/v1/beta/jobs/'+jid+'/run',headers=admin)
    assert result.status_code==200 and result.json()['status']=='COMPLETE', result.text
    assert client.get('/api/v1/beta/jobs/'+jid,headers=reader).status_code==404
    token=client.post('/api/v1/beta/jobs/'+jid+'/download-token',headers=admin).json()['token']
    assert client.get('/api/v1/beta/jobs/'+jid+'/download',headers=admin,params={'token':token}).content.startswith(b'PK')
    assert client.get('/api/v1/beta/jobs/'+jid+'/download',headers=reader,params={'token':token}).status_code==404
'''
    environment = {**os.environ, 'AMP_DATABASE_URL':f'sqlite:///{tmp_path}/api.db',
        'AMP_DATA_ROOT':str(tmp_path/'data'), 'AMP_WORKER_ENABLED':'false', 'AMP_DEMO_MODE':'true',
        'PYTHONPATH':str(Path(__file__).resolve().parents[1])}
    result = subprocess.run([sys.executable, '-c', program], env=environment, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
