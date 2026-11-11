"""A7C5H3J: Apple-signed TEST event receipt, without financial authority.

All provider signatures are simulated here; real SDK negative verification passed
separately in H3H/I. These tests MUST NOT claim positive Apple-signed evidence.
"""
from datetime import datetime, timedelta, timezone
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase, main
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from services.apple_storekit_contract import (
    AppleContractError, AppleNotificationFacts, verify_signed_notification,
)
from services.apple_storekit_delivery import AppleDeliveryConfiguration
import services.apple_storekit_notifications as n

NOW = datetime(2026, 10, 24, 18, 0, tzinfo=timezone.utc)
UID = '22345678-1234-4234-8234-123456789012'
CONF = AppleDeliveryConfiguration('godinc_dev','Sandbox',None,(b'0fakecert',))

def signed_test(**kw):
    d = dict(notification_id=UID, notification_type='TEST', subtype=None,
             signed_date=NOW-timedelta(minutes=1), transaction=None,
             action='ignore', grace_expires_at=None, auto_renew_enabled=None)
    d.update(kw)
    return AppleNotificationFacts(**d)

class Cursor:
    def __init__(self, db): self.db=db; self.latest=''
    def __enter__(self): return self
    def __exit__(self,*args): return False
    def execute(self, sql, params=()):
        self.latest=sql
        self.db.calls.append((sql,params))
        if 'INSERT INTO payment_events' in sql:
            if self.db.fail_insert: raise RuntimeError('simulated unique conflict')
            self.db.inserts+=1
            self.db.insert_parameters=params
    def fetchone(self):
        if 'current_database()' in self.latest: return {'db_name':self.db.name}
        if 'FROM payment_events' in self.latest: return self.db.existing
        if 'INSERT INTO payment_events' in self.latest: return {'id':'receipt'}
        raise AssertionError(self.latest)

class Connection:
    def __init__(self, db): self.db=db; self.autocommit=db.autocommit
    def cursor(self): return Cursor(self.db)
    def commit(self): self.db.commits+=1
    def rollback(self): self.db.rollbacks+=1
    def close(self): self.db.closed+=1

class DB:
    def __init__(self):
        self.name='godinc_dev'; self.autocommit=False; self.fail_insert=False
        self.existing=None;self.inserts=0;self.commits=0;self.rollbacks=0
        self.closed=0; self.connections=0; self.calls=[];self.insert_parameters=None
    def connect(self): self.connections+=1;return Connection(self)

class AppleTestNotificationAcceptance(TestCase):
    def invoke(self, *, db=None, note=None, verifier_error=None):
        db=db or DB()
        note=note or signed_test()
        with patch.object(n,'build_signed_data_verifier',return_value=object()) as build, \
             patch.object(n,'verify_signed_notification',return_value=note,side_effect=verifier_error) as verify, \
             patch.object(n,'settle_verified_apple_transaction') as settle, \
             patch('services.apple_storekit_lifecycle.apply_verified_apple_lifecycle') as lifecycle:
            result=n.process_verified_apple_notification(
                payload={'signedPayload':'aaa.bbb.ccc'},config=CONF,
                connection_factory=db.connect,compute_floors=lambda *a:{},now=NOW)
            settle.assert_not_called()
            lifecycle.assert_not_called()
            build.assert_called_once()
            verify.assert_called_once()
        return result,db

    def test_signed_test_creates_metadata_only_receipt(self):
        result,db=self.invoke()
        self.assertEqual(result,{'ok':True,'notification_type':'TEST',
                                 'notification_receipt':'recorded','entitlement_changed':False})
        self.assertEqual((db.connections,db.inserts,db.commits,db.closed),(1,1,1,1))
        p=db.insert_parameters
        self.assertEqual(p[1],'APPLE_NOTIFICATION_VERIFIED_TEST')
        self.assertEqual(p[3],UID)
        self.assertIs(p[4],False)
        data=json.loads(p[5])
        self.assertEqual(data['notification_type'],'TEST')
        self.assertEqual(data['configured_environment'],'Sandbox')
        self.assertNotIn('signedPayload',str(p))
        self.assertNotIn('account',str(p))
        self.assertFalse(any('INSERT INTO subscriptions' in sql or 'UPDATE users' in sql
                             for sql, _ in db.calls))

    def test_replay_checks_complete_durable_evidence_and_does_not_write(self):
        first,db=self.invoke()
        p=db.insert_parameters
        db.existing=dict(event_type=p[1],processing_status='processed',
                         object_type='notification_test',object_id=UID,user_id=None,
                         subscription_id=None,transaction_id=None,livemode=False,
                         payload_json=json.loads(p[5]),event_created_at=p[2])
        db.calls=[];db.inserts=0;db.commits=0;db.rollbacks=0
        replay,db=self.invoke(db=db)
        self.assertEqual(replay['notification_receipt'],'already_recorded')
        self.assertEqual((db.inserts,db.commits,db.rollbacks),(0,0,1))

    def test_conflicting_replays_never_ack(self):
        _,db=self.invoke()
        p=db.insert_parameters
        basis=dict(event_type=p[1],processing_status='processed',
                  object_type='notification_test',object_id=UID,user_id=None,
                  subscription_id=None,transaction_id=None,livemode=False,
                  payload_json=json.loads(p[5]),event_created_at=p[2])
        for delta in (
            {'event_type':'APPLE_NOTIFICATION_VERIFIED_GRANT'},
            {'processing_status':'failed'},
            {'object_type':'transaction'},
            {'user_id':UID},
            {'subscription_id':UID},
            {'transaction_id':UID},
            {'livemode':True},
            {'payload_json':{}},
            {'event_created_at':NOW},
        ):
            other=DB();other.existing=dict(basis,**delta)
            with self.subTest(delta=delta),self.assertRaises(n.AppleNotificationDeferred):
                self.invoke(db=other)
            self.assertEqual((other.inserts,other.commits,other.rollbacks,other.closed),(0,0,1,1))

    def test_bad_signature_cannot_access_ledger(self):
        db=DB()
        with self.assertRaises(AppleContractError):
            self.invoke(db=db, verifier_error=AppleContractError('signature invalid'))
        self.assertEqual(db.connections,0)

    def test_client_supplied_test_is_not_accepted(self):
        db=DB()
        with patch.object(n,'build_signed_data_verifier') as verifier, \
             self.assertRaises(n.AppleNotificationError):
            n.process_verified_apple_notification(
                payload={'notificationType':'TEST'},config=CONF,
                connection_factory=db.connect,compute_floors=lambda *a:{},now=NOW)
        verifier.assert_not_called()
        self.assertEqual(db.connections,0)

    def test_unrecognized_signed_notification_type_does_not_ack(self):
        from services.apple_storekit_lifecycle import AppleLifecycleError
        db=DB()
        with patch.object(n,'build_signed_data_verifier',return_value=object()), \
             patch.object(n,'verify_signed_notification',return_value=signed_test(notification_type='SOME_FUTURE_EVENT')), \
             patch('services.apple_storekit_lifecycle.apply_verified_apple_lifecycle',side_effect=AppleLifecycleError('unsupported')), \
             self.assertRaises(n.AppleNotificationDeferred):
            n.process_verified_apple_notification(payload={'signedPayload':'aaa.bbb.ccc'},
                config=CONF,connection_factory=db.connect,compute_floors=lambda *a:{},now=NOW)
        self.assertEqual(db.connections,0)

    def test_test_with_unexpected_transaction_or_subtype_refused(self):
        for attrs in ({'transaction':object()}, {'subtype':'XYZ'},
                      {'action':'grant'}, {'grace_expires_at':NOW},
                      {'auto_renew_enabled':False}):
            db=DB()
            with self.subTest(attrs=attrs),self.assertRaises(n.AppleNotificationDeferred):
                self.invoke(db=db,note=signed_test(**attrs))
            self.assertEqual(db.connections,0)

    def test_invalid_ids_and_future_signature_refused(self):
        for attrs in ({'notification_id':'not-a-uuid'},
                      {'notification_id':'abcdefab-1234-4234-8234-123456789abc'.upper()},
                      {'signed_date':NOW+timedelta(days=1)},
                      {'signed_date':NOW.replace(tzinfo=None)}):
            db=DB()
            with self.subTest(attrs=attrs),self.assertRaises(n.AppleNotificationDeferred):
                self.invoke(db=db,note=signed_test(**attrs))
            self.assertEqual(db.connections,0)

    def test_autocommit_and_database_conflict_rollback(self):
        for set_fields in ({'name':'wrong_scratch'}, {'autocommit':True}):
            db=DB()
            for k,v in set_fields.items(): setattr(db,k,v)
            with self.subTest(set_fields=set_fields),self.assertRaises(n.AppleNotificationDeferred):
                self.invoke(db=db)
            self.assertEqual((db.inserts,db.commits,db.rollbacks,db.closed),(0,0,1,1))

    def test_insert_error_rollback_and_retry_not_ack(self):
        db=DB();db.fail_insert=True
        with self.assertRaisesRegex(RuntimeError,'unique conflict'):
            self.invoke(db=db)
        self.assertEqual((db.inserts,db.commits,db.rollbacks,db.closed),(0,0,1,1))

    def test_test_without_nested_transaction_from_verified_outer(self):
        outer=SimpleNamespace(notificationUUID=UID, rawNotificationType='TEST',
                              rawSubtype=None, signedDate=int(NOW.timestamp()*1000),
                              data=None)
        verifier=SimpleNamespace(verify_and_decode_notification=lambda *_: outer)
        facts=verify_signed_notification('aaa.bbb.ccc',verifier=verifier)
        self.assertEqual(facts.notification_type,'TEST')
        self.assertEqual(facts.action,'ignore')
        self.assertIsNone(facts.transaction)

    def test_verified_outer_signature_rejection_fail_closed(self):
        def reject(*_): raise ValueError('untrusted')
        with self.assertRaises(AppleContractError):
            verify_signed_notification('aaa.bbb.ccc',verifier=SimpleNamespace(verify_and_decode_notification=reject))

if __name__=='__main__': main(verbosity=2)
