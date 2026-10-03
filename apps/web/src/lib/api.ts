export interface Wallet {
  id: string;
  address: string;
  chain_id: number;
  is_primary: boolean;
  verified_at: string;
  created_at: string;
}

export interface User {
  id: string;
  primary_role: "CLIENT" | "DEVELOPER" | "AGENT_OPERATOR" | "VERIFIER" | "ADMIN" | string;
  roles: string[];
  primary_wallet: Wallet | null;
  created_at: string;
}

export interface VerifyResponse {
  authenticated: boolean;
  user: User;
  wallet: Wallet;
  session_expires_at: string;
  token?: string;
}

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000/api/v1";

export async function fetchNonce(walletAddress: string, chainId?: number): Promise<{ nonce: string; expires_at: string }> {
  const res = await fetch(`${API_BASE}/auth/nonce`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ wallet_address: walletAddress, chain_id: chainId }),
    credentials: "include",
  });
  if (!res.ok) {
    const errorData = await res.json().catch(() => ({}));
    throw new Error(errorData.detail || `Failed to fetch nonce (${res.status})`);
  }
  return res.json();
}

export async function verifySignature(message: string, signature: string): Promise<VerifyResponse> {
  const res = await fetch(`${API_BASE}/auth/verify`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message, signature }),
    credentials: "include",
  });
  if (!res.ok) {
    const errorData = await res.json().catch(() => ({}));
    throw new Error(errorData.detail || `Signature verification failed (${res.status})`);
  }
  return res.json();
}

export async function fetchAuthMe(): Promise<User | null> {
  const res = await fetch(`${API_BASE}/auth/me`, {
    method: "GET",
    credentials: "include",
  });
  if (res.status === 401) {
    return null;
  }
  if (!res.ok) {
    throw new Error(`Failed to fetch current user (${res.status})`);
  }
  return res.json();
}

export async function logoutUser(): Promise<{ success: boolean; message: string }> {
  const res = await fetch(`${API_BASE}/auth/logout`, {
    method: "POST",
    credentials: "include",
  });
  if (!res.ok) {
    throw new Error(`Logout failed (${res.status})`);
  }
  return res.json();
}

export async function fetchUserWallets(): Promise<Wallet[]> {
  const res = await fetch(`${API_BASE}/users/me/wallets`, {
    method: "GET",
    credentials: "include",
  });
  if (!res.ok) {
    throw new Error(`Failed to fetch wallets (${res.status})`);
  }
  return res.json();
}

// ==========================================
// Phase 3: Agent Registry & Execution Types
// ==========================================

export interface AgentVersionInfo {
  id: string;
  version: string;
  manifest: Record<string, unknown>;
  input_schema: Record<string, unknown>;
  output_schema: Record<string, unknown>;
  runtime_config: Record<string, unknown>;
  pricing_config: Record<string, unknown>;
  verification_config: Record<string, unknown>;
  created_at: string;
  published_at: string | null;
}

export interface AgentItem {
  id: string;
  owner_user_id: string;
  name: string;
  slug: string;
  description: string | null;
  status: "DRAFT" | "VALIDATING" | "PUBLISHED" | "SUSPENDED" | "DEPRECATED" | string;
  current_version_id: string | null;
  current_version: AgentVersionInfo | null;
  capabilities: string[];
  created_at: string;
  updated_at: string;
  published_at: string | null;
}

export interface AgentListResponse {
  items: AgentItem[];
  total: number;
  page: number;
  page_size: number;
  total_pages: number;
}

export interface AgentCreateInput {
  name: string;
  slug: string;
  description?: string;
  manifest: Record<string, unknown>;
}

export interface ExecutionSubmitResponse {
  execution_id: string;
  status: string;
  input_hash: string;
  agent_id: string;
  agent_version: string;
}

export interface ExecutionDetailResponse {
  id: string;
  agent_id: string;
  agent_version_id: string;
  requested_by: string;
  status: "QUEUED" | "RUNNING" | "SUCCEEDED" | "FAILED" | "CANCELLED" | "TIMED_OUT" | string;
  input_data: Record<string, unknown>;
  output_data: Record<string, unknown> | null;
  input_hash: string;
  output_hash: string | null;
  queued_at?: string | null;
  started_at: string | null;
  completed_at: string | null;
  timeout_at?: string | null;
  cancelled_at?: string | null;
  attempt_count?: number;
  worker_id?: string | null;
  cancellation_requested?: boolean;
  error_code: string | null;
  error_message: string | null;
  metadata_json: Record<string, unknown>;
  created_at: string;
  agent?: AgentItem;
}

export interface WorkerInfo {
  worker_id: string;
  timestamp: string;
  status: string;
  active_execution_count: number;
  version: string;
  hostname?: string | null;
  is_alive: boolean;
  heartbeat_age_seconds: number;
}

export interface WorkerListResponse {
  workers: WorkerInfo[];
  total: number;
}

export async function fetchAgents(params?: {
  capability?: string;
  status?: string;
  owner_id?: string;
  search?: string;
  page?: number;
  page_size?: number;
}): Promise<AgentListResponse> {
  const query = new URLSearchParams();
  if (params?.capability) query.set("capability", params.capability);
  if (params?.status) query.set("status", params.status);
  if (params?.owner_id) query.set("owner_id", params.owner_id);
  if (params?.search) query.set("search", params.search);
  if (params?.page) query.set("page", params.page.toString());
  if (params?.page_size) query.set("page_size", params.page_size.toString());

  const url = `${API_BASE}/agents${query.toString() ? `?${query.toString()}` : ""}`;
  const res = await fetch(url, {
    method: "GET",
    credentials: "include",
  });
  if (!res.ok) {
    const errorData = await res.json().catch(() => ({}));
    throw new Error(errorData.detail || `Failed to fetch agents (${res.status})`);
  }
  return res.json();
}

export async function fetchAgent(agentId: string): Promise<AgentItem> {
  const res = await fetch(`${API_BASE}/agents/${agentId}`, {
    method: "GET",
    credentials: "include",
  });
  if (!res.ok) {
    const errorData = await res.json().catch(() => ({}));
    throw new Error(errorData.detail || `Failed to fetch agent (${res.status})`);
  }
  return res.json();
}

export async function createAgent(data: AgentCreateInput): Promise<AgentItem> {
  const res = await fetch(`${API_BASE}/agents`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
    credentials: "include",
  });
  if (!res.ok) {
    const errorData = await res.json().catch(() => ({}));
    throw new Error(errorData.detail || `Failed to create agent (${res.status})`);
  }
  return res.json();
}

export async function validateAgent(agentId: string): Promise<AgentItem> {
  const res = await fetch(`${API_BASE}/agents/${agentId}/validate`, {
    method: "POST",
    credentials: "include",
  });
  if (!res.ok) {
    const errorData = await res.json().catch(() => ({}));
    throw new Error(errorData.detail || `Failed to validate agent (${res.status})`);
  }
  return res.json();
}

export async function publishAgent(agentId: string): Promise<AgentItem> {
  const res = await fetch(`${API_BASE}/agents/${agentId}/publish`, {
    method: "POST",
    credentials: "include",
  });
  if (!res.ok) {
    const errorData = await res.json().catch(() => ({}));
    throw new Error(errorData.detail || `Failed to publish agent (${res.status})`);
  }
  return res.json();
}

export async function suspendAgent(agentId: string, reason?: string): Promise<AgentItem> {
  const url = `${API_BASE}/agents/${agentId}/suspend${reason ? `?reason=${encodeURIComponent(reason)}` : ""}`;
  const res = await fetch(url, {
    method: "POST",
    credentials: "include",
  });
  if (!res.ok) {
    const errorData = await res.json().catch(() => ({}));
    throw new Error(errorData.detail || `Failed to suspend agent (${res.status})`);
  }
  return res.json();
}

export async function executeAgent(
  agentId: string,
  input: Record<string, unknown>
): Promise<ExecutionSubmitResponse> {
  const res = await fetch(`${API_BASE}/agents/${agentId}/execute`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ input }),
    credentials: "include",
  });
  if (!res.ok) {
    const errorData = await res.json().catch(() => ({}));
    throw new Error(errorData.detail || `Failed to execute agent (${res.status})`);
  }
  return res.json();
}

export async function fetchExecution(executionId: string): Promise<ExecutionDetailResponse> {
  const res = await fetch(`${API_BASE}/executions/${executionId}`, {
    method: "GET",
    credentials: "include",
  });
  if (!res.ok) {
    const errorData = await res.json().catch(() => ({}));
    throw new Error(errorData.detail || `Failed to fetch execution (${res.status})`);
  }
  return res.json();
}

export async function cancelExecution(executionId: string, reason?: string): Promise<ExecutionDetailResponse> {
  const res = await fetch(`${API_BASE}/executions/${executionId}/cancel`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ reason: reason || "User cancelled execution from dashboard" }),
    credentials: "include",
  });
  if (!res.ok) {
    const errorData = await res.json().catch(() => ({}));
    throw new Error(errorData.detail || `Failed to cancel execution (${res.status})`);
  }
  return res.json();
}

export async function fetchWorkers(): Promise<WorkerListResponse> {
  const res = await fetch(`${API_BASE}/operator/workers`, {
    method: "GET",
    credentials: "include",
  });
  if (!res.ok) {
    const errorData = await res.json().catch(() => ({}));
    throw new Error(errorData.detail || `Failed to fetch workers (${res.status})`);
  }
  return res.json();
}

export function getExecutionEventsUrl(executionId: string, sseToken?: string): string {
  if (sseToken) {
    return `${API_BASE}/executions/${executionId}/events?sse_token=${encodeURIComponent(sseToken)}`;
  }
  return `${API_BASE}/executions/${executionId}/events`;
}

export async function fetchExecutionSSETicket(
  executionId: string
): Promise<{ sse_token: string; expires_in: number; execution_id: string }> {
  const res = await fetch(`${API_BASE}/executions/${executionId}/sse-token`, {
    method: "POST",
    credentials: "include",
  });
  if (!res.ok) {
    const errorData = await res.json().catch(() => ({}));
    throw new Error(errorData.detail || `Failed to fetch SSE ticket (${res.status})`);
  }
  return res.json();
}

// ==========================================
// Phase 5: Multi-Agent Orchestrator Types & API
// ==========================================

export interface OrchestrationTask {
  id: string;
  orchestration_id: string;
  task_key: string;
  agent_id: string;
  agent_version: string;
  status: "PENDING" | "READY" | "RUNNING" | "SUCCEEDED" | "FAILED" | "CANCELLED" | "SKIPPED" | string;
  dependencies: string[];
  execution_id: string | null;
  attempt: number;
  max_attempts: number;
  input_hash: string;
  output_hash: string | null;
  output_data: Record<string, unknown> | null;
  error_code: string | null;
  error_message: string | null;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
}

export interface OrchestrationArtifact {
  id: string;
  orchestration_id: string;
  task_id: string;
  producer_agent_id: string;
  producer_agent_version: string;
  execution_id: string | null;
  artifact_key: string;
  content_type: string;
  schema_version: string;
  sha256: string;
  size_bytes: number;
  created_at: string;
  content_json: Record<string, unknown> | null;
}

export interface Orchestration {
  id: string;
  requested_by: string;
  goal: string;
  status: "PLANNING" | "READY" | "RUNNING" | "SUCCEEDED" | "FAILED" | "CANCELLED" | string;
  graph_version: string;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
  failed_at: string | null;
  cancelled_at: string | null;
  result: Record<string, unknown> | null;
  error_code: string | null;
  error_message: string | null;
  tasks: OrchestrationTask[];
  artifacts: OrchestrationArtifact[];
  metadata: Record<string, unknown>;
}

export interface OrchestrationListResponse {
  items: Orchestration[];
  total: number;
}

export interface TaskDefinitionPayload {
  task_key: string;
  agent_id?: string;
  agent_version?: string;
  capability?: string;
  input?: Record<string, unknown>;
  depends_on?: string[];
  max_attempts?: number;
}

export async function createOrchestration(
  goal: string,
  tasks?: TaskDefinitionPayload[],
  metadata?: Record<string, unknown>
): Promise<Orchestration> {
  const res = await fetch(`${API_BASE}/orchestrations`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ goal, tasks: tasks || null, metadata: metadata || {} }),
    credentials: "include",
  });
  if (!res.ok) {
    const errorData = await res.json().catch(() => ({}));
    throw new Error(errorData.detail || `Failed to create orchestration (${res.status})`);
  }
  return res.json();
}

export async function fetchOrchestrations(limit = 20, offset = 0): Promise<OrchestrationListResponse> {
  const res = await fetch(`${API_BASE}/orchestrations?limit=${limit}&offset=${offset}`, {
    method: "GET",
    credentials: "include",
  });
  if (!res.ok) {
    const errorData = await res.json().catch(() => ({}));
    throw new Error(errorData.detail || `Failed to fetch orchestrations (${res.status})`);
  }
  return res.json();
}

export async function fetchOrchestration(id: string): Promise<Orchestration> {
  const res = await fetch(`${API_BASE}/orchestrations/${id}`, {
    method: "GET",
    credentials: "include",
  });
  if (!res.ok) {
    const errorData = await res.json().catch(() => ({}));
    throw new Error(errorData.detail || `Failed to fetch orchestration (${res.status})`);
  }
  return res.json();
}

export async function startOrchestration(id: string): Promise<Orchestration> {
  const res = await fetch(`${API_BASE}/orchestrations/${id}/start`, {
    method: "POST",
    credentials: "include",
  });
  if (!res.ok) {
    const errorData = await res.json().catch(() => ({}));
    throw new Error(errorData.detail || `Failed to start orchestration (${res.status})`);
  }
  return res.json();
}

export async function cancelOrchestration(id: string, reason?: string): Promise<Orchestration> {
  const res = await fetch(`${API_BASE}/orchestrations/${id}/cancel`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ reason: reason || "User cancelled orchestration" }),
    credentials: "include",
  });
  if (!res.ok) {
    const errorData = await res.json().catch(() => ({}));
    throw new Error(errorData.detail || `Failed to cancel orchestration (${res.status})`);
  }
  return res.json();
}

export async function fetchOrchestrationSSETicket(
  id: string
): Promise<{ token: string; expires_in: number; stream_url: string }> {
  const res = await fetch(`${API_BASE}/orchestrations/${id}/sse-token`, {
    method: "POST",
    credentials: "include",
  });
  if (!res.ok) {
    const errorData = await res.json().catch(() => ({}));
    throw new Error(errorData.detail || `Failed to fetch orchestration SSE ticket (${res.status})`);
  }
  return res.json();
}

export function getOrchestrationEventsUrl(id: string, sseToken?: string): string {
  if (sseToken) {
    return `${API_BASE}/orchestrations/${id}/events?sse_token=${encodeURIComponent(sseToken)}`;
  }
  return `${API_BASE}/orchestrations/${id}/events`;
}

export interface SettlementHistoryItem {
  id: string;
  from_status: string;
  to_status: string;
  reason: string | null;
  actor_id: string | null;
  actor_role: string | null;
  metadata_json: Record<string, any>;
  created_at: string;
}

export interface Settlement {
  id: string;
  idempotency_key: string;
  chain_id: number;
  escrow_contract: string;
  escrow_id: string;
  client_address: string;
  beneficiary_address: string;
  token_address: string;
  amount: string;
  action: "RELEASE" | "REFUND" | "DISPUTE_RESOLVE_RELEASE" | "DISPUTE_RESOLVE_REFUND" | string;
  status: "PENDING_AUTHORIZATION" | "AUTHORIZED" | "SUBMITTED" | "CONFIRMED" | "FAILED" | "BLOCKED" | "CANCELLED" | string;
  authorization_version: number;
  orchestration_id: string | null;
  execution_id: string | null;
  authorized_by: string | null;
  authorized_at: string | null;
  authorization_reason: string | null;
  blocked_reason: string | null;
  transaction_intent_id: string | null;
  submitted_at: string | null;
  confirmed_at: string | null;
  failed_at: string | null;
  cancelled_at: string | null;
  settlement_tx_hash: string | null;
  block_number: number | null;
  block_hash: string | null;
  confirmations: number;
  error_message: string | null;
  metadata_json: Record<string, any>;
  created_at: string;
  updated_at: string;
  history?: SettlementHistoryItem[];
}

export async function fetchSettlements(
  chainId?: number,
  status?: string,
  escrowId?: string
): Promise<{ settlements: Settlement[]; total: number }> {
  const params = new URLSearchParams();
  if (chainId !== undefined) params.append("chain_id", chainId.toString());
  if (status) params.append("status", status);
  if (escrowId) params.append("escrow_id", escrowId);

  const res = await fetch(`${API_BASE}/settlements?${params.toString()}`, {
    method: "GET",
    credentials: "include",
  });
  if (!res.ok) {
    const errorData = await res.json().catch(() => ({}));
    throw new Error(errorData.detail || `Failed to fetch settlements (${res.status})`);
  }
  return res.json();
}

export async function fetchSettlement(id: string): Promise<Settlement> {
  const res = await fetch(`${API_BASE}/settlements/${id}`, {
    method: "GET",
    credentials: "include",
  });
  if (!res.ok) {
    const errorData = await res.json().catch(() => ({}));
    throw new Error(errorData.detail || `Failed to fetch settlement (${res.status})`);
  }
  return res.json();
}

export async function createSettlement(payload: {
  chain_id: number;
  escrow_id: string;
  action: string;
  orchestration_id?: string;
  execution_id?: string;
  authorization_version?: number;
  beneficiary_amount?: number;
  client_refund_amount?: number;
  metadata_json?: Record<string, any>;
}): Promise<Settlement> {
  const res = await fetch(`${API_BASE}/settlements`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
    credentials: "include",
  });
  if (!res.ok) {
    const errorData = await res.json().catch(() => ({}));
    throw new Error(errorData.detail || `Failed to create settlement (${res.status})`);
  }
  return res.json();
}

export async function authorizeSettlement(id: string, reason?: string): Promise<Settlement> {
  const res = await fetch(`${API_BASE}/settlements/${id}/authorize`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ reason }),
    credentials: "include",
  });
  if (!res.ok) {
    const errorData = await res.json().catch(() => ({}));
    throw new Error(errorData.detail || `Failed to authorize settlement (${res.status})`);
  }
  return res.json();
}

export async function cancelSettlement(id: string, reason?: string): Promise<Settlement> {
  const res = await fetch(`${API_BASE}/settlements/${id}/cancel`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ reason }),
    credentials: "include",
  });
  if (!res.ok) {
    const errorData = await res.json().catch(() => ({}));
    throw new Error(errorData.detail || `Failed to cancel settlement (${res.status})`);
  }
  return res.json();
}

export interface Distribution {
  id: string;
  idempotency_key: string;
  distribution_key: string;
  chain_id: number;
  escrow_contract: string;
  escrow_id: string;
  settlement_id: string;
  distributor_contract: string;
  token_address: string;
  gross_amount: string;
  developer_amount: string;
  developer_recipient: string;
  staker_amount: string;
  staker_recipient: string;
  dao_amount: string;
  dao_recipient: string;
  distribution_version: number;
  status: string;
  transaction_intent_id?: string;
  distribution_tx_hash?: string;
  onchain_distribution_id?: string;
  block_number?: number;
  block_hash?: string;
  confirmations: number;
  error_message?: string;
  created_at: string;
  updated_at: string;
  confirmed_at?: string;
}

export async function fetchDistributions(params?: {
  chain_id?: number;
  status?: string;
  escrow_id?: string;
  settlement_id?: string;
  skip?: number;
  limit?: number;
}): Promise<{ items: Distribution[]; total: number }> {
  const searchParams = new URLSearchParams();
  if (params?.chain_id !== undefined) searchParams.append("chain_id", params.chain_id.toString());
  if (params?.status) searchParams.append("status", params.status);
  if (params?.escrow_id) searchParams.append("escrow_id", params.escrow_id);
  if (params?.settlement_id) searchParams.append("settlement_id", params.settlement_id);
  if (params?.skip !== undefined) searchParams.append("skip", params.skip.toString());
  if (params?.limit !== undefined) searchParams.append("limit", params.limit.toString());

  const url = `${API_BASE}/distributions?${searchParams.toString()}`;
  const res = await fetch(url, {
    method: "GET",
    credentials: "include",
  });
  if (!res.ok) {
    const errorData = await res.json().catch(() => ({}));
    throw new Error(errorData.detail || `Failed to fetch distributions (${res.status})`);
  }
  return res.json();
}

export async function fetchDistribution(id: string): Promise<Distribution> {
  const res = await fetch(`${API_BASE}/distributions/${id}`, {
    method: "GET",
    credentials: "include",
  });
  if (!res.ok) {
    const errorData = await res.json().catch(() => ({}));
    throw new Error(errorData.detail || `Failed to fetch distribution (${res.status})`);
  }
  return res.json();
}

export interface ResultNotarization {
  id: string;
  execution_id: string;
  chain_id: number;
  contract_address: string;
  result_hash: string;
  hash_algorithm: string;
  canonicalization_version: string;
  artifact_reference?: string | null;
  artifact_commitment?: string | null;
  status: "PENDING" | "SUBMITTED" | "CONFIRMING" | "CONFIRMED" | "REORGED" | "FAILED" | string;
  transaction_hash?: string | null;
  block_number?: number | null;
  block_hash?: string | null;
  confirmations: number;
  is_canonical: boolean;
  error_message?: string | null;
  payload_json?: Record<string, any>;
  created_at: string;
  updated_at: string;
  confirmed_at?: string | null;
  reorged_at?: string | null;
}

export interface NotarizationVerifyResponse {
  execution_id: string;
  verification_status: "VERIFIED" | "HASH_MISMATCH" | "NOT_CONFIRMED" | "NOT_NOTARIZED" | "REORGED";
  result_hash?: string | null;
  computed_hash?: string | null;
  onchain_hash?: string | null;
  hash_algorithm: string;
  canonicalization_version: string;
  chain_id: number;
  contract_address: string;
  transaction_hash?: string | null;
  block_number?: number | null;
  confirmations: number;
  is_canonical: boolean;
  notarized_at?: string | null;
  verified_at: string;
  details?: string | null;
}

export async function fetchNotarizationByExecutionId(executionId: string, chainId?: number): Promise<ResultNotarization> {
  const params = new URLSearchParams();
  if (chainId !== undefined) params.append("chain_id", chainId.toString());
  const res = await fetch(`${API_BASE}/notarizations/execution/${executionId}?${params.toString()}`, {
    method: "GET",
    credentials: "include",
  });
  if (!res.ok) {
    const errorData = await res.json().catch(() => ({}));
    throw new Error(errorData.detail || `Failed to fetch notarization (${res.status})`);
  }
  return res.json();
}

export async function fetchNotarizationById(id: string): Promise<ResultNotarization> {
  const res = await fetch(`${API_BASE}/notarizations/${id}`, {
    method: "GET",
    credentials: "include",
  });
  if (!res.ok) {
    const errorData = await res.json().catch(() => ({}));
    throw new Error(errorData.detail || `Failed to fetch notarization (${res.status})`);
  }
  return res.json();
}

export async function verifyNotarization(
  executionId: string,
  params?: { result_hash?: string; chain_id?: number }
): Promise<NotarizationVerifyResponse> {
  const searchParams = new URLSearchParams();
  if (params?.chain_id !== undefined) searchParams.append("chain_id", params.chain_id.toString());
  if (params?.result_hash) searchParams.append("result_hash", params.result_hash);

  const res = await fetch(`${API_BASE}/notarizations/execution/${executionId}/verify?${searchParams.toString()}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({}),
    credentials: "include",
  });
  if (!res.ok) {
    const errorData = await res.json().catch(() => ({}));
    throw new Error(errorData.detail || `Failed to verify notarization (${res.status})`);
  }
  return res.json();
}

// ==========================================
// Phase 6.4: Verified Reputation Types & APIs
// ==========================================

export interface ReputationProfile {
  agent_id: string;
  chain_id: number;
  total_verified_executions: number;
  verified_successes: number;
  verified_failures: number;
  verified_timeouts: number;
  verified_cancellations: number;
  canonical_event_count: number;
  first_verified_execution_id?: string | null;
  latest_verified_execution_id?: string | null;
  latest_verified_outcome?: string | null;
  latest_verified_at?: string | null;
  success_rate?: number | null;
  last_recalculated_at: string;
  created_at: string;
  updated_at: string;
}

export interface ReputationEvent {
  id: string;
  idempotency_key: string;
  reputation_key: string;
  agent_id: string;
  agent_version_id: string;
  execution_id: string;
  outcome_type: "VERIFIED_SUCCESS" | "VERIFIED_FAILURE" | "VERIFIED_TIMEOUT" | "VERIFIED_CANCELLATION" | string;
  result_hash?: string | null;
  notarization_id?: string | null;
  evidence_hash: string;
  chain_id: number;
  contract_address: string;
  status: "PENDING" | "SUBMITTED" | "CONFIRMING" | "CONFIRMED" | "REORGED" | "FAILED" | "INVALIDATED" | string;
  transaction_intent_id?: string | null;
  transaction_hash?: string | null;
  block_number?: number | null;
  block_hash?: string | null;
  confirmations: number;
  is_canonical: boolean;
  error_message?: string | null;
  metadata_json: Record<string, unknown>;
  created_at: string;
  updated_at: string;
  confirmed_at?: string | null;
  reorged_at?: string | null;
}

export interface ReputationVerifyResponse {
  reputation_event_id: string;
  verification_status: "CONFIRMED" | "PENDING" | "REORGED" | "INVALIDATED" | string;
  is_verified: boolean;
  agent_id: string;
  agent_version_id: string;
  execution_id: string;
  outcome_type: string;
  result_hash?: string | null;
  has_valid_notarization: boolean;
  notarization_status?: string | null;
  chain_id: number;
  contract_address: string;
  transaction_hash?: string | null;
  confirmations: number;
  confirmations_required: number;
  is_canonical: boolean;
  details: Record<string, unknown>;
}

export async function fetchAgentReputationProfile(
  agentId: string,
  chainId?: number
): Promise<ReputationProfile> {
  const params = new URLSearchParams();
  if (chainId !== undefined) params.append("chain_id", chainId.toString());
  const res = await fetch(`${API_BASE}/agents/${agentId}/reputation?${params.toString()}`, {
    method: "GET",
    credentials: "include",
  });
  if (!res.ok) {
    const errorData = await res.json().catch(() => ({}));
    throw new Error(errorData.detail || `Failed to fetch reputation profile (${res.status})`);
  }
  return res.json();
}

export async function fetchAgentReputationEvents(
  agentId: string,
  options?: { chainId?: number; limit?: number; offset?: number; canonicalOnly?: boolean }
): Promise<ReputationEvent[]> {
  const params = new URLSearchParams();
  if (options?.chainId !== undefined) params.append("chain_id", options.chainId.toString());
  if (options?.limit !== undefined) params.append("limit", options.limit.toString());
  if (options?.offset !== undefined) params.append("offset", options.offset.toString());
  if (options?.canonicalOnly !== undefined) params.append("canonical_only", String(options.canonicalOnly));

  const res = await fetch(`${API_BASE}/agents/${agentId}/reputation/events?${params.toString()}`, {
    method: "GET",
    credentials: "include",
  });
  if (!res.ok) {
    const errorData = await res.json().catch(() => ({}));
    throw new Error(errorData.detail || `Failed to fetch reputation events (${res.status})`);
  }
  return res.json();
}

export async function fetchExecutionReputationEvent(
  executionId: string,
  chainId?: number
): Promise<ReputationEvent> {
  const params = new URLSearchParams();
  if (chainId !== undefined) params.append("chain_id", chainId.toString());
  const res = await fetch(`${API_BASE}/executions/${executionId}/reputation?${params.toString()}`, {
    method: "GET",
    credentials: "include",
  });
  if (!res.ok) {
    const errorData = await res.json().catch(() => ({}));
    throw new Error(errorData.detail || `Failed to fetch execution reputation (${res.status})`);
  }
  return res.json();
}

export async function fetchReputationEventById(eventId: string): Promise<ReputationEvent> {
  const res = await fetch(`${API_BASE}/reputation/events/${eventId}`, {
    method: "GET",
    credentials: "include",
  });
  if (!res.ok) {
    const errorData = await res.json().catch(() => ({}));
    throw new Error(errorData.detail || `Failed to fetch reputation event (${res.status})`);
  }
  return res.json();
}

export async function verifyReputationEvent(eventId: string): Promise<ReputationVerifyResponse> {
  const res = await fetch(`${API_BASE}/reputation/verify/${eventId}`, {
    method: "GET",
    credentials: "include",
  });
  if (!res.ok) {
    const errorData = await res.json().catch(() => ({}));
    throw new Error(errorData.detail || `Failed to verify reputation event (${res.status})`);
  }
  return res.json();
}

// ─── Phase 6.5: Reputation Scoring Types ──────────────────────────────────────

export interface ReputationScore {
  id: string;
  agent_id: string;
  chain_id: number;
  policy_version: string;
  score_scaled: number;   // [0, 10000]; divide by 10000 for [0.0, 1.0]
  score_min: number;
  score_max: number;
  score_float: number;    // score_scaled / 10000
  total_verified_executions: number;
  verified_successes: number;
  verified_failures: number;
  verified_timeouts: number;
  verified_cancellations: number;
  success_rate_scaled: number | null;   // [0, 10000] or null when total==0
  failure_rate_scaled: number | null;
  timeout_rate_scaled: number | null;
  cancellation_rate_scaled: number | null;
  experience_count: number;
  evidence_set_hash: string;
  evidence_event_count: number;
  calculation_reason: string;
  explanation_json: Record<string, unknown>;
  calculated_at: string;
  created_at: string;
  updated_at: string;
}

export interface ReputationMetrics {
  agent_id: string;
  chain_id: number;
  policy_version: string;
  total_verified_executions: number;
  verified_successes: number;
  verified_failures: number;
  verified_timeouts: number;
  verified_cancellations: number;
  success_rate_scaled: number | null;
  failure_rate_scaled: number | null;
  timeout_rate_scaled: number | null;
  cancellation_rate_scaled: number | null;
  success_rate: number | null;
  failure_rate: number | null;
  timeout_rate: number | null;
  cancellation_rate: number | null;
  reputation_score: number | null;
  experience_count: number;
  evidence_set_hash: string | null;
  evidence_event_count: number;
  calculated_at: string | null;
}

export interface ReputationScoreHistoryItem {
  id: string;
  reputation_score_id: string;
  agent_id: string;
  chain_id: number;
  policy_version: string;
  previous_score_scaled: number | null;
  previous_evidence_set_hash: string | null;
  new_score_scaled: number;
  new_evidence_set_hash: string;
  total_verified_executions: number;
  verified_successes: number;
  verified_failures: number;
  verified_timeouts: number;
  verified_cancellations: number;
  calculation_reason: string;
  explanation_json: Record<string, unknown>;
  calculated_at: string;
}

export interface ReputationExplanation {
  agent_id: string;
  chain_id: number;
  policy_version: string;
  policy_formula_summary: string;
  evidence_set_hash: string;
  evidence_event_count: number;
  score_scaled: number;
  score_float: number;
  is_cold_start: boolean;
  experience_count: number;
  explanation_detail: Record<string, unknown>;
  calculated_at: string;
}

export interface PolicyVersion {
  id: string;
  policy_name: string;
  version_number: number;
  description: string | null;
  formula_json: Record<string, unknown>;
  is_active: boolean;
  activated_at: string;
  deprecated_at: string | null;
  created_at: string;
  version_string: string;
}

// ─── Phase 6.5: Reputation Scoring API Functions ──────────────────────────────

export async function fetchAgentReputationScore(
  agentId: string,
  chainId?: number,
  policyVersion?: string
): Promise<ReputationScore> {
  const params = new URLSearchParams();
  if (chainId !== undefined) params.append("chain_id", chainId.toString());
  if (policyVersion) params.append("policy_version", policyVersion);
  const res = await fetch(`${API_BASE}/agents/${agentId}/reputation/score?${params.toString()}`, {
    method: "GET",
    credentials: "include",
  });
  if (!res.ok) {
    const errorData = await res.json().catch(() => ({}));
    throw new Error(errorData.detail || `Failed to fetch reputation score (${res.status})`);
  }
  return res.json();
}

export async function fetchAgentReputationMetrics(
  agentId: string,
  chainId?: number
): Promise<ReputationMetrics> {
  const params = new URLSearchParams();
  if (chainId !== undefined) params.append("chain_id", chainId.toString());
  const res = await fetch(`${API_BASE}/agents/${agentId}/reputation/metrics?${params.toString()}`, {
    method: "GET",
    credentials: "include",
  });
  if (!res.ok) {
    const errorData = await res.json().catch(() => ({}));
    throw new Error(errorData.detail || `Failed to fetch reputation metrics (${res.status})`);
  }
  return res.json();
}

export async function fetchAgentReputationHistory(
  agentId: string,
  options?: { chainId?: number; policyVersion?: string; limit?: number; offset?: number }
): Promise<ReputationScoreHistoryItem[]> {
  const params = new URLSearchParams();
  if (options?.chainId !== undefined) params.append("chain_id", options.chainId.toString());
  if (options?.policyVersion) params.append("policy_version", options.policyVersion);
  if (options?.limit !== undefined) params.append("limit", options.limit.toString());
  if (options?.offset !== undefined) params.append("offset", options.offset.toString());
  const res = await fetch(`${API_BASE}/agents/${agentId}/reputation/history?${params.toString()}`, {
    method: "GET",
    credentials: "include",
  });
  if (!res.ok) {
    const errorData = await res.json().catch(() => ({}));
    throw new Error(errorData.detail || `Failed to fetch reputation history (${res.status})`);
  }
  return res.json();
}

export async function fetchAgentReputationExplanation(
  agentId: string,
  chainId?: number
): Promise<ReputationExplanation> {
  const params = new URLSearchParams();
  if (chainId !== undefined) params.append("chain_id", chainId.toString());
  const res = await fetch(`${API_BASE}/agents/${agentId}/reputation/explanation?${params.toString()}`, {
    method: "GET",
    credentials: "include",
  });
  if (!res.ok) {
    const errorData = await res.json().catch(() => ({}));
    throw new Error(errorData.detail || `Failed to fetch reputation explanation (${res.status})`);
  }
  return res.json();
}

export async function fetchAgentReputationPolicy(agentId: string): Promise<PolicyVersion> {
  const res = await fetch(`${API_BASE}/agents/${agentId}/reputation/policy`, {
    method: "GET",
    credentials: "include",
  });
  if (!res.ok) {
    const errorData = await res.json().catch(() => ({}));
    throw new Error(errorData.detail || `Failed to fetch reputation policy (${res.status})`);
  }
  return res.json();
}

// ==========================================
// Phase 6.8: Marketplace Lifecycle Types & APIs
// ==========================================

export interface MarketplaceOrder {
  id: string;
  order_number: string;
  client_user_id: string;
  client_address: string;
  goal: string;
  task_input: Record<string, unknown>;
  max_budget_atomic: number | null;
  price_currency: string;
  selection_decision_id: string;
  selected_agent_id: string;
  selected_agent_version: string;
  pinned_price_atomic: number;
  platform_fee_atomic: number;
  total_escrow_atomic: number;
  escrow_chain_id: number;
  escrow_contract: string;
  escrow_reference_id: string;
  escrow_salt: string;
  escrow_id: string | null;
  execution_id: string | null;
  orchestration_id: string | null;
  orchestration_task_id: string | null;
  settlement_id: string | null;
  distribution_id: string | null;
  status:
    | "DISCOVERED"
    | "SELECTED"
    | "PRICE_LOCKED"
    | "ESCROW_PENDING"
    | "ESCROW_FUNDED"
    | "EXECUTION_QUEUED"
    | "EXECUTING"
    | "RESULT_AVAILABLE"
    | "RESULT_NOTARIZED"
    | "OUTCOME_VERIFIED"
    | "SETTLEMENT_PENDING"
    | "SETTLED"
    | "REFUNDED"
    | "DISPUTED"
    | "CANCELLED"
    | "TIMED_OUT"
    | "FAILED";
  error_message: string | null;
  created_at: string;
  updated_at: string;
  completed_at: string | null;
  metadata_json: Record<string, unknown>;
}

export interface CreateMarketplaceOrderParams {
  goal: string;
  task_input?: Record<string, unknown>;
  chain_id?: number;
  idempotency_key: string;
  max_budget_atomic?: number;
  price_currency?: string;
  required_capabilities?: string[];
  preferred_agent_id?: string;
  preferred_agent_version?: string;
}

export async function createMarketplaceOrder(params: CreateMarketplaceOrderParams): Promise<MarketplaceOrder> {
  const res = await fetch(`${API_BASE}/marketplace/orders`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(params),
    credentials: "include",
  });
  if (!res.ok) {
    const errorData = await res.json().catch(() => ({}));
    throw new Error(errorData.detail || `Failed to create marketplace order (${res.status})`);
  }
  return res.json();
}

export async function fetchMarketplaceOrder(orderId: string): Promise<MarketplaceOrder> {
  const res = await fetch(`${API_BASE}/marketplace/orders/${orderId}`, {
    method: "GET",
    credentials: "include",
  });
  if (!res.ok) {
    const errorData = await res.json().catch(() => ({}));
    throw new Error(errorData.detail || `Failed to fetch marketplace order (${res.status})`);
  }
  return res.json();
}

export async function verifyMarketplaceEscrow(orderId: string): Promise<MarketplaceOrder> {
  const res = await fetch(`${API_BASE}/marketplace/orders/${orderId}/verify-escrow`, {
    method: "POST",
    credentials: "include",
  });
  if (!res.ok) {
    const errorData = await res.json().catch(() => ({}));
    throw new Error(errorData.detail || `Failed to verify escrow (${res.status})`);
  }
  return res.json();
}

export async function executeMarketplaceOrder(orderId: string): Promise<MarketplaceOrder> {
  const res = await fetch(`${API_BASE}/marketplace/orders/${orderId}/execute`, {
    method: "POST",
    credentials: "include",
  });
  if (!res.ok) {
    const errorData = await res.json().catch(() => ({}));
    throw new Error(errorData.detail || `Failed to enqueue execution (${res.status})`);
  }
  return res.json();
}

export async function settleMarketplaceOrder(orderId: string): Promise<MarketplaceOrder> {
  const res = await fetch(`${API_BASE}/marketplace/orders/${orderId}/settle`, {
    method: "POST",
    credentials: "include",
  });
  if (!res.ok) {
    const errorData = await res.json().catch(() => ({}));
    throw new Error(errorData.detail || `Failed to settle marketplace order (${res.status})`);
  }
  return res.json();
}

export async function finalizeMarketplaceOrder(orderId: string): Promise<MarketplaceOrder> {
  const res = await fetch(`${API_BASE}/marketplace/orders/${orderId}/finalize`, {
    method: "POST",
    credentials: "include",
  });
  if (!res.ok) {
    const errorData = await res.json().catch(() => ({}));
    throw new Error(errorData.detail || `Failed to finalize marketplace order (${res.status})`);
  }
  return res.json();
}

