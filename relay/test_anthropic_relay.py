"""Offline tests for the relay's cost accounting and spend cap (run: pytest relay/)."""
import importlib
import json
import os
import sys
import tempfile
from pathlib import Path

import pytest


@pytest.fixture()
def relay(monkeypatch):
    ledger = Path(tempfile.mkdtemp()) / 'spend.json'
    monkeypatch.setenv('ANTHROPIC_WORKSPACE_ID', 'wrkspc_test')
    monkeypatch.setenv('RELAY_LEDGER', str(ledger))
    monkeypatch.setenv('RELAY_SPEND_CAP_USD', '1.00')
    monkeypatch.setenv('RELAY_SEED_USD', '0.25')
    sys.path.insert(0, str(Path(__file__).parent))
    module = importlib.reload(importlib.import_module('anthropic_relay'))
    yield module
    sys.path.pop(0)


def test_short_prompt_cost_uses_one_hour_writes_when_ttl_unknown(relay):
    usage = {'input_tokens': 1_000, 'cache_read_input_tokens': 50_000,
             'cache_creation_input_tokens': 10_000, 'output_tokens': 2_000}
    expected = (1_000 * 0.10 + 50_000 * 0.01 + 10_000 * 0.20 + 2_000 * 0.50) / 1e6
    assert relay.actual_cost(usage) == pytest.approx(expected)


def test_ttl_split_and_long_prompt_card(relay):
    usage = {'input_tokens': 120_000, 'cache_read_input_tokens': 0, 'cache_creation_input_tokens': 0,
             'output_tokens': 1_000}
    assert relay.actual_cost(usage) == pytest.approx((120_000 * 0.50 + 1_000 * 2.50) / 1e6)
    split = {'input_tokens': 0, 'cache_creation_input_tokens': 4_000, 'output_tokens': 0,
             'cache_creation': {'ephemeral_5m_input_tokens': 3_000, 'ephemeral_1h_input_tokens': 1_000}}
    assert relay.actual_cost(split) == pytest.approx((3_000 * 0.125 + 1_000 * 0.20) / 1e6)


def test_usage_from_sse_merges_start_and_delta(relay):
    stream = b'\n'.join([
        b'event: message_start',
        b'data: ' + json.dumps({'type': 'message_start', 'message': {'usage': {
            'input_tokens': 5, 'cache_read_input_tokens': 900, 'cache_creation_input_tokens': 100,
            'output_tokens': 1}}}).encode(),
        b'event: content_block_delta', b'data: {"type":"content_block_delta","delta":{"text":"hi"}}',
        b'event: message_delta',
        b'data: ' + json.dumps({'type': 'message_delta', 'usage': {'output_tokens': 42}}).encode(), b''])
    usage = relay.usage_from(stream, streamed=True)
    assert usage == {'input_tokens': 5, 'cache_read_input_tokens': 900, 'cache_creation_input_tokens': 100,
                     'output_tokens': 42}
    assert relay.usage_from(json.dumps({'usage': {'output_tokens': 7}}).encode(), streamed=False) == {'output_tokens': 7}


def test_budget_seed_reservation_and_cap(relay):
    budget = relay.BUDGET
    assert budget.spent == pytest.approx(0.25)          # seeded on first start
    assert budget.reserve(0.70)                         # 0.25 + 0.70 <= 1.00
    assert not budget.reserve(0.10)                     # 0.25 + 0.70 + 0.10 > 1.00: refused while in flight
    budget.settle(0.70, 0.05)                           # actual cost replaces the reservation
    assert budget.spent == pytest.approx(0.30) and budget.reserved == pytest.approx(0.0)
    assert json.loads(relay.LEDGER.read_text())['spent_usd'] == pytest.approx(0.30)
    assert budget.reserve(0.70) and not budget.reserve(0.01)
