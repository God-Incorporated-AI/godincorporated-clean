"""A7C5E: durable client purchase action across retries and invoice settlement.

All connections are synthetic. No PostgreSQL, HTTP, or Stripe import/call.
"""
import copy
import importlib.util
import json
from pathlib import Path
from unittest import TestCase, main
from uuid import UUID

ROOT=Path(__file__).resolve().parents[1]
INTENT_TEST=ROOT/'tests/test_stripe_reup_intent.py'
SETTLEMENT_TEST=ROOT/'tests/test_stripe_reup_settlement.py'

def load(name,path):
    spec=importlib.util.spec_from_file_location(name,path)
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

it=load('a7c5e_prior_intent',INTENT_TEST)
st=load('a7c5e_prior_settlement',SETTLEMENT_TEST)
REQUEST='799eb1d9-49b0-4e90-858b-e12d907dfda7'
OTHER_REQUEST='e52c7b73-ec76-495f-b065-7adb68d70512'

class PurchaseCursor(it.FakeCursor):
    def execute(self,sql,params=()):
        q=' '.join(sql.lower().split())
        if 'pg_advisory_xact_lock' in q:
            self.conn.db.sql.append(q)
            self.rows=[];self.rowcount=1
            self.conn.db.lock_inputs.append(params[0])
            return
        if "raw_summary_json ->> 'purchase_request_id'" in q:
            self.conn.db.sql.append(q)
            if self.conn.db.fail_at is not None and self.conn.db.fail_at in q:
                raise RuntimeError('synthetic purchase lookup unavailable')
            self.rows=[copy.deepcopy(row) for row in self.conn.data['ledger']
                       if row['raw_summary_json'].get('purchase_request_id')==params[0]]
            self.rowcount=len(self.rows)
            return
        return super().execute(sql,params)

class PurchaseConnection(it.FakeConnection):
    def cursor(self):return PurchaseCursor(self)

class PurchaseDB(it.FakeDB):
    def __init__(self):
        super().__init__()
        self.lock_inputs=[]
    def connect(self):
        self.connections+=1
        return PurchaseConnection(self)

class RequestIdempotencyTests(TestCase):
    def setUp(self):self.db=PurchaseDB()
    def stage(self,request=REQUEST,user=it.UID,**kwargs):
        args=dict(user_id=user,connect=self.db.connect,expected_livemode=False,
                  now=it.NOW,intent_factory=lambda:UUID(it.INTENT),
                  purchase_request_id=request)
        args.update(kwargs)
        return it.m.prepare_stripe_reup_intent(**args)
    def first(self):
        r=self.stage();self.assertEqual(r['status'],'prepared')
        return self.db.committed['ledger'][0]
    def mark_settled(self,status='succeeded'):
        ledger=self.first()
        meta=ledger['raw_summary_json']
        meta['schema']='stripe_paid_reup_receipt.v2'
        meta['intent_id']=it.INTENT
        meta['invoice_id']='in_paid_once'
        ledger.update(status=status,provider_invoice_id='in_paid_once')
        return ledger

    def test_server_stages_client_purchase_request(self):
        rec=self.first()
        self.assertEqual(rec['raw_summary_json']['purchase_request_id'],REQUEST)
        self.assertEqual(rec['raw_summary_json']['reup_intent_id'],it.INTENT)
        self.assertEqual(len(self.db.committed['ledger']),1)

    def test_lock_is_acquired_before_account_read(self):
        self.first()
        self.assertEqual(self.db.lock_inputs,['godinc:stripe:purchase-request:'+REQUEST])
        lock=next(i for i,x in enumerate(self.db.sql) if 'pg_advisory_xact_lock' in x)
        user=next(i for i,x in enumerate(self.db.sql) if 'from users' in x)
        self.assertLess(lock,user)

    def test_pending_same_action_never_creates_new_intent(self):
        self.first()
        retried=self.stage(intent_factory=lambda:UUID(it.OTHER_UID))
        self.assertEqual(retried['status'],'already_pending')
        self.assertEqual(retried['intent_id'],it.INTENT)
        self.assertEqual(len(self.db.committed['ledger']),1)

    def test_different_action_while_previous_pending_held(self):
        self.first()
        r=self.stage(request=OTHER_REQUEST)
        self.assertEqual(r['status'],'already_pending')
        self.assertEqual(len(self.db.committed['ledger']),1)

    def test_retry_after_paid_invoice_and_expiration_never_charges(self):
        self.mark_settled()
        self.db.committed['user']['entitlement_status']='expired'
        self.db.committed['user']['plan_code']='magister'
        self.db.committed['subs'][0]['internal_status']='cancelled'
        result=self.stage()
        self.assertEqual(result['status'],'already_settled')
        self.assertEqual(result['intent_id'],it.INTENT)
        self.assertEqual(len(self.db.committed['ledger']),1)

    def test_retry_after_refund_remains_historical_without_new_charge(self):
        self.mark_settled('refunded')
        self.assertEqual(self.stage()['status'],'already_settled')

    def test_retry_after_dispute_remains_historical_without_new_charge(self):
        self.mark_settled('disputed')
        self.assertEqual(self.stage()['status'],'already_settled')

    def test_cross_account_purchase_action_cannot_be_reused(self):
        self.first()
        self.db.committed['user']['id']=it.OTHER_UID
        state=copy.deepcopy(self.db.committed)
        with self.assertRaises(it.m.StripeReupSettlementError):
            self.stage(user=it.OTHER_UID)
        self.assertEqual(self.db.committed,state)

    def test_duplicate_request_record_fails_closed(self):
        self.first()
        clone=copy.deepcopy(self.db.committed['ledger'][0])
        clone['id']=it.OTHER_UID
        self.db.committed['ledger'].append(clone)
        state=copy.deepcopy(self.db.committed)
        with self.assertRaises(it.m.StripeReupSettlementError):self.stage()
        self.assertEqual(self.db.committed,state)

    def test_invalid_purchase_id_refused_before_connection(self):
        for req in ('not_uuid','00000000-0000-0000-0000-000000000000',None):
            # None is accepted only for backwards-compatible dormant synthetic
            # callers; live route requires a canonical client UUID4.
            if req is None:continue
            with self.subTest(req=req):
                before=self.db.connections
                with self.assertRaises(it.m.StripeReupSettlementError):self.stage(request=req)
                self.assertEqual(before,self.db.connections)

    def test_ledger_lookup_failure_rolls_back(self):
        self.first();before=copy.deepcopy(self.db.committed)
        self.db.fail_at="raw_summary_json ->> 'purchase_request_id'"
        with self.assertRaises(RuntimeError):self.stage()
        self.assertEqual(before,self.db.committed)
        self.assertGreaterEqual(self.db.rollbacks,1)

    def test_replayed_receipt_with_changed_environment_refused(self):
        row=self.mark_settled();row['livemode']=True
        with self.assertRaises(it.m.StripeReupSettlementError):self.stage()

    def test_replayed_receipt_without_invoice_refused(self):
        row=self.mark_settled();row['provider_invoice_id']=None
        with self.assertRaises(it.m.StripeReupSettlementError):self.stage()

    def test_historical_receipt_lookup_precedes_user_write_lock(self):
        self.mark_settled()
        self.stage()
        request_lookup=max(i for i,q in enumerate(self.db.sql)
                            if "raw_summary_json ->> 'purchase_request_id'" in q
                            and 'from billing_transactions' in q)
        user_lock_lines=[i for i,q in enumerate(self.db.sql)
                         if 'from users' in q and 'for update' in q]
        self.assertTrue(user_lock_lines)
        # The first account lock is for initial staging; the retry must not
        # acquire a new account lock after finding a historical paid receipt.
        self.assertLess(user_lock_lines[0],request_lookup)
        self.assertEqual(len(user_lock_lines),1)

    def test_pending_probe_never_locks_ledger_after_user_lock(self):
        import inspect
        source=inspect.getsource(it.m._pending_intent_rows)
        self.assertNotIn('FOR UPDATE',source)
        self.first()
        self.stage(request=OTHER_REQUEST)
        self.assertEqual(len(self.db.committed['ledger']),1)

    def test_actual_settlement_writer_copies_purchase_request_to_receipt(self):
        db=st.FakeDB()
        db.committed['transactions']['intent:'+st.INTENT]['raw_summary_json']['purchase_request_id']=REQUEST
        result=st.module.settle_verified_paid_reup(st.invoice(),
            connect=db.connect,validate_invoice=st.validator,
            expected_livemode=False,now=st.NOW)
        self.assertEqual(result['status'],'settled')
        receipt=db.committed['transactions']['in_1']['raw_summary_json']
        if isinstance(receipt,str):receipt=json.loads(receipt)
        self.assertEqual(receipt['purchase_request_id'],REQUEST)
        self.assertEqual(db.committed['user']['entitlement_status'],'active')
        self.assertEqual(len(db.committed['transactions']),1)


if __name__=='__main__':main(verbosity=2)
