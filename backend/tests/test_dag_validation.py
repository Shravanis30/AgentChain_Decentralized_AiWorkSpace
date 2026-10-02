import pytest

from app.core.config import settings
from app.schemas.orchestration import TaskDefinitionInput
from app.services.dag_validator import DAGValidationError, DAGValidator


def test_valid_linear_dag():
    tasks = [
        TaskDefinitionInput(task_key="task_a", depends_on=[]),
        TaskDefinitionInput(task_key="task_b", depends_on=["task_a"]),
        TaskDefinitionInput(task_key="task_c", depends_on=["task_b"]),
    ]
    order = DAGValidator.validate_dag(tasks)
    assert order == ["task_a", "task_b", "task_c"]


def test_valid_diamond_dag():
    tasks = [
        TaskDefinitionInput(task_key="root", depends_on=[]),
        TaskDefinitionInput(task_key="left", depends_on=["root"]),
        TaskDefinitionInput(task_key="right", depends_on=["root"]),
        TaskDefinitionInput(task_key="join", depends_on=["left", "right"]),
    ]
    order = DAGValidator.validate_dag(tasks)
    assert order[0] == "root"
    assert "left" in order[1:3]
    assert "right" in order[1:3]
    assert order[3] == "join"


def test_empty_dag_rejected():
    with pytest.raises(DAGValidationError, match="at least one task"):
        DAGValidator.validate_dag([])


def test_duplicate_task_keys_rejected():
    tasks = [
        TaskDefinitionInput(task_key="duplicate", depends_on=[]),
        TaskDefinitionInput(task_key="duplicate", depends_on=[]),
    ]
    with pytest.raises(DAGValidationError, match="Duplicate task_key"):
        DAGValidator.validate_dag(tasks)


def test_self_dependency_cycle_rejected():
    tasks = [
        TaskDefinitionInput(task_key="task_a", depends_on=["task_a"]),
    ]
    with pytest.raises(DAGValidationError, match="cannot depend on itself"):
        DAGValidator.validate_dag(tasks)


def test_missing_dependency_rejected():
    tasks = [
        TaskDefinitionInput(task_key="task_a", depends_on=["non_existent"]),
    ]
    with pytest.raises(DAGValidationError, match="non-existent task"):
        DAGValidator.validate_dag(tasks)


def test_cycle_detection_kahn():
    tasks = [
        TaskDefinitionInput(task_key="task_a", depends_on=["task_c"]),
        TaskDefinitionInput(task_key="task_b", depends_on=["task_a"]),
        TaskDefinitionInput(task_key="task_c", depends_on=["task_b"]),
    ]
    with pytest.raises(DAGValidationError, match="Cycle detected"):
        DAGValidator.validate_dag(tasks)


def test_max_graph_tasks_limit():
    tasks = [
        TaskDefinitionInput(task_key=f"task_{i}", depends_on=[])
        for i in range(settings.MAX_GRAPH_TASKS + 1)
    ]
    with pytest.raises(DAGValidationError, match="exceeding the maximum allowed limit"):
        DAGValidator.validate_dag(tasks)


def test_max_dependencies_per_task():
    roots = [TaskDefinitionInput(task_key=f"root_{i}", depends_on=[]) for i in range(6)]
    child = TaskDefinitionInput(task_key="child", depends_on=[f"root_{i}" for i in range(6)])
    with pytest.raises(DAGValidationError, match="exceeding max allowed"):
        DAGValidator.validate_dag(roots + [child])


def test_max_graph_depth_limit():
    # Chain that exceeds MAX_GRAPH_DEPTH
    tasks = [TaskDefinitionInput(task_key="t_0", depends_on=[])]
    for i in range(1, settings.MAX_GRAPH_DEPTH + 2):
        tasks.append(TaskDefinitionInput(task_key=f"t_{i}", depends_on=[f"t_{i-1}"]))

    with pytest.raises(DAGValidationError, match="DAG depth .* exceeds the maximum allowed limit"):
        DAGValidator.validate_dag(tasks)


def test_max_task_input_payload_size():
    huge_input = {"big": "a" * (settings.MAX_TASK_INPUT_BYTES + 100)}
    tasks = [
        TaskDefinitionInput(task_key="big_task", input=huge_input, depends_on=[]),
    ]
    with pytest.raises(DAGValidationError, match="input payload size .* exceeds limit"):
        DAGValidator.validate_dag(tasks)
