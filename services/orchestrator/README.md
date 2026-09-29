# Orchestrator Service

The AgentChain Orchestrator coordinates multi-agent DAG workflows using LangGraph state machines, managing:
- Task decomposition into executable DAG nodes
- Agent selection and capability routing
- Tool invocation guardrails
- Continuous state persistence to Redis and PostgreSQL
- Real-time Server-Sent Events (SSE) streaming
