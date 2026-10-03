# AgentChain Execution Queue Architecture

## 1. Technology & Durability Semantics

AgentChain utilizes **Redis Streams** as the durable, low-latency queuing and task distribution backbone for asynchronous agent executions.

### Durability Guarantees:
* **Append-Only File (AOF) Persistence**: Redis is configured with `appendonly yes` and `appendfsync everysec`.
* **Consumer Groups (`XGROUP`)**: Ensures that every execution message is delivered to exactly one active worker consumer while maintaining an in-memory and disk-persisted Pending Entries List (PEL).
* **At-Least-Once Delivery**: A message remains in the Pending Entries List until explicitly acknowledged via `XACK` upon successful completion or terminal failure.
* **Dual Persistence**: Before a job is enqueued to Redis Streams, an immutable record is flushed to PostgreSQL with status `QUEUED`. If Redis crashes, PostgreSQL serves as the persistent system of record.

## 2. Queue Message Attributes

Every queued execution message contains:

```json
{
  "execution_id": "0ba92ad3-d7f9-46b0-b0e0-c7ccf4608aa6",
  "agent_id": "8510ca7f-b096-4534-a9be-c85ccd5c1d16",
  "agent_version_id": "1dfca06f-6d23-4ec8-995f-7a22287d5fdc",
  "requested_by": "63dbb85f-052b-4877-823a-75a238be37c5",
  "input_hash": "5b0b3fa6b12f3cf17cf7b1a6a99494807310584b6c8d012d1a615e7d34b55049",
  "attempt_number": "0",
  "created_at": "2026-09-27T00:00:00Z",
  "correlation_id": "3e6a0e46-1468-45ea-b369-4423c3456ada",
  "queue_name": "agent_executions"
}
```

## 3. Abstract Queue Interface

```python
class ExecutionQueue(ABC):
    @abstractmethod
    async def enqueue_execution(self, job: QueueJob) -> str:
        """Publishes message to Redis stream and returns the stream ID."""
        pass

    @abstractmethod
    async def claim_execution(
        self,
        worker_id: str,
        queue_name: str = "agent_executions",
        batch_size: int = 1,
        block_ms: int = 2000,
    ) -> list[QueueJob]:
        """Claims messages for the worker using consumer group semantics."""
        pass

    @abstractmethod
    async def ack_execution(self, message_id: str, queue_name: str = "agent_executions") -> None:
        """Removes the message from the Pending Entries List (PEL)."""
        pass

    @abstractmethod
    async def requeue_execution(
        self,
        job: QueueJob,
        delay_seconds: float = 0.0,
        max_attempts: int = 3,
    ) -> str | None:
        """Re-enqueues for transient retry with backoff, or routes to dead-letter queue."""
        pass

    @abstractmethod
    async def dead_letter_execution(self, job: QueueJob, reason: str) -> str:
        """Transfers message to agentchain:dead_letter:{queue_name}."""
        pass

    @abstractmethod
    async def reclaim_stale_executions(
        self,
        worker_id: str,
        queue_name: str = "agent_executions",
        stale_threshold_seconds: int = 60,
        batch_size: int = 10,
    ) -> list[QueueJob]:
        """Reclaims abandoned in-flight messages from dead or hung workers."""
        pass
```
