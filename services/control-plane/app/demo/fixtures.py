from __future__ import annotations
import io
import hashlib
from xml.sax.saxutils import escape


def pdf(text):
    value=text.replace('\\','\\\\').replace('(','\\(').replace(')','\\)')
    stream=f'BT /F1 12 Tf 50 740 Td ({value}) Tj ET'.encode()
    objects=[b'<< /Type /Catalog /Pages 2 0 R >>',b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
        b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>',
        b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',b'<< /Length '+str(len(stream)).encode()+b' >>\nstream\n'+stream+b'\nendstream']
    out=bytearray(b'%PDF-1.4\n'); offsets=[0]
    for n,obj in enumerate(objects,1):
        offsets.append(len(out));out.extend(str(n).encode()+b' 0 obj\n'+obj+b'\nendobj\n')
    start=len(out);out.extend(f'xref\n0 {len(objects)+1}\n0000000000 65535 f \n'.encode())
    for offset in offsets[1:]:out.extend(f'{offset:010d} 00000 n \n'.encode())
    out.extend(f'trailer\n<< /Size {len(objects)+1} /Root 1 0 R >>\nstartxref\n{start}\n%%EOF'.encode())
    return bytes(out)


def documents():
    from docx import Document
    from pypdf import PdfReader,PdfWriter
    result={}
    for i in range(1,9):
        kind='INVOICE' if i<=4 else 'CONTRACT'
        country='UK' if i%2 else 'DE'
        customer='Barclays' if i<=5 else 'Acme'
        text=f'{kind} {i}. {customer} payment dispute and archive evidence. Contact client{i}@example.test.'
        payload=pdf(text) if i%2 else text.encode()
        key=f'{kind.lower()}s/record-{i}'+('.pdf' if i%2 else '.txt')
        annotation=f'<record><type>{kind}</type><country>{country}</country><customer>{customer}</customer><date>04/10/2025</date><amount>{100+i*25}</amount><owner>legal</owner></record>'.encode()
        result[key]={'data':payload,'annotations':{'business':annotation},'version':str(1000+i),'retention':'0','hold':'false'}
    source=result['invoices/record-1.pdf'];result['duplicates/copy.pdf']={**source,'version':'2001'}
    d=Document();d.add_paragraph('Contract review. Acme employment dispute. employee@example.test');out=io.BytesIO();d.save(out)
    result['contracts/employment.docx']={'data':out.getvalue(),'annotations':{'business':b'<record><type>CONTRACT</type><country>UK</country><customer>Acme</customer><date>04/10/2025</date><amount>0</amount><owner>legal</owner></record>'},'version':'3001','retention':'0','hold':'true'}
    writer=PdfWriter();writer.add_page(PdfReader(io.BytesIO(pdf('Protected contract'))).pages[0]);writer.encrypt('demo-only');out=io.BytesIO();writer.write(out)
    result['restricted/encrypted.pdf']={'data':out.getvalue(),'annotations':{'business':b'<record><type>CONTRACT</type><country>UK</country><customer>Restricted</customer><date>04/10/2025</date><amount>0</amount><owner>legal</owner></record>'},'version':'4001','retention':'-1','hold':'true'}
    result['quarantine/invalid-date.txt']={'data':b'Mapping error demonstration','annotations':{'business':b'<record><type>INVOICE</type><country>UK</country><customer>Acme</customer><date>not-a-date</date><amount>25</amount></record>'},'version':'5001','retention':'0','hold':'false'}
    return result


def headers(key,item):
    import mimetypes
    return {'Content-Type':mimetypes.guess_type(key)[0] or 'application/octet-stream','Content-Length':str(len(item['data'])),
        'ETag':hashlib.sha256(item['data']).hexdigest(),'X-HCP-VersionID':item['version'],'X-HCP-Type':'object',
        'X-HCP-Size':str(len(item['data'])),'X-HCP-Retention':item['retention'],'X-HCP-RetentionHold':item['hold'],
        'X-HCP-ChangeTimeMilliseconds':'1759622400000','X-HCP-Custom-Metadata':'true',
        'X-HCP-CustomMetadataAnnotations':', '.join(name+';'+str(len(data)) for name,data in item['annotations'].items())}
