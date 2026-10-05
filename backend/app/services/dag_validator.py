from collections import defaultdict, deque
import json
import logging
from typing import Any

from app.core.config import settings
from app.core.metrics import metrics
from app.schemas.orchestration import TaskDefinitionInput

logger = logging.getLogger("agentchain.dag_validator")


class DAGValidationError(ValueError):
    """Raised when an orchestration task DAG is invalid, cyclic, or exceeds security limits."""
    pass


class DAGValidator:
    """
    Validates user-submitted or generated DAGs:
    - Unique task keys
    - Defined dependencies
    - No self-dependencies
    - No cycles (Kahn's algorithm)
    - Max graph size (tasks count)
    - Max dependency depth
    - Max dependencies per task
    - Max input payload size per task
    """

    @classmethod
    def validate_dag(cls, tasks: list[TaskDefinitionInput]) -> list[str]:
        """
        Validates the DAG and returns a topologically sorted list of task keys.
        Raises DAGValidationError if validation fails.
        """
        if not tasks:
            raise DAGValidationError("DAG must contain at least one task")

        # 1. Check max graph size
        if len(tasks) > settings.MAX_GRAPH_TASKS:
            metrics.inc_counter("orchestration_graph_limit_rejection_total")
            raise DAGValidationError(
                f"DAG contains {len(tasks)} tasks, exceeding the maximum allowed limit of {settings.MAX_GRAPH_TASKS}"
            )

        task_map: dict[str, TaskDefinitionInput] = {}
        # 2. Check unique task keys and input payload sizes
        for task in tasks:
            if task.task_key in task_map:
                raise DAGValidationError(f"Duplicate task_key '{task.task_key}' in DAG")
            task_map[task.task_key] = task

            # Check input bytes limit
            try:
                raw_bytes = len(json.dumps(task.input).encode("utf-8"))
                if raw_bytes > settings.MAX_TASK_INPUT_BYTES:
                    metrics.inc_counter("orchestration_graph_limit_rejection_total")
                    raise DAGValidationError(
                        f"Task '{task.task_key}' input payload size ({raw_bytes} bytes) "
                        f"exceeds limit ({settings.MAX_TASK_INPUT_BYTES} bytes)"
                    )
            except (TypeError, ValueError) as err:
                raise DAGValidationError(
                    f"Task '{task.task_key}' input is not valid JSON serializable: {err}"
                )

        # 3. Check dependencies existence and limits
        for task in tasks:
            if len(task.depends_on) > settings.MAX_DEPENDENCIES_PER_TASK:
                metrics.inc_counter("orchestration_graph_limit_rejection_total")
                raise DAGValidationError(
                    f"Task '{task.task_key}' has {len(task.depends_on)} dependencies, "
                    f"exceeding max allowed ({settings.MAX_DEPENDENCIES_PER_TASK})"
                )

            for dep in task.depends_on:
                if dep == task.task_key:
                    metrics.inc_counter("orchestration_cycle_rejection_total")
                    raise DAGValidationError(
                        f"Task '{task.task_key}' cannot depend on itself (self-cycle)"
                    )
                if dep not in task_map:
                    raise DAGValidationError(
                        f"Task '{task.task_key}' depends on non-existent task '{dep}'"
                    )

        # 4. Cycle detection and topological sort via Kahn's algorithm
        # in_degree: number of dependencies that must complete before this node can run
        in_degree: dict[str, int] = {t.task_key: len(t.depends_on) for t in tasks}
        # adj: maps dependency -> list of downstream tasks that depend on it
        adj: dict[str, list[str]] = defaultdict(list)
        for task in tasks:
            for dep in task.depends_on:
                adj[dep].append(task.task_key)

        # Roots: tasks with in_degree 0
        queue = deque([k for k, deg in in_degree.items() if deg == 0])
        topological_order: list[str] = []
        depth_map: dict[str, int] = {k: 1 for k in queue}

        while queue:
            curr = queue.popleft()
            topological_order.append(curr)
            curr_depth = depth_map[curr]

            for neighbor in adj[curr]:
                in_degree[neighbor] -= 1
                depth_map[neighbor] = max(depth_map.get(neighbor, 1), curr_depth + 1)

                if in_degree[neighbor] == 0:
                    queue.append(neighbor)

        # If topological_order does not include all tasks, a cycle exists
        if len(topological_order) != len(tasks):
            metrics.inc_counter("orchestration_cycle_rejection_total")
            raise DAGValidationError("Cycle detected in orchestration DAG")

        # 5. Check maximum dependency depth
        max_depth = max(depth_map.values()) if depth_map else 0
        if max_depth > settings.MAX_GRAPH_DEPTH:
            metrics.inc_counter("orchestration_graph_limit_rejection_total")
            raise DAGValidationError(
                f"DAG depth of {max_depth} exceeds the maximum allowed limit of {settings.MAX_GRAPH_DEPTH}"
            )

        return topological_order
