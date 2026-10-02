-- PostgreSQL initialization script for AgentChain
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- Grant permissions if necessary
GRANT ALL PRIVILEGES ON DATABASE agentchain_db TO agentchain_user;
