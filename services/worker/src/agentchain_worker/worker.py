import asyncio
import json
import logging
import time
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from agentchain_worker.config import WorkerConfig, worker_config
from agentchain_worker.heartbeat import WorkerHeartbeatService
from agentchain_worker.retry import calculate_backoff_delay, is_transient_error
from agentchain_worker.sandbox.base import SandboxConfig, SandboxResult, SandboxRunner
from agentchain_worker.sandbox.docker_runner import DockerSandboxRunner
from agentchain_worker.sandbox.local_runner import LocalProcessSandboxRunner
from app.core.metrics import metrics
from app.db.session import async_session_factory
from app.models.agent import Agent, AgentExecution, AgentStatus, ExecutionStatus
from app.models.audit import AuditEventType
from app.models.base import utc_now
from app.services.audit import AuditService
from app.services.hashing import compute_canonical_hash
from app.services.queue import (
    DEFAULT_QUEUE_NAME,
    ExecutionQueue,
    QueueJob,
    get_execution_queue,
    is_cancellation_requested,
    publish_execution_event,
    publish_orchestration_wakeup,
)
from app.services.orchestration_recovery import OrchestrationWakeupDispatcher
from app.services.rate_limiter import get_redis_client
from app.services.state_machine import ExecutionStateMachine, StaleLeaseConflictError

logger = logging.getLogger("agentchain.worker")


class AgentExecutionWorker:
    def __init__(
        self,
        config: WorkerConfig | None = None,
        queue: ExecutionQueue | None = None,
        sandbox_runner: SandboxRunner | None = None,
    ):
        self.config = config or worker_config
        self.queue = queue or get_execution_queue()
        self.worker_id = self.config.worker_id
        self.heartbeat_service = WorkerHeartbeatService(
            worker_id=self.worker_id,
            version=self.config.version,
            interval_seconds=self.config.heartbeat_interval_seconds,
        )

        if sandbox_runner:
            self.sandbox_runner = sandbox_runner
        elif self.config.sandbox_type == "local":
            self.sandbox_runner = LocalProcessSandboxRunner()
        else:
            self.sandbox_runner = DockerSandboxRunner(
                image=self.config.sandbox_image,
                docker_host=self.config.docker_host,
                fallback_to_local_if_no_docker=True,
            )

        self._stopping = False
        self._stale_task: asyncio.Task[None] | None = None
        self._outbox_task: asyncio.Task[None] | None = None
        self._worker_tasks: list[asyncio.Task[None]] = []

    async def start(self) -> None:
        """Start worker background loops and heartbeat."""
        logger.info(f"Starting AgentChain Worker {self.worker_id} (concurrency={self.config.concurrency})")
        self._stopping = False
        await self.heartbeat_service.start()

        # Start periodic stale execution recovery sweeper
        self._stale_task = asyncio.create_task(self._stale_recovery_loop())

        # Start transactional outbox dispatch sweeper
        self._outbox_task = asyncio.create_task(self._outbox_dispatch_loop())

        # Start consumer tasks
        for idx in range(self.config.concurrency):
            t = asyncio.create_task(self._consumer_loop(consumer_index=idx))
            self._worker_tasks.append(t)

    async def stop(self) -> None:
        """Gracefully stop worker tasks and drain executions."""
        logger.info(f"Stopping worker {self.worker_id}...")
        self._stopping = True
        self.heartbeat_service.set_status("DRAINING")

        if self._stale_task and not self._stale_task.done():
            self._stale_task.cancel()
            try:
                await self._stale_task
            except asyncio.CancelledError:
                pass

        if self._outbox_task and not self._outbox_task.done():
            self._outbox_task.cancel()
            try:
                await self._outbox_task
            except asyncio.CancelledError:
                pass

        for t in self._worker_tasks:
            t.cancel()

        await asyncio.gather(*self._worker_tasks, return_exceptions=True)
        self._worker_tasks.clear()

        await self.heartbeat_service.stop()
        logger.info(f"Worker {self.worker_id} successfully stopped")

    async def _outbox_dispatch_loop(self) -> None:
        """Periodically sweeps and publishes pending outbox records to Redis Streams."""
        from app.services.outbox import OutboxDispatcher
        while not self._stopping:
            try:
                await asyncio.sleep(2.0)
                async with async_session_factory() as db:
                    await OutboxDispatcher.dispatch_pending(db=db, limit=50, queue=self.queue)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(f"Error in outbox dispatch loop: {e}")

    async def _consumer_loop(self, consumer_index: int) -> None:
        """Continuously claim and process jobs from queue."""
        while not self._stopping:
            try:
                jobs = await self.queue.claim_execution(
                    worker_id=f"{self.worker_id}-{consumer_index}",
                    queue_name=self.config.queue_name,
                    batch_size=1,
                    block_ms=self.config.poll_block_ms,
                )

                if not jobs:
                    await asyncio.sleep(0.1)
                    continue

                for job in jobs:
                    if self._stopping:
                        break
                    await self.process_job(job)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Unexpected error in consumer loop: {e}", exc_info=True)
                await asyncio.sleep(1.0)

    async def _stale_recovery_loop(self) -> None:
        """Periodically recovers executions abandoned by crashed/dead workers."""
        while not self._stopping:
            try:
                await asyncio.sleep(self.config.stale_check_interval_seconds)
                stale_jobs = await self.queue.reclaim_stale_executions(
                    worker_id=self.worker_id,
                    queue_name=self.config.queue_name,
                    stale_threshold_seconds=self.config.stale_execution_threshold_seconds,
                    batch_size=5,
                )
                if stale_jobs:
                    logger.info(f"Reclaimed {len(stale_jobs)} stale executions from crashed workers")
                    for job in stale_jobs:
                        asyncio.create_task(self.process_job(job))
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(f"Error in stale execution recovery loop: {e}")

    async def process_job(self, job: QueueJob) -> bool:
        """
        Process a single claimed execution job with full lifecycle,
        state machine transitions, isolation, timeout enforcement,
        metrics, audit events, and controlled retries.
        """
        start_time = time.perf_counter()
        execution_id = job.execution_id
        redis = get_redis_client()
        metrics.inc_counter("executions_total")
        self.heartbeat_service.increment_active()
        metrics.set_gauge("worker_active_executions", self.heartbeat_service._active_executions)

        logger.info(
            f"Processing job execution_id={execution_id} agent_id={job.agent_id} "
            f"attempt={job.attempt_number} worker_id={self.worker_id} "
            f"lease_owner={self.worker_id} correlation_id={job.correlation_id}"
        )

        try:
            # 1. Check if execution was already cancelled while queued
            if await is_cancellation_requested(redis, execution_id):
                logger.info(f"Execution {execution_id} was cancelled before worker pickup. Finalizing cancellation.")
                async with async_session_factory() as db:
                    execution = await db.get(AgentExecution, uuid.UUID(execution_id))
                    if execution and execution.status != ExecutionStatus.CANCELLED.value:
                        await ExecutionStateMachine.transition_to_cancelled(db, execution, reason="Cancelled in queue")
                        await db.commit()
                if job.message_id:
                    await self.queue.ack_execution(job.message_id, job.queue_name)
                metrics.inc_counter("executions_cancelled")
                return True

            # 2. Fetch authoritative execution & agent from DB (NEVER trust queue payload alone)
            async with async_session_factory() as db:
                query = (
                    select(AgentExecution)
                    .options(
                        selectinload(AgentExecution.agent).selectinload(Agent.current_version),
                        selectinload(AgentExecution.version),
                    )
                    .where(AgentExecution.id == uuid.UUID(execution_id))
                )
                res = await db.execute(query)
                execution = res.scalar_one_or_none()

                if not execution:
                    logger.error(f"Execution record {execution_id} not found in database. Discarding job.")
                    if job.message_id:
                        await self.queue.ack_execution(job.message_id, job.queue_name)
                    return False

                # At-least-once duplicate check: if already terminal, do not rerun or overwrite
                terminal_statuses = {
                    ExecutionStatus.SUCCEEDED.value,
                    ExecutionStatus.FAILED.value,
                    ExecutionStatus.TIMED_OUT.value,
                    ExecutionStatus.CANCELLED.value,
                }
                if execution.status in terminal_statuses:
                    logger.info(
                        f"Execution {execution_id} is already in terminal state ({execution.status}). "
                        f"Skipping duplicate execution delivery. worker_id={self.worker_id}"
                    )
                    metrics.inc_counter("execution_duplicate_delivery_total")
                    if job.message_id:
                        await self.queue.ack_execution(job.message_id, job.queue_name)
                    return True

                agent = execution.agent
                version = execution.version

                if agent.status != AgentStatus.PUBLISHED.value:
                    logger.error(f"Agent {agent.id} is {agent.status} (not PUBLISHED). Failing execution.")
                    await ExecutionStateMachine.transition_to_failed(
                        db=db,
                        execution=execution,
                        error_code="AGENT_NOT_PUBLISHED",
                        error_message=f"Agent '{agent.name}' is in status '{agent.status}'",
                        worker_id=self.worker_id,
                    )
                    await db.commit()
                    if job.message_id:
                        await self.queue.ack_execution(job.message_id, job.queue_name)
                    metrics.inc_counter("executions_failed")
                    return False

                # 3. Transition: QUEUED -> RUNNING (acquire lease)
                timeout_sec = version.runtime_config.get("timeout_seconds", 300)
                try:
                    await ExecutionStateMachine.transition_to_running(
                        db=db,
                        execution=execution,
                        worker_id=self.worker_id,
                        timeout_seconds=int(timeout_sec),
                        lease_duration_seconds=int(self.config.stale_execution_threshold_seconds),
                    )
                except StaleLeaseConflictError as err:
                    logger.warning(
                        f"Execution {execution_id} lease acquisition conflict: {err}. "
                        f"worker_id={self.worker_id}"
                    )
                    return False

                await AuditService.record_event(
                    db=db,
                    event_type=AuditEventType.AGENT_EXECUTION_STARTED,
                    user_id=execution.requested_by,
                    metadata={
                        "execution_id": execution_id,
                        "worker_id": self.worker_id,
                        "attempt": execution.attempt_count,
                    },
                )
                await db.commit()

            # 4. Publish SSE event: started
            await publish_execution_event(
                redis=redis,
                execution_id=execution_id,
                event_type="started",
                sequence=2,
                payload={
                    "status": "RUNNING",
                    "execution_id": execution_id,
                    "worker_id": self.worker_id,
                    "started_at": utc_now().isoformat(),
                },
            )

            # 5. Build SandboxConfig and execution context
            runtime_conf = version.runtime_config or {}
            sandbox_cfg = SandboxConfig(
                cpu_limit=float(runtime_conf.get("cpu_limit", 1.0)),
                memory_limit_mb=int(runtime_conf.get("memory_mb", 256)),
                timeout_seconds=int(timeout_sec),
                pids_limit=int(runtime_conf.get("pids_limit", 64)),
                read_only_root=True,
                tmpfs_size_mb=int(runtime_conf.get("tmpfs_size_mb", 64)),
                network_mode=runtime_conf.get("network_mode", "none"),
                user="1000:1000",
                env_allowlist={
                    "AGENT_EXECUTION_ID": execution_id,
                    "AGENT_ID": str(agent.id),
                    "AGENT_VERSION": version.version,
                },
            )

            context_dict = {
                "execution_id": execution_id,
                "agent_id": str(agent.id),
                "agent_version": version.version,
                "timeout_seconds": timeout_sec,
            }

            # 6. Execute inside isolated sandbox with active lease keepalive
            keepalive_active = True
            async def _lease_keepalive():
                while keepalive_active:
                    try:
                        await asyncio.sleep(max(3.0, self.config.stale_execution_threshold_seconds / 2.0))
                        async with async_session_factory() as keepalive_db:
                            row = await keepalive_db.get(AgentExecution, uuid.UUID(execution_id))
                            if row and row.status == ExecutionStatus.RUNNING.value and row.lease_owner == self.worker_id:
                                await ExecutionStateMachine.extend_lease(
                                    keepalive_db,
                                    row,
                                    worker_id=self.worker_id,
                                    extension_seconds=int(self.config.stale_execution_threshold_seconds),
                                )
                                await keepalive_db.commit()
                    except asyncio.CancelledError:
                        break
                    except Exception as e:
                        logger.debug(f"Lease keepalive tick: {e}")

            keepalive_task = asyncio.create_task(_lease_keepalive())
            try:
                sandbox_res: SandboxResult = await self.sandbox_runner.run_agent(
                    agent_manifest=version.manifest,
                    input_data=execution.input_data,
                    context=context_dict,
                    config=sandbox_cfg,
                )
            finally:
                keepalive_active = False
                keepalive_task.cancel()
                try:
                    await keepalive_task
                except asyncio.CancelledError:
                    pass

            # 7. Check if cancellation arrived during execution
            if sandbox_res.cancelled or await is_cancellation_requested(redis, execution_id):
                logger.info(f"Execution {execution_id} was cancelled during runtime. Finalizing cancellation.")
                async with async_session_factory() as db:
                    execution = await db.get(AgentExecution, uuid.UUID(execution_id))
                    if execution:
                        await ExecutionStateMachine.transition_to_cancelled(db, execution, reason="Cancelled during run")
                        await db.commit()
                if job.message_id:
                    await self.queue.ack_execution(job.message_id, job.queue_name)
                metrics.inc_counter("executions_cancelled")
                return True

            duration_ms = int((time.perf_counter() - start_time) * 1000)

            # 8. Handle Execution Outcome with Lease Validation
            if sandbox_res.timed_out or sandbox_res.error_code == "EXECUTION_TIMEOUT":
                logger.warning(f"Execution {execution_id} timed out after {timeout_sec}s")
                async with async_session_factory() as db:
                    execution = await db.get(AgentExecution, uuid.UUID(execution_id))
                    if execution:
                        await ExecutionStateMachine.transition_to_timed_out(
                            db, execution, timeout_seconds=int(timeout_sec), worker_id=self.worker_id
                        )
                        await AuditService.record_event(
                            db=db,
                            event_type=AuditEventType.AGENT_EXECUTION_TIMED_OUT,
                            user_id=execution.requested_by,
                            metadata={"execution_id": execution_id, "timeout_seconds": timeout_sec},
                        )
                        orch_id = str(execution.orchestration_id) if execution.orchestration_id else None
                        task_id = str(execution.orchestration_task_id) if execution.orchestration_task_id else None
                        await db.commit()

                        if orch_id:
                            try:
                                await OrchestrationWakeupDispatcher.dispatch_entry_by_execution_id(
                                    execution_id=uuid.UUID(execution_id),
                                    redis=redis,
                                )
                            except Exception as disp_err:
                                logger.warning(f"Failed to dispatch orchestration wakeup outbox for {execution_id}: {disp_err}")
                            try:
                                await publish_orchestration_wakeup(
                                    redis=redis,
                                    orchestration_id=orch_id,
                                    execution_id=execution_id,
                                    task_id=task_id,
                                    status="TIMED_OUT",
                                )
                            except Exception as wake_err:
                                logger.warning(f"Failed to publish orchestration wakeup for {execution_id}: {wake_err}")

                await publish_execution_event(
                    redis=redis,
                    execution_id=execution_id,
                    event_type="timeout",
                    sequence=10,
                    payload={"status": "TIMED_OUT", "execution_id": execution_id, "timeout_seconds": timeout_sec},
                )
                if job.message_id:
                    await self.queue.ack_execution(job.message_id, job.queue_name)
                metrics.inc_counter("executions_timed_out")
                return True

            elif sandbox_res.success and sandbox_res.output is not None:
                # Execution SUCCEEDED
                output_hash = compute_canonical_hash(sandbox_res.output)
                async with async_session_factory() as db:
                    execution = await db.get(AgentExecution, uuid.UUID(execution_id))
                    if execution:
                        await ExecutionStateMachine.transition_to_succeeded(
                            db=db,
                            execution=execution,
                            output_data=sandbox_res.output,
                            output_hash=output_hash,
                            execution_time_ms=sandbox_res.execution_time_ms or duration_ms,
                            worker_id=self.worker_id,
                            metadata={
                                "worker_id": self.worker_id,
                                "sandbox_runner": type(self.sandbox_runner).__name__,
                            },
                        )
                        await AuditService.record_event(
                            db=db,
                            event_type=AuditEventType.AGENT_EXECUTION_SUCCEEDED,
                            user_id=execution.requested_by,
                            metadata={
                                "execution_id": execution_id,
                                "output_hash": output_hash,
                                "execution_time_ms": sandbox_res.execution_time_ms or duration_ms,
                            },
                        )
                        orch_id = str(execution.orchestration_id) if execution.orchestration_id else None
                        task_id = str(execution.orchestration_task_id) if execution.orchestration_task_id else None
                        await db.commit()

                        if orch_id:
                            try:
                                await OrchestrationWakeupDispatcher.dispatch_entry_by_execution_id(
                                    execution_id=uuid.UUID(execution_id),
                                    redis=redis,
                                )
                            except Exception as disp_err:
                                logger.warning(f"Failed to dispatch orchestration wakeup outbox for {execution_id}: {disp_err}")
                            try:
                                await publish_orchestration_wakeup(
                                    redis=redis,
                                    orchestration_id=orch_id,
                                    execution_id=execution_id,
                                    task_id=task_id,
                                    status="SUCCEEDED",
                                )
                            except Exception as wake_err:
                                logger.warning(f"Failed to publish orchestration wakeup for {execution_id}: {wake_err}")

                await publish_execution_event(
                    redis=redis,
                    execution_id=execution_id,
                    event_type="completed",
                    sequence=10,
                    payload={
                        "status": "SUCCEEDED",
                        "execution_id": execution_id,
                        "output": sandbox_res.output,
                        "output_hash": output_hash,
                        "execution_time_ms": sandbox_res.execution_time_ms or duration_ms,
                    },
                )
                if job.message_id:
                    await self.queue.ack_execution(job.message_id, job.queue_name)
                metrics.inc_counter("executions_succeeded")
                metrics.observe_histogram("execution_duration_seconds", duration_ms / 1000.0)
                logger.info(
                    f"Execution {execution_id} SUCCEEDED in {duration_ms}ms "
                    f"worker_id={self.worker_id} lease_owner={self.worker_id}"
                )
                return True

            else:
                # Execution FAILED - evaluate retry policy
                error_code = sandbox_res.error_code or "AGENT_EXECUTION_ERROR"
                error_msg = sandbox_res.error_message or "Agent execution failed in sandbox"
                is_transient = is_transient_error(error_code)

                async with async_session_factory() as db:
                    execution = await db.get(AgentExecution, uuid.UUID(execution_id))
                    if not execution:
                        return False

                    max_retries = execution.max_attempts or 3
                    should_retry = is_transient and (execution.attempt_count <= max_retries)

                    if should_retry:
                        delay = calculate_backoff_delay(attempt=execution.attempt_count)
                        logger.warning(
                            f"Execution {execution_id} transient failure ({error_code}). "
                            f"Requeueing attempt {execution.attempt_count + 1}/{max_retries} with delay={delay}s"
                        )
                        await ExecutionStateMachine.transition_to_requeued(
                            db=db,
                            execution=execution,
                            reason=f"Transient failure: {error_code}",
                        )
                        await db.commit()

                        await publish_execution_event(
                            redis=redis,
                            execution_id=execution_id,
                            event_type="progress",
                            sequence=5,
                            payload={
                                "status": "QUEUED",
                                "execution_id": execution_id,
                                "retry_attempt": execution.attempt_count,
                                "delay_seconds": delay,
                                "reason": error_msg,
                            },
                        )

                        await self.queue.requeue_execution(job=job, delay_seconds=delay, max_attempts=max_retries)
                        return True
                    else:
                        # Non-retryable permanent failure or attempts exhausted
                        logger.error(
                            f"Execution {execution_id} permanent failure ({error_code}): {error_msg}"
                        )
                        await ExecutionStateMachine.transition_to_failed(
                            db=db,
                            execution=execution,
                            error_code=error_code,
                            error_message=error_msg,
                            worker_id=self.worker_id,
                        )
                        await AuditService.record_event(
                            db=db,
                            event_type=AuditEventType.AGENT_EXECUTION_FAILED,
                            user_id=execution.requested_by,
                            metadata={
                                "execution_id": execution_id,
                                "error_code": error_code,
                                "error_message": error_msg,
                            },
                        )
                        orch_id = str(execution.orchestration_id) if execution.orchestration_id else None
                        task_id = str(execution.orchestration_task_id) if execution.orchestration_task_id else None
                        await db.commit()

                        if orch_id:
                            try:
                                await OrchestrationWakeupDispatcher.dispatch_entry_by_execution_id(
                                    execution_id=uuid.UUID(execution_id),
                                    redis=redis,
                                )
                            except Exception as disp_err:
                                logger.warning(f"Failed to dispatch orchestration wakeup outbox for {execution_id}: {disp_err}")
                            try:
                                await publish_orchestration_wakeup(
                                    redis=redis,
                                    orchestration_id=orch_id,
                                    execution_id=execution_id,
                                    task_id=task_id,
                                    status="FAILED",
                                )
                            except Exception as wake_err:
                                logger.warning(f"Failed to publish orchestration wakeup for {execution_id}: {wake_err}")

                        await publish_execution_event(
                            redis=redis,
                            execution_id=execution_id,
                            event_type="failed",
                            sequence=10,
                            payload={
                                "status": "FAILED",
                                "execution_id": execution_id,
                                "error_code": error_code,
                                "error_message": error_msg,
                            },
                        )

                        if job.message_id:
                            if execution.attempt_count >= max_retries:
                                await self.queue.dead_letter_execution(job, reason="MAX_RETRIES_EXCEEDED")
                            else:
                                await self.queue.ack_execution(job.message_id, job.queue_name)

                        metrics.inc_counter("executions_failed")
                        return False

        except StaleLeaseConflictError as err:
            logger.warning(
                f"Execution {execution_id} lease lost to another worker: {err}. "
                f"worker_id={self.worker_id}"
            )
            if job.message_id:
                try:
                    await self.queue.ack_execution(job.message_id, job.queue_name)
                except Exception:
                    pass
            return False

        except Exception as exc:
            logger.error(f"Critical unhandled exception processing job {execution_id}: {exc}", exc_info=True)
            async with async_session_factory() as db:
                try:
                    execution = await db.get(AgentExecution, uuid.UUID(execution_id))
                    if execution and execution.status == ExecutionStatus.RUNNING.value:
                        await ExecutionStateMachine.transition_to_failed(
                            db=db,
                            execution=execution,
                            error_code="WORKER_INTERNAL_ERROR",
                            error_message=str(exc),
                            worker_id=self.worker_id,
                        )
                        await db.commit()
                except Exception:
                    pass

            if job.message_id:
                try:
                    await self.queue.ack_execution(job.message_id, job.queue_name)
                except Exception:
                    pass

            metrics.inc_counter("executions_failed")
            return False

        finally:
            self.heartbeat_service.decrement_active()
            metrics.set_gauge("worker_active_executions", self.heartbeat_service._active_executions)
