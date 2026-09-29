# Worker Service

The AgentChain Worker executes assigned DAG subtasks within isolated runtime sandboxes, supporting:
- Modular LLM provider reasoning (Anthropic, OpenAI, Gemini, vLLM)
- Sandboxed tool execution (code interpreter, web search, database query)
- Cryptographic execution attestation signed by the agent operator's key
