"""SDK contract using a mock server; this does not qualify a real endpoint."""
from datetime import datetime, timedelta, timezone
import boto3
from botocore.stub import Stubber
from moto import mock_aws
from app.beta.destinations import Destination, digest


def test_s3_multipart_versions_and_native_policy_readback(tmp_path):
    with mock_aws():
        client = boto3.client('s3', region_name='us-east-1')
        client.create_bucket(Bucket='amp-beta-test', ObjectLockEnabledForBucket=True)
        destination = Destination({'kind':'AWS_S3', 'bucket':'amp-beta-test','region':'us-east-1',
            'options':{'archive_object_lock_verified':True}})
        path = tmp_path/'large.txt'
        path.write_bytes(b'archive-content-' * 600000)
        policy={'mode':'COMPLIANCE','retention_until':(datetime.now(timezone.utc)+timedelta(days=30)).replace(microsecond=0).isoformat(), 'legal_hold':True}
        requests=[]
        destination.s3.client.meta.events.register('before-parameter-build.s3.CreateMultipartUpload', lambda params, **kwargs: requests.append(dict(params)))
        receipt=destination.write('test/payload',path,'text/plain',policy)
        assert receipt['version'] and receipt['bytes']==path.stat().st_size
        assert requests[0]['ObjectLockMode']=='COMPLIANCE' and requests[0]['ObjectLockLegalHoldStatus']=='ON'
        # Moto's GetObjectRetention route errors with this SDK. Validate the
        # separate readback API contract with explicit SDK responses instead.
        args={'Bucket':'amp-beta-test','Key':'test/payload','VersionId':receipt['version']}
        with Stubber(destination.s3.client) as stub:
            stub.add_response('get_object_retention',{'Retention':{'Mode':'COMPLIANCE','RetainUntilDate':datetime.fromisoformat(policy['retention_until'])}},args)
            stub.add_response('get_object_legal_hold',{'LegalHold':{'Status':'ON'}},args)
            evidence=destination.verify_policy('test/payload',receipt['version'],policy)
            assert evidence['verified'] and evidence['legal_hold']=='ON'
            stub.assert_no_pending_responses()
        client.put_object(Bucket='amp-beta-test', Key='test/payload', Body=b'new version')
        target=tmp_path/'readback'
        assert destination.read_to('test/payload',receipt['version'],target)==digest(path)
        assert target.read_bytes()==path.read_bytes()
