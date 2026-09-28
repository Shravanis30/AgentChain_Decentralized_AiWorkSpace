export type UserRole = "REQUESTER" | "AGENT_OPERATOR" | "VALIDATOR" | "ADMIN";

export type TaskStatus =
  | "DRAFT"
  | "PLANNING"
  | "PENDING_DEPOSIT"
  | "ACTIVE"
  | "VERIFYING"
  | "COMPLETED"
  | "DISPUTED"
  | "CANCELLED"
  | "FAILED";

export interface DependencyHealth {
  database: string;
  redis: string;
  qdrant: string;
  object_storage: string;
}

export interface HealthResponse {
  status: "ok" | "degraded";
  version: string;
  dependencies: DependencyHealth;
}

export interface UserProfile {
  id: string;
  wallet_address: string;
  role: UserRole;
  created_at: string;
}

export interface AgentProfile {
  id: string;
  operator_id: string;
  onchain_agent_id?: number | null;
  name: string;
  description?: string | null;
  reputation_score: number;
  is_active: boolean;
}

export interface TaskDTO {
  id: string;
  requester_id: string;
  onchain_task_id?: number | null;
  title: string;
  description: string;
  status: TaskStatus;
  budget_usdc: string;
  platform_fee_usdc: string;
  escrow_tx_hash?: string | null;
  result_hash?: string | null;
  created_at: string;
  updated_at: string;
}
