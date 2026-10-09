"""Wire the Executor's scoped Run input callback adapter."""

from app.execution.infrastructure.run_inputs_http import RUN_INPUT_CALLBACK_PATH, RunInputCallbackClient


def build_run_input_callback_client(
    *, callback_base_url, callback_token, callback_token_id, run_id, attempt_id,
):
    return RunInputCallbackClient(
        callback_url=f"{callback_base_url}{RUN_INPUT_CALLBACK_PATH}",
        callback_token=callback_token, callback_token_id=callback_token_id,
        run_id=run_id, attempt_id=attempt_id,
    )
