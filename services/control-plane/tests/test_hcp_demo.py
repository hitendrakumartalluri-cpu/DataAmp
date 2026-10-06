import json
import os
import uuid
import httpx
import pytest
from fastapi.testclient import TestClient
from app.services.hcp import HCPRestStorage,flatten_xml
from app.services.storage import backend_from_record,BackendOperationError
from app.beta.hcp_demo import HCPDemoService,FEATURES,MAPPING
from app.beta.security import Identity
from test_archive_beta import beta


@pytest.fixture
def hcp_flow(beta,monkeypatch):
    from app.demo.hcp_server import app
    client=TestClient(app)
    original=HCPRestStorage.__init__
    requests=[]
    def init(self,record):
        original(self,record)
        self.client.close()
        def transport(request):
            requests.append((request.method,str(request.url)))
            result=client.request(request.method,str(request.url.path)+'?'+request.url.query.decode(),headers=dict(request.headers))
            return httpx.Response(result.status_code,headers=result.headers,content=result.content)
        self.client=httpx.Client(transport=httpx.MockTransport(transport))
    monkeypatch.setattr(HCPRestStorage,'__init__',init)
    archive,workbench,db,enterprise,_,_,_=beta
    monkeypatch.setenv('AMP_HCP_DEMO_URL','http://fixture.test')
    monkeypatch.setenv('AMP_DEMO_MODE','true')
    service=HCPDemoService(db,archive.catalog,archive.processing,workbench,archive,enterprise)
    service.init_schema()
    flow=service.setup_demo('test')
    return service,flow,requests


def test_hcp_rest_is_not_s3_and_reads_pinned_annotations(hcp_flow):
    service,flow,requests=hcp_flow
    preview=service.preview('test',flow['id'],'invoices/record-1.pdf')
    assert preview['native_metadata']['business.record.type']=='INVOICE'
    assert preview['transformed_metadata']['amount']==125
    assert preview['transformed_metadata']['business_date']=='2025-10-04T00:00:00+00:00'
    assert 'payment dispute' in preview['text_preview']
    assert any('annotation=business' in url and 'version=1001' in url for _,url in requests)
    assert all(method in {'GET','HEAD'} for method,_ in requests)
    backend=backend_from_record(service.catalog.backend_record_for_group(flow['group_id']))
    assert isinstance(backend,HCPRestStorage)
    with pytest.raises(BackendOperationError):backend.put('a',b'payload')
    with pytest.raises(ValueError):backend.head('../escape')


def test_hcp_workflow_indexes_valid_records_reports_failed_transform_and_all_demo_actions(hcp_flow):
    service,flow,requests=hcp_flow
    run=service.enqueue('test',flow['id']);result=service.execute('test',run['id'])
    assert result['status']=='COMPLETE_WITH_ERRORS',result
    assert result['result']['processed']==12 and result['result']['indexed']==11,result
    assert len(result['result']['errors'])==1 and 'invalid-date' in result['result']['errors'][0]['key']
    operator=Identity('test','presenter',('admin',))
    for issue,name,mode,action in FEATURES:
        if not action or action=='pipeline':continue
        response=service.feature(operator,flow['id'],action)
        if action in {'pii','classify','export','evidence','bulk'}:
            assert response['status']=='COMPLETE',(issue,response)
        if action=='duplicates':assert len(response['duplicate_groups'])==1
        if action=='encrypted':assert len(response['documents'])==1
        if action=='authorization':assert 0<response['reader_count']<response['admin_count']
        if action=='guardrails':assert response['rejected']
    again=service.execute('test',service.enqueue('test',flow['id'])['id'])
    assert again['result']['indexed']==11
    assert service.db.scalar("SELECT COUNT(*) FROM document_projection WHERE tenant_id='test'")==11
    with pytest.raises(KeyError):service.workflow('foreign',flow['id'])


def test_hcp_annotation_errors_fail_closed_and_safe_xml():
    with pytest.raises(Exception):flatten_xml(b'<!DOCTYPE a [<!ENTITY x SYSTEM "file:///etc/passwd">]><a>&x;</a>')
    assert flatten_xml(b'<record><tag>A</tag><tag>B</tag></record>')['record.tag']==['A','B']
    with pytest.raises(ValueError):HCPRestStorage({'endpoint':'http://namespace.example','options':{}})


def test_solr_split_collection_live_contract():
    endpoint=os.getenv('AMP_TEST_SOLR_URL')
    if not endpoint:pytest.skip('real SolrCloud endpoint only exercised in dedicated CI')
    from app.beta.solr import SolrPair
    name='amp_test_'+uuid.uuid4().hex[:10]
    pair=SolrPair({'endpoint':endpoint,'metadata_collection':name+'_meta','text_collection':name+'_text'})
    pair.provision()
    source,recon=str(uuid.uuid4()),str(uuid.uuid4())
    fields={'object_key':'invoice.pdf','source_version':'1001','size_bytes':100,'jurisdiction':'UK','record_type':'INVOICE'}
    pair.upsert('test',source,recon,'invoice.pdf',fields,'Payment dispute involving Barclays')
    doc={'source_id':source,'recon_id':recon,'fields':fields}
    assert pair.describe()['metadata_count']==1
    assert pair.search('test','payment',[doc],[{'field':'jurisdiction','value':'UK'}])['total']==1
    assert pair.search('test','',[doc],[{'field':'jurisdiction','value':'DE'}])['total']==0
    assert pair.search('foreign','payment',[doc])['total']==0
    assert pair.search('test','payment',[])['total']==0
    assert pair.search('test','',[doc])['routes']==[name+'_meta']
