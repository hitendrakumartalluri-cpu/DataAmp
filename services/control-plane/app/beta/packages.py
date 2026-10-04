from __future__ import annotations
import csv
import io
import json
import os
import shutil
import tarfile
import tempfile
import zipfile
from pathlib import Path, PurePosixPath


def safe_name(name):
    normalized = name.replace('\\', '/')
    path = PurePosixPath(normalized)
    if path.is_absolute() or '..' in path.parts or ':' in normalized or not normalized:
        raise ValueError('unsafe package member path')
    return path.as_posix()


def normalize_manifest(path, batch_id):
    data = Path(path).read_bytes()
    if len(data) > 4 * 1024 * 1024:
        raise ValueError('manifest exceeds 4 MiB')
    if Path(path).suffix == '.json':
        result = json.loads(data)
    elif Path(path).suffix == '.csv':
        items = []
        for row in csv.DictReader(io.StringIO(data.decode('utf-8-sig'))):
            items.append({key: row[key] for key in ('path', 'business_id', 'revision', 'idempotency_key')})
            items[-1]['metadata'] = json.loads(row.get('metadata_json') or '{}')
            if row.get('sha256'):
                items[-1]['sha256'] = row['sha256']
        result = {'schema_version': 1, 'batch_id': batch_id, 'items': items}
    elif Path(path).suffix == '.xml':
        from defusedxml.ElementTree import fromstring
        root = fromstring(data)
        items = []
        for entry in root.findall('item'):
            item = {key: entry.findtext(key) for key in ('path','business_id','revision','idempotency_key','sha256')}
            item['metadata'] = {value.attrib['name']: value.text for value in entry.findall('metadata/field')}
            items.append(item)
        result = {'schema_version': 1, 'batch_id': root.attrib.get('batch_id', batch_id), 'items': items}
    else:
        raise ValueError('manifest must be JSON, CSV or XML')
    if result.get('schema_version') != 1 or not result.get('batch_id') or not 1 <= len(result.get('items', [])) <= 10000:
        raise ValueError('invalid manifest contract')
    return result


def ingest_package(service, tenant, profile_id, uploaded, manifest_name='manifest.json', batch_id='package'):
    limit = int(os.getenv('AMP_MAX_PACKAGE_BYTES', str(1024 ** 3)))
    with tempfile.TemporaryDirectory(dir=service.root) as directory:
        root = Path(directory)
        total = 0
        seen = set()
        if zipfile.is_zipfile(uploaded):
            with zipfile.ZipFile(uploaded) as package:
                if len(package.infolist()) > 10000:
                    raise ValueError('too many package members')
                for info in package.infolist():
                    name = safe_name(info.filename)
                    if info.is_dir():
                        continue
                    if info.external_attr >> 16 & 0o170000 == 0o120000:
                        raise ValueError('package symlinks are not permitted')
                    total += info.file_size
                    if total > limit or name in seen or len(PurePosixPath(name).parts) > 20:
                        raise ValueError('package limit or duplicate member')
                    seen.add(name)
                    target = root / name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with package.open(info) as source, target.open('xb') as out:
                        shutil.copyfileobj(source, out, 1024 * 1024)
        elif tarfile.is_tarfile(uploaded):
            with tarfile.open(uploaded) as package:
                count = 0
                for info in package:
                    count += 1
                    name = safe_name(info.name)
                    if info.isdir():
                        continue
                    if not info.isfile():
                        raise ValueError('only regular package files are permitted')
                    total += info.size
                    if count > 10000 or total > limit or name in seen or len(PurePosixPath(name).parts) > 20:
                        raise ValueError('package limit or duplicate member')
                    seen.add(name)
                    target = root / name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with package.extractfile(info) as source, target.open('xb') as out:
                        shutil.copyfileobj(source, out, 1024 * 1024)
        else:
            raise ValueError('upload must be a ZIP or TAR package')
        manifest = normalize_manifest(root / safe_name(manifest_name), batch_id)
        results = []
        for item in manifest['items']:
            try:
                name = safe_name(item['path'])
                source = root / name
                if not source.is_file():
                    raise ValueError('declared document is missing')
                from .destinations import digest
                if item.get('sha256') and digest(source) != item['sha256']:
                    raise ValueError('declared document checksum mismatch')
                results.append(service.submit(tenant,profile_id,source,name,item['business_id'],str(item['revision']),item.get('metadata',{}),item['idempotency_key'],manifest['batch_id']))
            except Exception as exc:
                results.append({'name':item.get('path'),'archive_status':'FAILED','error':str(exc)})
        return {'batch_id':manifest['batch_id'],'items':results,'package_bytes':total}
