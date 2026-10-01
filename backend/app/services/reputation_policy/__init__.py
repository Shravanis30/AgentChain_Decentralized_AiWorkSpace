"""Phase 6.5 — Deterministic Reputation Scoring & Policy Engine.

Architectural separation:
- Phase 6.4 (ReputationRegistry, ReputationProjector): "What objectively happened?"
- Phase 6.5 (ReputationPolicyEngine):                  "Given those verified facts, what is the score?"

The policy engine consumes canonical verified events from Phase 6.4 and
deterministically calculates reputation scores.  It NEVER creates evidence.
"""
