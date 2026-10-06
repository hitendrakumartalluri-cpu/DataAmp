from __future__ import annotations
import os
import time
import json
from pathlib import Path
from ..services.catalog import uid,now
from ..services.storage import backend_from_record
from .solr import SolrPair

STAGES=['COLLECT','EXTRACT','TRANSFORM','INDEX','RECONCILE']
MAPPING={
 'record_type':{'source':'business.record.type','required':True},
 'jurisdiction':{'source':'business.record.country','required':True},
 'customer_name':{'source':'business.record.customer','required':True},
 'business_date':{'source':'business.record.date','type':'date','format':'%d/%m/%Y','timezone':'UTC','required':True},
 'amount':{'source':'business.record.amount','type':'integer','required':True},
 'department':{'source':'business.record.owner','constant':'legal'}}

FEATURES=[
 (1,'Encrypted-document detection','WORKING','encrypted'),(2,'Schema evolution','WORKING','schema'),
 (3,'Duplicate detection','WORKING','duplicates'),(4,'Cost estimates','WORKING_ESTIMATE','cost'),
 (5,'Query/access guardrails','WORKING','guardrails'),(6,'Classification','WORKING_RULES','classify'),
 (7,'Storage optimizer','PLAN_ONLY','optimizer'),(8,'Gateway adapters','RETIRED',None),
 (9,'Canonical metadata/date transformation','WORKING','transform'),(10,'Evidence pack','WORKING','evidence'),
 (11,'Unified gateway','RETIRED',None),(12,'Index completeness ledger','WORKING_LOCAL','ledger'),
 (13,'PII scanning','WORKING','pii'),(14,'Gateway portable sidecars','RETIRED',None),
 (15,'Split metadata/full-text routing','SOLR_OPTIONAL','split'),(16,'Async export/download','WORKING_BOUNDED','export'),
 (17,'Collection design/rollover','PLAN_AND_SOLR_PROVISION','capacity'),(18,'Authorization maps','WORKING','authorization'),
 (19,'English query assistant','DETERMINISTIC_PREVIEW','assistant'),(20,'Analytics/dashboard recommendations','WORKING_RULES','dashboards'),
 (21,'Lifecycle audit','WORKING_LOCAL','audit'),(22,'Retention/hold workflow','PLAN_ONLY','governance'),
 (23,'Gateway epic','RETIRED',None),(24,'Reconciliation assurance','WORKING_LOCAL','reconcile'),
 (25,'HCP collection/index/search','REST_CONTRACT_LIVE_GATE','pipeline'),(26,'Search-selected bulk actions','PLAN_ONLY','bulk'),
 (27,'Application archiving expansion','SEPARATE_BETA_SCOPE','archive')]

class HCPDemoService:
    def __init__(self,db,catalog,processing,workbench,archive,enterprise):
        self.db,self.catalog,self.processing,self.workbench,self.archive,self.enterprise=db,catalog,processing,workbench,archive,enterprise
        self.processing.workbench=workbench

    def init_schema(self):
        for sql in [
          '''CREATE TABLE IF NOT EXISTS hcp_workflows(id TEXT PRIMARY KEY,tenant_id TEXT NOT NULL,name TEXT NOT NULL,
             group_id TEXT NOT NULL,config_json TEXT NOT NULL,created_at TEXT NOT NULL)''',
          '''CREATE TABLE IF NOT EXISTS hcp_runs(id TEXT PRIMARY KEY,tenant_id TEXT NOT NULL,workflow_id TEXT NOT NULL,
             status TEXT NOT NULL,result_json TEXT NOT NULL DEFAULT '{}',created_at TEXT NOT NULL,updated_at TEXT NOT NULL)''']:
            self.db.execute(sql)

    def workflow(self,tenant,wid):
        row=self.db.fetchone('SELECT * FROM hcp_workflows WHERE tenant_id=? AND id=?',(tenant,wid))
        if not row:raise KeyError(wid)
        row['config']=self.db.loads(row.pop('config_json'),{})
        return row

    def workflows(self,tenant):
        return [self.workflow(tenant,row['id']) for row in self.db.fetchall('SELECT id FROM hcp_workflows WHERE tenant_id=? ORDER BY created_at',(tenant,))]

    def create(self,tenant,body):
        group=self.catalog.get_catalogue_group(body['group_id'])
        if group['tenant_id']!=tenant:raise PermissionError('source tenant mismatch')
        if self.catalog.get_storage(group['storage_id'])['kind'] not in {'HCP','HCP_REST'}:raise ValueError('HCP native REST source required')
        reserved={'source_id','source_group_id','object_key','content_sha256','size_bytes','source_version','archive_id','extraction'}
        if reserved.intersection(body.get('mapping',{})):raise ValueError('mapping cannot replace source identity or integrity fields')
        config={'mapping':body.get('mapping',MAPPING),'prefix':body.get('prefix',''),'limit':int(body.get('limit',1000)),
            'solr_pair':body.get('solr_pair'),'mode':body.get('mode','LIVE_HCP_UNQUALIFIED')}
        if not 1<=config['limit']<=10000:raise ValueError('workflow limit must be 1..10000')
        if config['solr_pair']:SolrPair(config['solr_pair'])
        wid=uid();self.db.execute('INSERT INTO hcp_workflows VALUES(?,?,?,?,?,?)',(wid,tenant,body['name'],group['id'],self.db.dumps(config),now()))
        return self.workflow(tenant,wid)

    def setup_demo(self,tenant):
        if os.getenv('AMP_DEMO_MODE','true').lower()!='true':raise PermissionError('demo setup disabled')
        previous=next((w for w in self.workflows(tenant) if w['config']['mode']=='SIMULATOR'),None)
        if previous:return previous
        storage=self.catalog.create_storage({'tenant_id':tenant,'name':'HCP REST synthetic demo','kind':'HCP',
            'endpoint':os.getenv('AMP_HCP_DEMO_URL','http://hcp-simulator:8080'),'options':{'demo_http':True}})
        group=self.catalog.create_catalogue_group(tenant=tenant,storage_id=storage['id'],container_type='HCP_NAMESPACE',container_name='hcp-demo',name='HCP demo namespace')
        self.enterprise.create_index({'tenant_id':tenant,'name':'HCP business records','engine':'LOCAL_BETA','source_group_ids':[group['id']],
            'aliases':{'document_type':'record_type','country':'jurisdiction','customer':'customer_name','date':'business_date'}})
        return self.create(tenant,{'name':'HCP collect → extract → transform → index','group_id':group['id'],'mode':'SIMULATOR'})

    def preview(self,tenant,wid,key):
        workflow=self.workflow(tenant,wid);backend=backend_from_record(self.catalog.backend_record_for_group(workflow['group_id']))
        stat=backend.head(key)
        if not stat:raise KeyError(key)
        metadata=backend.metadata(key,stat)
        from .archive import ArchiveService
        mapped=ArchiveService.enrich(None,metadata,{'config':{'mapping':workflow['config']['mapping']}})
        data=backend.get(key,stat.version_id)
        text,extraction=self.processing.extract_text(data,stat.content_type,key)
        return {'mode':workflow['config']['mode'],'key':key,'version':stat.version_id,'native_metadata':metadata,
            'transformed_metadata':mapped,'text_preview':text[:1500],'extraction':extraction,'stages':STAGES}

    def enqueue(self,tenant,wid):
        self.workflow(tenant,wid)
        rid=uid();self.db.execute('INSERT INTO hcp_runs VALUES(?,?,?,?,?,?,?)',(rid,tenant,wid,'QUEUED','{}',now(),now()))
        return self.run(tenant,rid)

    def run(self,tenant,rid):
        row=self.db.fetchone('SELECT * FROM hcp_runs WHERE tenant_id=? AND id=?',(tenant,rid))
        if not row:raise KeyError(rid)
        row['result']=self.db.loads(row.pop('result_json'),{})
        return row

    def execute(self,tenant,rid):
        run=self.run(tenant,rid);workflow=self.workflow(tenant,run['workflow_id']);config=workflow['config']
        with self.db.connection() as conn:
            changed=conn.execute(self.db._sql("UPDATE hcp_runs SET status='RUNNING',updated_at=? WHERE id=? AND status='QUEUED'"),(now(),rid)).rowcount
        if changed!=1:raise ValueError('run is not queued')
        start=time.monotonic()
        try:
            if config.get('solr_pair'):SolrPair(config['solr_pair']).provision()
            outcome=self.processing.index_source(workflow['group_id'],config['prefix'],config['limit'],config['mapping'],config.get('solr_pair'))
            status='COMPLETE_WITH_ERRORS' if outcome['errors'] else 'COMPLETE'
            result={**outcome,'mode':config['mode'],'seconds':round(time.monotonic()-start,3),
                'stages':[{'name':stage,'status':'COMPLETE_WITH_ERRORS' if outcome['errors'] else 'COMPLETE'} for stage in STAGES],
                'reconciliation':self.workbench.ledger.verify_local(tenant),'source_modified':False}
            self.catalog.audit(tenant,'HCP_WORKFLOW_COMPLETE',details={'run_id':rid,'workflow_id':workflow['id'],'indexed':outcome['indexed'],'failed':len(outcome['errors'])})
        except Exception as exc:status='FAILED';result={'error':str(exc),'mode':config['mode']}
        self.db.execute('UPDATE hcp_runs SET status=?,result_json=?,updated_at=? WHERE id=?',(status,self.db.dumps(result),now(),rid))
        return self.run(tenant,rid)

    def tick(self):
        for row in self.db.fetchall("SELECT id,tenant_id FROM hcp_runs WHERE status='QUEUED' ORDER BY created_at LIMIT 1"):
            self.execute(row['tenant_id'],row['id'])

    def feature(self,identity,wid,action):
        workflow=self.workflow(identity.tenant,wid);group=self.catalog.get_catalogue_group(workflow['group_id'])
        filters=[{'field':'source_group_id','op':'eq','value':group['id']}]
        docs=self.workbench.search(identity,'',filters,1000)['results']
        if not docs:raise ValueError('run the HCP workflow first')
        if action=='transform':return {'mode':'WORKING','sample':docs[0]['fields'],'mapping':workflow['config']['mapping']}
        if action=='encrypted':return {'mode':'WORKING','documents':[d for d in docs if d['fields'].get('extraction',{}).get('encrypted')]}
        if action=='duplicates':
            groups={}
            for doc in docs:groups.setdefault(doc['fields'].get('content_sha256'),[]).append(doc['name'])
            return {'mode':'WORKING','duplicate_groups':[{'sha256':key,'documents':value} for key,value in groups.items() if len(value)>1]}
        if action in {'ledger','reconcile'}:return self.workbench.ledger.verify_local(identity.tenant)
        if action=='cost':return self.workbench.cost(identity,.025,12,5)
        if action=='capacity':return self.workbench.index_plan({'name':'hcp_records','documents':10000000,'source_bytes':1024**4,'date_field':'business_date'})
        if action=='split':
            pair=workflow['config'].get('solr_pair')
            return SolrPair(pair).describe() if pair else {'mode':'LOCAL_REFERENCE','metadata_table':'document_projection','text_table':'search_documents','next':'Configure Solr pair to demonstrate physical collection separation'}
        if action=='schema':
            schema=self.workbench.schema(identity,'hcp_business_records',{'record_type':'string','jurisdiction':'string','business_date':'date','amount':'integer','document_type':'string'})
            projection=self.workbench.reproject(identity,'hcp_business_records',{'document_type':'record_type'})
            return {'schema':schema,'projection':projection,'source_reads':0,'text_reindex':False,'solr_schema_migration':False}
        if action=='assistant':return self.workbench.query_plan(identity,'Find Barclays invoices in UK from 2025 about payment dispute')
        if action=='guardrails':
            try:self.workbench.search(identity,'',[{'field':'unknown_secret_field','value':'x'}])
            except ValueError as exc:return {'mode':'WORKING','rejected':True,'reason':str(exc),'arbitrary_solr_execution':False}
        if action=='authorization':
            from .security import Identity
            reader=Identity(identity.tenant,'hcp-demo-legal-reader',(),('legal-demo',))
            self.workbench.add_access(identity.tenant,'group:legal-demo','GRANT',{'jurisdiction':'UK'})
            self.workbench.add_access(identity.tenant,'group:legal-demo','DENY',{'customer_name':'Restricted'})
            visible=self.workbench.search(reader,'',filters)['results']
            return {'mode':'WORKING','admin_count':len(docs),'reader_count':len(visible),'reader_documents':[d['name'] for d in visible],'principal':reader.actor}
        if action=='dashboards':return {'recommendations':self.workbench.recommend_dashboards(identity),'metrics':self.workbench.aggregate(identity,'record_type')}
        if action=='audit':return self.db.fetchall("SELECT * FROM audit_events WHERE tenant_id=? ORDER BY created_at DESC LIMIT 100",(identity.tenant,))
        if action=='optimizer':return self.workbench.optimizer(identity,[{'selector':{'record_type':'INVOICE'},'destination':'cold-tier'}])
        if action=='governance':return {'mode':'PLAN_ONLY','native_mutation':False,'candidates':[{'document':d['name'],'hold':d['fields'].get('hcp_hold'),'retention':d['fields'].get('hcp_retention')} for d in docs],
            'steps':['select','verify current HCP metadata','approve','execute qualified native API','readback evidence'],'blocked_reason':'Native HCP retention mutation executor is not qualified'}
        if action in {'pii','classify','export','evidence','bulk'}:
            if action=='pii' and not self.db.scalar('SELECT COUNT(*) FROM pii_rules WHERE tenant_id=? AND name=?',(identity.tenant,'HCP demo email'),0):
                self.enterprise.create_rule({'tenant_id':identity.tenant,'name':'HCP demo email','pattern':r'[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}','fields':['text_content'],'classification':'PERSONAL_DATA'})
            if action=='classify' and not self.db.scalar('SELECT COUNT(*) FROM beta_classifiers WHERE tenant_id=? AND name=?',(identity.tenant,'HCP invoices'),0):
                self.db.execute('INSERT INTO beta_classifiers VALUES(?,?,?,?,?,?)',(uid(),identity.tenant,'HCP invoices',self.db.dumps({'record_type':'INVOICE'}),self.db.dumps({'classification':'FINANCE','document_category':'INVOICE'}),now()))
            kind={'pii':'PII','classify':'CLASSIFY','export':'EXPORT','evidence':'EVIDENCE','bulk':'BULK_PLAN'}[action]
            job=self.workbench.new_job(identity,kind,{'filters':filters,'include_documents':action=='export','ttl_seconds':3600,'action':'HCP_METADATA_UPDATE'})
            return self.workbench.run_job(identity,job['id'])
        if action=='archive':return {'mode':'SEPARATE_SCOPE','profiles':self.archive.profiles(identity.tenant),'note':'Application archive intake remains available; HCP REST archive writes are not qualified'}
        if action=='pipeline':return self.enqueue(identity.tenant,wid)
        raise ValueError('unknown demo action')
