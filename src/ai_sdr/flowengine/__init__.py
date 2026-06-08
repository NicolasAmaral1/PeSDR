"""FlowEngine — single-LLM-call-per-turn conversational orchestrator.

Replaces the per-node LLM pattern (Plano 2, LangGraph-based) with a unified
state machine over Lead/Talk/TalkFlow + one structured-output LLM call per
inbound turn. See docs/superpowers/specs/2026-06-08-flow-engine-architecture-design.md.

FE-01a: schemas and migrations only — no runtime yet.
"""
