import hashlib
import io
import json
import tempfile
import time
import os
import uuid
from pathlib import Path

import pytest

from app.db import Database
from app.services.catalog import CatalogService
from app.services.processing import ProcessingService
from app.services.enterprise import EnterpriseService
from app.beta.archive import ArchiveService, Conflict
from app.beta.workbench import WorkbenchService
from app.beta.security import Identity


@pytest.fixture
def beta(tmp_path):
    postgres_url = os.getenv("AMP_TEST_POSTGRES_URL")
    schema = "amp_test_" + uuid.uuid4().hex
    if postgres_url:
        import psycopg
        with psycopg.connect(postgres_url, autocommit=True) as connection:
            connection.execute('CREATE SCHEMA ' + schema)
        separator = '&' if '?' in postgres_url else '?'
        url = postgres_url + separator + 'options=-csearch_path%3D' + schema
    else:
        url = f"sqlite:///{tmp_path / 'state.db'}"
    db = Database(url)
    db.init_schema()
    catalog = CatalogService(db)
    processing = ProcessingService(db, catalog)
    enterprise = EnterpriseService(db)
    enterprise.init_schema()
    workbench = WorkbenchService(db, catalog, processing, tmp_path)
    workbench.init_schema()
    archive = ArchiveService(db, catalog, processing, tmp_path)
    archive.init_schema()
    archive.workbench, workbench.archive = workbench, archive
    storage = catalog.create_storage({"tenant_id": "test", "name": "archive", "kind": "LOCAL", "root_path": str(tmp_path / 'objects')})
    group = catalog.create_catalogue_group(tenant="test", storage_id=storage['id'], container_name=".", container_type="DIRECTORY")
    profile = archive.create_profile("test", "Documents", {"destination_group_id": group['id'], "source": {"kind": "API"}, "mapping": {"record_type": {"constant": "INVOICE"}}})
    yield archive, workbench, db, enterprise, profile, group, tmp_path
    if postgres_url:
        with psycopg.connect(postgres_url, autocommit=True) as connection:
            connection.execute('DROP SCHEMA ' + schema + ' CASCADE')


def submit(beta, key="inv1", metadata=None):
    archive, workbench, db, enterprise, profile, group, root = beta
    source = root / (key + '.txt')
    source.write_text("Invoice: contact billing@example.org")
    return archive.submit("test", profile['id'], source, source.name, key, "1", metadata or {"jurisdiction": "UK"}, key)


def test_archive_receipt_payload_metadata_search_and_retrieval(beta):
    archive, workbench, db, enterprise, profile, group, root = beta
    item = submit(beta)
    assert item['archive_status'] == 'ACCEPTED'
    result = archive.process('test', item['id'])
    assert result['archive_status'] == 'ARCHIVED'
    assert result['index_status'] == 'SEARCHABLE'
    assert result['receipt']['payload']['protection']['mode'] == 'INTEGRITY_ONLY'
    restored = root / 'restored'
    archive.download('test', item['id'], restored)
    assert hashlib.sha256(restored.read_bytes()).hexdigest() == result['sha256']
    assert len(workbench.search(Identity('test','operator',('admin',)), 'invoice')['results']) == 1
    assert archive.reconcile('test')['count'] == 0


def test_duplicate_submission_and_changed_content_conflict(beta):
    archive, _, _, _, profile, _, root = beta
    first = submit(beta)
    second = submit(beta)
    assert first['id'] == second['id']
    changed = root / 'changed.txt'
    changed.write_text('different')
    with pytest.raises(Conflict):
        archive.submit('test', profile['id'], changed, 'inv1.txt', 'inv1', '1', {'jurisdiction':'UK'}, 'inv1')


def test_index_failure_preserves_archive_and_retry_does_not_rewrite(beta, monkeypatch):
    archive, _, db, _, _, _, _ = beta
    item = submit(beta)
    real = archive.index_item
    monkeypatch.setattr(archive, 'index_item', lambda *args: (_ for _ in ()).throw(RuntimeError('index unavailable')))
    first = archive.process('test', item['id'])
    assert first['archive_status'] == 'ARCHIVED' and first['index_status'] == 'PENDING'
    receipt = first['receipt']
    monkeypatch.setattr(archive, 'index_item', real)
    second = archive.process('test', item['id'])
    assert second['index_status'] == 'SEARCHABLE'
    assert second['receipt'] == receipt
    assert db.scalar('SELECT COUNT(*) FROM archive_outbox') == 1


def test_crash_after_payload_write_is_reconciled(beta, monkeypatch):
    archive, _, _, _, _, _, root = beta
    item = submit(beta)
    original = archive._artifact
    calls = 0
    def broken(*args):
        nonlocal calls
        calls += 1
        result = original(*args)
        if calls == 1:
            raise RuntimeError('crash after payload success')
        return result
    monkeypatch.setattr(archive, '_artifact', broken)
    assert archive.process('test', item['id'])['archive_status'] == 'RETRY_PENDING'
    monkeypatch.setattr(archive, '_artifact', original)
    assert archive.process('test', item['id'])['archive_status'] == 'ARCHIVED'
    assert len(list((root / 'objects').rglob('payload'))) == 1


def test_required_worm_on_local_destination_quarantines(beta):
    archive, _, _, _, _, group, root = beta
    profile = archive.create_profile('test', 'Protected', {'destination_group_id':group['id'], 'protection':{'legal_hold': True}})
    path = root / 'doc'
    path.write_bytes(b'doc')
    item = archive.submit('test',profile['id'],path,'doc','doc','1',{},'protected')
    assert item['archive_status'] == 'QUARANTINED'
    assert archive.process('test',item['id'])['receipt'] == {}
    assert not list((root/'objects').rglob('payload'))


def test_tampered_metadata_and_index_drift_are_detected(beta):
    archive, _, db, _, _, _, root = beta
    item = archive.process('test',submit(beta)['id'])
    path = root / 'objects' / item['metadata_key']
    path.write_text('tampered')
    db.execute('DELETE FROM search_documents WHERE recon_id=?',(item['id'],))
    types = {f['type'] for f in archive.reconcile('test')['findings']}
    assert {'METADATA_CHECKSUM_MISMATCH','MISSING_FROM_INDEX'} <= types


def test_tenant_isolation_and_default_deny(beta):
    archive, workbench, *_ = beta
    item = archive.process('test', submit(beta)['id'])
    with pytest.raises(KeyError):
        archive.item('other', item['id'])
    assert workbench.search(Identity('test','alice'), 'invoice')['total'] == 0
    assert workbench.search(Identity('other','operator',('admin',)), 'invoice')['total'] == 0


def test_deny_overrides_grant_and_is_applied_before_facets(beta):
    archive, workbench, *_ = beta
    archive.process('test', submit(beta, 'public')['id'])
    archive.process('test', submit(beta, 'secret', {'jurisdiction':'UK','sensitivity':'SECRET'})['id'])
    workbench.add_access('test','group:staff','GRANT',{'jurisdiction':'UK'})
    workbench.add_access('test','group:staff','DENY',{'sensitivity':'SECRET'})
    alice = Identity('test','alice',groups=('staff',))
    assert workbench.search(alice,'invoice')['total'] == 1
    assert workbench.aggregate(alice)['count'] == 1


def test_export_is_owner_bound_expires_and_revalidates_access(beta):
    archive, workbench, *_ = beta
    archive.process('test',submit(beta)['id'])
    workbench.add_access('test','alice','GRANT',{'jurisdiction':'UK'})
    alice = Identity('test','alice')
    job = workbench.new_job(alice,'EXPORT',{'include_documents':True,'ttl_seconds':60})
    result = workbench.run_job(alice,job['id'])
    assert result['status'] == 'COMPLETE'
    token = workbench.download_token(alice,job['id'])['token']
    assert workbench.download_path(alice,job['id'],token).is_file()
    with pytest.raises(KeyError):
        workbench.download_path(Identity('test','bob'),job['id'],token)
    workbench.add_access('test','alice','DENY',{'jurisdiction':'UK'})
    with pytest.raises(PermissionError):
        workbench.download_path(alice,job['id'],token)
    workbench.db.execute('UPDATE beta_jobs SET expires_at=? WHERE id=?',(time.time()-1,job['id']))
    with pytest.raises(ValueError):
        workbench.download_token(alice,job['id'])


def test_metadata_mapping_required_fields_and_dates(beta):
    archive, _, _, _, _, group, root = beta
    profile = archive.create_profile('test','Dates',{'destination_group_id':group['id'],'mapping':{'event_date':{'source':'date','type':'date','format':'%d/%m/%Y','timezone':'Europe/London','required':True}}})
    path = root/'doc';path.write_bytes(b'doc')
    value = archive.submit('test',profile['id'],path,'doc','one','1',{'date':'04/10/2026'},'one')
    assert value['metadata']['event_date'] == '2026-10-03T23:00:00+00:00'
    missing = archive.submit('test',profile['id'],path,'doc','two','1',{},'two')
    assert missing['archive_status'] == 'QUARANTINED'


def test_mount_requires_ready_marker_rejects_path_escape_and_keeps_source(beta):
    archive, _, _, _, _, group, root = beta
    inbox = root/'inbox';inbox.mkdir();(inbox/'record.txt').write_text('record')
    manifest={'schema_version':1,'batch_id':'batch1','items':[{'path':'record.txt','business_id':'case1','revision':'1','idempotency_key':'case1'}, {'path':'../inv1.txt','business_id':'bad','revision':'1','idempotency_key':'bad'}]}
    (inbox/'batch.json').write_text(json.dumps(manifest))
    profile=archive.create_profile('test','Inbox',{'destination_group_id':group['id'],'source':{'kind':'MOUNT','root':str(inbox)}})
    with pytest.raises(ValueError):
        archive.collect_mount('test',profile['id'],'batch.json')
    (inbox/'batch.json.ready').touch()
    result=archive.collect_mount('test',profile['id'],'batch.json')
    assert result['items'][0]['archive_status']=='ACCEPTED'
    assert result['items'][1]['archive_status']=='FAILED'
    assert (inbox/'record.txt').is_file()


def test_schema_evolution_does_not_reread_or_change_text(beta):
    archive, workbench, db, *_=beta
    item=archive.process('test',submit(beta)['id'])
    who=Identity('test','operator',('admin',))
    workbench.schema(who,'business',{'country':'string','content':'text'})
    before=db.fetchall('SELECT * FROM search_documents')
    result=workbench.reproject(who,'business',{'country':'jurisdiction'})
    assert result['payloads_reread']==0
    assert db.fetchall('SELECT * FROM search_documents')==before
    assert workbench.search(who,filters=[{'field':'country','value':'UK'}])['total']==1


def test_pii_scan_uses_metadata_fields_and_never_persists_plaintext(beta):
    archive, workbench, db, enterprise, *_=beta
    archive.process('test',submit(beta)['id'])
    enterprise.create_rule({'tenant_id':'test','name':'Email','pattern':r'[\w.]+@[\w.]+','fields':['content']})
    who=Identity('test','operator',('admin',))
    job=workbench.new_job(who,'PII',{})
    assert workbench.run_job(who,job['id'])['result']['matches']==1
    assert all(row['evidence_masked']=='[REDACTED]' for row in db.fetchall('SELECT * FROM pii_findings'))


def test_capacity_and_cost_plans_are_explicit_and_use_source_bytes(beta):
    archive, workbench, *_=beta
    archive.process('test',submit(beta)['id'])
    plan=workbench.index_plan({'documents':25000000,'source_bytes':0,'max_documents_per_shard':10000000})
    assert plan['shards']==3 and plan['mode']=='PLAN_ONLY'
    cost=workbench.cost(Identity('test','operator',('admin',)),1,2)
    assert cost['mode']=='USER_SUPPLIED_RATE_ESTIMATE'
    assert cost['series'][0]['gib']>0


def test_encrypted_pdf_is_preserved_and_not_gibberish_indexed(beta):
    import pypdf
    archive, workbench, db, _, profile, _, root=beta
    writer=pypdf.PdfWriter();writer.add_blank_page(width=100,height=100);writer.encrypt('secret')
    source=root/'protected.pdf'
    with source.open('wb') as stream:writer.write(stream)
    value=archive.submit('test',profile['id'],source,'protected.pdf','secure','1',{},'secure')
    result=archive.process('test',value['id'])
    assert result['archive_status']=='ARCHIVED'
    doc=workbench.documents(Identity('test','operator',('admin',)))[0]
    assert doc['fields']['extraction']['encrypted'] is True
    assert 'PDF' not in db.fetchone('SELECT text_content FROM search_documents')['text_content']


def test_package_ingestion_rejects_traversal_and_preserves_per_item_results(beta):
    import zipfile
    from app.beta.packages import ingest_package
    archive, _, _, _, profile, _, root=beta
    manifest={'schema_version':1,'batch_id':'zip1','items':[{'path':'doc.txt','business_id':'doc','revision':'1','idempotency_key':'zipdoc'},{'path':'missing.txt','business_id':'bad','revision':'1','idempotency_key':'zipbad'}]}
    package=root/'batch.zip'
    with zipfile.ZipFile(package,'w') as out:
        out.writestr('manifest.json',json.dumps(manifest));out.writestr('doc.txt','package document')
    result=ingest_package(archive,'test',profile['id'],package)
    assert result['items'][0]['archive_status']=='ACCEPTED'
    assert result['items'][1]['archive_status']=='FAILED'
    with zipfile.ZipFile(package,'w') as out:out.writestr('../escape','bad')
    with pytest.raises(ValueError):ingest_package(archive,'test',profile['id'],package)


def test_csv_and_xml_manifest_contracts(beta):
    from app.beta.packages import normalize_manifest
    root=beta[-1]
    csv_path=root/'manifest.csv'
    csv_path.write_text('path,business_id,revision,idempotency_key,metadata_json\ndoc.txt,record,1,key,"{}"\n')
    assert normalize_manifest(csv_path,'csv')['items'][0]['business_id']=='record'
    xml_path=root/'manifest.xml'
    xml_path.write_text('<batch batch_id="xml"><item><path>doc.txt</path><business_id>record</business_id><revision>1</revision><idempotency_key>key</idempotency_key><metadata><field name="jurisdiction">UK</field></metadata></item></batch>')
    assert normalize_manifest(xml_path,'xml')['items'][0]['metadata']=={'jurisdiction':'UK'}


def test_local_destination_config_is_pinned_to_submission(beta):
    archive, _, db, _, _, group, root=beta
    item=submit(beta)
    db.execute('UPDATE storage_systems SET root_path=? WHERE id=?',(str(root/'other'),group['storage_id']))
    result=archive.process('test',item['id'])
    assert result['archive_status']=='ARCHIVED'
    assert (root/'objects'/result['payload_key']).is_file()
    assert not (root/'other').exists()


def test_search_filter_unknown_field_and_cross_tenant_index_are_rejected(beta):
    archive, workbench, db, enterprise, *_=beta
    archive.process('test',submit(beta)['id'])
    user=Identity('test','operator',('admin',))
    with pytest.raises(ValueError):workbench.search(user,filters=[{'field':'unmapped','value':'a'}])
    other=enterprise.create_index({'tenant_id':'other','name':'private'})
    with pytest.raises(ValueError):workbench.search(user,index_ids=[other['id']])


def test_evidence_and_bulk_plan_are_real_jobs_without_source_mutation(beta):
    archive, workbench, *_=beta
    archive.process('test',submit(beta)['id'])
    user=Identity('test','operator',('admin',))
    evidence=workbench.new_job(user,'EVIDENCE',{})
    assert workbench.run_job(user,evidence['id'])['result']['download_ready']
    plan=workbench.new_job(user,'BULK_PLAN',{'action':'DELETE'})
    result=workbench.run_job(user,plan['id'])['result']
    assert result['mode']=='PLAN_ONLY' and len(result['candidates'])==1
    assert archive.reconcile('test')['count']==0


def test_expected_checksum_is_quarantined_before_worker_can_claim(beta):
    archive, workbench, db, enterprise, profile, group, root = beta
    source = root/'bad-hash.txt'
    source.write_text('producer checksum mismatch')
    item = archive.submit('test', profile['id'], source, source.name, 'bad', '1', {}, 'bad', expected_sha256='0'*64)
    assert item['archive_status']=='QUARANTINED'
    assert archive.tick()==0
    assert not list((root/'objects').rglob('payload'))
