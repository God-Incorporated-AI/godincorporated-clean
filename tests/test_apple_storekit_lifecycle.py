"""Local zero-provider Apple lifecycle acceptance with simulated billing cursors.

No production DB connection. Real PostgreSQL constraints/concurrency remain a later gate.
"""
from datetime import datetime, timedelta, timezone
from uuid import UUID
from dataclasses import replace
from pathlib import Path
import ast
import sys
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from services.apple_storekit_contract import AppleNotificationFacts, AppleTransactionFacts, PRODUCT_ID
import services.apple_storekit_lifecycle as life

NOW=datetime(2026,10,24,14,0,tzinfo=timezone.utc)
USER='12345678-1234-4234-8234-123456789012'
OTHER='12345678-1234-4234-8234-333333333333'
SUB='22345678-1234-4234-8234-123456789012'
NID='32345678-1234-4234-8234-123456789012'
T=AppleTransactionFacts('Sandbox',USER,'800000000000001','800000000000002',PRODUCT_ID,
                       NOW-timedelta(days=30),NOW-timedelta(hours=1),NOW-timedelta(days=20),None,False)

def notification(action='terminate',typ='EXPIRED',subtype='VOLUNTARY',tx=T,**kwargs):
    return AppleNotificationFacts(NID,typ,subtype,NOW-timedelta(minutes=5),tx,action,**kwargs)

class FakeDB:
    def __init__(self):
        self.db='godinc_dev';self.autocommit=False
        self.user=dict(id=USER,email_verified=True,is_banned=False,highest_paid_plan_ever='seeker',
                       scroll_count=0,plan_code='seeker',entitlement_status='active',subscription_expires_at=T.expires_date)
        self.chain=dict(id=SUB,user_id=USER,provider='apple',provider_subscription_id=T.original_transaction_id,
                       plan_code='seeker',support_mode='monthly_recurring',provider_price_id=PRODUCT_ID,
                       livemode=False,provider_status='active',internal_status='active',
                       current_period_start=T.purchase_date,current_period_end=T.expires_date,
                       grace_period_ends_at=None, subscription_metadata_json={'schema':'apple_verified_entitlement_receipt.v1','transaction_id':T.transaction_id},auto_renews=True)
        self.others=[];self.event=None;self.last_event={'event_created_at':T.signed_date, 'event_type':'APPLE_VERIFIED_PURCHASE'}
        self.missing_indexes=False;self.insert_fail=False;self.users_update_fail=False
        self.actions=[];self.commits=0;self.rollbacks=0;self.closed=0;self.connections=0
    def connection(self):self.connections+=1;return FakeConnection(self)

class FakeConnection:
    def __init__(self,db):self.db=db;self.autocommit=db.autocommit
    def cursor(self):return FakeCursor(self.db)
    def commit(self):self.db.commits+=1
    def rollback(self):self.db.rollbacks+=1
    def close(self):self.db.closed+=1

class FakeCursor:
    def __init__(self,db):self.db=db;self.query=None;self.params=();self.rowcount=0
    def __enter__(self):return self
    def __exit__(self,*_):return False
    def execute(self,sql,params=()):
        self.query=sql;self.params=params;self.db.actions.append((sql,params));self.rowcount=0
        if 'INSERT INTO payment_events' in sql:
            if self.db.insert_fail:raise RuntimeError('Simulated durable event failure')
            self.rowcount=1
        if 'UPDATE users' in sql:
            self.rowcount=0 if self.db.users_update_fail else 1
        if 'UPDATE subscriptions' in sql:self.rowcount=1
    def fetchone(self):
        sql=self.query
        if 'current_database()' in sql:return {'db_name':self.db.db}
        if 'FROM users' in sql:return self.db.user
        if 'FROM payment_events' in sql and 'provider_event_id' in sql:return self.db.event
        if 'FROM payment_events' in sql and 'ORDER BY event_created_at' in sql:return self.db.last_event
        if 'INSERT INTO payment_events' in sql:return {'id':NID}
        raise AssertionError('Unrecognized singleton query: '+sql)
    def fetchall(self):
        if 'pg_catalog.pg_index' in self.query:return [] if self.db.missing_indexes else [{'name':'a'},{'name':'b'}]
        if 'FROM subscriptions' in self.query:return [self.db.chain,*self.db.others]
        raise AssertionError('Unrecognized list query: '+self.query)

FLOORS=lambda h,s:{'donor':'pilgrim','scroll':None,'fallback':'pilgrim'}
def run(db=None,n=None):
    db=db or FakeDB()
    n=n or notification()
    r=life.apply_verified_apple_lifecycle(notification=n,environment='Sandbox',
            connection_factory=db.connection,compute_floors=FLOORS,expected_database='godinc_dev',now=NOW)
    return db,r

def sqls(db):return '\n'.join(a[0] for a in db.actions)

class PolicyTests(unittest.TestCase):
    def test_active_rail_requires_future_paid_period_not_grace(self):
        source=(ROOT/'main.py').read_text()
        tree=ast.parse(source)
        reader=[node for node in tree.body if isinstance(node,ast.FunctionDef)
                and node.name=='get_active_paid_rail_for_user']
        self.assertEqual(len(reader),1)
        sql='\n'.join(x.value for x in ast.walk(reader[0])
                      if isinstance(x,ast.Constant) and isinstance(x.value,str))
        self.assertIn("COALESCE(provider_status, '') IN ('active', 'trialing')",sql)
        self.assertIn("COALESCE(internal_status, '') = 'active'",sql)
        self.assertIn('current_period_end IS NOT NULL',sql)
        self.assertIn('current_period_end > NOW()',sql)
        self.assertIn('ended_at IS NULL',sql)
        self.assertNotIn('current_period_end IS NULL OR',sql)
        self.assertNotIn("COALESCE(provider_status, '') = 'grace'",sql)
        self.assertNotIn('grace_period_ends_at >= NOW()',sql)

    def test_notification_route_mode_is_lifecycle_only_and_dormant(self):
        tree=ast.parse((ROOT/'main.py').read_text())
        nodes=[x for x in tree.body if isinstance(x,ast.AsyncFunctionDef)
               and x.name=='apple_server_notifications']
        self.assertEqual(len(nodes),1)
        src=ast.get_source_segment((ROOT/'main.py').read_text(),nodes[0])
        self.assertIn('GODINC_APPLE_NOTIFICATIONS_MODE',src)
        self.assertIn('"verified_lifecycle"',src)
        self.assertNotIn('"verified_grant_test_only"',src)

    def test_expired_downgrades_with_existing_floor_and_no_money_record(self):
        db,r=run()
        self.assertEqual((r.outcome,r.changed_entitlement),('expired',True))
        self.assertEqual((db.commits,db.rollbacks,db.closed),(1,0,1))
        self.assertIn("entitlement_status='expired'",sqls(db))
        self.assertIn('fallback_floor_plan_code',sqls(db))
        self.assertNotIn('INSERT INTO billing_transactions',sqls(db))
        self.assertNotIn('gross_amount_cents',sqls(db))

    def test_signed_refund_ends_active_period_at_revocation_date(self):
        tx=replace(T, expires_date=NOW+timedelta(days=20),revoked_at=NOW-timedelta(minutes=40))
        db=FakeDB();db.chain['current_period_end']=tx.expires_date
        db,r=run(db,notification('terminate','REFUND',None,tx))
        self.assertEqual(r.outcome,'revoked')
        updates=[a for a in db.actions if 'UPDATE users' in a[0]]
        self.assertEqual(updates[0][1][0],tx.revoked_at)

    def test_revocation_requires_signed_revocation_date(self):
        db=FakeDB()
        with self.assertRaises(life.AppleLifecycleError):run(db,notification('terminate','REFUND',None))
        self.assertEqual(db.connections,0)

    def test_unverified_dataclasses_and_invalid_actions_fail_before_connection(self):
        for n in (object(),notification(action='ignore',typ='TEST',subtype=None),
                  notification(action='grant',typ='DID_RENEW',subtype=None)):
            db=FakeDB()
            with self.subTest(action=getattr(n,'action',None)),self.assertRaises(life.AppleLifecycleError):run(db,n)
            self.assertEqual(db.connections,0)

    def test_unknown_expired_subtype_is_not_accepted(self):
        db=FakeDB()
        with self.assertRaises(life.AppleLifecycleError):run(db,notification(subtype='UNKNOWN'))
        self.assertEqual(db.connections,0)

    def test_wrong_account_or_environment_fails_before_connection(self):
        for tx in (replace(T,app_account_token='invalid'),replace(T,environment='Production')):
            db=FakeDB()
            with self.assertRaises(life.AppleLifecycleError):run(db,notification(tx=tx))
            self.assertEqual(db.connections,0)

    def test_renewal_status_disable_preserves_paid_access(self):
        n=notification('metadata','DID_CHANGE_RENEWAL_STATUS','AUTO_RENEW_DISABLED',
                       auto_renew_enabled=False)
        db,r=run(n=n)
        self.assertEqual(r.outcome,'auto_renew_disabled')
        self.assertNotIn("entitlement_status='expired'",sqls(db))
        self.assertNotIn('subscription_expires_at=',sqls(db))
        self.assertTrue(any('UPDATE users SET cancel_at_period_end=' in q for q,_ in db.actions))

    def test_enable_after_cancellation_resets_both_flags(self):
        n=notification('metadata','DID_CHANGE_RENEWAL_STATUS','AUTO_RENEW_ENABLED',
                       auto_renew_enabled=True)
        db,r=run(n=n)
        self.assertEqual(r.outcome,'auto_renew_enabled')
        params=[p for q,p in db.actions if 'UPDATE subscriptions' in q][0]
        self.assertEqual(params[:3],(True,False,False))

    def test_renewal_status_contradiction_fails_closed(self):
        n=notification('metadata','DID_CHANGE_RENEWAL_STATUS','AUTO_RENEW_ENABLED',auto_renew_enabled=False)
        db=FakeDB()
        with self.assertRaises(life.AppleLifecycleError):run(db,n)
        self.assertEqual(db.connections,0)

    def test_unpaid_grace_is_refused_before_database_connection(self):
        n=notification('grace','DID_FAIL_TO_RENEW','GRACE_PERIOD',
                       grace_expires_at=NOW+timedelta(days=2))
        db=FakeDB()
        with self.assertRaises(life.AppleLifecycleError):run(db,n)
        self.assertEqual(db.connections,0)
        self.assertEqual(db.commits,0)
        self.assertFalse(db.actions)

    def test_grace_after_its_end_fails_closed(self):
        n=notification('grace','DID_FAIL_TO_RENEW','GRACE_PERIOD',
                       grace_expires_at=NOW-timedelta(minutes=1))
        db=FakeDB()
        with self.assertRaises(life.AppleLifecycleError):run(db,n)
        self.assertEqual(db.commits,0)

    def test_grace_expiration_requires_recorded_grace(self):
        n=notification('terminate','GRACE_PERIOD_EXPIRED',None)
        db=FakeDB()
        with self.assertRaises(life.AppleLifecycleError):run(db,n)
        self.assertEqual(db.commits,0)

    def test_historical_grace_expiry_cannot_extend_paid_period(self):
        db=FakeDB();db.chain['grace_period_ends_at']=NOW+timedelta(days=2)
        n=notification('terminate','GRACE_PERIOD_EXPIRED',None)
        db,r=run(db,n)
        if T.expires_date > NOW:
            self.assertEqual((r.outcome,r.changed_entitlement),('future_end_recorded',False))
            self.assertNotIn('UPDATE users',sqls(db))
        else:
            self.assertEqual(r.outcome,'expired')
            self.assertEqual([p for q,p in db.actions if 'UPDATE users SET' in q][0][0],T.expires_date)
            self.assertIn('grace_period_ends_at=NULL',sqls(db))

    def test_future_expiry_never_prematurely_revokes(self):
        tx=replace(T,expires_date=NOW+timedelta(days=3))
        db=FakeDB();db.chain['current_period_end']=tx.expires_date
        db,r=run(db,notification('terminate','EXPIRED','VOLUNTARY',tx))
        self.assertEqual((r.outcome,r.changed_entitlement),('future_end_recorded',False))
        self.assertNotIn('UPDATE users',sqls(db))

    def test_billing_retry_preserves_unexpired_paid_time(self):
        tx=replace(T,expires_date=NOW+timedelta(minutes=10))
        db=FakeDB();db.chain['current_period_end']=tx.expires_date
        db,r=run(db,notification('terminate','DID_FAIL_TO_RENEW',None,tx))
        self.assertEqual(r.outcome,'future_end_recorded')
        self.assertIn('last_failed_payment_at',sqls(db))
        self.assertNotIn('UPDATE users',sqls(db))

    def test_unknown_chain_no_new_subscription_or_account(self):
        db=FakeDB();db.chain['provider_subscription_id']='another'
        with self.assertRaises(life.AppleLifecycleError):run(db)
        self.assertNotIn('INSERT INTO subscriptions',sqls(db))
        self.assertEqual(db.commits,0)

    def test_chain_ownership_must_match_signed_account(self):
        db=FakeDB();db.chain['user_id']=OTHER
        with self.assertRaises(life.AppleLifecycleError):run(db)
        self.assertEqual(db.commits,0)

    def test_other_active_paid_rail_never_revoked(self):
        db=FakeDB();db.others=[dict(provider='stripe',internal_status='active',provider_status='active',
                                   current_period_end=NOW+timedelta(days=5))]
        with self.assertRaises(life.AppleLifecycleError):run(db)
        self.assertEqual(db.commits,0)

    def test_older_signed_notification_cannot_overwrite_newer_state(self):
        db=FakeDB();db.last_event['event_created_at']=NOW
        db,r=run(db)
        self.assertEqual(r.outcome,'stale_ignored')
        self.assertNotIn('UPDATE users',sqls(db))
        self.assertNotIn('UPDATE subscriptions',sqls(db))

    def test_old_period_refund_cannot_revoke_new_renewal(self):
        db=FakeDB();db.chain['subscription_metadata_json']['transaction_id']='newer'
        db,r=run(db)
        self.assertEqual(r.outcome,'stale_ignored')
        self.assertNotIn('UPDATE users',sqls(db))

    def test_replay_is_read_only(self):
        db=FakeDB();db.event={'event_type':'APPLE_NOTIFICATION_LIFECYCLE','processing_status':'processed',
                'object_id':T.transaction_id,'user_id':USER,'subscription_id':SUB,'livemode':False,
                'payload_json':{'schema':'apple_verified_lifecycle_receipt.v1',
                'notification_type':'EXPIRED','subtype':'VOLUNTARY','action':'terminate'}}
        db,r=run(db)
        self.assertEqual(r.outcome,'already_processed')
        self.assertEqual((db.commits,db.rollbacks),(0,1))
        self.assertNotIn('UPDATE users',sqls(db))

    def test_conflicting_replay_fails_closed(self):
        db=FakeDB();db.event={'event_type':'APPLE_NOTIFICATION_LIFECYCLE','processing_status':'processed',
                'object_id':'OTHER','user_id':USER,'subscription_id':SUB,'livemode':False,
                'payload_json':{'schema':'apple_verified_lifecycle_receipt.v1',
                'notification_type':'EXPIRED','subtype':'VOLUNTARY','action':'terminate'}}
        with self.assertRaises(life.AppleLifecycleError):run(db)
        self.assertEqual(db.commits,0)

    def test_postgres_unique_failure_rolls_back_no_ack(self):
        db=FakeDB();db.insert_fail=True
        with self.assertRaises(RuntimeError):run(db)
        self.assertEqual((db.commits,db.rollbacks),(0,1))

    def test_partial_entitlement_update_rolls_back(self):
        db=FakeDB();db.users_update_fail=True
        with self.assertRaises(life.AppleLifecycleError):run(db)
        self.assertEqual((db.commits,db.rollbacks),(0,1))

    def test_wrong_db_and_autocommit_fail_before_write(self):
        for attr,value in (('db','godinc_prod'),('autocommit',True),('missing_indexes',True)):
            db=FakeDB();setattr(db,attr,value)
            with self.subTest(attr=attr),self.assertRaises(life.AppleLifecycleError):run(db)
            self.assertEqual(db.commits,0)

    def test_all_connections_closed(self):
        db,r=run()
        self.assertEqual((db.connections,db.closed),(1,1))

    def test_receipt_contains_metadata_only_not_jws_or_dollars(self):
        db,r=run()
        query,params=[a for a in db.actions if 'INSERT INTO payment_events' in a[0]][0]
        self.assertIn("'apple'",query)
        self.assertEqual(len(params),9)
        self.assertIn('apple_verified_lifecycle_receipt.v1',str(params))
        self.assertNotIn('signedPayload',str(params))
        self.assertNotIn('gross',str(params))

if __name__=='__main__':unittest.main(verbosity=2)
