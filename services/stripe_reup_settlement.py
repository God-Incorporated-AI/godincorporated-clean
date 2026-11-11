"""Dormant atomic Stripe same-tier re-up settlement using existing financial tables.

No provider calls, no new entitlement authority. The caller must pass a
Stripe-signature-verified event and the accepted paid-invoice validator.
A separately authenticated, durable purchase-intent registration MUST create
one pending billing_transactions row before the provider purchase is sent.
No such registration or live caller is installed by this gate.
"""

import datetime as _dt
import json as _json
from uuid import UUID as _UUID


class StripeReupSettlementError(ValueError):
    """Fail closed on inconsistent payment, account or replay evidence."""


def _require(ok, message):
    if not ok:
        raise StripeReupSettlementError(message)


def _id(value):
    return value.get('id') if isinstance(value, dict) else value


def _invoice_binding(invoice):
    parent = invoice.get('parent') or {}
    _require(isinstance(parent, dict), 'Malformed invoice parent')
    details = parent.get('subscription_details') or invoice.get('subscription_details') or {}
    _require(isinstance(details, dict), 'Malformed invoice subscription details')
    upper, lower = _id(invoice.get('subscription')), _id(details.get('subscription'))
    _require(not (upper and lower and upper != lower), 'Contradictory subscription identities')
    sub = upper or lower
    _require(isinstance(sub, str) and sub.startswith('sub_'), 'Invoice subscription missing')
    metadata = details.get('metadata') or {}
    _require(isinstance(metadata, dict) and metadata.get('change_source') == 'phase8_paid_reup',
             'Missing explicit re-up marker')
    intent_id = metadata.get('reup_intent_id')
    try:
        parsed = _UUID(str(intent_id))
    except (TypeError, ValueError, AttributeError) as exc:
        raise StripeReupSettlementError('Purchase-intent UUID missing') from exc
    _require(parsed.version == 4 and str(parsed) == intent_id, 'Purchase intent must be canonical UUIDv4')
    return sub, intent_id


def _as_summary(raw):
    if isinstance(raw, str):
        raw = _json.loads(raw)
    _require(isinstance(raw, dict), 'Invalid billing receipt JSON')
    return raw


def _payment_event(cur, *, event, invoice_id, user_id, subscription_row_id, livemode,
                   receipt_id, helper):
    """Insert or complete an existing Stripe event within the caller's transaction."""
    event_id = event['id']
    event_type = event.get('type')
    _require(event_type in {'invoice.paid', 'invoice.payment_failed'},
             'Unsupported Stripe early-re-up event type')
    handler_version = ('a7c3_paid_reup_v2' if event_type == 'invoice.paid'
                       else 'a7c5c_failed_reup_v1')
    created = _dt.datetime.fromtimestamp(event['created'], _dt.timezone.utc)
    summary = _json.dumps({
        'schema': ('stripe_paid_reup_event.v2' if event_type == 'invoice.paid'
                   else 'stripe_failed_reup_event.v1'),
        'event_id': event_id,
        'invoice_id': invoice_id,
        'subscription_row_id': str(subscription_row_id),
    }, sort_keys=True)
    cur.execute(f'''
        INSERT INTO payment_events (
            provider, provider_event_id, stripe_event_id, event_type,
            event_created_at, object_type, object_id, user_id,
            subscription_id, livemode, api_version, payload_json,
            processing_status, handler_version
        ) VALUES (
            'stripe', %s, %s, '{event_type}',
            %s, 'invoice', %s, %s::uuid,
            %s::uuid, %s, %s, %s::jsonb,
            'received', '{handler_version}'
        ) ON CONFLICT (provider, provider_event_id) DO NOTHING
    ''', (event_id, event_id, created, invoice_id, user_id,
          str(subscription_row_id), livemode, event.get('api_version'), summary))
    cur.execute('''
        SELECT id, event_type, object_id, user_id, subscription_id, livemode,
               processing_status, transaction_id
          FROM payment_events
         WHERE provider = 'stripe' AND provider_event_id = %s FOR UPDATE
    ''', (event_id,))
    row = cur.fetchone()
    _require(row is not None and row['event_type'] == event_type
             and row['object_id'] == invoice_id
             and str(row['user_id']) == user_id
             and (row['subscription_id'] is None
                  or str(row['subscription_id']) == str(subscription_row_id))
             and row['livemode'] is livemode,
             'Stripe payment event collision or account mismatch')
    _require(row['processing_status'] in {'received', 'failed', 'processed'},
             'Unsupported event state')
    existing_id = row['transaction_id']
    _require(existing_id is None or str(existing_id) == str(receipt_id),
             'Event already linked to a different financial receipt')
    _require(row['processing_status'] != 'processed' or existing_id is not None,
             'Processed event lacks linked receipt')
    cur.execute('''
        UPDATE payment_events SET
            processing_status = 'processed', transaction_id = %s::uuid,
            subscription_id = %s::uuid, helper_name = %s,
            helper_applied_at = NOW(), processed_at = NOW(),
            processing_error = NULL, error_text = NULL
         WHERE provider = 'stripe' AND provider_event_id = %s
    ''', (str(receipt_id), str(subscription_row_id), helper, event_id))
    _require(cur.rowcount == 1, 'Failed to finish payment event')
    return row


def settle_verified_paid_reup(event, *, connect, validate_invoice,
                               expected_livemode, now=None):
    """One verified invoice -> one existing intent -> one committed new period.

    Caller requirements: Stripe signature verified; authenticated purchase intent
    recorded durably *before* submitting charge; a stable intent-based Stripe
    idempotency key (not period-based) and completed payment confirmation.
    This function is deliberately not attached to a webhook in A7C3.
    """
    _require(type(expected_livemode) is bool, 'Expected Stripe environment unavailable')
    _require(isinstance(event, dict) and event.get('type') == 'invoice.paid',
             'Only a verified invoice.paid event may settle a purchase')
    eid = event.get('id')
    _require(isinstance(eid, str) and eid.startswith('evt_') and len(eid) > 4,
             'Stripe event ID unavailable')
    _require(type(event.get('livemode')) is bool
             and event['livemode'] == expected_livemode, 'Stripe environment mismatch')
    created = event.get('created')
    _require(type(created) is int and created > 0, 'Event timestamp missing')
    payload = event.get('data') or {}
    _require(isinstance(payload, dict), 'Malformed event envelope')
    invoice = payload.get('object')
    _require(isinstance(invoice, dict) and invoice.get('object') == 'invoice',
             'Stripe invoice object required')
    invoice_id = invoice.get('id')
    _require(isinstance(invoice_id, str) and invoice_id.startswith('in_')
             and len(invoice_id) > 3, 'Invoice ID missing')
    _require(invoice.get('billing_reason') == 'subscription_update',
             'Not an explicit subscription update')
    sub_id, intent_id = _invoice_binding(invoice)
    if now is None:
        now = _dt.datetime.now(_dt.timezone.utc)
    _require(isinstance(now, _dt.datetime) and now.tzinfo is not None,
             'Timezone-aware settlement clock required')
    now = now.astimezone(_dt.timezone.utc)

    conn = connect()
    try:
        with conn.cursor() as cur:
            cur.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))',
                        ('godinc:stripe:paid-reup:' + invoice_id,))
            # Receipt MUST be resolved before current entitlement, status and
            # price checks. A settled invoice may be replayed after expiry,
            # cancellation, upgrade, re-pricing or even a newer paid period.
            cur.execute('''
                SELECT id, user_id, subscription_id, status, provider_invoice_id,
                       gross_amount_cents, livemode, raw_summary_json
                  FROM billing_transactions
                 WHERE provider = 'stripe' AND provider_invoice_id = %s
                 FOR UPDATE
            ''', (invoice_id,))
            receipts = cur.fetchall()
            _require(len(receipts) <= 1, 'More than one ledger receipt for invoice')
            if receipts:
                receipt = receipts[0]
                original = _as_summary(receipt['raw_summary_json'])
                _require(original.get('schema') == 'stripe_paid_reup_receipt.v2'
                         and original.get('invoice_id') == invoice_id
                         and original.get('intent_id') == intent_id
                         and original.get('stripe_subscription_id') == sub_id
                         and original.get('livemode') is expected_livemode,
                         'Existing invoice receipt does not match replay')
                uid = str(receipt['user_id'])
                sub_row_id = str(receipt['subscription_id'])
                _require(uid == original.get('user_id')
                         and sub_row_id == original.get('subscription_row_id')
                         and receipt['livemode'] is expected_livemode
                         and receipt['status'] in {'succeeded', 'refunded', 'disputed'},
                         'Existing financial receipt identity or status differs')
                fact = validate_invoice(
                    invoice, expected_user_id=uid,
                    expected_subscription_id=sub_id,
                    expected_customer_id=original['customer_id'],
                    expected_price_id=original['price_id'],
                    expected_livemode=expected_livemode,
                    minimum_paid_cents=original['paid_cents'],
                    previous_period_start=1,
                )
                _require(fact['period_start'] == original['period_start']
                         and fact['period_end'] == original['period_end']
                         and fact['paid_cents'] == original['paid_cents']
                         and receipt['gross_amount_cents'] == original['paid_cents'],
                         'Replay contradicts historical paid period')
                # Deliberately do NOT inspect/modify the current subscription,
                # plan map, paid tier or question allowance on a replay.
                _payment_event(cur, event=event, invoice_id=invoice_id,
                               user_id=uid, subscription_row_id=sub_row_id,
                               livemode=expected_livemode, receipt_id=receipt['id'],
                               helper='duplicate_paid_reup_invoice')
                conn.commit()
                return {'status': 'duplicate', 'event_id': eid,
                        'invoice_id': invoice_id, 'receipt_status': receipt['status']}

            # New payment: require the explicitly staged intent from our own
            # financial ledger, not merely a client-supplied UUID or Stripe
            # subscription metadata. Missing/ambiguous intent NEVER grants.
            cur.execute('''
                SELECT id, user_id, subscription_id, status,
                       provider_invoice_id, raw_summary_json
                  FROM billing_transactions
                 WHERE provider = 'stripe' AND transaction_kind = 'monthly_renewal'
                   AND raw_summary_json ->> 'reup_intent_id' = %s
                 FOR UPDATE
            ''', (intent_id,))
            intent_rows = cur.fetchall()
            _require(len(intent_rows) == 1, 'No unique durable re-up intent')
            intent = intent_rows[0]
            _require(intent['status'] == 'pending'
                     and intent['provider_invoice_id'] is None,
                     'Purchase intent is already settled or invalid')
            stored = _as_summary(intent['raw_summary_json'])
            _require(stored.get('schema') == 'stripe_paid_reup_intent.v1'
                     and stored.get('reup_intent_id') == intent_id
                     and stored.get('dispatch_status') == 'sent',
                     'Purchase intent has not been dispatched')
            user_id = str(intent['user_id'])
            subscription_row_id = str(intent['subscription_id'])
            _require(stored.get('user_id') == user_id
                     and stored.get('subscription_row_id') == subscription_row_id
                     and stored.get('stripe_subscription_id') == sub_id
                     and stored.get('livemode') is expected_livemode,
                     'Intent ownership or environment differs')

            cur.execute('''
                SELECT id, user_id, billing_customer_id, plan_code, support_mode,
                       provider, stripe_subscription_id, stripe_price_id,
                       provider_status, internal_status, livemode
                  FROM subscriptions
                 WHERE provider = 'stripe' AND stripe_subscription_id = %s
                 FOR UPDATE
            ''', (sub_id,))
            sub = cur.fetchone()
            _require(sub is not None
                     and str(sub['id']) == subscription_row_id
                     and str(sub['user_id']) == user_id
                     and sub['support_mode'] == 'monthly_recurring'
                     and sub['provider_status'] == 'active'
                     and sub['internal_status'] == 'active'
                     and sub['livemode'] is expected_livemode,
                     'Current Stripe subscription cannot receive a new cycle')
            plan = sub['plan_code']
            price_id = sub['stripe_price_id']
            _require(isinstance(price_id, str) and price_id.startswith('price_')
                     and plan == stored.get('plan_code')
                     and price_id == stored.get('price_id'),
                     'Current paid plan or price differs from purchase intent')

            cur.execute('''
                SELECT id, plan_code, entitlement_status, current_period_started_at
                  FROM users WHERE id = %s::uuid FOR UPDATE
            ''', (user_id,))
            user = cur.fetchone()
            _require(user is not None and str(user['id']) == user_id
                     and user['plan_code'] == plan
                     and user['entitlement_status'] == 'active',
                     'Canonical paid account changed since purchase intent')
            old_start = user['current_period_started_at']
            _require(isinstance(old_start, _dt.datetime) and old_start.tzinfo is not None,
                     'Previously paid period start missing')
            old_epoch = int(old_start.timestamp())
            _require(stored.get('previous_period_start') == old_epoch,
                     'Current allocation period differs from purchase intent')

            cur.execute('''
                SELECT id, user_id, stripe_customer_id, provider_customer_id, livemode
                  FROM billing_customers
                 WHERE provider = 'stripe' AND user_id = %s::uuid FOR UPDATE
            ''', (user_id,))
            customer = cur.fetchone()
            _require(customer is not None
                     and str(customer['user_id']) == user_id
                     and str(customer['id']) == str(sub['billing_customer_id'])
                     and customer['livemode'] is expected_livemode,
                     'Stripe customer-account binding mismatch')
            cid = customer['stripe_customer_id']
            _require(isinstance(cid, str) and cid.startswith('cus_')
                     and customer['provider_customer_id'] == cid
                     and stored.get('customer_id') == cid,
                     'Stripe customer external identity differs')

            cur.execute('''
                SELECT stripe_price_id, plan_code, support_mode, livemode
                  FROM stripe_price_map
                 WHERE stripe_price_id = %s AND active = TRUE
            ''', (price_id,))
            price = cur.fetchone()
            _require(price is not None and price['stripe_price_id'] == price_id
                     and price['plan_code'] == plan
                     and price['support_mode'] == 'monthly_recurring'
                     and price['livemode'] is expected_livemode,
                     'Active Stripe price map differs')
            cur.execute('''
                SELECT monthly_price_cents, monthly_enabled, active
                  FROM plan_catalog WHERE plan_code = %s
            ''', (plan,))
            catalog = cur.fetchone()
            _require(catalog is not None and catalog['monthly_enabled'] is True
                     and catalog['active'] is True,
                     'Purchase plan unavailable')
            cents = catalog['monthly_price_cents']
            _require(type(cents) is int and cents > 0
                     and stored.get('expected_paid_cents') == cents,
                     'Purchase price differs from durable intent/catalog')

            fact = validate_invoice(
                invoice, expected_user_id=user_id,
                expected_subscription_id=sub_id,
                expected_customer_id=cid,
                expected_price_id=price_id,
                expected_livemode=expected_livemode,
                minimum_paid_cents=cents,
                previous_period_start=old_epoch,
            )
            start_dt = _dt.datetime.fromtimestamp(fact['period_start'], _dt.timezone.utc)
            end_dt = _dt.datetime.fromtimestamp(fact['period_end'], _dt.timezone.utc)
            _require(start_dt <= now + _dt.timedelta(minutes=5) and end_dt > now,
                     'New paid period is not currently usable')

            receipt_json = _json.dumps({
                'schema': 'stripe_paid_reup_receipt.v2',
                'invoice_id': invoice_id,
                'intent_id': intent_id,
                'reup_intent_id': intent_id,
                'purchase_request_id': stored.get('purchase_request_id'),
                'user_id': user_id,
                'subscription_row_id': subscription_row_id,
                'stripe_subscription_id': sub_id,
                'customer_id': cid,
                'price_id': price_id,
                'plan_code': plan,
                'livemode': expected_livemode,
                'paid_cents': fact['paid_cents'],
                'period_start': fact['period_start'],
                'period_end': fact['period_end'],
                'change_source': 'phase8_paid_reup',
            }, sort_keys=True)
            cur.execute('''
                UPDATE billing_transactions
                   SET status = 'succeeded', provider_invoice_id = %s,
                       stripe_invoice_id = %s, stripe_event_id = %s,
                       gross_amount_cents = %s, amount_total = %s,
                       occurred_at = %s, raw_summary_json = %s::jsonb
                 WHERE id = %s::uuid AND status = 'pending'
                 RETURNING id
            ''', (invoice_id, invoice_id, eid, fact['paid_cents'],
                  invoice['total'],
                  _dt.datetime.fromtimestamp(created, _dt.timezone.utc),
                  receipt_json, str(intent['id'])))
            paid_row = cur.fetchone()
            _require(paid_row is not None and str(paid_row['id']) == str(intent['id']),
                     'Pending purchase intent was not settled exactly once')
            cur.execute('''
                UPDATE subscriptions SET
                    current_period_start = %s, current_period_end = %s,
                    latest_invoice_id = %s, last_payment_at = %s,
                    provider_status = 'active', internal_status = 'active',
                    updated_at = NOW()
                 WHERE id = %s::uuid
            ''', (start_dt, end_dt, invoice_id, now, subscription_row_id))
            _require(cur.rowcount == 1, 'Failed to update paid subscription period')
            cur.execute('''
                UPDATE users SET
                    plan_code = %s, entitlement_status = 'active',
                    subscription_started_at = COALESCE(subscription_started_at, %s),
                    current_period_started_at = %s,
                    subscription_renews_at = %s, subscription_expires_at = %s,
                    cancel_at_period_end = FALSE,
                    last_paid_plan_code = %s, renewal_offer_plan_code = %s,
                    last_support_mode = 'monthly_recurring',
                    last_support_ended_at = NULL, updated_at = NOW()
                 WHERE id = %s::uuid
            ''', (plan, start_dt, start_dt, end_dt, end_dt, plan, plan, user_id))
            _require(cur.rowcount == 1, 'Failed to update canonical paid allowance')
            _payment_event(cur, event=event, invoice_id=invoice_id,
                           user_id=user_id, subscription_row_id=subscription_row_id,
                           livemode=expected_livemode, receipt_id=intent['id'],
                           helper='settle_verified_paid_reup')
        conn.commit()
        return {'status': 'settled', 'event_id': eid, 'invoice_id': invoice_id,
                'paid_cents': fact['paid_cents'], 'period_start': start_dt,
                'period_end': end_dt}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _canonical_reup_uuid(value, label):
    try:
        parsed = _UUID(str(value))
    except (TypeError, ValueError, AttributeError) as exc:
        raise StripeReupSettlementError(label + ' must be a canonical UUID') from exc
    _require(parsed.version == 4 and str(parsed) == str(value),
             label + ' must be a canonical UUIDv4')
    return str(parsed)


def _reup_clock(now):
    value = now if now is not None else _dt.datetime.now(_dt.timezone.utc)
    _require(isinstance(value, _dt.datetime) and value.tzinfo is not None,
             'Timezone-aware clock required')
    return value.astimezone(_dt.timezone.utc)


def _pending_intent_rows(cur, *, user_id):
    cur.execute('''
        SELECT id, user_id, subscription_id, status, provider_invoice_id,
               raw_summary_json
          FROM billing_transactions
         WHERE provider = 'stripe' AND transaction_kind = 'monthly_renewal'
           AND status = 'pending' AND user_id = %s::uuid
           AND raw_summary_json ->> 'schema' = 'stripe_paid_reup_intent.v1'
    ''', (user_id,))
    return cur.fetchall()


def prepare_stripe_reup_intent(*, user_id, connect, expected_livemode,
                                now=None, intent_factory=None,
                                purchase_request_id=None,
                                exhausted_quote=None, confirmed_terms=None):
    """Stage one paid re-up intent in existing billing_transactions, no charge.

    Caller MUST derive user_id from the authenticated server session. Returning
    an already-pending intent does not authorize a second Stripe dispatch.
    Reconciliation of uncertain intents is a distinct, manual/verified gate.
    """
    from uuid import uuid4
    user_id = _canonical_reup_uuid(user_id, 'Authenticated user')
    _require(type(expected_livemode) is bool, 'Stripe environment not specified')
    if purchase_request_id is not None:
        purchase_request_id = _canonical_reup_uuid(purchase_request_id, 'Client purchase request')
    instant = _reup_clock(now)
    conn = connect()
    try:
        with conn.cursor() as cur:
            if purchase_request_id is not None:
                # A request is immutable across HTTP retries, including after
                # the paid receipt has replaced its pending intent record.
                # Serialize even across accounts before looking up the key.
                cur.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))',
                            ('godinc:stripe:purchase-request:' + purchase_request_id,))
            if purchase_request_id is not None:
                # Resolve a completed purchase BEFORE checking current tier or
                # billing window. A delayed browser retry after settlement,
                # cancellation, refund or upgrade cannot be a fresh purchase.
                cur.execute('''
                    SELECT id, user_id, subscription_id, status,
                           provider_invoice_id, livemode, raw_summary_json
                      FROM billing_transactions
                     WHERE provider = 'stripe'
                       AND raw_summary_json ->> 'purchase_request_id' = %s
                     FOR UPDATE
                ''', (purchase_request_id,))
                historical = cur.fetchall()
                _require(len(historical) <= 1, 'Purchase request matches multiple ledger records')
                if historical:
                    record = historical[0]
                    data = _as_summary(record['raw_summary_json'])
                    _require(str(record['user_id']) == user_id
                             and data.get('user_id') == user_id
                             and data.get('purchase_request_id') == purchase_request_id
                             and data.get('subscription_row_id') == str(record['subscription_id'])
                             and data.get('livemode') is expected_livemode
                             and record['livemode'] is expected_livemode,
                             'Historical purchase request owner or environment changed')
                    if data.get('schema') == 'stripe_paid_reup_intent.v1':
                        _require(record['status'] == 'pending'
                                 and record['provider_invoice_id'] is None
                                 and data.get('dispatch_status') in {
                                     'prepared', 'dispatching', 'sent', 'uncertain'},
                                 'Prior pending purchase intent is inconsistent')
                        reply = {'status': 'already_pending',
                                 'intent_id': _canonical_reup_uuid(
                                     data.get('reup_intent_id'), 'Stored intent'),
                                 'dispatch_status': data['dispatch_status']}
                    elif data.get('schema') == 'stripe_paid_reup_receipt.v2':
                        _require(record['status'] in {'succeeded', 'refunded', 'disputed'}
                                 and isinstance(record['provider_invoice_id'], str)
                                 and record['provider_invoice_id'] == data.get('invoice_id'),
                                 'Prior settled purchase receipt is inconsistent')
                        reply = {'status': 'already_settled',
                                 'intent_id': _canonical_reup_uuid(
                                     data.get('intent_id'), 'Prior settled intent')}
                    else:
                        raise StripeReupSettlementError('Purchase request ledger state unknown')
                    conn.commit()
                    return reply
            # Acquire the account lock AFTER a historical receipt lookup.
            # The signed invoice writer locks the ledger before the user;
            # reverse ordering here would deadlock on concurrent retries.
            # For a new action, the user lock still serializes registrations.
            cur.execute('''
                SELECT id, plan_code, entitlement_status,
                       current_period_started_at, subscription_expires_at
                  FROM users WHERE id = %s::uuid FOR UPDATE
            ''', (user_id,))
            user = cur.fetchone()
            _require(user is not None and str(user['id']) == user_id,
                     'Authenticated account missing')
            _require(user['entitlement_status'] == 'active',
                     'User has no active paid entitlement')
            start = user['current_period_started_at']
            expiry = user['subscription_expires_at']
            _require(isinstance(start, _dt.datetime) and start.tzinfo is not None
                     and isinstance(expiry, _dt.datetime) and expiry.tzinfo is not None
                     and start <= instant < expiry,
                     'Current paid period is missing or expired')

            # A nonlocking pending read under the user lock prevents
            # user/ledger lock inversion against the signed invoice writer.
            # Existing pending intents block further purchases, even if the
            # original Stripe API outcome is unknown or the price later changes.
            pending = _pending_intent_rows(cur, user_id=user_id)
            _require(len(pending) <= 1, 'Multiple unresolved paid re-up intents')
            if pending:
                record = pending[0]
                stored = _as_summary(record['raw_summary_json'])
                ident = _canonical_reup_uuid(stored.get('reup_intent_id'), 'Stored intent')
                _require(record['status'] == 'pending'
                         and record['provider_invoice_id'] is None
                         and str(record['user_id']) == user_id
                         and stored.get('schema') == 'stripe_paid_reup_intent.v1'
                         and stored.get('user_id') == user_id
                         and stored.get('dispatch_status') in {
                             'prepared', 'dispatching', 'sent', 'uncertain'},
                         'Unresolved purchase intent is inconsistent')
                conn.commit()
                return {'status': 'already_pending', 'intent_id': ident,
                        'dispatch_status': stored['dispatch_status']}

            # The GET quote is advisory; the authenticated POST must bind it to
            # THIS locked account period before creating a new purchase intent.
            # Historical settled/pending UUID requests were resolved above.
            if exhausted_quote is not None:
                _require(
                    purchase_request_id is not None
                    and type(exhausted_quote) is dict
                    and exhausted_quote.get('eligible') is True
                    and exhausted_quote.get('quote_only') is True
                    and exhausted_quote.get('plan_code') == 'seeker'
                    and exhausted_quote.get('support_mode') == 'monthly_recurring'
                    and exhausted_quote.get('currency') == 'usd'
                    and user['plan_code'] == 'seeker'
                    and type(exhausted_quote.get('new_period_question_limit')) is int
                    and exhausted_quote['new_period_question_limit'] > 0
                    and type(exhausted_quote.get('questions_used')) is int
                    and exhausted_quote['questions_used'] >= exhausted_quote['new_period_question_limit']
                    and type(exhausted_quote.get('amount_cents')) is int
                    and exhausted_quote['amount_cents'] > 0
                    and type(exhausted_quote.get('quoted_period_start_epoch')) is int
                    and exhausted_quote['quoted_period_start_epoch'] == int(start.timestamp())
                    and type(exhausted_quote.get('quoted_period_end_epoch')) is int
                    and exhausted_quote['quoted_period_end_epoch'] == int(expiry.timestamp()),
                    'Server quote does not authorize a fresh exhausted Seeker period',
                )
                # Historical UUID replay was resolved above. For a NEW
                # financial action, only the exact reviewed customer terms
                # authorize this quote, never untrusted client pricing.
                _require(
                    type(confirmed_terms) is dict
                    and type(confirmed_terms.get('amount_cents')) is int
                    and confirmed_terms['amount_cents'] == exhausted_quote['amount_cents']
                    and confirmed_terms['amount_cents'] > 0
                    and confirmed_terms.get('currency') == 'usd'
                    and confirmed_terms['currency'] == exhausted_quote['currency']
                    and type(confirmed_terms.get('new_period_question_limit')) is int
                    and confirmed_terms['new_period_question_limit']
                        == exhausted_quote['new_period_question_limit']
                    and type(confirmed_terms.get('existing_period_ends_at')) is str
                    and confirmed_terms['existing_period_ends_at']
                        == exhausted_quote.get('existing_period_ends_at'),
                    'Confirmed customer amount or billing terms differ from current quote',
                )
            cur.execute('''
                SELECT id, user_id, billing_customer_id, plan_code, support_mode,
                       provider, stripe_subscription_id, stripe_price_id,
                       provider_status, internal_status, livemode,
                       cancel_at_period_end, current_period_start, current_period_end
                  FROM subscriptions WHERE user_id = %s::uuid
                    AND internal_status = 'active' FOR UPDATE
            ''', (user_id,))
            active = cur.fetchall()
            _require(len(active) == 1, 'Account must have exactly one active paid rail')
            sub = active[0]
            _require(sub['provider'] == 'stripe'
                     and str(sub['user_id']) == user_id
                     and sub['support_mode'] == 'monthly_recurring'
                     and sub['provider_status'] == 'active'
                     and sub['internal_status'] == 'active'
                     and sub['livemode'] is expected_livemode
                     and sub['cancel_at_period_end'] is False
                     and sub['plan_code'] == user['plan_code'],
                     'Active subscription cannot start an early re-up')
            old_period = sub['current_period_start']
            old_end = sub['current_period_end']
            _require(isinstance(old_period, _dt.datetime) and old_period.tzinfo is not None
                     and isinstance(old_end, _dt.datetime) and old_end.tzinfo is not None
                     and int(old_period.timestamp()) == int(start.timestamp())
                     and int(old_end.timestamp()) == int(expiry.timestamp()),
                     'Subscription and account paid windows disagree')
            sub_id = sub['stripe_subscription_id']
            price_id = sub['stripe_price_id']
            _require(isinstance(sub_id, str) and sub_id.startswith('sub_')
                     and isinstance(price_id, str) and price_id.startswith('price_'),
                     'Stripe product identity unavailable')
            customer_row_id = str(sub['billing_customer_id'])
            cur.execute('''
                SELECT id, user_id, provider, stripe_customer_id,
                       provider_customer_id, livemode
                  FROM billing_customers WHERE id = %s::uuid
                    AND user_id = %s::uuid AND provider = 'stripe' FOR UPDATE
            ''', (customer_row_id, user_id))
            customer = cur.fetchone()
            _require(customer is not None and str(customer['id']) == customer_row_id
                     and str(customer['user_id']) == user_id
                     and customer['livemode'] is expected_livemode
                     and isinstance(customer['stripe_customer_id'], str)
                     and customer['stripe_customer_id'].startswith('cus_')
                     and customer['provider_customer_id'] == customer['stripe_customer_id'],
                     'Stripe billing customer ownership mismatch')
            cur.execute('''
                SELECT stripe_price_id, plan_code, support_mode, livemode
                  FROM stripe_price_map WHERE stripe_price_id = %s AND active = TRUE
            ''', (price_id,))
            mapping = cur.fetchone()
            _require(mapping is not None and mapping['stripe_price_id'] == price_id
                     and mapping['plan_code'] == sub['plan_code']
                     and mapping['support_mode'] == 'monthly_recurring'
                     and mapping['livemode'] is expected_livemode,
                     'Active Stripe price mapping mismatch')
            cur.execute('''
                SELECT monthly_price_cents, monthly_enabled, active
                  FROM plan_catalog WHERE plan_code = %s
            ''', (sub['plan_code'],))
            plan = cur.fetchone()
            _require(plan is not None and plan['monthly_enabled'] is True
                     and plan['active'] is True
                     and type(plan['monthly_price_cents']) is int
                     and plan['monthly_price_cents'] > 0,
                     'Configured monthly price is unavailable')
            if exhausted_quote is not None:
                _require(exhausted_quote['amount_cents'] == plan['monthly_price_cents'],
                         'Quoted charge no longer matches the configured monthly price')
            generator = intent_factory if intent_factory is not None else uuid4
            _require(callable(generator), 'Intent generator unavailable')
            intent_id = _canonical_reup_uuid(generator(), 'New server intent')
            cur.execute('''
                SELECT id FROM billing_transactions
                 WHERE provider = 'stripe'
                   AND raw_summary_json ->> 'reup_intent_id' = %s LIMIT 1
            ''', (intent_id,))
            _require(cur.fetchone() is None, 'Purchase-intent identity collision')
            metadata = {
                'schema': 'stripe_paid_reup_intent.v1',
                'reup_intent_id': intent_id,
                'dispatch_status': 'prepared',
                'user_id': user_id,
                'subscription_row_id': str(sub['id']),
                'stripe_subscription_id': sub_id,
                'customer_id': customer['stripe_customer_id'],
                'price_id': price_id,
                'plan_code': sub['plan_code'],
                'livemode': expected_livemode,
                'expected_paid_cents': plan['monthly_price_cents'],
                'previous_period_start': int(start.timestamp()),
                'prepared_at': instant.isoformat(),
            }
            if purchase_request_id is not None:
                metadata['purchase_request_id'] = purchase_request_id
            cur.execute('''
                INSERT INTO billing_transactions (
                    user_id, subscription_id, plan_code, provider,
                    transaction_kind, support_mode, currency,
                    gross_amount_cents, status, livemode, raw_summary_json
                ) VALUES (
                    %s::uuid, %s::uuid, %s, 'stripe',
                    'monthly_renewal', 'monthly_recurring', 'usd',
                    0, 'pending', %s, %s::jsonb
                ) RETURNING id
            ''', (user_id, str(sub['id']), sub['plan_code'], expected_livemode,
                  _json.dumps(metadata, sort_keys=True)))
            inserted = cur.fetchone()
            _require(inserted is not None and inserted.get('id') is not None,
                     'Durable purchase intent could not be created')
        conn.commit()
        return {'status': 'prepared', 'intent_id': intent_id,
                'expected_paid_cents': plan['monthly_price_cents'],
                'plan_code': sub['plan_code']}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def transition_stripe_reup_intent(*, user_id, intent_id, connect,
                                   action, stripe_result=None):
    """Claim, confirm, or quarantine one durable intent; NEVER charge/grant.

    CLAIM must precede any Stripe API call. A claimed/uncertain/sent intent
    cannot be dispatched again automatically. CONFIRM requires a consistent
    provider response but is not paid-invoice confirmation; only the existing
    settlement contract can grant. Failure after CLAIM must use UNCERTAIN.
    """
    user_id = _canonical_reup_uuid(user_id, 'Authenticated user')
    intent_id = _canonical_reup_uuid(intent_id, 'Purchase intent')
    _require(action in {'claim', 'confirm', 'uncertain'}, 'Unsupported intent transition')
    conn = connect()
    try:
        with conn.cursor() as cur:
            cur.execute('''
                SELECT id, user_id, subscription_id, status, provider_invoice_id,
                       raw_summary_json
                  FROM billing_transactions
                 WHERE provider = 'stripe' AND transaction_kind = 'monthly_renewal'
                   AND raw_summary_json ->> 'reup_intent_id' = %s FOR UPDATE
            ''', (intent_id,))
            rows = cur.fetchall()
            _require(len(rows) == 1, 'Purchase intent missing or ambiguous')
            row = rows[0]
            meta = _as_summary(row['raw_summary_json'])
            _require(row['status'] == 'pending'
                     and row['provider_invoice_id'] is None
                     and str(row['user_id']) == user_id
                     and meta.get('schema') == 'stripe_paid_reup_intent.v1'
                     and meta.get('reup_intent_id') == intent_id
                     and meta.get('user_id') == user_id
                     and meta.get('subscription_row_id') == str(row['subscription_id']),
                     'Purchase intent ownership or state mismatch')
            state = meta.get('dispatch_status')
            _require(state in {'prepared', 'dispatching', 'sent', 'uncertain'},
                     'Unknown purchase dispatch status')
            if action == 'claim':
                if state != 'prepared':
                    conn.commit()
                    return {'status': 'held', 'dispatch_status': state,
                            'intent_id': intent_id}
                # Last safe check before a provider operation. A renewal or
                # plan change since registration must block dispatch.
                cur.execute('''
                    SELECT id, user_id, plan_code, provider, support_mode,
                           stripe_subscription_id, stripe_price_id,
                           provider_status, internal_status, livemode,
                           cancel_at_period_end, current_period_start
                      FROM subscriptions WHERE id = %s::uuid FOR UPDATE
                ''', (str(row['subscription_id']),))
                sub = cur.fetchone()
                _require(sub is not None and str(sub['id']) == str(row['subscription_id'])
                         and str(sub['user_id']) == user_id
                         and sub['provider'] == 'stripe'
                         and sub['support_mode'] == 'monthly_recurring'
                         and sub['stripe_subscription_id'] == meta.get('stripe_subscription_id')
                         and sub['stripe_price_id'] == meta.get('price_id')
                         and sub['plan_code'] == meta.get('plan_code')
                         and sub['provider_status'] == 'active'
                         and sub['internal_status'] == 'active'
                         and sub['livemode'] is meta.get('livemode')
                         and sub['cancel_at_period_end'] is False
                         and isinstance(sub['current_period_start'], _dt.datetime)
                         and int(sub['current_period_start'].timestamp())
                            == meta.get('previous_period_start'),
                         'Stripe subscription changed before dispatch')
                cur.execute('''
                    SELECT id, plan_code, entitlement_status,
                           current_period_started_at, subscription_expires_at
                      FROM users WHERE id = %s::uuid FOR UPDATE
                ''', (user_id,))
                user = cur.fetchone()
                now = _reup_clock(None)
                _require(user is not None and str(user['id']) == user_id
                         and user['plan_code'] == meta.get('plan_code')
                         and user['entitlement_status'] == 'active'
                         and isinstance(user['current_period_started_at'], _dt.datetime)
                         and int(user['current_period_started_at'].timestamp())
                            == meta.get('previous_period_start')
                         and isinstance(user['subscription_expires_at'], _dt.datetime)
                         and user['subscription_expires_at'] > now,
                         'Canonical paid window changed before dispatch')
                next_state = 'dispatching'
            elif action == 'uncertain':
                if state == 'uncertain':
                    conn.commit()
                    return {'status': 'held', 'dispatch_status': state,
                            'intent_id': intent_id}
                _require(state == 'dispatching',
                         'Only a claimed charge may enter uncertain state')
                next_state = 'uncertain'
            else:
                _require(state == 'dispatching',
                         'Only a claimed charge may be confirmed')
                _require(isinstance(stripe_result, dict),
                         'Provider confirmation data missing')
                def ref(value):
                    return value.get('id') if isinstance(value, dict) else value
                _require(stripe_result.get('id') == meta.get('stripe_subscription_id')
                         and ref(stripe_result.get('customer')) == meta.get('customer_id')
                         and stripe_result.get('livemode') is meta.get('livemode')
                         and stripe_result.get('status') == 'active',
                         'Stripe response ownership or status differs')
                provider_meta = stripe_result.get('metadata') or {}
                _require(isinstance(provider_meta, dict)
                         and provider_meta.get('change_source') == 'phase8_paid_reup'
                         and provider_meta.get('reup_intent_id') == intent_id
                         and provider_meta.get('user_id') == user_id,
                         'Stripe response lacks explicit purchase intent')
                items = (stripe_result.get('items') or {}).get('data') or []
                _require(len(items) == 1 and isinstance(items[0], dict)
                         and ref(items[0].get('price')) == meta.get('price_id'),
                         'Stripe response subscription price differs')
                new_start = stripe_result.get('current_period_start')
                new_end = stripe_result.get('current_period_end')
                latest_invoice = ref(stripe_result.get('latest_invoice'))
                _require(type(new_start) is int and type(new_end) is int
                         and new_start > meta.get('previous_period_start', 0)
                         and new_end > new_start and 27 * 86400 <= new_end - new_start <= 32 * 86400
                         and isinstance(latest_invoice, str)
                         and latest_invoice.startswith('in_'),
                         'Stripe did not provide a newly invoiced monthly period')
                meta['stripe_latest_invoice_id'] = latest_invoice
                meta['provider_period_start'] = new_start
                meta['provider_period_end'] = new_end
                next_state = 'sent'
            meta['dispatch_status'] = next_state
            cur.execute('''
                UPDATE billing_transactions SET raw_summary_json = %s::jsonb
                 WHERE id = %s::uuid AND status = 'pending'
                   AND provider_invoice_id IS NULL RETURNING id
            ''', (_json.dumps(meta, sort_keys=True), str(row['id'])))
            result = cur.fetchone()
            _require(result is not None and str(result['id']) == str(row['id']),
                     'Intent dispatch state update was not durable')
        conn.commit()
        reply = {'status': next_state, 'intent_id': intent_id}
        if next_state == 'dispatching':
            reply['dispatch'] = {
                'user_id': user_id,
                'subscription_row_id': meta['subscription_row_id'],
                'stripe_subscription_id': meta['stripe_subscription_id'],
                'customer_id': meta['customer_id'],
                'plan_code': meta['plan_code'],
                'price_id': meta['price_id'],
                'previous_period_start': meta['previous_period_start'],
                'expected_paid_cents': meta['expected_paid_cents'],
                'livemode': meta['livemode'],
            }
        return reply
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def audit_failed_stripe_reup_invoice(event, *, connect, expected_livemode):
    """Audit a *signed* explicit failed additional purchase; preserve paid access.

    A valid previously paid period MUST NOT be cancelled or reset because an
    independent early-re-up payment attempt failed. The existing intent
    remains pending/held for manual or server-verified reconciliation.
    This function NEVER calls Stripe or changes users/subscriptions.
    Caller MUST verify Stripe signature *before* invocation.
    """
    _require(type(expected_livemode) is bool, 'Expected Stripe environment unavailable')
    route = classify_stripe_reup_invoice_event(event)
    _require(isinstance(route, dict) and route.get('route') == 'reup_payment_failed',
             'Only explicit failed Stripe early re-ups are auditable here')
    eid = event.get('id')
    created = event.get('created')
    _require(isinstance(eid, str) and eid.startswith('evt_') and len(eid) > 4,
             'Stripe failure event identity missing')
    _require(type(created) is int and created > 0,
             'Stripe failure event timestamp missing')
    _require(type(event.get('livemode')) is bool
             and event['livemode'] is expected_livemode,
             'Stripe failure event environment mismatch')
    invoice = event['data']['object']
    _require(type(invoice.get('livemode')) is bool
             and invoice['livemode'] is expected_livemode,
             'Stripe invoice environment mismatch')
    _require(invoice.get('paid') is not True and invoice.get('status') != 'paid',
             'A paid invoice cannot be audited as a failed payment')
    invoice_id = route['invoice_id']
    intent_id = route['intent_id']
    external_sub = route['subscription_id']
    customer_id = _id(invoice.get('customer'))
    _require(isinstance(customer_id, str) and customer_id.startswith('cus_'),
             'Failed invoice customer identity missing')
    parent = invoice.get('parent') or {}
    nested = parent.get('subscription_details') or invoice.get('subscription_details') or {}
    metadata = nested.get('metadata') or {}
    _require(isinstance(metadata, dict) and isinstance(metadata.get('user_id'), str),
             'Signed invoice account identity missing')
    conn = connect()
    try:
        with conn.cursor() as cur:
            # Same lock key and acquisition order as the atomic PAID writer.
            cur.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))',
                        ('godinc:stripe:paid-reup:' + invoice_id,))
            # Historical receipt lookup first: late, out-of-order failure
            # events must never reverse an already settled paid cycle.
            cur.execute('''
                SELECT id, user_id, subscription_id, status, provider_invoice_id,
                       livemode, raw_summary_json
                  FROM billing_transactions
                 WHERE provider = 'stripe' AND provider_invoice_id = %s
                 FOR UPDATE
            ''', (invoice_id,))
            receipts = cur.fetchall()
            _require(len(receipts) <= 1, 'Ambiguous Stripe invoice financial record')
            if receipts:
                ledger = receipts[0]
                meta = _as_summary(ledger['raw_summary_json'])
                _require(meta.get('schema') == 'stripe_paid_reup_receipt.v2'
                         and meta.get('invoice_id') == invoice_id
                         and meta.get('intent_id') == intent_id
                         and meta.get('stripe_subscription_id') == external_sub
                         and meta.get('customer_id') == customer_id
                         and meta.get('user_id') == str(ledger['user_id'])
                         and meta.get('livemode') is expected_livemode
                         and metadata['user_id'] == str(ledger['user_id'])
                         and ledger['livemode'] is expected_livemode
                         and ledger['status'] in {'succeeded', 'refunded', 'disputed'},
                         'Late failure contradicts existing paid re-up receipt')
                status = 'historical_failure_audited'
            else:
                cur.execute('''
                    SELECT id, user_id, subscription_id, status,
                           provider_invoice_id, livemode, raw_summary_json
                      FROM billing_transactions
                     WHERE provider = 'stripe' AND transaction_kind = 'monthly_renewal'
                       AND raw_summary_json ->> 'reup_intent_id' = %s
                     FOR UPDATE
                ''', (intent_id,))
                matches = cur.fetchall()
                _require(len(matches) == 1, 'Failed invoice has no unique durable intent')
                ledger = matches[0]
                meta = _as_summary(ledger['raw_summary_json'])
                _require(ledger['status'] == 'pending'
                         and ledger['provider_invoice_id'] is None
                         and meta.get('schema') == 'stripe_paid_reup_intent.v1'
                         and meta.get('reup_intent_id') == intent_id
                         and meta.get('dispatch_status') in {'dispatching', 'sent', 'uncertain'}
                         and meta.get('user_id') == str(ledger['user_id'])
                         and meta.get('subscription_row_id') == str(ledger['subscription_id'])
                         and meta.get('stripe_subscription_id') == external_sub
                         and meta.get('customer_id') == customer_id
                         and meta.get('livemode') is expected_livemode
                         and metadata['user_id'] == str(ledger['user_id'])
                         and ledger['livemode'] is expected_livemode,
                         'Failed invoice does not match an attempted re-up')
                provider_invoice = meta.get('stripe_latest_invoice_id')
                _require(provider_invoice is None or provider_invoice == invoice_id,
                         'Failed invoice differs from dispatched purchase invoice')
                status = 'failed_attempt_audited'

            receipt_id = str(ledger['id'])
            # A unique intent cannot be silently associated with two different
            # failed invoice IDs; disagreement requires manual reconciliation.
            cur.execute('''
                SELECT object_id FROM payment_events
                 WHERE provider = 'stripe' AND transaction_id = %s::uuid
                   AND event_type = 'invoice.payment_failed'
                 FOR UPDATE
            ''', (receipt_id,))
            previous = cur.fetchall()
            _require(all(row['object_id'] == invoice_id for row in previous),
                     'The purchase intent was linked to another failed invoice')
            _payment_event(cur, event=event, invoice_id=invoice_id,
                           user_id=str(ledger['user_id']),
                           subscription_row_id=str(ledger['subscription_id']),
                           livemode=expected_livemode, receipt_id=receipt_id,
                           helper='audit_failed_stripe_reup_invoice')
        conn.commit()
        return {'status': status, 'invoice_id': invoice_id, 'event_id': eid,
                'purchase_held': status == 'failed_attempt_audited'}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def classify_stripe_reup_invoice_event(event):
    """Identify explicit re-up invoices before ordinary webhook dispatch.

    The caller MUST supply an already Stripe-signature-verified event.
    This is a pure classifier, not a permission grant or failure settlement.
    None means the original billing handler owns the event. Unknown mid-cycle
    invoice identities fail closed instead of falling into the generic grant
    or cancellation handlers. No provider, database or entitlement calls.
    """
    if not isinstance(event, dict):
        raise StripeReupSettlementError('Webhook event must be a dictionary')
    kind = event.get('type')
    if kind not in {'invoice.paid', 'invoice.payment_failed'}:
        return None
    payload = event.get('data') or {}
    _require(isinstance(payload, dict), 'Malformed invoice webhook payload')
    invoice = payload.get('object')
    _require(isinstance(invoice, dict) and invoice.get('object') == 'invoice',
             'Invoice webhook object missing')
    reason = invoice.get('billing_reason')
    if reason in {'subscription_cycle', 'subscription_create'}:
        # Normal renewal and initial checkout remain in the existing rail.
        return None
    _require(reason == 'subscription_update', 'Unknown invoice billing reason requires review')
    parent = invoice.get('parent') or {}
    legacy = invoice.get('subscription_details') or {}
    _require(isinstance(parent, dict) and isinstance(legacy, dict),
             'Invalid invoice subscription metadata container')
    nested = parent.get('subscription_details') or {}
    _require(isinstance(nested, dict), 'Invalid parent subscription metadata')
    first = nested.get('metadata') or {}
    second = legacy.get('metadata') or {}
    _require(isinstance(first, dict) and isinstance(second, dict),
             'Invalid invoice subscription metadata')
    # If Stripe supplied both legacy/current forms, neither is allowed to
    # silently override a contradictory purchase-intent identity.
    if nested and legacy:
        for name in ('change_source', 'reup_intent_id', 'user_id'):
            _require(first.get(name) == second.get(name),
                     'Contradictory invoice subscription metadata')
        _require(_id(nested.get('subscription')) == _id(legacy.get('subscription')),
                 'Contradictory invoice subscription reference')
    meta = first if nested else second
    change_source = meta.get('change_source')
    if change_source == 'phase8_change_plan' and 'reup_intent_id' not in meta:
        # Existing Stripe upgrade path is accepted and remains unchanged.
        return None
    _require(change_source == 'phase8_paid_reup',
             'Unclassified mid-cycle invoice requires reconciliation')
    sub_id, intent_id = _invoice_binding(invoice)
    invoice_id = invoice.get('id')
    _require(isinstance(invoice_id, str) and invoice_id.startswith('in_')
             and len(invoice_id) > 3, 'Explicit re-up invoice identity missing')
    return {
        'route': 'reup_paid' if kind == 'invoice.paid' else 'reup_payment_failed',
        'event_type': kind,
        'invoice_id': invoice_id,
        'subscription_id': sub_id,
        'intent_id': intent_id,
    }
