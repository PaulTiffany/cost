"""Audit v4 channel modules.

Each channel exports a single async coroutine `run_<name>_channel` that
streams ObservationPackets to a shared output JSONL file. The orchestrator
in supplementary/experiments/audit_experiment_orchestrator.py composes
them via asyncio.gather().
"""
