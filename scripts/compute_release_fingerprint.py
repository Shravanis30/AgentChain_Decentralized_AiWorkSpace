#!/usr/bin/env python3
"""
AgentChain Deterministic Release Fingerprint Generator.

Computes a canonical SHA-256 release fingerprint across all security-critical inputs:
- Smart contracts (contracts/src/*)
- Blockchain services & signing (backend/app/services/blockchain/*)
- Production safety configuration (backend/app/core/production_config.py, config.py)
- Orchestration engine & DAG validation (backend/app/services/orchestration_engine.py, etc.)
- Worker execution & sandbox services (services/worker/src/agentchain_worker/*)
- Deployment scripts (contracts/script/*, scripts/*)
- Toolchain configuration & lockfiles (contracts/foundry.toml, backend/pyproject.toml)
- CI workflows (.github/workflows/*)

Algorithm:
1. Canonical list of paths discovered and filtered (excluding volatile/temp artifacts).
2. Sorted in strict alphabetical order (POSIX ASCII).
3. Each file content normalized to LF (\\n) and hashed via SHA-256.
4. Master fingerprint is SHA-256 over concatenated canonical lines:
   `<relative_path>:<sha256>\\n`
"""

import hashlib
import json
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parent.parent

INCLUDED_DIRECTORIES = [
    "contracts/src",
    "contracts/script",
    "backend/app/services/blockchain",
    "services/worker/src/agentchain_worker",
    ".github/workflows",
]

INCLUDED_FILES = [
    "backend/app/core/production_config.py",
    "backend/app/core/config.py",
    "backend/app/services/orchestration_engine.py",
    "backend/app/services/dag_validator.py",
    "backend/app/services/agent_execution.py",
    "backend/app/services/queue.py",
    "backend/app/services/outbox.py",
    "contracts/foundry.toml",
    "backend/pyproject.toml",
    "scripts/verify_production_safety_gates.py",
    "scripts/verify_base_sepolia_read_only.py",
    "scripts/check_config_drift.py",
]

EXCLUDE_EXTENSIONS = {".pyc", ".swp", ".DS_Store"}


def hash_file_canonical(path: Path) -> str:
    """Read file, normalize line endings to LF, and compute SHA-256."""
    raw = path.read_bytes()
    # Normalize CRLF to LF for deterministic hashing across platforms
    normalized = raw.replace(b"\r\n", b"\n")
    return hashlib.sha256(normalized).hexdigest()


def collect_target_files() -> list[str]:
    """Collect all canonical release-critical relative file paths in sorted order."""
    rel_paths: set[str] = set()

    for dir_rel in INCLUDED_DIRECTORIES:
        dir_path = REPO_ROOT / dir_rel
        if dir_path.is_dir():
            for p in dir_path.rglob("*"):
                if p.is_file() and p.suffix not in EXCLUDE_EXTENSIONS and not p.name.startswith("."):
                    rel = p.relative_to(REPO_ROOT).as_posix()
                    rel_paths.add(rel)

    for file_rel in INCLUDED_FILES:
        fp = REPO_ROOT / file_rel
        if fp.is_file():
            rel_paths.add(fp.relative_to(REPO_ROOT).as_posix())

    return sorted(rel_paths)


def compute_fingerprint() -> dict[str, any]:
    """Compute master release fingerprint and per-file manifest."""
    files = collect_target_files()
    file_hashes: dict[str, str] = {}
    master_hasher = hashlib.sha256()

    for rel_path in files:
        full_path = REPO_ROOT / rel_path
        h = hash_file_canonical(full_path)
        file_hashes[rel_path] = h
        line = f"{rel_path}:{h}\n"
        master_hasher.update(line.encode("utf-8"))

    master_fingerprint = master_hasher.hexdigest()

    return {
        "algorithm": "SHA-256",
        "normalization": "LF",
        "file_count": len(files),
        "release_fingerprint": master_fingerprint,
        "files": file_hashes,
    }


def main():
    data = compute_fingerprint()
    if "--json" in sys.argv:
        print(json.dumps(data, indent=2))
    else:
        print(f"Algorithm:           {data['algorithm']}")
        print(f"Hashed Files:        {data['file_count']}")
        print(f"Release Fingerprint: {data['release_fingerprint']}")


if __name__ == "__main__":
    main()
