"""A7C5H3D dormant verified grant notification acceptance. No real providers or DB."""
import ast
import asyncio
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace as O
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from services.apple_storekit_contract import AppleNotificationFacts, AppleTransactionFacts, AppleContractError, PRODUCT_ID
from services.apple_storekit_delivery import AppleDeliveryConfiguration, AppleDeliveryUnavailable
from services.apple_storekit_settlement import AppleSettlementError, AppleSettlementResult
import services.apple_storekit_notifications as n

USER='12345678-1234-4234-8234-123456789012'
UUID='22345678-1234-4234-8234-123456789012'
NOW=datetime.now(timezone.utc)
TX=AppleTransactionFacts('Sandbox', USER,'881000000000001','881000000000002',PRODUCT_ID,
                         NOW-timedelta(days=1),NOW+timedelta(days=28),
                         NOW-timedelta(minutes=3),None,False)
CONF=AppleDeliveryConfiguration('godinc_dev','Sandbox',None,(b'0fakecert',))

def fact(action='grant', typ='DID_RENEW', transaction=TX):
    return AppleNotificationFacts(UUID,typ,None,NOW-timedelta(minutes=1),transaction,action)

class FakeCursor:
    def __init__(self,db): self.db=db; self.latest=''; self.params=()
    def __enter__(self):return self
    def __exit__(self,exc_type,exc,tb):return False
    def execute(self,query,params=()):
        self.latest=query;self.params=params
        self.db.queries.append((query,params))
        if 'INSERT INTO payment_events' in query:
            if self.db.insert_fail: raise RuntimeError('Simulated ledger failure')
            self.db.inserts+=1
    def fetchone(self):
        if 'current_database()' in self.latest:return {'db_name':self.db.database}
        if 'FROM payment_events' in self.latest:return self.db.event
        if 'INSERT INTO payment_events' in self.latest:return {'id':'receipt'}
        raise AssertionError('Unknown fetch: '+self.latest)

class FakeConn:
    def __init__(self,db):self.db=db;self.autocommit=db.autocommit
    def cursor(self):return FakeCursor(self.db)
    def commit(self):self.db.commits+=1
    def rollback(self):self.db.rollbacks+=1
    def close(self):self.db.closed+=1

class DB:
    def __init__(self):
        self.database='godinc_dev';self.autocommit=False;self.event=None
        self.insert_fail=False;self.commits=0;self.rollbacks=0;self.closed=0
        self.inserts=0;self.queries=[];self.connections=0
    def connect(self):self.connections+=1;return FakeConn(self)

def run(db=None,notification=None,settlement=None):
    db=db or DB()
    notification=notification or fact()
    settlement=settlement or AppleSettlementResult('granted','881000000000002',USER,True)
    with patch.object(n,'build_signed_data_verifier',return_value=O()) as build,\
         patch.object(n,'verify_signed_notification',return_value=notification) as verify,\
         patch.object(n,'settle_verified_apple_transaction',return_value=settlement) as grant:
        result=n.process_verified_apple_notification(
            payload={'signedPayload':'aaa.bbb.ccc'}, config=CONF,
            connection_factory=db.connect,compute_floors=lambda *a:{},now=NOW)
        return result,db,build,verify,grant

class ProcessorTests(unittest.TestCase):
    def test_verified_grant_receipt_to_existing_ledger(self):
        result,db,b,v,g=run()
        self.assertEqual(result,{'ok':True,'notification_receipt':'recorded','entitlement_outcome':'granted'})
        self.assertEqual((db.connections,db.inserts,db.commits,db.closed),(1,1,1,1))
        self.assertEqual(g.call_args.kwargs['expected_user_id'], USER)
        self.assertEqual(g.call_args.kwargs['expected_database'],'godinc_dev')
        self.assertEqual(v.call_args.kwargs['verifier'],b.return_value)
        inserts=[x for x in db.queries if 'INSERT INTO payment_events' in x[0]]
        self.assertEqual(len(inserts),1)
        self.assertTrue(inserts[0][1][0].startswith('notification:Sandbox:'))
        self.assertNotIn('aaa.bbb.ccc',str(inserts[0]))
        self.assertEqual(inserts[0][1][3],USER)

    def test_replay_requires_consistent_durable_receipt(self):
        db=DB()
        db.event=dict(event_type='APPLE_NOTIFICATION_VERIFIED_GRANT',
                      processing_status='processed',object_id=TX.transaction_id,
                      user_id=USER,subscription_id=USER,livemode=False)
        result,db,*_=run(db=db,settlement=AppleSettlementResult('already_processed',TX.transaction_id,USER,False))
        self.assertEqual(result['notification_receipt'],'already_recorded')
        self.assertEqual((db.inserts,db.commits,db.rollbacks),(0,0,1))

    def test_conflicting_replay_never_acked(self):
        for change in ({'user_id':'22345678-1234-4234-8234-123456789012'},
                       {'object_id':'different'}, {'processing_status':'failed'},
                       {'subscription_id':'22345678-1234-4234-8234-123456789012'},
                       {'livemode':True}):
            db=DB()
            db.event=dict(event_type='APPLE_NOTIFICATION_VERIFIED_GRANT',
                          processing_status='processed',object_id=TX.transaction_id,
                          user_id=USER,subscription_id=USER,livemode=False,**{})
            db.event.update(change)
            with self.subTest(change=change),self.assertRaises(n.AppleNotificationDeferred),\
                 patch.object(n,'build_signed_data_verifier',return_value=O()),\
                 patch.object(n,'verify_signed_notification',return_value=fact()),\
                 patch.object(n,'settle_verified_apple_transaction',return_value=AppleSettlementResult('already_processed',TX.transaction_id,USER,False)):
                n.process_verified_apple_notification(payload={'signedPayload':'aaa.bbb.ccc'},
                    config=CONF,connection_factory=db.connect,compute_floors=lambda *a:{},now=NOW)
            self.assertEqual((db.inserts,db.commits,db.rollbacks),(0,0,1))

    def test_signed_grant_replay_and_stale_outcomes_are_not_claimed_as_new_access(self):
        for outcome in ('already_processed','stale_ignored'):
            with self.subTest(outcome=outcome):
                result,db,*_=run(settlement=AppleSettlementResult(outcome,TX.transaction_id,USER,False))
                self.assertEqual(result['entitlement_outcome'],outcome)
                self.assertEqual(db.commits,1)

    def test_non_grant_actions_delegate_to_lifecycle_authority(self):
        from services.apple_storekit_lifecycle import AppleLifecycleResult
        import services.apple_storekit_lifecycle as lifecycle
        for action in ('terminate','grace','metadata'):
            db=DB()
            selected=fact(action=action)
            with self.subTest(action=action),patch.object(n,'build_signed_data_verifier',return_value=O()),\
                 patch.object(n,'verify_signed_notification',return_value=selected),\
                 patch.object(n,'settle_verified_apple_transaction') as grant,\
                 patch.object(lifecycle,'apply_verified_apple_lifecycle',return_value=AppleLifecycleResult('expired',USER,UUID,True)) as writer:
                result=n.process_verified_apple_notification(payload={'signedPayload':'aaa.bbb.ccc'},
                    config=CONF,connection_factory=db.connect,compute_floors=lambda *a:{},now=NOW)
            self.assertEqual(result['lifecycle_outcome'],'expired')
            self.assertEqual(db.connections,0)
            grant.assert_not_called()
            self.assertEqual(writer.call_args.kwargs['expected_database'],'godinc_dev')

    def test_unknown_non_grant_action_remains_unacknowledged(self):
        db=DB()
        with self.assertRaises(n.AppleNotificationDeferred),\
             patch.object(n,'build_signed_data_verifier',return_value=O()),\
             patch.object(n,'verify_signed_notification',return_value=fact(action='ignore')):
            n.process_verified_apple_notification(payload={'signedPayload':'aaa.bbb.ccc'},
                config=CONF,connection_factory=db.connect,compute_floors=lambda *a:{},now=NOW)
        self.assertEqual(db.connections,0)

    def test_unverified_outer_jws_cannot_grant(self):
        db=DB()
        with patch.object(n,'build_signed_data_verifier',return_value=O()),\
             patch.object(n,'verify_signed_notification',side_effect=AppleContractError('bad signature')) as verify,\
             patch.object(n,'settle_verified_apple_transaction') as writer,\
             self.assertRaises(AppleContractError):
            n.process_verified_apple_notification(payload={'signedPayload':'aaa.bbb.ccc'},
                config=CONF,connection_factory=db.connect,compute_floors=lambda *a:{},now=NOW)
        writer.assert_not_called()
        self.assertEqual(db.connections,0)

    def test_invalid_payload_cannot_invoke_verifier(self):
        for payload in (None,{},[],{'signedPayload':''},{'signedPayload':4},
                        {'signedPayload':'aaa.bbb.ccc','account':USER},
                        {'signedPayload':'x'*100001}):
            with self.subTest(payload=str(payload)[:30]),\
                 patch.object(n,'build_signed_data_verifier') as verifier,\
                 self.assertRaises(n.AppleNotificationError):
                n.process_verified_apple_notification(payload=payload,config=CONF,
                    connection_factory=lambda:None,compute_floors=lambda *a:None)
            verifier.assert_not_called()

    def test_invalid_runtime_config_refuses_before_verifier(self):
        with patch.object(n,'build_signed_data_verifier') as verifier,\
             self.assertRaises(n.AppleNotificationError):
            n.process_verified_apple_notification(payload={'signedPayload':'aaa.bbb.ccc'},
                config={},connection_factory=lambda:None,compute_floors=lambda *a:None)
        verifier.assert_not_called()

    def test_future_notification_deferred_without_writes(self):
        future=AppleNotificationFacts(UUID,'DID_RENEW',None,NOW+timedelta(hours=2),TX,'grant')
        db=DB()
        with patch.object(n,'build_signed_data_verifier',return_value=O()),\
             patch.object(n,'verify_signed_notification',return_value=future),\
             patch.object(n,'settle_verified_apple_transaction') as writer,\
             self.assertRaises(n.AppleNotificationDeferred):
            n.process_verified_apple_notification(payload={'signedPayload':'aaa.bbb.ccc'},
                config=CONF,connection_factory=db.connect,compute_floors=lambda *a:{},now=NOW)
        writer.assert_not_called();self.assertEqual(db.connections,0)

    def test_wrong_nested_environment_refused(self):
        wrong=AppleNotificationFacts(UUID,'DID_RENEW',None,NOW,TX.__class__(
            'Production',TX.app_account_token,TX.original_transaction_id,TX.transaction_id,
            TX.product_id,TX.purchase_date,TX.expires_date,TX.signed_date,None,False),'grant')
        with patch.object(n,'build_signed_data_verifier',return_value=O()),\
             patch.object(n,'verify_signed_notification',return_value=wrong),\
             patch.object(n,'settle_verified_apple_transaction') as writer,\
             self.assertRaises(n.AppleNotificationError):
            n.process_verified_apple_notification(payload={'signedPayload':'aaa.bbb.ccc'},
                config=CONF,connection_factory=lambda:None,compute_floors=lambda *a:{},now=NOW)
        writer.assert_not_called()

    def test_wrong_database_rejects_post_grant_ack(self):
        db=DB();db.database='godinc_prod'
        with self.assertRaises(n.AppleNotificationDeferred),\
             patch.object(n,'build_signed_data_verifier',return_value=O()),\
             patch.object(n,'verify_signed_notification',return_value=fact()),\
             patch.object(n,'settle_verified_apple_transaction',return_value=AppleSettlementResult('granted',TX.transaction_id,USER,True)):
            n.process_verified_apple_notification(payload={'signedPayload':'aaa.bbb.ccc'},
                config=CONF,connection_factory=db.connect,compute_floors=lambda *a:{},now=NOW)
        self.assertEqual(db.rollbacks,1)

    def test_notification_insert_failure_rolls_back_and_never_acks(self):
        db=DB();db.insert_fail=True
        with self.assertRaises(RuntimeError),\
             patch.object(n,'build_signed_data_verifier',return_value=O()),\
             patch.object(n,'verify_signed_notification',return_value=fact()),\
             patch.object(n,'settle_verified_apple_transaction',return_value=AppleSettlementResult('granted',TX.transaction_id,USER,True)):
            n.process_verified_apple_notification(payload={'signedPayload':'aaa.bbb.ccc'},
                config=CONF,connection_factory=db.connect,compute_floors=lambda *a:{},now=NOW)
        self.assertEqual((db.commits,db.rollbacks),(0,1))

    def test_autocommit_connection_rejected(self):
        db=DB();db.autocommit=True
        with self.assertRaises(n.AppleNotificationDeferred),\
             patch.object(n,'build_signed_data_verifier',return_value=O()),\
             patch.object(n,'verify_signed_notification',return_value=fact()),\
             patch.object(n,'settle_verified_apple_transaction',return_value=AppleSettlementResult('granted',TX.transaction_id,USER,True)):
            n.process_verified_apple_notification(payload={'signedPayload':'aaa.bbb.ccc'},
                config=CONF,connection_factory=db.connect,compute_floors=lambda *a:{},now=NOW)
        self.assertEqual(db.rollbacks,1)

    def test_incomplete_settlement_result_never_acks(self):
        for result in (AppleSettlementResult('granted',TX.transaction_id,None,True),
                       AppleSettlementResult('unknown',TX.transaction_id,USER,True),None):
            db=DB()
            with self.subTest(result=result),self.assertRaises(n.AppleNotificationDeferred),\
                 patch.object(n,'build_signed_data_verifier',return_value=O()),\
                 patch.object(n,'verify_signed_notification',return_value=fact()),\
                 patch.object(n,'settle_verified_apple_transaction',return_value=result):
                n.process_verified_apple_notification(payload={'signedPayload':'aaa.bbb.ccc'},
                    config=CONF,connection_factory=db.connect,compute_floors=lambda *a:{},now=NOW)
            self.assertEqual(db.connections,0)

    def test_module_never_logs_secret_jws_or_creates_ledger(self):
        source=(ROOT/'services/apple_storekit_notifications.py').read_text()
        self.assertNotIn('logger.info(',source)
        self.assertNotIn('INSERT INTO billing_transactions',source)
        self.assertNotIn('UPDATE users',source)
        self.assertNotIn('UPDATE subscriptions',source)

class MockHTTPException(Exception):
    def __init__(self,status_code,detail):super().__init__(detail);self.status_code=status_code
class FakeApp:
    def post(self,path):
        self.path=path
        def register(fn):return fn
        return register
class FakeLogger:
    def warning(self,*args):pass
class FakeRequest:
    def __init__(self,body=None,headers=None):
        self.headers=headers if headers is not None else {'content-type':'application/json'}
        self.data=body if body is not None else json.dumps({'signedPayload':'aaa.bbb.ccc'}).encode()
        self.called=0
    async def body(self):self.called+=1;return self.data

class RouteTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        tree=ast.parse((ROOT/'main.py').read_text())
        nodes=[x for x in tree.body if isinstance(x,ast.AsyncFunctionDef) and x.name=='apple_server_notifications']
        self.assertEqual(len(nodes),1)
        self.assertEqual(len(nodes[0].decorator_list),1)
        ctx=dict(app=FakeApp(), Request=object, HTTPException=MockHTTPException,
                 os=os,json=json,logger=FakeLogger(),get_db_connection=lambda:None,
                 compute_donor_floor_plan=lambda _:None,compute_scroll_floor_plan=lambda _:None,
                 compute_fallback_floor_plan=lambda *a:'pilgrim')
        exec(compile(ast.Module(body=nodes,type_ignores=[]),'main.py Apple notifications','exec'),ctx)
        self.handle=ctx['apple_server_notifications'];self.app=ctx['app']

    async def test_one_existing_route_and_disabled_before_body(self):
        self.assertEqual(self.app.path,'/billing/apple/server-notifications')
        req=FakeRequest()
        with patch.dict(os.environ,{'GODINC_APPLE_NOTIFICATIONS_MODE':''}):
            with self.assertRaises(MockHTTPException) as err:await self.handle(req)
        self.assertEqual(err.exception.status_code,404)
        self.assertEqual(req.called,0)

    async def test_enabled_requires_delivery_trust_mode(self):
        req=FakeRequest()
        with patch.dict(os.environ,{'GODINC_APPLE_NOTIFICATIONS_MODE':'verified_lifecycle',
                                    'GODINC_APPLE_DELIVERY_MODE':''}):
            with self.assertRaises(MockHTTPException) as err:await self.handle(req)
        self.assertEqual(err.exception.status_code,503);self.assertEqual(req.called,0)

    async def test_malformed_json_and_extra_fields_not_acked(self):
        env=dict(GODINC_APPLE_NOTIFICATIONS_MODE='verified_lifecycle',
                 GODINC_APPLE_DELIVERY_MODE='verified_settlement')
        for body in (b'{bad', b'[]', b'{"notificationType":"DID_RENEW"}',
                     json.dumps({'signedPayload':'aaa.bbb.ccc','account':USER}).encode()):
            with self.subTest(body=body),patch.dict(os.environ,env):
                with self.assertRaises(MockHTTPException) as err:await self.handle(FakeRequest(body=body))
                self.assertEqual(err.exception.status_code,400)

    async def test_body_length_and_content_type_limited(self):
        env=dict(GODINC_APPLE_NOTIFICATIONS_MODE='verified_lifecycle',
                 GODINC_APPLE_DELIVERY_MODE='verified_settlement')
        reqs=(FakeRequest(headers={'content-type':'text/plain'}),
              FakeRequest(headers={'content-type':'application/json','content-length':'200000'}),
              FakeRequest(body=b'x'*105001),FakeRequest(body=b''))
        for req in reqs:
            with self.subTest(body=len(req.data)),patch.dict(os.environ,env):
                with self.assertRaises(MockHTTPException) as err:await self.handle(req)
                self.assertIn(err.exception.status_code,(413,415))

    async def test_cryptographic_refusal_is_never_success(self):
        env=dict(GODINC_APPLE_NOTIFICATIONS_MODE='verified_lifecycle',
                 GODINC_APPLE_DELIVERY_MODE='verified_settlement')
        import services.apple_storekit_delivery as delivery
        with patch.dict(os.environ,env),\
             patch.object(delivery,'load_delivery_configuration',return_value=CONF),\
             patch.object(n,'process_verified_apple_notification',side_effect=AppleContractError('bad')):
            with self.assertRaises(MockHTTPException) as err:await self.handle(FakeRequest())
            self.assertEqual(err.exception.status_code,422)

    async def test_full_grant_route_passes_existing_billing_authority(self):
        env=dict(GODINC_APPLE_NOTIFICATIONS_MODE='verified_lifecycle',
                 GODINC_APPLE_DELIVERY_MODE='verified_settlement')
        import services.apple_storekit_delivery as delivery
        with patch.dict(os.environ,env),\
             patch.object(delivery,'load_delivery_configuration',return_value=CONF),\
             patch.object(n,'process_verified_apple_notification',return_value={'ok':True}) as process:
            result=await self.handle(FakeRequest())
        self.assertEqual(result,{'ok':True})
        self.assertEqual(process.call_args.kwargs['payload'],{'signedPayload':'aaa.bbb.ccc'})
        self.assertTrue(callable(process.call_args.kwargs['connection_factory']))

    async def test_deferred_unhandled_lifecycle_event_yields_503(self):
        env=dict(GODINC_APPLE_NOTIFICATIONS_MODE='verified_lifecycle',
                 GODINC_APPLE_DELIVERY_MODE='verified_settlement')
        import services.apple_storekit_delivery as delivery
        with patch.dict(os.environ,env),\
             patch.object(delivery,'load_delivery_configuration',return_value=CONF),\
             patch.object(n,'process_verified_apple_notification',side_effect=n.AppleNotificationDeferred('refund')):
            with self.assertRaises(MockHTTPException) as err:await self.handle(FakeRequest())
        self.assertEqual(err.exception.status_code,503)

    async def test_legacy_unverified_ack_removed(self):
        source=(ROOT/'main.py').read_text()
        self.assertNotIn('APPLE_SERVER_NOTIFICATION_RECEIVED',source)
        self.assertEqual(source.count('@app.post("/billing/apple/server-notifications")'),1)

if __name__=='__main__': unittest.main(verbosity=2)
