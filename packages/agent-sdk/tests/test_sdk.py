import asyncio
import uuid
import pytest

from agentchain_sdk import Agent, AgentContext, AgentError, AgentManifest, AgentResult


SAMPLE_MANIFEST_DICT = {
    "protocol_version": "1.0",
    "agent": {
        "id": "11111111-1111-1111-1111-111111111111",
        "name": "Echo Agent",
        "version": "1.0.0",
    },
    "description": "Test echo agent for SDK verification",
    "capabilities": ["echo"],
    "input_schema": {
        "type": "object",
        "required": ["message"],
        "properties": {
            "message": {"type": "string"},
            "count": {"type": "integer", "minimum": 1},
        },
    },
    "output_schema": {
        "type": "object",
        "required": ["reply"],
        "properties": {
            "reply": {"type": "string"},
            "repeated": {"type": "integer"},
        },
    },
    "runtime": {
        "timeout_seconds": 2,
        "max_retries": 1,
    },
    "pricing": {
        "model": "free",
    },
}


class EchoAgent(Agent):
    def __init__(self):
        super().__init__(AgentManifest(**SAMPLE_MANIFEST_DICT))

    async def run(self, context: AgentContext, input_data: dict) -> dict:
        msg = input_data["message"]
        cnt = input_data.get("count", 1)
        return {"reply": msg * cnt, "repeated": cnt}


class BadOutputAgent(Agent):
    def __init__(self):
        super().__init__(AgentManifest(**SAMPLE_MANIFEST_DICT))

    async def run(self, context: AgentContext, input_data: dict) -> dict:
        # Returns invalid output violating output_schema (missing "reply")
        return {"wrong_field": 42}


class SlowAgent(Agent):
    def __init__(self):
        super().__init__(AgentManifest(**SAMPLE_MANIFEST_DICT))

    async def run(self, context: AgentContext, input_data: dict) -> dict:
        # Exceeds 2-second timeout
        await asyncio.sleep(5)
        return {"reply": "done", "repeated": 1}


@pytest.mark.asyncio
async def test_sdk_valid_input_and_output_execution():
    agent = EchoAgent()
    exec_id = uuid.uuid4()
    context = AgentContext(
        execution_id=exec_id,
        agent_id=uuid.UUID(SAMPLE_MANIFEST_DICT["agent"]["id"]),
        agent_version="1.0.0",
        requested_by=uuid.uuid4(),
    )

    result = await agent.execute(context, {"message": "hello", "count": 2})

    assert isinstance(result, AgentResult)
    assert result.execution_id == str(exec_id)
    assert result.status == "SUCCEEDED"
    assert result.output["reply"] == "hellohello"
    assert result.output["repeated"] == 2


@pytest.mark.asyncio
async def test_sdk_invalid_input_rejection():
    agent = EchoAgent()
    context = AgentContext(
        execution_id=uuid.uuid4(),
        agent_id=uuid.UUID(SAMPLE_MANIFEST_DICT["agent"]["id"]),
        agent_version="1.0.0",
        requested_by=uuid.uuid4(),
    )

    # Missing required 'message'
    with pytest.raises(AgentError) as exc_info:
        await agent.execute(context, {"count": 2})

    assert exc_info.value.code == "INPUT_VALIDATION_ERROR"
    assert "validation error" in exc_info.value.message.lower()


@pytest.mark.asyncio
async def test_sdk_invalid_output_rejection():
    agent = BadOutputAgent()
    context = AgentContext(
        execution_id=uuid.uuid4(),
        agent_id=uuid.UUID(SAMPLE_MANIFEST_DICT["agent"]["id"]),
        agent_version="1.0.0",
        requested_by=uuid.uuid4(),
    )

    with pytest.raises(AgentError) as exc_info:
        await agent.execute(context, {"message": "hello"})

    assert exc_info.value.code == "OUTPUT_VALIDATION_ERROR"
    assert "output validation error" in exc_info.value.message.lower()


@pytest.mark.asyncio
async def test_sdk_execution_id_propagation():
    agent = EchoAgent()
    test_exec_id = uuid.uuid4()
    context = AgentContext(
        execution_id=test_exec_id,
        agent_id=uuid.UUID(SAMPLE_MANIFEST_DICT["agent"]["id"]),
        agent_version="1.0.0",
        requested_by=uuid.uuid4(),
    )

    result = await agent.execute(context, {"message": "ping"})
    assert result.execution_id == str(test_exec_id)


@pytest.mark.asyncio
async def test_sdk_structured_errors():
    error = AgentError(
        message="Custom runtime failure",
        code="RUNTIME_ERROR",
        details={"info": "diagnostic data"},
    )
    assert error.code == "RUNTIME_ERROR"
    assert str(error) == "Custom runtime failure"
    assert error.details["info"] == "diagnostic data"


@pytest.mark.asyncio
async def test_sdk_timeout_awareness():
    agent = SlowAgent()
    context = AgentContext(
        execution_id=uuid.uuid4(),
        agent_id=uuid.UUID(SAMPLE_MANIFEST_DICT["agent"]["id"]),
        agent_version="1.0.0",
        requested_by=uuid.uuid4(),
        timeout_seconds=0.1,  # explicitly low timeout
    )

    with pytest.raises(AgentError) as exc_info:
        await agent.execute(context, {"message": "slow"})

    assert exc_info.value.code == "EXECUTION_TIMEOUT"
