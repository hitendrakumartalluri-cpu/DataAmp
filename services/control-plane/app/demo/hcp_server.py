"""Isolated read-only HCP-shaped HTTP simulator; never described as an HCP VM."""
from fastapi import FastAPI,Request
from fastapi.responses import Response
from xml.sax.saxutils import quoteattr
from .fixtures import documents,headers

app=FastAPI(title='AMP HCP REST simulator — synthetic source only')
DATA=documents()

@app.get('/healthz')
def health():return {'mode':'SIMULATOR','objects':len(DATA),'native_hcp':False}

@app.api_route('/rest/{key:path}',methods=['GET','HEAD'])
def source(key:str,request:Request):
    if not key or key.endswith('/'):
        prefix=key
        children={}
        for path in DATA:
            if not path.startswith(prefix):continue
            remaining=path[len(prefix):]
            name=remaining.split('/')[0]
            children[name]='directory' if '/' in remaining else 'object'
        xml='<directoryEntries>'+''.join('<entry urlName='+quoteattr(prefix+name+('/' if kind=='directory' else ''))+' type='+quoteattr(kind)+'/>' for name,kind in sorted(children.items()))+'</directoryEntries>'
        return Response(xml,media_type='application/xml',headers={'X-HCP-Type':'directory'})
    item=DATA.get(key)
    if not item:return Response(status_code=404)
    if request.query_params.get('version') not in {None,item['version']}:return Response(status_code=404)
    if request.query_params.get('type')=='custom-metadata':
        value=item['annotations'].get(request.query_params.get('annotation','default'))
        return Response(value,media_type='application/xml') if value is not None else Response(status_code=404)
    return Response(b'' if request.method=='HEAD' else item['data'],headers=headers(key,item))
