"""Token telemetry must not poison an otherwise successful terminal result."""
from types import SimpleNamespace
import json
from unittest.mock import AsyncMock
from claude_agent_sdk import ResultMessage
from app.execution.infrastructure.harness.claude.result_usage import ClaudeResultUsage

import pytest

from app.runtime.token_metrics import MAX_TOKEN_COUNT, token_count
from app.executors.claude_agent_sdk_runner import _merge_sdk_usage
from app.execution.domain.worker_observability import executor_observability
from app.execution.application.worker_terminal_projection import project_worker_terminal_result
from app.runs.application.lifecycle import RunLifecycleService, _result_observability_values
from app.runs.infrastructure.lifecycle_postgres import PostgresRunLifecyclePersistence

INVALID = [-1, -0.5, True, False, float('inf'), float('-inf'), float('nan'), 2**31, 1e308, '2147483648', '-1']

@pytest.mark.parametrize('value,expected', [(0,0),(12,12),(MAX_TOKEN_COUNT,MAX_TOKEN_COUNT),('12',12),(12.9,12)])
def test_valid_legacy_coercions(value, expected):
    assert token_count(value) == expected
    usage = _merge_sdk_usage({}, {'input_tokens':value})
    observed = executor_observability({'sdk_usage':usage}, latency_ms=1)
    assert observed['token_counts'] == {'input':expected,'output':0,'total':expected}

@pytest.mark.parametrize('value', INVALID)
def test_invalid_counters_do_not_become_counts_or_partial_totals(value):
    assert token_count(value) is None
    # Test both SDK adaptation and the defensive worker entry independently.
    for usage in [{'input_tokens':value,'output_tokens':5},
                  _merge_sdk_usage({}, {'input_tokens':value,'output_tokens':5})]:
        result = executor_observability({'sdk_usage':usage}, latency_ms=1)
        assert result['token_counts'] == {'input':0,'output':0,'total':0}
    assert _result_observability_values({'token_counts':{'input':value,'output':5}})[1:4] == (0,0,0)

@pytest.mark.parametrize('usage', [
    {'input_tokens':MAX_TOKEN_COUNT,'cache_read_input_tokens':1},
    {'input_tokens':MAX_TOKEN_COUNT,'cache_creation_input_tokens':1},
    {'input_tokens':MAX_TOKEN_COUNT,'output_tokens':1},
    {'input_tokens':2,'total_tokens':2**31},
])
def test_aggregate_overflow_is_unknown_not_saturated(usage):
    result = executor_observability({'sdk_usage':usage}, latency_ms=1)
    assert result['token_counts'] == {'input':0,'output':0,'total':0}


def test_sdk_continuation_unknown_is_sticky_and_cost_merge_unchanged():
    first = _merge_sdk_usage({}, {'input_tokens':MAX_TOKEN_COUNT,'total_cost_usd':0.1})
    second = _merge_sdk_usage(first, {'input_tokens':1,'total_cost_usd':0.2})
    third = _merge_sdk_usage(second, {'input_tokens':1})
    assert third['input_tokens'] == -1
    assert third['total_cost_usd'] == pytest.approx(0.3)
    assert _merge_sdk_usage({'input_tokens':-2},{'input_tokens':4})['input_tokens'] == -1


def test_valid_alias_cache_and_total_contract():
    observed = executor_observability({'sdk_usage':{'input':'3','output':4.9,
        'cache_creation_input_tokens':5,'cache_read_input_tokens':6}},latency_ms=10)
    assert observed['token_counts'] == {'input':14,'output':4,'total':18}
    assert _result_observability_values(observed)[1:4] == (14,4,18)

@pytest.mark.parametrize('value', INVALID)
def test_internal_override_is_defensive_but_not_a_callback_whitelist_claim(value):
    observed = executor_observability({'input_token_count':value},latency_ms=1)
    assert observed['token_counts'] == {'input':0,'output':0,'total':0}


class RecordingConnection:
    """Records real persistence SQL parameters; deliberately NOT PostgreSQL."""
    def __init__(self):
        self.calls = []
    async def execute(self, sql, params):
        self.calls.append((sql, params))
        return SimpleNamespace(fetchone=AsyncMock(return_value={'id':'run-a'}))


def service():
    return RunLifecycleService(persistence=PostgresRunLifecyclePersistence(),
        append_event=AsyncMock(),append_audit_log=AsyncMock(),validate_result_size=lambda _:None,
        sanitize_payload=lambda p:p,sanitize_text=str,make_trace_id=str)

@pytest.mark.asyncio
@pytest.mark.parametrize('value', [-1,True,2**31,1e308])
async def test_finite_sdk_result_to_terminal_projection_and_sql_parameters(value):
    sdk_result = ResultMessage(subtype='success',duration_ms=1,duration_api_ms=1,
        is_error=False,num_turns=1,session_id='sdk-a',total_cost_usd=0.25,
        usage={'input_tokens':value,'output_tokens':5})
    observation = ClaudeResultUsage(session_id='sdk-a',prompt='synthetic input')
    observation.observe_result(sdk_result)
    usage = observation.project(_merge_sdk_usage({},sdk_result.usage),terminal_result=True)
    # JSON round-trip models the receipt carrier, not an HTTP integration test.
    payload = json.loads(json.dumps({'sdk_usage':usage},allow_nan=False))
    result = SimpleNamespace(status='succeeded',result={'message':'business answer'},executor_payload=payload,
        schema_version='1',adapter_version='1',executor_type='claude-agent-worker',
        executor_version='1',capabilities=[],artifacts=[])
    projection = project_worker_terminal_result(result,[],trace_id='trace-a',latency_ms=1,
        invoked_skill_ids=lambda _:set())
    assert projection.result_payload['message'] == 'business answer'
    assert result.status == 'succeeded'
    assert projection.result_payload['token_counts'] == {'input':0,'output':0,'total':0}
    assert projection.terminal_event_kwargs['input_token_count'] == 0
    assert projection.terminal_event_kwargs['output_token_count'] == 0
    assert projection.terminal_event_kwargs['total_token_count'] == 0
    assert projection.terminal_event_kwargs['estimated_cost_minor'] == 25
    conn = RecordingConnection()
    assert await service().complete_run(conn,tenant_id='tenant-a',run_id='run-a',result_json=projection.result_payload)
    sql, params = conn.calls[0]
    assert "status = 'succeeded'" in sql
    assert params[2:5] == (0,0,0)
    assert json.loads(params[0])['message'] == 'business answer'

@pytest.mark.asyncio
async def test_runs_boundary_independently_bounds_aggregate_before_persistence():
    conn = RecordingConnection()
    result = {'message':'done','token_counts':{'input':MAX_TOKEN_COUNT,'output':1}}
    assert await service().complete_run(conn,tenant_id='t',run_id='r',result_json=result)
    assert conn.calls[0][1][2:5] == (0,0,0)

@pytest.mark.asyncio
@pytest.mark.parametrize('target', ['failed','cancelled'])
@pytest.mark.parametrize('value', [-1,True,2**31,1e308])
async def test_staged_terminal_metrics_cannot_block_business_failure_or_cancel(target,value):
    persistence = SimpleNamespace(
        load_staged_terminalization=AsyncMock(return_value={
            'status':'running','terminalization_target':target,
            'terminalization_reason':'executor_done',
            'terminalization_result_json':{'message':'answer retained','token_counts':{'input':value}},
            'input_token_count':0,'output_token_count':0,'total_token_count':0,
        }),
        finalize_staged_terminalization=AsyncMock(return_value={
            'status':target,'input_token_count':0,'output_token_count':0,'total_token_count':0,
        }),
    )
    lifecycle = service()
    lifecycle._persistence = persistence
    progress = await lifecycle.progress_run_terminalization(object(),tenant_id='t',run_id='r')
    assert progress.completed and progress.status == target
    kwargs = persistence.finalize_staged_terminalization.call_args.kwargs
    assert tuple(kwargs[key] for key in ('input_token_count','output_token_count','total_token_count')) == (0,0,0)
    event = lifecycle._append_event.call_args.kwargs
    assert event['event_type'] == ('run_failed' if target == 'failed' else 'run_cancelled')
    assert event['payload']['result']['message'] == 'answer retained'
    assert tuple(event[key] for key in ('input_token_count','output_token_count','total_token_count')) == (0,0,0)


def test_explicit_string_zero_total_keeps_legacy_runs_coercion():
    assert _result_observability_values({'token_counts':{'input':2,'output':3,'total':'0'}})[1:4] == (2,3,0)


def test_fractional_turns_are_summed_before_legacy_int_projection():
    merged = _merge_sdk_usage({'input_tokens':12.9}, {'input_tokens':12.9})
    assert merged['input_tokens'] == 25.8
    assert executor_observability({'sdk_usage':merged},latency_ms=1)['token_counts']['input'] == 25


def test_none_primary_retains_known_legacy_alias():
    observed = executor_observability({'sdk_usage':{'input_tokens':None,'input':3,'output_tokens':2}},latency_ms=1)
    assert observed['token_counts'] == {'input':3,'output':2,'total':5}


def test_sdk_numeric_string_keeps_legacy_replacement_semantics():
    assert _merge_sdk_usage({'input_tokens':12},{'input_tokens':'3'})['input_tokens'] == '3'
    assert _merge_sdk_usage({'input_tokens':'12'},{'input_tokens':3})['input_tokens'] == 3


def test_null_and_missing_runs_metrics_preserve_legacy_defaults():
    assert _result_observability_values({'token_counts':{'input':2,'output':3,'total':None}})[1:4] == (2,3,5)
    assert _result_observability_values({'token_counts':{'input':None,'output':3}})[1:4] == (0,3,3)


@pytest.mark.parametrize('bad', [-1,True,2**31,1e308])
def test_invalid_sdk_turn_stays_unknown_across_normal_or_null_turn(bad):
    first = _merge_sdk_usage({}, {'input_tokens':bad})
    for current in [4,None,'4']:
        merged = _merge_sdk_usage(first, {'input_tokens':current,'output_tokens':3})
        assert executor_observability({'sdk_usage':merged},latency_ms=1)['token_counts'] == {'input':0,'output':0,'total':0}


def test_null_sdk_turn_retains_legacy_numeric_restart():
    assert _merge_sdk_usage({'input_tokens':None},{'input_tokens':3})['input_tokens'] == 3
