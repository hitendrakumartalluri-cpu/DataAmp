"""Optional real SolrCloud metadata/text pair. External errors never fall back silently."""
import json
import re
import httpx

class SolrPair:
    def __init__(self,config):
        self.base=config['endpoint'].rstrip('/')
        self.meta=config['metadata_collection']; self.text=config['text_collection']
        if not all(re.fullmatch(r'[a-zA-Z][a-zA-Z0-9_]{0,90}',x) for x in (self.meta,self.text)):
            raise ValueError('invalid Solr collection name')
        self.client=httpx.Client(timeout=60,follow_redirects=False,trust_env=False)

    def call(self,method,path,**kwargs):
        response=self.client.request(method,self.base+'/'+path,**kwargs)
        response.raise_for_status();body=response.json()
        if body.get('error') or body.get('responseHeader',{}).get('status',0)!=0:raise ValueError('Solr rejected operation')
        return body

    def provision(self,shards=1):
        if not 1<=int(shards)<=4:raise ValueError('beta provisions 1..4 shards')
        existing=self.call('GET','admin/collections',params={'action':'LIST','wt':'json'})['collections']
        for name in (self.meta,self.text):
            if name not in existing:
                self.call('GET','admin/collections',params={'action':'CREATE','name':name,'numShards':int(shards),'replicationFactor':1,'collection.configName':'_default','wt':'json'})
        return self.describe()

    def describe(self):
        return {'mode':'SOLR_NATIVE','metadata_collection':self.meta,'text_collection':self.text,
            'metadata_count':self.call('GET',self.meta+'/select',params={'q':'*:*','rows':0,'wt':'json'})['response']['numFound'],
            'text_count':self.call('GET',self.text+'/select',params={'q':'*:*','rows':0,'wt':'json'})['response']['numFound']}

    def upsert(self,tenant,source,recon,key,fields,text):
        identity=source+':'+recon
        common={'id':identity,'tenant_s':tenant,'source_s':source,'object_key_s':key,'recon_s':recon}
        metadata={**common,'metadata_s':json.dumps(fields,separators=(',',':')),'size_l':int(fields['size_bytes'])}
        for name,value in fields.items():
            if re.fullmatch(r'[a-zA-Z][a-zA-Z0-9_]{0,70}',name) and isinstance(value,(str,int,float,bool)):
                metadata[name+'_s']=str(value)
        # Replace older versions of the same source/key in each physical index.
        escaped=json.dumps(key)
        delete={'delete':{'query':'tenant_s:'+json.dumps(tenant)+' AND source_s:'+json.dumps(source)+' AND object_key_s:'+escaped}}
        for collection,doc in ((self.meta,metadata),(self.text,{**common,'body_txt':text})):
            self.call('POST',collection+'/update',params={'commit':'true'},json=delete)
            self.call('POST',collection+'/update',params={'commit':'true'},json=[doc])

    def search(self,tenant,query,authorized,filters=None):
        if len(authorized)>1000:raise ValueError('Solr demo scope exceeds 1000 authorized records; async/distributed routing required')
        if not authorized:return {'results':[],'total':0,'engine':'SOLR_NATIVE','routes':[]}
        ids=[x['source_id']+':'+x['recon_id'] for x in authorized]
        byid=dict(zip(ids,authorized))
        # IDs are trusted UUID pairs from the authorization-filtered ledger.
        fq=['tenant_s:'+json.dumps(tenant),'{!terms f=id}'+','.join(ids)]
        for filt in filters or []:
            name=filt['field']
            if not re.fullmatch(r'[a-zA-Z][a-zA-Z0-9_]{0,70}',name) or filt.get('op','eq')!='eq':
                raise ValueError('Solr demo accepts canonical equality filters; other operators use local reference search')
            fq.append(name+'_s:'+json.dumps(str(filt['value'])))
        meta=self.call('POST',self.meta+'/query',json={'query':'*:*','filter':fq,'limit':1000})['response']['docs']
        candidates=[d['id'] for d in meta]
        if query and candidates:
            body={'query':{'edismax':{'query':query,'qf':'body_txt','uf':'','q.op':'AND'}},'filter':['tenant_s:'+json.dumps(tenant),'{!terms f=id}'+','.join(candidates)],'limit':1000}
            hit=self.call('POST',self.text+'/query',json=body)['response']['docs']
            candidates=[d['id'] for d in hit]
        results=[{**byid[key],'name':byid[key]['fields'].get('object_key'),'source_version':byid[key]['fields'].get('source_version'),'snippet':'','engine':'SOLR_NATIVE'} for key in candidates]
        return {'results':results,'total':len(results),'engine':'SOLR_NATIVE','routes':[self.meta]+([self.text] if query else []),'authorization':'FILTERED_BEFORE_SOLR'}
