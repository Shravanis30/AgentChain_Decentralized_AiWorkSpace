import asyncio
import copy
from datetime import datetime
import json
import logging
from typing import Any, TypedDict
import uuid

import jsonschema
from langgraph.graph import END, StateGraph
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.core.metrics import metrics
from app.db.session import async_session_maker
from app.models.agent import (
    Agent,
    AgentExecution,
    AgentStatus,
    AgentVersion,
    ExecutionStatus,
)
from app.models.base import utc_now
from app.models.orchestration import (
    Artifact,
    Orchestration,
    OrchestrationCheckpoint,
    OrchestrationStatus,
    OrchestrationTask,
    OrchestrationTaskStatus,
)
from app.models.user import UserRole
from app.services.agent_execution import AgentExecutionService
from app.services.agent_selector import DeterministicAgentSelector
from app.services.dag_validator import DAGValidationError, DAGValidator
from app.services.hashing import canonicalize_json, compute_canonical_hash
from app.services.queue import publish_orchestration_event, signal_cancellation
from app.services.rate_limiter import get_redis_client
from app.schemas.orchestration import TaskDefinitionInput

logger = logging.getLogger("agentchain.orchestrator")

RETRYABLE_ERROR_CODES = {
    "WORKER_TIMEOUT",
    "TIMED_OUT",
    "TIMEOUT",
    "INFRASTRUCTURE_FAILURE",
    "TRANSIENT_FAILURE",
    "SANDBOX_CONTAINER_ERROR",
    "LEASE_EXPIRED",
    "QUEUE_FAILURE",
}

NON_RETRYABLE_ERROR_CODES = {
    "INVALID_INPUT",
    "SCHEMA_VALIDATION_ERROR",
    "SCHEMA_ERROR",
    "PERMISSION_DENIED",
    "UNAUTHORIZED",
    "AGENT_VERSION_NOT_FOUND",
    "UNPUBLISHED_AGENT",
    "EMPTY_INPUT",
}


class OrchestrationState(TypedDict):
    orchestration_id: str
    user_id: str
    goal: str
    graph_version: str
    task_states: dict[str, dict[str, Any]]
    ready_tasks: list[str]
    running_tasks: list[str]
    completed_tasks: list[str]
    failed_tasks: list[str]
    artifacts: list[dict[str, Any]]
    final_result: dict[str, Any] | None
    error: dict[str, Any] | None
    status: str
    iteration: int


class OrchestrationEngine:
    """
    LangGraph Multi-Agent Orchestrator Service.
    Decomposes user goals into a DAG of tasks, binds them to immutable agent versions,
    and schedules execution strictly through the AgentExecutionService -> Outbox -> Redis -> Worker runtime.
    LangGraph never directly executes an Agent SDK implementation.
    """

    def __init__(self) -> None:
        self.agent_selector = DeterministicAgentSelector()
        self._compiled_graph = self._build_graph()

    def _build_graph(self) -> Any:
        workflow = StateGraph(OrchestrationState)
        workflow.add_node("schedule_tasks", self._node_schedule_tasks)
        workflow.add_node("collect_results", self._node_collect_results)
        workflow.add_node("synthesize_and_complete", self._node_synthesize_and_complete)
        workflow.add_node("fail_orchestration", self._node_fail_orchestration)

        workflow.set_entry_point("schedule_tasks")

        workflow.add_conditional_edges(
            "schedule_tasks",
            self._route_after_schedule,
            {
                "collect_results": "collect_results",
                "synthesize_and_complete": "synthesize_and_complete",
                "fail_orchestration": "fail_orchestration",
            },
        )

        workflow.add_conditional_edges(
            "collect_results",
            self._route_after_collect,
            {
                "schedule_tasks": "schedule_tasks",
                "synthesize_and_complete": "synthesize_and_complete",
                "fail_orchestration": "fail_orchestration",
                "end": END,
            },
        )

        workflow.add_edge("synthesize_and_complete", END)
        workflow.add_edge("fail_orchestration", END)

        return workflow.compile()

    # -------------------------------------------------------------------------
    # Creation & Planning
    # -------------------------------------------------------------------------

    async def create_orchestration(
        self,
        db: AsyncSession,
        user_id: uuid.UUID,
        goal: str,
        tasks: list[TaskDefinitionInput] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Orchestration:
        """
        Creates, plans, and persists an orchestration and its initial DAG tasks.
        Enforces user quotas, DAG validity, and deterministic agent resolution.
        """
        # 1. Enforce user orchestration limit
        user_orch_count_res = await db.execute(
            select(func.count(Orchestration.id)).where(
                Orchestration.requested_by == user_id,
                Orchestration.status.in_([
                    OrchestrationStatus.PLANNING.value,
                    OrchestrationStatus.READY.value,
                    OrchestrationStatus.RUNNING.value,
                ]),
            )
        )
        active_count = user_orch_count_res.scalar_one()
        if active_count >= settings.MAX_ORCHESTRATIONS_PER_USER:
            raise DAGValidationError(
                f"User has {active_count} active orchestrations, exceeding limit of {settings.MAX_ORCHESTRATIONS_PER_USER}"
            )

        # 2. Plan default 3-stage vertical slice if tasks omitted
        if not tasks:
            tasks = [
                TaskDefinitionInput(
                    task_key="research",
                    capability="text_summarization",
                    input={"text": goal},
                    depends_on=[],
                ),
                TaskDefinitionInput(
                    task_key="analysis",
                    capability="analysis",
                    input={"text": goal},
                    depends_on=["research"],
                ),
                TaskDefinitionInput(
                    task_key="synthesis",
                    capability="synthesis",
                    input={"text": goal},
                    depends_on=["analysis"],
                ),
            ]

        # 3. Validate DAG (cycles, size, depth limits)
        topological_order = DAGValidator.validate_dag(tasks)
        task_dict = {t.task_key: t for t in tasks}

        # 4. Create Orchestration DB record
        now = utc_now()
        orchestration = Orchestration(
            requested_by=user_id,
            goal=goal,
            status=OrchestrationStatus.READY.value,
            graph_version="1.0.0",
            created_at=now,
            metadata_json=metadata or {},
        )
        db.add(orchestration)
        await db.flush()

        # 5. Resolve immutable agent references and persist tasks
        for task_key in topological_order:
            task_input = task_dict[task_key]
            selection_result = await self.agent_selector.select_agent(
                db, task_input, orchestration_id=orchestration.id, attempt_number=1
            )
            agent_ref = selection_result.agent_ref

            # Enforce input payload bytes limit
            raw_input_bytes = len(json.dumps(task_input.input).encode("utf-8"))
            if raw_input_bytes > settings.MAX_TASK_INPUT_BYTES:
                metrics.inc_counter("orchestration_graph_limit_rejection_total")
                raise DAGValidationError(
                    f"Task '{task_key}' input payload size ({raw_input_bytes} bytes) "
                    f"exceeds limit ({settings.MAX_TASK_INPUT_BYTES} bytes)"
                )

            input_hash = compute_canonical_hash(task_input.input)
            initial_status = (
                OrchestrationTaskStatus.READY.value
                if not task_input.depends_on
                else OrchestrationTaskStatus.PENDING.value
            )

            orch_task = OrchestrationTask(
                orchestration_id=orchestration.id,
                task_key=task_key,
                agent_id=agent_ref.agent_id,
                agent_version=agent_ref.agent_version,
                price_atomic=agent_ref.price_atomic,
                price_currency=agent_ref.price_currency,
                input_data=task_input.input,
                input_hash=input_hash,
                status=initial_status,
                dependencies=task_input.depends_on,
                max_attempts=task_input.max_attempts,
                attempt=1,
                created_at=now,
            )
            db.add(orch_task)
            await db.flush()
            
            decision = selection_result.decision
            decision.task_id = orch_task.id
            db.add(decision)
            
            metrics.inc_counter("orchestration_task_created_total")

        await db.commit()
        metrics.inc_counter("orchestration_created_total")

        # Initial checkpoint
        await self._save_initial_checkpoint(db, orchestration.id, user_id, goal, tasks)

        logger.info(
            f"Created orchestration {orchestration.id} with {len(tasks)} tasks for user {user_id}"
        )
        return await self.get_orchestration(db, orchestration.id)

    async def get_orchestration(
        self,
        db: AsyncSession,
        orchestration_id: uuid.UUID,
    ) -> Orchestration | None:
        query = (
            select(Orchestration)
            .options(
                selectinload(Orchestration.tasks),
                selectinload(Orchestration.artifacts),
                selectinload(Orchestration.checkpoints),
            )
            .where(Orchestration.id == orchestration_id)
        )
        res = await db.execute(query)
        return res.scalar_one_or_none()

    # -------------------------------------------------------------------------
    # Checkpointing & State Persistence
    # -------------------------------------------------------------------------

    async def _save_initial_checkpoint(
        self,
        db: AsyncSession,
        orchestration_id: uuid.UUID,
        user_id: uuid.UUID,
        goal: str,
        tasks: list[TaskDefinitionInput],
    ) -> None:
        task_states = {
            t.task_key: {
                "task_key": t.task_key,
                "status": "READY" if not t.depends_on else "PENDING",
                "dependencies": t.depends_on,
                "attempt": 1,
                "max_attempts": t.max_attempts,
                "execution_id": None,
                "input_hash": compute_canonical_hash(t.input),
                "output_hash": None,
                "output_data": None,
                "error_code": None,
                "error_message": None,
            }
            for t in tasks
        }
        state: OrchestrationState = {
            "orchestration_id": str(orchestration_id),
            "user_id": str(user_id),
            "goal": goal,
            "graph_version": "1.0.0",
            "task_states": task_states,
            "ready_tasks": [k for k, v in task_states.items() if v["status"] == "READY"],
            "running_tasks": [],
            "completed_tasks": [],
            "failed_tasks": [],
            "artifacts": [],
            "final_result": None,
            "error": None,
            "status": OrchestrationStatus.READY.value,
            "iteration": 0,
        }
        await self._persist_checkpoint(db, state)

    async def _persist_checkpoint(
        self,
        db: AsyncSession,
        state: OrchestrationState,
    ) -> OrchestrationCheckpoint:
        orchestration_id = uuid.UUID(state["orchestration_id"])
        checkpoint_id = f"ckpt_{uuid.uuid4()}"
        graph_version = state.get("graph_version", "1.0.0")

        # Check bounded size
        state_json_str = canonicalize_json(state)
        state_bytes = len(state_json_str.encode("utf-8"))
        if state_bytes > settings.MAX_CHECKPOINT_BYTES:
            metrics.inc_counter("orchestration_checkpoint_failure_total")
            raise ValueError(
                f"Checkpoint payload size ({state_bytes} bytes) exceeds limit ({settings.MAX_CHECKPOINT_BYTES} bytes)"
            )

        state_hash = compute_canonical_hash(state)
        checkpoint = OrchestrationCheckpoint(
            orchestration_id=orchestration_id,
            checkpoint_id=checkpoint_id,
            graph_version=graph_version,
            state_json=state,
            state_hash=state_hash,
            created_at=utc_now(),
        )
        db.add(checkpoint)
        try:
            await db.commit()
            metrics.inc_counter("orchestration_checkpoint_save_total")
            return checkpoint
        except Exception as err:
            await db.rollback()
            metrics.inc_counter("orchestration_checkpoint_failure_total")
            logger.error(f"Failed to persist checkpoint for orchestration {orchestration_id}: {err}")
            raise

    async def restore_checkpoint(
        self,
        db: AsyncSession,
        orchestration_id: uuid.UUID,
    ) -> OrchestrationState | None:
        query = (
            select(OrchestrationCheckpoint)
            .where(OrchestrationCheckpoint.orchestration_id == orchestration_id)
            .order_by(OrchestrationCheckpoint.created_at.desc())
        )
        res = await db.execute(query)
        ckpt = res.scalars().first()
        if not ckpt:
            return None

        # Verify cryptographic state hash
        computed_hash = compute_canonical_hash(ckpt.state_json)
        if computed_hash != ckpt.state_hash:
            metrics.inc_counter("orchestration_checkpoint_failure_total")
            raise ValueError(
                f"Corrupt checkpoint detected for orchestration {orchestration_id}: hash mismatch"
            )

        metrics.inc_counter("orchestration_checkpoint_restore_total")
        return ckpt.state_json  # type: ignore[return-value]

    # -------------------------------------------------------------------------
    # Execution Lifecycle & Graph Stepping
    # -------------------------------------------------------------------------

    async def start_orchestration(
        self,
        db: AsyncSession,
        orchestration_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> Orchestration:
        orchestration = await self.get_orchestration(db, orchestration_id)
        if not orchestration:
            raise ValueError(f"Orchestration '{orchestration_id}' not found")

        if orchestration.status not in (
            OrchestrationStatus.READY.value,
            OrchestrationStatus.PLANNING.value,
        ):
            raise ValueError(
                f"Orchestration '{orchestration_id}' is already in status '{orchestration.status}'"
            )

        orchestration.status = OrchestrationStatus.RUNNING.value
        orchestration.started_at = utc_now()
        await db.commit()
        metrics.inc_counter("orchestration_started_total")

        # Emit SSE Event
        redis = get_redis_client()
        await publish_orchestration_event(
            redis=redis,
            orchestration_id=str(orchestration_id),
            event_type="ORCHESTRATION_STARTED",
            sequence=1,
            payload={
                "orchestration_id": str(orchestration_id),
                "goal": orchestration.goal,
                "status": "RUNNING",
                "timestamp": orchestration.started_at.isoformat(),
            },
        )

        return orchestration

    async def step(self, state: OrchestrationState) -> OrchestrationState:
        """
        Executes a single progression step of the LangGraph state machine.
        """
        return await self._compiled_graph.ainvoke(state)

    async def advance_orchestration(
        self,
        orchestration_id: uuid.UUID,
        correlation_id: str | None = None,
    ) -> OrchestrationState | None:
        """
        Authoritatively advances the orchestration by a single workflow step.
        Guarantees:
        - PostgreSQL transaction-scoped advisory lock (pg_try_advisory_xact_lock)
        - Concurrency protection: If another worker/orchestrator is currently advancing,
          fails fast without waiting, incrementing orchestration_concurrent_advance_conflict_total.
        - Reloads authoritative state from PostgreSQL.
        - Invokes LangGraph StateGraph step (collects results, schedules unblocked tasks).
        - Releases lock automatically on commit or rollback.
        """
        async with async_session_maker() as db:
            # 1. Acquire PostgreSQL transaction-scoped advisory lock
            lock_query = text("SELECT pg_try_advisory_xact_lock(hashtext(:lock_key))")
            lock_key = f"orch_advance_{orchestration_id}"
            res = await db.execute(lock_query, {"lock_key": lock_key})
            acquired = res.scalar()
            if not acquired:
                metrics.inc_counter("orchestration_concurrent_advance_conflict_total")
                logger.info(
                    f"Concurrent advancement lock conflict for orchestration {orchestration_id}; another worker is advancing.",
                    extra={
                        "orchestration_id": str(orchestration_id),
                        "correlation_id": correlation_id,
                        "event_type": "CONCURRENT_ADVANCEMENT_CONFLICT",
                    },
                )
                return None

            # 2. Check current status from DB
            orch_res = await db.execute(
                select(Orchestration)
                .options(selectinload(Orchestration.tasks))
                .where(Orchestration.id == orchestration_id)
            )
            orch = orch_res.scalar_one_or_none()
            if not orch:
                logger.warning(f"Orchestration {orchestration_id} not found for advancement")
                return None

            terminal_statuses = (
                OrchestrationStatus.SUCCEEDED.value,
                OrchestrationStatus.FAILED.value,
                OrchestrationStatus.CANCELLED.value,
            )
            if orch.status in terminal_statuses:
                metrics.inc_counter("orchestration_wakeup_duplicate_total")
                return await self.restore_checkpoint(db, orchestration_id)

            # 3. Restore state
            state = await self.restore_checkpoint(db, orchestration_id)
            if not state:
                tasks = [
                    TaskDefinitionInput(
                        task_key=t.task_key,
                        agent_id=t.agent_id,
                        agent_version=t.agent_version,
                        input=t.input_data,
                        depends_on=t.dependencies,
                        max_attempts=t.max_attempts,
                    )
                    for t in orch.tasks
                ]
                await self._save_initial_checkpoint(db, orch.id, orch.requested_by, orch.goal, tasks)
                state = await self.restore_checkpoint(db, orchestration_id)

            if not state:
                logger.error(f"Failed to restore state for orchestration {orchestration_id}")
                return None

            # 4. Invoke LangGraph step
            try:
                new_state = await self._compiled_graph.ainvoke(state)
                await db.commit()
                return new_state
            except Exception as e:
                metrics.inc_counter("orchestration_wakeup_failure_total")
                logger.error(
                    f"Error advancing orchestration {orchestration_id}: {e}",
                    extra={
                        "orchestration_id": str(orchestration_id),
                        "correlation_id": correlation_id,
                        "error": str(e),
                    },
                )
                raise

    async def handle_execution_completed_wakeup(
        self,
        orchestration_id: uuid.UUID,
        execution_id: uuid.UUID,
        task_id: uuid.UUID | None = None,
        status: str | None = None,
        correlation_id: str | None = None,
        attempt: int | None = None,
        wakeup_event_id: str | None = None,
        delivery_attempt: int | None = None,
    ) -> OrchestrationState | None:
        """
        Idempotent wake-up entrypoint triggered when an AgentExecution completes.
        - Increments orchestration_wakeup_total.
        - Emits structured log with correlation_id, task_id, execution_id, attempt, wakeup_event_id, delivery_attempt.
        - Re-reads authoritative state from PostgreSQL via advance_orchestration().
        - If concurrent lock conflict, returns None safely.
        """
        metrics.inc_counter("orchestration_wakeup_total")
        logger.info(
            f"Orchestration {orchestration_id} wake-up event received for execution {execution_id} ({status})",
            extra={
                "orchestration_id": str(orchestration_id),
                "execution_id": str(execution_id),
                "task_id": str(task_id) if task_id else None,
                "status": status,
                "attempt": attempt,
                "wakeup_event_id": wakeup_event_id,
                "delivery_attempt": delivery_attempt,
                "correlation_id": correlation_id,
                "event_type": "AGENT_EXECUTION_COMPLETED",
            },
        )
        return await self.advance_orchestration(orchestration_id, correlation_id=correlation_id)

    async def run_until_complete(
        self,
        orchestration_id: uuid.UUID,
        timeout_seconds: float = 60.0,
        poll_interval: float = 0.5,
    ) -> OrchestrationState:
        """
        Runs the orchestration loop until it reaches terminal state (SUCCEEDED, FAILED, CANCELLED)
        or times out. Uses advance_orchestration() to ensure authoritative state locking and idempotency.
        """
        start_time = asyncio.get_event_loop().time()
        last_state = None

        while True:
            if asyncio.get_event_loop().time() - start_time > timeout_seconds:
                raise TimeoutError(f"Orchestration {orchestration_id} timed out after {timeout_seconds}s")

            state = await self.advance_orchestration(orchestration_id)
            if state is not None:
                last_state = state
                if state.get("status") in (
                    OrchestrationStatus.SUCCEEDED.value,
                    OrchestrationStatus.FAILED.value,
                    OrchestrationStatus.CANCELLED.value,
                ):
                    return state

            await asyncio.sleep(poll_interval)

        return last_state

    # -------------------------------------------------------------------------
    # LangGraph Nodes
    # -------------------------------------------------------------------------

    async def _node_schedule_tasks(self, state: OrchestrationState) -> dict[str, Any]:
        """
        Inspects DAG dependencies. Identifies tasks ready for execution,
        respects MAX_CONCURRENT_TASKS_PER_ORCHESTRATION, and enqueues child AgentExecutions
        via the Phase 4.1 execution infrastructure.
        """
        async with async_session_maker() as db:
            orch_id = uuid.UUID(state["orchestration_id"])
            user_id = uuid.UUID(state["user_id"])

            orch_tasks_res = await db.execute(
                select(OrchestrationTask).where(OrchestrationTask.orchestration_id == orch_id)
            )
            db_tasks = {t.task_key: t for t in orch_tasks_res.scalars().all()}

            completed_set = set(state.get("completed_tasks", []))
            running_tasks = list(state.get("running_tasks", []))
            task_states = copy.deepcopy(state.get("task_states", {}))
            artifacts_map = {a["producer_task_id"]: a for a in state.get("artifacts", [])}

            # Find all tasks eligible to run
            candidates: list[str] = []
            for task_key, t_info in task_states.items():
                if t_info["status"] in ("PENDING", "READY"):
                    deps = t_info.get("dependencies", [])
                    # All dependencies must be in completed_tasks
                    if all(d in completed_set for d in deps):
                        candidates.append(task_key)

            # Respect bounded concurrency
            capacity = max(0, settings.MAX_CONCURRENT_TASKS_PER_ORCHESTRATION - len(running_tasks))
            to_schedule = candidates[:capacity]

            redis = get_redis_client()
            for task_key in to_schedule:
                db_task = db_tasks.get(task_key)
                if not db_task:
                    continue

                t_info = task_states[task_key]
                attempt = db_task.attempt
                # Logical task key: orch-{orchestration_id}-task-{task_id}
                logical_task_key = f"orch-{orch_id}-task-{db_task.id}"
                # Execution attempt idempotency key: orch-{orchestration_id}-task-{task_id}-attempt-{attempt}
                execution_attempt_idempotency_key = f"orch-{orch_id}-task-{db_task.id}-attempt-{attempt}"

                # Prepare input with upstream outputs/artifacts if dependent
                augmented_input = dict(db_task.input_data)
                for dep in db_task.dependencies:
                    dep_state = task_states.get(dep, {})
                    if dep_state.get("output_data"):
                        dep_out = dep_state["output_data"]
                        # Inject summary/key outputs for vertical slice agents
                        if "summary" in dep_out and "text" in augmented_input:
                            augmented_input["summary"] = dep_out["summary"]
                            augmented_input["research_summary"] = dep_out["summary"]
                        if "key_points" in dep_out:
                            augmented_input["analysis"] = dep_out
                        augmented_input[f"upstream_{dep}"] = dep_out

                # Idempotent execution check for THIS attempt
                existing_exec = None
                if db_task.execution_id:
                    curr_exec = await AgentExecutionService.get_execution_by_id(
                        db=db,
                        execution_id=db_task.execution_id,
                    )
                    if curr_exec and curr_exec.idempotency_key == execution_attempt_idempotency_key:
                        existing_exec = curr_exec

                if not existing_exec:
                    existing_exec = await AgentExecutionService.get_execution_by_idempotency_key(
                        db=db,
                        user_id=user_id,
                        idempotency_key=execution_attempt_idempotency_key,
                    )

                if existing_exec:
                    metrics.inc_counter("orchestration_execution_attempt_duplicate_total")
                    metrics.inc_counter("orchestration_duplicate_schedule_total")
                    logger.info(
                        f"Reusing existing execution {existing_exec.id} for task {task_key} attempt {attempt} (idempotent)",
                        extra={
                            "orchestration_id": str(orch_id),
                            "task_id": str(db_task.id),
                            "execution_id": str(existing_exec.id),
                            "attempt": attempt,
                            "logical_task_key": logical_task_key,
                            "event_type": "DUPLICATE_ATTEMPT_SCHEDULE",
                        },
                    )
                    execution = existing_exec
                else:
                    # Enqueue child execution through transactional outbox & Redis Streams
                    execution = await AgentExecutionService.enqueue_agent_execution(
                        db=db,
                        agent_id=db_task.agent_id,
                        requested_by_user_id=user_id,
                        input_data=augmented_input,
                        idempotency_key=execution_attempt_idempotency_key,
                        agent_version=db_task.agent_version,
                        orchestration_id=orch_id,
                        task_id=db_task.id,
                        task_attempt=attempt,
                    )
                    metrics.inc_counter("orchestration_task_started_total")
                    logger.info(
                        f"Dispatched child execution {execution.id} for task {task_key} attempt {attempt}",
                        extra={
                            "orchestration_id": str(orch_id),
                            "task_id": str(db_task.id),
                            "execution_id": str(execution.id),
                            "attempt": attempt,
                            "logical_task_key": logical_task_key,
                            "event_type": "CHILD_EXECUTION_DISPATCHED",
                        },
                    )

                # Update DB task record
                db_task.status = OrchestrationTaskStatus.RUNNING.value
                db_task.execution_id = execution.id
                db_task.started_at = db_task.started_at or utc_now()
                await db.commit()

                # Update in-memory state
                running_tasks.append(task_key)
                t_info["status"] = "RUNNING"
                t_info["execution_id"] = str(execution.id)

                # Broadcast SSE events
                await publish_orchestration_event(
                    redis=redis,
                    orchestration_id=str(orch_id),
                    event_type="TASK_READY",
                    sequence=1,
                    payload={"task_key": task_key, "attempt": attempt},
                )
                await publish_orchestration_event(
                    redis=redis,
                    orchestration_id=str(orch_id),
                    event_type="TASK_STARTED",
                    sequence=2,
                    payload={
                        "task_key": task_key,
                        "execution_id": str(execution.id),
                        "agent_id": str(db_task.agent_id),
                        "agent_version": db_task.agent_version,
                    },
                )

            metrics.set_gauge("orchestration_active_tasks", len(running_tasks))

            new_state = dict(state)
            new_state["running_tasks"] = running_tasks
            new_state["task_states"] = task_states
            new_state["iteration"] = state.get("iteration", 0) + 1

            # Persist checkpoint after schedule
            await self._persist_checkpoint(db, new_state)  # type: ignore[arg-type]
            return {
                "running_tasks": running_tasks,
                "task_states": task_states,
                "iteration": new_state["iteration"],
            }

    async def _node_collect_results(self, state: OrchestrationState) -> dict[str, Any]:
        """
        Observes child execution records in PostgreSQL.
        Validates outputs against schemas, computes output hashes, creates artifacts,
        and applies orchestration retry/failure policy.
        """
        async with async_session_maker() as db:
            orch_id = uuid.UUID(state["orchestration_id"])
            running_tasks = list(state.get("running_tasks", []))
            completed_tasks = list(state.get("completed_tasks", []))
            failed_tasks = list(state.get("failed_tasks", []))
            task_states = dict(state.get("task_states", {}))
            artifacts = list(state.get("artifacts", []))

            still_running: list[str] = []
            redis = get_redis_client()

            for task_key in running_tasks:
                t_info = task_states[task_key]
                execution_id_str = t_info.get("execution_id")
                if not execution_id_str:
                    still_running.append(task_key)
                    continue

                exec_id = uuid.UUID(execution_id_str)
                # Fetch child execution
                query = (
                    select(AgentExecution)
                    .options(selectinload(AgentExecution.version))
                    .where(AgentExecution.id == exec_id)
                )
                res = await db.execute(query)
                execution = res.scalar_one_or_none()

                if not execution:
                    still_running.append(task_key)
                    continue

                # Fetch task DB record
                t_query = select(OrchestrationTask).where(
                    OrchestrationTask.orchestration_id == orch_id,
                    OrchestrationTask.task_key == task_key,
                )
                t_res = await db.execute(t_query)
                db_task = t_res.scalar_one()

                # Case A: SUCCEEDED
                if execution.status == ExecutionStatus.SUCCEEDED.value:
                    output_data = execution.output_data or {}

                    # Validate output schema
                    if execution.version and execution.version.output_schema:
                        try:
                            jsonschema.validate(
                                instance=output_data, schema=execution.version.output_schema
                            )
                        except jsonschema.exceptions.ValidationError as e:
                            logger.error(f"Task {task_key} output schema validation failed: {e.message}")
                            db_task.status = OrchestrationTaskStatus.FAILED.value
                            db_task.error_code = "SCHEMA_VALIDATION_ERROR"
                            db_task.error_message = f"Output schema validation failed: {e.message}"
                            await db.commit()

                            t_info["status"] = "FAILED"
                            t_info["error_code"] = "SCHEMA_VALIDATION_ERROR"
                            failed_tasks.append(task_key)
                            metrics.inc_counter("orchestration_task_failed_total")
                            continue

                    output_hash = compute_canonical_hash(output_data)
                    db_task.status = OrchestrationTaskStatus.SUCCEEDED.value
                    db_task.output_data = output_data
                    db_task.output_hash = output_hash
                    db_task.completed_at = utc_now()

                    # Create and persist Artifact
                    raw_art_bytes = len(json.dumps(output_data).encode("utf-8"))
                    artifact = Artifact(
                        orchestration_id=orch_id,
                        task_id=db_task.id,
                        producer_agent_id=db_task.agent_id,
                        producer_agent_version=db_task.agent_version,
                        execution_id=execution.id,
                        artifact_key=f"{task_key}_output",
                        content_type="application/json",
                        schema_version="1.0",
                        sha256=output_hash,
                        size_bytes=raw_art_bytes,
                        content_json=output_data,
                        created_at=utc_now(),
                    )
                    db.add(artifact)
                    await db.commit()

                    completed_tasks.append(task_key)
                    t_info["status"] = "SUCCEEDED"
                    t_info["output_data"] = output_data
                    t_info["output_hash"] = output_hash

                    artifact_entry = {
                        "artifact_id": str(artifact.id),
                        "producer_task_id": task_key,
                        "artifact_key": artifact.artifact_key,
                        "content_type": "application/json",
                        "schema_version": "1.0",
                        "sha256": output_hash,
                        "size_bytes": raw_art_bytes,
                        "content": output_data,
                    }
                    artifacts.append(artifact_entry)
                    metrics.inc_counter("orchestration_task_succeeded_total")

                    await publish_orchestration_event(
                        redis=redis,
                        orchestration_id=str(orch_id),
                        event_type="TASK_SUCCEEDED",
                        sequence=3,
                        payload={
                            "task_key": task_key,
                            "execution_id": str(execution.id),
                            "output_hash": output_hash,
                        },
                    )

                # Case B: FAILED or TIMED_OUT
                elif execution.status in (
                    ExecutionStatus.FAILED.value,
                    ExecutionStatus.TIMED_OUT.value,
                ):
                    error_code = execution.error_code or "EXECUTION_FAILED"
                    error_msg = execution.error_message or "Execution failed"

                    # Determine retryability
                    is_retryable = (
                        execution.status == ExecutionStatus.TIMED_OUT.value
                        or error_code in RETRYABLE_ERROR_CODES
                        or (
                            error_code not in NON_RETRYABLE_ERROR_CODES
                            and "schema" not in error_msg.lower()
                            and "validation" not in error_msg.lower()
                        )
                    )

                    if is_retryable and db_task.attempt < db_task.max_attempts:
                        # Schedule retry
                        prev_attempt = db_task.attempt
                        db_task.attempt += 1
                        db_task.status = OrchestrationTaskStatus.READY.value
                        db_task.execution_id = None
                        await db.commit()

                        t_info["attempt"] = db_task.attempt
                        t_info["status"] = "READY"
                        t_info["execution_id"] = None
                        metrics.inc_counter("orchestration_task_retry_total")
                        logger.info(
                            f"Task {task_key} attempt {prev_attempt} failed retryably ({error_code}); scheduled retry attempt {db_task.attempt}/{db_task.max_attempts}",
                            extra={
                                "orchestration_id": str(orch_id),
                                "task_id": str(db_task.id),
                                "execution_id": str(execution.id),
                                "attempt": db_task.attempt,
                                "event_type": "TASK_RETRYING",
                            },
                        )

                        await publish_orchestration_event(
                            redis=redis,
                            orchestration_id=str(orch_id),
                            event_type="TASK_RETRYING",
                            sequence=4,
                            payload={
                                "task_key": task_key,
                                "attempt": db_task.attempt,
                                "max_attempts": db_task.max_attempts,
                                "reason": error_msg,
                            },
                        )
                    else:
                        # Permanent failure
                        db_task.status = OrchestrationTaskStatus.FAILED.value
                        db_task.error_code = error_code
                        db_task.error_message = error_msg
                        db_task.completed_at = utc_now()
                        await db.commit()

                        failed_tasks.append(task_key)
                        t_info["status"] = "FAILED"
                        t_info["error_code"] = error_code
                        t_info["error_message"] = error_msg
                        metrics.inc_counter("orchestration_task_failed_total")

                        await publish_orchestration_event(
                            redis=redis,
                            orchestration_id=str(orch_id),
                            event_type="TASK_FAILED",
                            sequence=5,
                            payload={
                                "task_key": task_key,
                                "error_code": error_code,
                                "error_message": error_msg,
                            },
                        )

                # Case C: CANCELLED
                elif execution.status == ExecutionStatus.CANCELLED.value:
                    db_task.status = OrchestrationTaskStatus.CANCELLED.value
                    db_task.completed_at = utc_now()
                    await db.commit()

                    t_info["status"] = "CANCELLED"
                    failed_tasks.append(task_key)
                else:
                    # Still queued or running
                    still_running.append(task_key)

            metrics.set_gauge("orchestration_active_tasks", len(still_running))

            new_state = dict(state)
            new_state["running_tasks"] = still_running
            new_state["completed_tasks"] = completed_tasks
            new_state["failed_tasks"] = failed_tasks
            new_state["task_states"] = task_states
            new_state["artifacts"] = artifacts

            await self._persist_checkpoint(db, new_state)  # type: ignore[arg-type]
            return {
                "running_tasks": still_running,
                "completed_tasks": completed_tasks,
                "failed_tasks": failed_tasks,
                "task_states": task_states,
                "artifacts": artifacts,
            }

    async def _node_synthesize_and_complete(
        self, state: OrchestrationState
    ) -> dict[str, Any]:
        """
        Assembles final result and marks Orchestration as SUCCEEDED.
        """
        async with async_session_maker() as db:
            orch_id = uuid.UUID(state["orchestration_id"])
            query = select(Orchestration).where(Orchestration.id == orch_id)
            res = await db.execute(query)
            orch = res.scalar_one()

            now = utc_now()
            orch.status = OrchestrationStatus.SUCCEEDED.value
            orch.completed_at = now

            # Assemble structured final result
            final_result: dict[str, Any] = {
                "goal": orch.goal,
                "completed_tasks": state.get("completed_tasks", []),
                "task_results": {
                    k: v.get("output_data")
                    for k, v in state.get("task_states", {}).items()
                    if v.get("output_data")
                },
                "artifacts_count": len(state.get("artifacts", [])),
                "summary": "Multi-agent orchestration completed successfully.",
            }
            orch.result = final_result
            await db.commit()

            metrics.inc_counter("orchestration_succeeded_total")
            metrics.set_gauge("orchestration_active_tasks", 0)

            # Observe duration
            if orch.started_at:
                duration = (now - orch.started_at).total_seconds()
                metrics.observe_histogram("orchestration_duration_seconds", duration)

            redis = get_redis_client()
            await publish_orchestration_event(
                redis=redis,
                orchestration_id=str(orch_id),
                event_type="ORCHESTRATION_SUCCEEDED",
                sequence=10,
                payload={
                    "orchestration_id": str(orch_id),
                    "status": "SUCCEEDED",
                    "result": final_result,
                },
            )

            new_state = dict(state)
            new_state["status"] = OrchestrationStatus.SUCCEEDED.value
            new_state["final_result"] = final_result
            await self._persist_checkpoint(db, new_state)  # type: ignore[arg-type]
            return {
                "status": OrchestrationStatus.SUCCEEDED.value,
                "final_result": final_result,
            }

    async def _node_fail_orchestration(
        self, state: OrchestrationState
    ) -> dict[str, Any]:
        """
        Marks Orchestration as FAILED and cancels/skips pending tasks.
        """
        async with async_session_maker() as db:
            orch_id = uuid.UUID(state["orchestration_id"])
            query = select(Orchestration).where(Orchestration.id == orch_id)
            res = await db.execute(query)
            orch = res.scalar_one()

            now = utc_now()
            orch.status = OrchestrationStatus.FAILED.value
            orch.failed_at = now

            failed_task_keys = state.get("failed_tasks", [])
            first_fail = (
                state["task_states"].get(failed_task_keys[0], {})
                if failed_task_keys
                else {}
            )
            orch.error_code = first_fail.get("error_code", "ORCHESTRATION_TASK_FAILED")
            orch.error_message = first_fail.get(
                "error_message", "One or more critical DAG tasks failed."
            )

            # Mark any pending tasks as SKIPPED
            t_res = await db.execute(
                select(OrchestrationTask).where(
                    OrchestrationTask.orchestration_id == orch_id,
                    OrchestrationTask.status.in_([
                        OrchestrationTaskStatus.PENDING.value,
                        OrchestrationTaskStatus.READY.value,
                    ]),
                )
            )
            for t in t_res.scalars().all():
                t.status = OrchestrationTaskStatus.SKIPPED.value
            await db.commit()

            metrics.inc_counter("orchestration_failed_total")
            metrics.set_gauge("orchestration_active_tasks", 0)

            redis = get_redis_client()
            await publish_orchestration_event(
                redis=redis,
                orchestration_id=str(orch_id),
                event_type="ORCHESTRATION_FAILED",
                sequence=11,
                payload={
                    "orchestration_id": str(orch_id),
                    "status": "FAILED",
                    "error_code": orch.error_code,
                    "error_message": orch.error_message,
                },
            )

            new_state = dict(state)
            new_state["status"] = OrchestrationStatus.FAILED.value
            new_state["error"] = {
                "error_code": orch.error_code,
                "error_message": orch.error_message,
            }
            await self._persist_checkpoint(db, new_state)  # type: ignore[arg-type]
            return {
                "status": OrchestrationStatus.FAILED.value,
                "error": new_state["error"],
            }

    # -------------------------------------------------------------------------
    # Graph Routers
    # -------------------------------------------------------------------------

    def _route_after_schedule(self, state: OrchestrationState) -> str:
        if state.get("failed_tasks"):
            return "fail_orchestration"

        all_tasks = set(state.get("task_states", {}).keys())
        completed = set(state.get("completed_tasks", []))
        if all_tasks and all_tasks == completed:
            return "synthesize_and_complete"

        return "collect_results"

    def _route_after_collect(self, state: OrchestrationState) -> str:
        if state.get("failed_tasks"):
            return "fail_orchestration"

        all_tasks = set(state.get("task_states", {}).keys())
        completed = set(state.get("completed_tasks", []))
        if all_tasks and all_tasks == completed:
            return "synthesize_and_complete"

        running = state.get("running_tasks", [])

        # Can only schedule more tasks if concurrent capacity is available
        if len(running) < settings.MAX_CONCURRENT_TASKS_PER_ORCHESTRATION:
            for task_key, t_info in state.get("task_states", {}).items():
                if t_info["status"] in ("PENDING", "READY"):
                    deps = t_info.get("dependencies", [])
                    if all(d in completed for d in deps):
                        return "schedule_tasks"

        if running:
            # Tasks are currently executing in workers; pause graph and yield to END
            return "end"

        # If nothing running, nothing ready, but not all completed -> deadlock/failure
        return "fail_orchestration"

    # -------------------------------------------------------------------------
    # Cancellation
    # -------------------------------------------------------------------------

    async def cancel_orchestration(
        self,
        db: AsyncSession,
        orchestration_id: uuid.UUID,
        user_id: uuid.UUID,
        user_role: str,
        reason: str | None = None,
    ) -> Orchestration:
        """
        Cancels an active orchestration and propagates cancellation down to all
        pending and running child executions.
        """
        orch = await self.get_orchestration(db, orchestration_id)
        if not orch:
            raise ValueError(f"Orchestration '{orchestration_id}' not found")

        is_requester = orch.requested_by == user_id
        is_admin = user_role in (UserRole.ADMIN.value, UserRole.ADMIN)
        if not (is_requester or is_admin):
            raise PermissionError("You are not authorized to cancel this orchestration")

        if orch.status in (
            OrchestrationStatus.SUCCEEDED.value,
            OrchestrationStatus.FAILED.value,
            OrchestrationStatus.CANCELLED.value,
        ):
            raise ValueError(f"Orchestration is already terminal: {orch.status}")

        now = utc_now()
        orch.status = OrchestrationStatus.CANCELLED.value
        orch.cancelled_at = now
        orch.error_message = reason or "Cancellation requested by user"

        redis = get_redis_client()
        # Propagate to tasks
        for task in orch.tasks:
            if task.status in (
                OrchestrationTaskStatus.PENDING.value,
                OrchestrationTaskStatus.READY.value,
            ):
                task.status = OrchestrationTaskStatus.CANCELLED.value
                task.completed_at = now
            elif task.status == OrchestrationTaskStatus.RUNNING.value:
                task.status = OrchestrationTaskStatus.CANCELLED.value
                task.completed_at = now
                if task.execution_id:
                    # Signal cancellation to worker via Redis & update AgentExecution
                    try:
                        await signal_cancellation(redis, str(task.execution_id))
                        await AgentExecutionService.cancel_execution(
                            db=db,
                            execution_id=task.execution_id,
                            user_id=user_id,
                            user_role=user_role,
                            reason=reason or "Orchestration cancelled",
                        )
                    except Exception as err:
                        logger.warning(
                            f"Cancellation propagation to child execution {task.execution_id} warning: {err}"
                        )

        await db.commit()
        metrics.inc_counter("orchestration_cancelled_total")

        # Broadcast SSE
        await publish_orchestration_event(
            redis=redis,
            orchestration_id=str(orchestration_id),
            event_type="ORCHESTRATION_CANCELLED",
            sequence=12,
            payload={
                "orchestration_id": str(orchestration_id),
                "status": "CANCELLED",
                "reason": orch.error_message,
            },
        )

        return orch


# Global singleton engine
orchestration_engine = OrchestrationEngine()
