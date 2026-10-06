"""Bounded native HCP REST read connector; no gateway or native mutations."""
from __future__ import annotations
import os
import posixpath
import json
import hashlib
from urllib.parse import quote, urlparse, unquote
import httpx
from defusedxml.ElementTree import fromstring
from .storage import StorageBackend, ObjectStat, BackendResponseMeta, BackendOperationError


def flatten_xml(data):
    result = {}
    root = fromstring(data)
    def walk(node, prefix=''):
        name = node.tag.split('}')[-1]
        path = (prefix + '.' + name).strip('.')
        if len(node):
            for child in node: walk(child, path)
        else:
            value = (node.text or '').strip()
            if path in result:
                previous = result[path]
                result[path] = previous + [value] if isinstance(previous, list) else [previous, value]
            else: result[path] = value
    walk(root)
    return result


class HCPRestStorage(StorageBackend):
    def __init__(self, record):
        self.record = record
        self.options = record.get('options') or {}
        self.endpoint = (record.get('endpoint') or '').rstrip('/')
        parsed = urlparse(self.endpoint)
        if parsed.scheme not in {'https','http'} or not parsed.hostname or parsed.username or parsed.query or parsed.fragment or parsed.path not in {'','/rest'}:
            raise ValueError('use a namespace endpoint: https://namespace.tenant.hcp-domain, without credentials or query')
        if parsed.scheme != 'https' and not self.options.get('demo_http'):
            raise ValueError('HCP requires HTTPS; demo_http is only for an isolated simulator')
        self.endpoint = self.endpoint.removesuffix('/rest')
        self.limit = int(self.options.get('max_read_bytes', 64*1024*1024))
        if not 1 <= self.limit <= 1024**3: raise ValueError('max_read_bytes must be 1..1 GiB')
        self.client = httpx.Client(verify=self.options.get('ca_bundle') or True, timeout=30, follow_redirects=False, trust_env=bool(self.options.get("use_proxy_env",False)))

    def close(self):
        self.client.close()

    def __del__(self):
        if hasattr(self,"client"):
            self.client.close()

    def _key(self, key):
        if key.startswith('/') or '\\' in key or any(p in {'.','..'} for p in key.split('/')) or '://' in key:
            raise ValueError('invalid namespace-relative object key')
        return quote(key, safe='/')

    def _request(self, method, key='', params=None, cap=None):
        headers = {}
        token = os.getenv(self.options.get('auth_env', 'AMP_HCP_AUTH_TOKEN'), '')
        if token: headers['Authorization'] = token if token.startswith('HCP ') else 'HCP ' + token
        with self.client.stream(method, self.endpoint+'/rest/'+self._key(key), params=params or {}, headers=headers) as response:
            if response.status_code == 404: return None
            if response.status_code not in {200,204}:
                raise BackendOperationError('HCP request rejected', status=response.status_code, code='HCPReadFailure')
            limit = cap or self.limit
            if method != 'HEAD' and int(response.headers.get('content-length', '0')) > limit:
                raise ValueError('HCP response exceeds configured read limit')
            content = bytearray()
            if method != 'HEAD':
                for chunk in response.iter_bytes():
                    content.extend(chunk)
                    if len(content) > limit: raise ValueError('HCP response exceeds configured read limit')
            return bytes(content), dict(response.headers)

    def _stat(self, key, headers):
        h = {k.lower():v for k,v in headers.items()}
        native = {k.removeprefix('x-hcp-').replace('-','_'):v for k,v in h.items() if k.startswith('x-hcp-')}
        return ObjectStat(key, int(h.get('x-hcp-size',h.get('content-length','0'))),h.get('etag',''),h.get('x-hcp-versionid',''),
            h.get('content-type','application/octet-stream'),backend=BackendResponseMeta(headers=h,raw={'HCPSystemMetadata':native}),
            compliance={'retention':h.get('x-hcp-retention'), 'hold':h.get('x-hcp-retentionhold','false')})

    def head(self,key,version_id=''):
        response=self._request('HEAD',key,{'version':version_id} if version_id else {})
        return None if response is None else self._stat(key,response[1])

    def get_with_stat(self,key,version_id=''):
        response=self._request('GET',key,{'version':version_id} if version_id else {})
        if response is None: raise BackendOperationError('HCP object missing',status=404)
        return response[0],self._stat(key,response[1])

    def list(self,prefix=''):
        # Explicit inventories are useful for small demos and avoid directory
        # pagination assumptions; large estates require an MQE checkpoint lane.
        keys=self.options.get('keys')
        if keys is not None:
            if len(keys)>10000: raise ValueError('explicit inventory exceeds 10000 keys')
            for key in keys:
                if key.startswith(prefix):
                    stat=self.head(key)
                    if stat: yield stat
            return
        stack=['']; seen=set(); count=0
        while stack:
            directory=stack.pop()
            if directory in seen: raise ValueError('directory cycle')
            seen.add(directory)
            if len(seen)>10000: raise ValueError('directory walk limit exceeded; use a bounded inventory')
            response=self._request('GET',directory,cap=8*1024*1024)
            if response is None: continue
            root=fromstring(response[0])
            for entry in root.iter():
                if entry.tag.split('}')[-1] not in {'entry','directoryEntry'}: continue
                name=entry.attrib.get('urlName') or entry.attrib.get('name')
                if not name: raise ValueError('unrecognized HCP directory entry')
                name=unquote(name)
                if name.startswith('/rest/'): name=name[6:]
                elif name.startswith('/'): name=name.lstrip('/')
                elif '/' not in name.strip('/'): name=posixpath.join(directory,name)
                self._key(name)
                kind=entry.attrib.get('type','').lower()
                if kind=='directory': stack.append(name.rstrip('/')+'/'); continue
                if kind not in {'object','file'}: continue
                if entry.attrib.get('deleted','false').lower()=='true' or not name.startswith(prefix): continue
                count+=1
                if count>10000: raise ValueError('object walk limit exceeded; use MQE for larger inventories')
                stat=self.head(name)
                if stat: yield stat

    def metadata(self,key,stat):
        fields={'hcp_namespace':self.record.get('bucket') or urlparse(self.endpoint).hostname.split('.')[0],
            'hcp_version':stat.version_id, 'hcp_retention':stat.compliance.get('retention'),
            'hcp_hold':stat.compliance.get('hold')=='true'}
        fields.update({'hcp_'+k:v for k,v in stat.backend.raw.get('HCPSystemMetadata',{}).items()})
        header=stat.backend.headers.get('x-hcp-custommetadataannotations','')
        names=[part.split(';')[0].strip() for part in header.split(',') if part.strip()]
        if not names and stat.backend.headers.get('x-hcp-custom-metadata')=='true': names=['default']
        if len(names)>100: raise ValueError('annotation count exceeds beta limit')
        for name in names:
            params={'type':'custom-metadata','annotation':name}
            if stat.version_id: params['version']=stat.version_id
            response=self._request('GET',key,params,cap=4*1024*1024)
            if response is None: raise ValueError('declared annotation missing')
            data=response[0]
            try: values=json.loads(data)
            except (ValueError,UnicodeDecodeError): values=flatten_xml(data)
            if not isinstance(values,dict): raise ValueError('annotation root must be a field map')
            fields.update({name+'.'+k:v for k,v in values.items()})
        return fields

    def put(self,*args,**kwargs): raise BackendOperationError('HCP connector is read-only',status=405)
    def delete(self,*args,**kwargs): raise BackendOperationError('HCP connector is read-only',status=405)
