"""A7C5H3A zero-provider authenticated delivery tests; NEVER import main.py."""
import ast
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace as O
import unittest
from unittest import mock
from uuid import UUID

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import services.apple_storekit_delivery as m
from services.apple_storekit_contract import AppleContractError, PRODUCT_ID
from services.apple_storekit_settlement import AppleSettlementError, AppleSettlementResult

USER = '12345678-1234-4234-8234-123456789012'
OTHER = '12345678-1234-4234-8234-123456789013'
NOW = datetime.now(timezone.utc)


def fake_decoded(**overrides):
    data = dict(bundleId='ai.godincorporated.temple', environment='Sandbox',
                productId=PRODUCT_ID, appAccountToken=USER,
                originalTransactionId='8111000000001', transactionId='8111000000002',
                purchaseDate=int((NOW-timedelta(days=1)).timestamp()*1000),
                expiresDate=int((NOW+timedelta(days=28)).timestamp()*1000),
                signedDate=int((NOW-timedelta(minutes=1)).timestamp()*1000),
                revocationDate=None, isUpgraded=False)
    data.update(overrides)
    return O(**data)


class MockHTTPException(Exception):
    def __init__(self, status_code, detail):
        self.status_code = status_code
        self.detail = detail
        super().__init__(detail)


class FakeApp:
    def __init__(self):
        self.paths = []

    def get(self, path):
        return self.decorate('GET', path)

    def post(self, path):
        return self.decorate('POST', path)

    def decorate(self, method, path):
        def wrap(fn):
            self.paths.append((method, path, fn.__name__))
            return fn
        return wrap


class FakeLogger:
    def warning(self, *args, **kwargs):
        pass


class FakeRequest:
    def __init__(self, user=USER, verified=True, body=None, headers=None):
        self.user = {'user_id': user, 'is_verified': verified} if user else None
        self.headers = headers or {'content-type': 'application/json'}
        self._body = body if body is not None else json.dumps({'signed_transaction':'aaa.bbb.ccc'}).encode()
        self.body_calls = 0

    async def body(self):
        self.body_calls += 1
        return self._body


class DeliveryTests(unittest.TestCase):
    def test_dormant_config_never_reads_cert(self):
        with self.assertRaises(m.AppleDeliveryError):
            m.load_delivery_configuration({})
        self.assertFalse(m.delivery_enabled({}))
        self.assertFalse(m.delivery_enabled({'GODINC_APPLE_DELIVERY_MODE':'enabled'}))

    def test_known_db_environment_required(self):
        for db, env in [('godinc_prod','Sandbox'), ('godinc_dev','Production'),
                        ('unknown_db','Sandbox'), ('godinc_clean_staging_db','Production')]:
            with self.subTest(db=db,env=env), self.assertRaises(m.AppleDeliveryError):
                m.load_delivery_configuration({'GODINC_APPLE_DELIVERY_MODE':'verified_settlement',
                                               'GODINC_APPLE_EXPECTED_DATABASE':db,
                                               'GODINC_APPLE_ENVIRONMENT':env})

    def config(self, environment='Sandbox', app_id='', roots=True):
        root = Path(self.tmp.name) / 'AppleRootCA.cer'
        if roots:
            root.write_bytes((ROOT / 'services' / 'apple_storekit_trust' /
                              'AppleRootCA-G2.cer').read_bytes())
        return {'GODINC_APPLE_DELIVERY_MODE':'verified_settlement',
                'GODINC_APPLE_EXPECTED_DATABASE': 'godinc_dev' if environment=='Sandbox' else 'godinc_prod',
                'GODINC_APPLE_ENVIRONMENT':environment,
                'GODINC_APPLE_APP_ID':app_id,
                'GODINC_APPLE_ROOT_CERT_PATHS':str(root)}

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def test_valid_local_config_and_token(self):
        config = m.load_delivery_configuration(self.config())
        self.assertEqual(config.expected_database, 'godinc_dev')
        self.assertEqual(config.environment, 'Sandbox')
        self.assertIsNone(config.apple_app_id)
        self.assertEqual(len(config.roots), 1)
        token = m.authenticated_account_token(user_id=USER, config=config)
        self.assertEqual(token['app_account_token'], USER)
        self.assertEqual(token['product_id'], PRODUCT_ID)
        self.assertNotIn('email', token)

    def test_production_app_id_and_roots_mandatory(self):
        with self.assertRaises(m.AppleDeliveryError):
            m.load_delivery_configuration(self.config('Production', ''))
        with self.assertRaises(m.AppleDeliveryError):
            m.load_delivery_configuration(self.config('Production', '0'))
        valid=m.load_delivery_configuration(self.config('Production', '123456789'))
        self.assertEqual(valid.apple_app_id,123456789)
        self.assertEqual(valid.expected_database,'godinc_prod')

    def test_sandbox_production_app_id_refused(self):
        with self.assertRaises(m.AppleDeliveryError):
            m.load_delivery_configuration(self.config(app_id='123456789'))

    def test_missing_root_refused(self):
        with self.assertRaises(m.AppleDeliveryError):
            m.load_delivery_configuration(self.config(roots=False))

    def test_duplicate_root_refused(self):
        conf=self.config()
        conf['GODINC_APPLE_ROOT_CERT_PATHS'] += os.pathsep + conf['GODINC_APPLE_ROOT_CERT_PATHS']
        with self.assertRaises(m.AppleDeliveryError):
            m.load_delivery_configuration(conf)

    def test_substituted_der_root_refused(self):
        conf = self.config()
        Path(conf['GODINC_APPLE_ROOT_CERT_PATHS']).write_bytes(b'\x30'+b'B'*250)
        with self.assertRaises(m.AppleDeliveryUnavailable):
            m.load_delivery_configuration(conf)

    def test_g3_trusted_root_accepted(self):
        conf = self.config()
        root = Path(conf['GODINC_APPLE_ROOT_CERT_PATHS'])
        root.write_bytes((ROOT / 'services' / 'apple_storekit_trust' /
                          'AppleRootCA-G3.cer').read_bytes())
        loaded = m.load_delivery_configuration(conf)
        self.assertEqual(loaded.roots, (root.read_bytes(),))

    def test_duplicate_certificate_from_different_paths_refused(self):
        conf = self.config()
        first = Path(conf['GODINC_APPLE_ROOT_CERT_PATHS'])
        second = first.parent / 'DuplicateRoot.cer'
        second.write_bytes(first.read_bytes())
        conf['GODINC_APPLE_ROOT_CERT_PATHS'] += os.pathsep + str(second)
        with self.assertRaises(m.AppleDeliveryUnavailable):
            m.load_delivery_configuration(conf)

    def test_both_official_roots_accepted(self):
        conf = self.config()
        second = Path(self.tmp.name) / 'AppleRootCA-G3.cer'
        second.write_bytes((ROOT / 'services' / 'apple_storekit_trust' /
                            'AppleRootCA-G3.cer').read_bytes())
        conf['GODINC_APPLE_ROOT_CERT_PATHS'] += os.pathsep + str(second)
        loaded = m.load_delivery_configuration(conf)
        self.assertEqual(len(loaded.roots), 2)

    def test_symlink_root_refused(self):
        conf=self.config()
        ref=Path(self.tmp.name) / 'alias.cer'
        ref.symlink_to(conf['GODINC_APPLE_ROOT_CERT_PATHS'])
        conf['GODINC_APPLE_ROOT_CERT_PATHS']=str(ref)
        with self.assertRaises(m.AppleDeliveryError):
            m.load_delivery_configuration(conf)

    def test_malformed_root_refused(self):
        conf=self.config()
        Path(conf['GODINC_APPLE_ROOT_CERT_PATHS']).write_bytes(b'X'*251)
        with self.assertRaises(m.AppleDeliveryError):
            m.load_delivery_configuration(conf)

    def test_unverified_account_identity_refused(self):
        conf=m.load_delivery_configuration(self.config())
        for value in (None,'123','ABCDEF78-1234-4234-8234-123456789012',OTHER+'junk'):
            with self.subTest(value=value), self.assertRaises(m.AppleDeliveryError):
                m.authenticated_account_token(user_id=value, config=conf)

    def call(self, decoded=None, result=None, payload=None, user=USER):
        conf=m.AppleDeliveryConfiguration('godinc_dev','Sandbox',None,(b'\x30abc',))
        verifier=O(verify_and_decode_signed_transaction=lambda signed: decoded or fake_decoded())
        settlement = mock.Mock(return_value=result or AppleSettlementResult('granted','8111000000002',OTHER,True))
        with mock.patch.object(m,'build_signed_data_verifier',return_value=verifier) as build, \
             mock.patch.object(m,'settle_verified_apple_transaction',settlement):
            data=m.deliver_apple_signed_transaction(
                payload=payload or {'signed_transaction':'aaa.bbb.ccc'},
                user_id=user, config=conf,
                connection_factory=lambda: None,
                compute_floors=lambda h,c:{'donor':None,'scroll':None,'fallback':'pilgrim'})
        return data,build,settlement

    def test_signed_jws_verification_before_write(self):
        outcome,build,settlement=self.call()
        self.assertEqual(outcome['status'],'granted')
        self.assertTrue(outcome['durably_recorded'])
        self.assertTrue(outcome['access_granted_this_delivery'])
        self.assertEqual(outcome['refresh_entitlement_from'],'/me')
        self.assertEqual(build.call_count,1)
        self.assertEqual(settlement.call_count,1)
        self.assertEqual(settlement.call_args.kwargs['expected_database'],'godinc_dev')
        self.assertEqual(settlement.call_args.kwargs['facts'].app_account_token,USER)

    def test_duplicate_durable_receipt_not_misrepresented_as_grant(self):
        data,_,_=self.call(result=AppleSettlementResult('already_processed','8111000000002',OTHER,False))
        self.assertEqual(data['status'],'already_processed')
        self.assertTrue(data['durably_recorded'])
        self.assertFalse(data['access_granted_this_delivery'])

    def test_stale_durable_receipt_not_misrepresented_as_grant(self):
        data,_,_=self.call(result=AppleSettlementResult('stale_ignored','8111000000002',OTHER,False))
        self.assertEqual(data['status'],'stale_ignored')
        self.assertFalse(data['access_granted_this_delivery'])

    def test_mismatched_apple_account_never_settles(self):
        with mock.patch.object(m,'settle_verified_apple_transaction') as writer, \
             mock.patch.object(m,'build_signed_data_verifier',return_value=O(
                 verify_and_decode_signed_transaction=lambda _:fake_decoded(appAccountToken=OTHER))):
            with self.assertRaises(AppleContractError):
                m.deliver_apple_signed_transaction(
                    payload={'signed_transaction':'aaa.bbb.ccc'},user_id=USER,
                    config=m.AppleDeliveryConfiguration('godinc_dev','Sandbox',None,(b'a',)),
                    connection_factory=lambda:None, compute_floors=lambda *args:None)
            writer.assert_not_called()

    def test_invalid_signature_never_settles(self):
        def fail(_): raise ValueError('Bad Apple signature')
        with mock.patch.object(m,'settle_verified_apple_transaction') as writer, \
             mock.patch.object(m,'build_signed_data_verifier',return_value=O(
                 verify_and_decode_signed_transaction=fail)):
            with self.assertRaises(AppleContractError):
                m.deliver_apple_signed_transaction(
                    payload={'signed_transaction':'aaa.bbb.ccc'},user_id=USER,
                    config=m.AppleDeliveryConfiguration('godinc_dev','Sandbox',None,(b'a',)),
                    connection_factory=lambda:None, compute_floors=lambda *args:None)
            writer.assert_not_called()

    def test_missing_apple_sdk_refuses_before_any_database_write(self):
        conf=m.AppleDeliveryConfiguration('godinc_dev','Sandbox',None,(b'\x30a',))
        with mock.patch.object(m,'build_signed_data_verifier',side_effect=AppleContractError('library absent')), \
             mock.patch.object(m,'settle_verified_apple_transaction') as writer:
            with self.assertRaises(m.AppleDeliveryUnavailable):
                m.deliver_apple_signed_transaction(
                    payload={'signed_transaction':'aaa.bbb.ccc'},user_id=USER,config=conf,
                    connection_factory=lambda:None,compute_floors=lambda *x:None)
            writer.assert_not_called()

    def test_payload_field_shape_refuses_before_verifier(self):
        conf=m.AppleDeliveryConfiguration('godinc_dev','Sandbox',None,(b'a',))
        for payload in ({}, {'signed_transaction':'abc'},
                        {'signed_transaction':'aaa.bbb.ccc','user_id':OTHER},
                        {'signed_transaction':1}, [1]):
            with self.subTest(payload=payload), mock.patch.object(m,'build_signed_data_verifier') as build:
                with self.assertRaises(m.AppleDeliveryError):
                    m.deliver_apple_signed_transaction(payload=payload,user_id=USER,config=conf,
                                                       connection_factory=lambda:None,compute_floors=lambda *x:None)
                build.assert_not_called()

    def test_no_separate_billing_writer_or_secret_jws_logging(self):
        source=(ROOT/'services/apple_storekit_delivery.py').read_text()
        self.assertNotIn('stripe.',source)
        self.assertNotIn('INSERT INTO ',source)
        self.assertNotIn('logger.info(',source)


class RegisteredRouteTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        source=(ROOT/'main.py').read_text()
        tree=ast.parse(source)
        names={'apple_storekit_account_token','apple_storekit_verified_transaction'}
        nodes=[n for n in tree.body if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)) and n.name in names]
        self.assertEqual(set(n.name for n in nodes),names)
        app=FakeApp()
        self.context={
            'app':app,'Request':object,'HTTPException':MockHTTPException,
            'os':os,'json':json,'logger':FakeLogger(),
            'get_current_user':lambda request:request.user,
            'get_active_paid_rail_for_user':lambda user_id:None,
            'get_db_connection':lambda:None,
            'compute_donor_floor_plan':lambda h:'pilgrim',
            'compute_scroll_floor_plan':lambda count:'pilgrim',
            'compute_fallback_floor_plan':lambda auth,donor,scroll:'pilgrim',
        }
        mod=ast.fix_missing_locations(ast.Module(body=nodes,type_ignores=[]))
        exec(compile(mod,'extracted main.py A7C5H3A routes','exec'),self.context)
        self.app=app
        self.get=self.context['apple_storekit_account_token']
        self.post=self.context['apple_storekit_verified_transaction']
        self.env={'GODINC_APPLE_DELIVERY_MODE':'verified_settlement'}
        self.config=m.AppleDeliveryConfiguration('godinc_dev','Sandbox',None,(b'a',))

    def test_exact_routes_registered_once(self):
        self.assertEqual(self.app.paths,[
            ('GET','/billing/apple/account-token','apple_storekit_account_token'),
            ('POST','/billing/apple/verified-transaction','apple_storekit_verified_transaction')])

    async def test_disabled_routes_return_404_before_user_lookup(self):
        with mock.patch.dict(os.environ,{'GODINC_APPLE_DELIVERY_MODE':''}), \
             mock.patch.dict(self.context,{'get_current_user':lambda r:1/0}):
            with self.assertRaises(MockHTTPException) as err:
                self.get(FakeRequest())
            self.assertEqual(err.exception.status_code,404)
            with self.assertRaises(MockHTTPException) as err:
                await self.post(FakeRequest())
            self.assertEqual(err.exception.status_code,404)

    async def test_anonymous_user_cannot_get_token_or_settle(self):
        with mock.patch.dict(os.environ,self.env), \
             mock.patch.object(m,'load_delivery_configuration',return_value=self.config):
            with self.assertRaises(MockHTTPException) as err:
                self.get(FakeRequest(user=None))
            self.assertEqual(err.exception.status_code,401)
            with self.assertRaises(MockHTTPException) as err:
                await self.post(FakeRequest(user=None))
            self.assertEqual(err.exception.status_code,401)

    async def test_unverified_user_cannot_deliver(self):
        with mock.patch.dict(os.environ,self.env), \
             mock.patch.object(m,'load_delivery_configuration',return_value=self.config):
            with self.assertRaises(MockHTTPException) as err:
                await self.post(FakeRequest(verified=False))
            self.assertEqual(err.exception.status_code,401)

    def test_native_account_token_only_verified_uuid(self):
        with mock.patch.dict(os.environ,self.env), \
             mock.patch.object(m,'load_delivery_configuration',return_value=self.config):
            self.assertEqual(self.get(FakeRequest())['app_account_token'],USER)

    def test_existing_stripe_paid_rail_blocks_purchase_token(self):
        self.context['get_active_paid_rail_for_user'] = lambda _: {'provider':'stripe','plan_code':'seeker'}
        with mock.patch.dict(os.environ,self.env), \
             mock.patch.object(m,'load_delivery_configuration',return_value=self.config):
            with self.assertRaises(MockHTTPException) as err:
                self.get(FakeRequest())
            self.assertEqual(err.exception.status_code,409)

    def test_existing_apple_rail_can_get_restore_token(self):
        self.context['get_active_paid_rail_for_user'] = lambda _: {'provider':'apple','plan_code':'seeker'}
        with mock.patch.dict(os.environ,self.env), \
             mock.patch.object(m,'load_delivery_configuration',return_value=self.config):
            self.assertEqual(self.get(FakeRequest())['app_account_token'],USER)

    def test_token_unavailable_without_trust_config(self):
        with mock.patch.dict(os.environ,self.env), \
             mock.patch.object(m,'load_delivery_configuration',side_effect=m.AppleDeliveryError('missing roots')):
            with self.assertRaises(MockHTTPException) as err:
                self.get(FakeRequest())
            self.assertEqual(err.exception.status_code,503)

    async def test_native_jws_routes_to_existing_authority(self):
        expected={'status':'granted','durably_recorded':True}
        with mock.patch.dict(os.environ,self.env), \
             mock.patch.object(m,'load_delivery_configuration',return_value=self.config), \
             mock.patch.object(m,'deliver_apple_signed_transaction',return_value=expected) as writer:
            result=await self.post(FakeRequest())
            self.assertEqual(result,expected)
            kwargs=writer.call_args.kwargs
            self.assertEqual(kwargs['user_id'],USER)
            self.assertEqual(kwargs['payload'],{'signed_transaction':'aaa.bbb.ccc'})
            self.assertTrue(callable(kwargs['connection_factory']))
            self.assertEqual(kwargs['compute_floors']('seeker',0),
                             {'donor':'pilgrim','scroll':'pilgrim','fallback':'pilgrim'})

    async def test_browser_origin_refused_before_body_read(self):
        request=FakeRequest(headers={'content-type':'application/json','origin':'https://attacker.example'})
        with mock.patch.dict(os.environ,self.env):
            with self.assertRaises(MockHTTPException) as err:
                await self.post(request)
            self.assertEqual(err.exception.status_code,403)
            self.assertEqual(request.body_calls,0)

    async def test_non_json_refused(self):
        request=FakeRequest(headers={'content-type':'text/plain'})
        with mock.patch.dict(os.environ,self.env):
            with self.assertRaises(MockHTTPException) as err:
                await self.post(request)
            self.assertEqual(err.exception.status_code,415)
            self.assertEqual(request.body_calls,0)

    async def test_oversized_body_and_bad_length_refused(self):
        with mock.patch.dict(os.environ,self.env):
            for request in (FakeRequest(body=b'x'*105001),
                            FakeRequest(headers={'content-type':'application/json','content-length':'999999'}),
                            FakeRequest(headers={'content-type':'application/json','content-length':'bad'})):
                with self.subTest(length=len(request._body)), self.assertRaises(MockHTTPException) as err:
                    await self.post(request)
                self.assertEqual(err.exception.status_code,413)

    async def test_bad_json_and_extra_client_account_field_refused(self):
        with mock.patch.dict(os.environ,self.env):
            for body,code in [(b'not-json',400),
                              (json.dumps({'signed_transaction':'aaa.bbb.ccc','user_id':OTHER}).encode(),400)]:
                with self.subTest(body=body),self.assertRaises(MockHTTPException) as err:
                    await self.post(FakeRequest(body=body))
                self.assertEqual(err.exception.status_code,code)

    async def test_cryptographic_rejection_is_not_success(self):
        with mock.patch.dict(os.environ,self.env), \
             mock.patch.object(m,'load_delivery_configuration',return_value=self.config), \
             mock.patch.object(m,'deliver_apple_signed_transaction',side_effect=AppleContractError('bad JWS')):
            with self.assertRaises(MockHTTPException) as err:
                await self.post(FakeRequest())
            self.assertEqual(err.exception.status_code,422)

    async def test_verifier_unavailable_is_503_not_invalid_transaction(self):
        with mock.patch.dict(os.environ,self.env), \
             mock.patch.object(m,'load_delivery_configuration',return_value=self.config), \
             mock.patch.object(m,'deliver_apple_signed_transaction',side_effect=m.AppleDeliveryUnavailable('missing sdk')):
            with self.assertRaises(MockHTTPException) as err:
                await self.post(FakeRequest())
            self.assertEqual(err.exception.status_code,503)

    async def test_settlement_refusal_is_not_success(self):
        with mock.patch.dict(os.environ,self.env), \
             mock.patch.object(m,'load_delivery_configuration',return_value=self.config), \
             mock.patch.object(m,'deliver_apple_signed_transaction',side_effect=AppleSettlementError('other rail')):
            with self.assertRaises(MockHTTPException) as err:
                await self.post(FakeRequest())
            self.assertEqual(err.exception.status_code,409)

    async def test_missing_verifier_is_not_success(self):
        with mock.patch.dict(os.environ,self.env), \
             mock.patch.object(m,'load_delivery_configuration',side_effect=m.AppleDeliveryError('missing cert')):
            with self.assertRaises(MockHTTPException) as err:
                await self.post(FakeRequest())
            self.assertEqual(err.exception.status_code,503)

    async def test_exact_native_route_does_not_modify_apple_notification_route(self):
        source=(ROOT/'main.py').read_text()
        self.assertEqual(source.count('@app.post("/billing/apple/server-notifications")'),1)
        self.assertEqual(source.count('@app.post("/billing/apple/verified-transaction")'),1)


if __name__=='__main__':
    unittest.main(verbosity=2)
