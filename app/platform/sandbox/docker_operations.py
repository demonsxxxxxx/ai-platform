"""Bounded, cancellation-aware thread ownership for blocking Docker operations.

Docker SDK requests cannot be cancelled by cancelling an asyncio waiter. Keep
admission charged to the actual worker future, and give cleanup its own lane.
The lifecycle coroutine runs on a private worker loop so *all* synchronous SDK
calls, including provider admission/readback helpers, stay off application loops.
"""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from concurrent.futures import Future, ThreadPoolExecutor
from contextvars import ContextVar, copy_context
from contextlib import contextmanager
from dataclasses import dataclass
from functools import wraps
import inspect
import threading
from typing import Any, TypeVar


_T = TypeVar("_T")


class DockerOperationUnavailable(RuntimeError):
    """The bounded Docker lane cannot admit or finish an operation in time."""


class _Cancellation:
    def __init__(self) -> None:
        self.cancelled = threading.Event()
        self.lock = threading.Lock()
        self.loop: asyncio.AbstractEventLoop | None = None
        self.task: asyncio.Task[Any] | None = None

    def cancel(self) -> None:
        self.cancelled.set()
        with self.lock:
            loop, task = self.loop, self.task
            if loop is not None and task is not None and not loop.is_closed():
                loop.call_soon_threadsafe(task.cancel)

    def checkpoint(self) -> None:
        if self.cancelled.is_set():
            raise asyncio.CancelledError


_current_cancellation: ContextVar[_Cancellation | None] = ContextVar(
    "docker_operation_cancellation", default=None,
)


def docker_operation_checkpoint() -> None:
    cancellation = _current_cancellation.get()
    if cancellation is not None:
        cancellation.checkpoint()


class DockerOperationLane:
    """No executor queue: a slot is held until the submitted callable finishes."""

    def __init__(self, *, capacity: int, name: str, claims: DockerOperationLane | None = None) -> None:
        self._slots = threading.BoundedSemaphore(capacity)
        self._executor = ThreadPoolExecutor(max_workers=capacity, thread_name_prefix=name)
        self._keys: dict[str, object] = {} if claims is None else claims._keys
        self._lock = threading.Lock() if claims is None else claims._lock
        self._accepted_completions: set[Future[Any]] = set()

    def _submit(self, operation: Callable[[], _T], *, key: str | None, owner: object | None = None) -> Future[_T]:
        with self._lock:
            if key is not None and key in self._keys:
                raise DockerOperationUnavailable("Docker operation is already in progress")
            if not self._slots.acquire(blocking=False):
                raise DockerOperationUnavailable("Docker operation capacity is exhausted")
            if key is not None:
                self._keys[key] = owner
        context = copy_context()
        try:
            future = self._executor.submit(context.run, operation)
        except BaseException:
            self._release(key, owner)
            raise
        future.add_done_callback(lambda _future: self._release(key, owner))
        return future

    @contextmanager
    def claim(self, key: str):
        """Own one run's inspection and effects, or skip a busy run atomically."""
        owner = object()
        with self._lock:
            acquired = key not in self._keys
            if acquired:
                self._keys[key] = owner
        try:
            yield acquired
        finally:
            if acquired:
                self._release_key(key, owner)

    def _release_key(self, key: str | None, owner: object | None) -> None:
        with self._lock:
            if key is not None and self._keys.get(key) is owner:
                self._keys.pop(key, None)

    def _release(self, key: str | None, owner: object | None) -> None:
        self._release_key(key, owner)
        self._slots.release()

    @staticmethod
    def _observe(future: asyncio.Future[Any]) -> None:
        # A detached operation can fail after the request has gone away. The
        # provider's cleanup owner retains reconciliation state; consume the
        # local Future exception rather than logging an unhandled task error.
        if not future.cancelled():
            future.exception()

    async def _settle_accepted_completions(self) -> None:
        # A completed lifecycle acknowledges its result before its worker
        # thread leaves the handoff. Drain only those no-more-effects tails so
        # immediate create -> validate does not see false capacity exhaustion.
        with self._lock:
            completed_operations = tuple(self._accepted_completions)
        for completed in completed_operations:
            await asyncio.shield(asyncio.wrap_future(completed))

    def _forget_accepted_completion(self, completed: Future[Any]) -> None:
        with self._lock:
            self._accepted_completions.discard(completed)

    async def run(self, operation: Callable[[], _T], *, timeout: float, key: str | None = None) -> _T:
        await self._settle_accepted_completions()
        future = asyncio.wrap_future(self._submit(operation, key=key, owner=object()))
        future.add_done_callback(self._observe)
        try:
            return await asyncio.wait_for(asyncio.shield(future), timeout=timeout)
        except TimeoutError as exc:
            raise DockerOperationUnavailable("Docker operation deadline exceeded") from exc

    async def run_lifecycle(
        self,
        operation: Callable[[], Awaitable[_T]],
        *,
        timeout: float | None,
        cleanup_timeout: float,
        key: str | None = None,
        on_cancel: Callable[[], None] | None = None,
        cleanup_errors: tuple[type[Exception], ...] = (),
    ) -> _T:
        await self._settle_accepted_completions()
        cancellation = _Cancellation()
        result: Future[_T] = Future()
        decision = threading.Event()
        accepted = threading.Event()

        def execute() -> None:
            async def invoke() -> _T:
                token = _current_cancellation.set(cancellation)
                started = False
                with cancellation.lock:
                    cancellation.loop = asyncio.get_running_loop()
                    cancellation.task = asyncio.current_task()
                try:
                    cancellation.checkpoint()
                    started = True
                    value = await operation()
                    cancellation.checkpoint()
                    return value
                except BaseException as exc:
                    if started and on_cancel is not None and (
                        isinstance(exc, asyncio.CancelledError) or cancellation.cancelled.is_set()
                    ):
                        on_cancel()
                    raise
                finally:
                    _current_cancellation.reset(token)
                    with cancellation.lock:
                        cancellation.loop = None
                        cancellation.task = None
            try:
                value = asyncio.run(invoke())
            except BaseException as exc:
                result.set_exception(exc)
                raise
            result.set_result(value)
            # Keep the keyed owner until the caller accepts the lease. A
            # cancellation racing successful create must still compensate.
            decision.wait()
            if not accepted.is_set() and on_cancel is not None:
                on_cancel()

        submitted = self._submit(execute, key=key, owner=cancellation)
        actual = asyncio.wrap_future(submitted)
        actual.add_done_callback(self._observe)
        future = asyncio.wrap_future(result)
        future.add_done_callback(self._observe)
        try:
            value = await asyncio.wait_for(asyncio.shield(future), timeout=timeout)
        except (asyncio.CancelledError, TimeoutError) as exc:
            cancellation.cancel()
            decision.set()
            # A stuck SDK call retains its actual worker slot and keyed owner.
            # Responsive cancellation preserves completed-compensation behavior.
            try:
                await asyncio.wait_for(asyncio.shield(actual), timeout=cleanup_timeout)
            except (asyncio.CancelledError, TimeoutError):
                pass
            except Exception as cleanup_exc:
                if isinstance(cleanup_exc, cleanup_errors):
                    raise
            if isinstance(exc, asyncio.CancelledError):
                raise
            raise DockerOperationUnavailable("Docker operation deadline exceeded") from exc
        else:
            with self._lock:
                self._accepted_completions.add(submitted)
            submitted.add_done_callback(self._forget_accepted_completion)
            accepted.set()
            # The lifecycle has completed and cannot perform any more effects.
            # Release its key now; worker capacity is released on actual exit.
            self._release_key(key, cancellation)
            decision.set()
            return value

    def close(self) -> None:
        """Stop admission without cancelling side effects already in progress."""
        self._executor.shutdown(wait=False, cancel_futures=False)


def docker_async_lifecycle(*, keyed: bool = False, cleanup: bool = False, compensate: str | None = None):
    """Keep the legacy provider's public async port outside blocking SDK work."""
    def decorate(operation):
        signature = inspect.signature(operation)
        first_argument = next(name for name in signature.parameters if name != "self")

        @wraps(operation)
        async def isolated(provider, *args, **kwargs):
            lane = provider._cleanup_operations if cleanup else provider._operations
            bound = signature.bind(provider, *args, **kwargs)
            key = str(bound.arguments[first_argument].run_id) if keyed else None
            on_cancel = None if compensate is None else lambda: getattr(provider, compensate)(*args, **kwargs)
            try:
                return await lane.run_lifecycle(
                    lambda: operation(provider, *args, **kwargs), key=key, on_cancel=on_cancel,
                    # SDK requests and readiness stages keep their original
                    # configured deadlines; do not shorten the aggregate flow.
                    timeout=None, cleanup_timeout=provider._sdk_timeout(),
                    cleanup_errors=(provider._cleanup_failure,),
                )
            except DockerOperationUnavailable as exc:
                raise provider._operation_unavailable("Docker operation is unavailable") from exc
        return isolated
    return decorate


def stop_and_remove_docker_container(container: Any) -> bool:
    stop_succeeded = not hasattr(container, "stop")
    if hasattr(container, "stop"):
        try:
            container.stop()
            stop_succeeded = True
        except Exception:
            pass
    remove_succeeded = not hasattr(container, "remove")
    if hasattr(container, "remove"):
        try:
            container.remove(force=True)
            remove_succeeded = True
        except Exception:
            pass
    return remove_succeeded or (stop_succeeded and not hasattr(container, "remove"))



@dataclass
class DockerOwnedResourceScope:
    """One cleanup owner for the exact per-lease bridge and its runtime pair."""

    provider: Any
    lease: Any
    primary: Any | None = None
    native: Any | None = None
    native_socket_owned: bool = False

    def abort(self) -> None:
        """Stop tracked owned containers, then detach and remove only the owned bridge."""
        self.provider._cleanup_runtime_pair_or_track(
            self.primary,
            self.native,
            self.lease,
            remove_native_socket=self.native_socket_owned,
        )



class DockerLeaseRegistry:
    """Thread-safe tracked lease observations, retaining exact-attempt fences."""

    @staticmethod
    def _same_tracked_lease(
        tracked: Any,
        expected: Any,
    ) -> bool:
        return (
            tracked.container_id == expected.container_id
            and tracked.tenant_id == expected.tenant_id
            and tracked.workspace_id == expected.workspace_id
            and tracked.user_id == expected.user_id
            and tracked.session_id == expected.session_id
            and tracked.run_id == expected.run_id
            and tracked.labels.get("ai-platform.attempt_id")
            == expected.labels.get("ai-platform.attempt_id")
        )

    def _remember_lease(self, lease: Any) -> None:
        with self._leases_lock:
            self._leases[lease.container_id] = lease

    def _remember_lease_if_absent(self, lease: Any) -> None:
        with self._leases_lock:
            self._leases.setdefault(lease.container_id, lease)

    def _forget_lease(self, lease: Any) -> None:
        with self._leases_lock:
            tracked = self._leases.get(lease.container_id)
            if tracked is not None and self._same_tracked_lease(tracked, lease):
                self._leases.pop(lease.container_id, None)

    def _cached_lease_for_run(self, run_id: str) -> Any | None:
        """Return the sole tracked Docker lease for a run, keyed by real container ID."""
        with self._leases_lock:
            return next(
                (lease for lease in self._leases.values() if lease.run_id == run_id),
                None,
            )


def cleanup_docker_orphan_resources(
    provider: Any, filters: dict[str, str], reason: str, *,
    container_status: Callable[[Any], Any],
    matches_filters: Callable[[Any, dict[str, str]], bool],
    scope_key: Callable[[Any], tuple[Any, ...]],
    network_lease: Callable[[Any], Any],
    lease_status: Callable[..., Any],
    network_name: Callable[[Any], str],
    stop_result: Callable[..., Any],
    normalize_error: Callable[[Exception], Exception | None],
) -> list[Any]:
    """Reconcile fresh exact-scope observations under the lifecycle run claim.

    The broad listings nominate candidates only. A startup may finish between
    listing and claim acquisition, so neither a stale container status nor the
    absence of its primary in that first list authorizes a deletion.
    """
    client = provider._get_client()
    try:
        containers = client.containers.list(all=True, filters={"label": ["ai-platform.owner"]})
    except Exception as exc:
        normalized = normalize_error(exc)
        if normalized is not None:
            raise normalized from exc
        raise
    results = []
    for candidate in containers:
        docker_operation_checkpoint()
        status = container_status(candidate)
        if status is None or not status.run_id or not matches_filters(status, filters):
            continue
        candidate_id = str(getattr(candidate, "id", "") or "")
        candidate_scope = scope_key(status)
        candidate_owner = status.detail.get("labels", {}).get("ai-platform.owner")
        with provider._cleanup_operations.claim(str(status.run_id)) as claimed:
            if not claimed:
                continue
            try:
                # Inspect by immutable Docker ID, then recheck the complete
                # ownership scope and current status inside the claim.
                current = client.containers.get(candidate_id)
                current_status = container_status(current)
                if (
                    not candidate_id
                    or str(getattr(current, "id", "")) != candidate_id
                    or current.attrs.get("Id") != candidate_id
                    or current_status is None
                    or scope_key(current_status) != candidate_scope
                    or current_status.detail.get("labels", {}).get("ai-platform.owner") != candidate_owner
                    or not matches_filters(current_status, filters)
                ):
                    continue
                if candidate_owner == "sandbox-native-tool":
                    if current_status.status in {"created", "running", "restarting"}:
                        peers = client.containers.list(all=True, filters={"label": ["ai-platform.owner=sandbox-runtime"]})
                        primary_statuses = (container_status(peer) for peer in peers)
                        if any(
                            primary is not None
                            and primary.detail.get("labels", {}).get("ai-platform.owner") == "sandbox-runtime"
                            and scope_key(primary) == candidate_scope
                            and primary.status in {"created", "running", "restarting"}
                            for primary in primary_statuses
                        ):
                            continue
                elif current_status.status not in {"exited", "dead", "removing", "removed"}:
                    continue
                docker_operation_checkpoint()
                if hasattr(current, "remove"):
                    current.remove(force=True)
            except Exception:
                results.append(stop_result(container_id=status.container_id, status="failed", message="Container cleanup failed"))
                continue
            results.append(stop_result(container_id=status.container_id, status="stopped", message=reason))
    try:
        networks = client.networks.list()
    except Exception:
        return results
    for network in networks:
        docker_operation_checkpoint()
        lease = network_lease(network)
        if lease is None or not matches_filters(lease_status(lease, status="removed"), filters):
            continue
        with provider._cleanup_operations.claim(str(lease.run_id)) as claimed:
            if not claimed:
                continue
            # This helper re-fetches the exact-owned network and reloads its
            # membership before API detachment/removal, all under the claim.
            if provider._remove_owned_governed_network(lease):
                results.append(stop_result(
                    container_id=f"network:{network_name(lease)}", status="stopped", message=reason,
                ))
    return results
