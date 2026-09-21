"""Optional ``AgentProvider`` implementations.

Nothing is imported here eagerly: each provider module (e.g.
``agentshield.providers.openai``) carries its own optional SDK dependency
and must be imported explicitly by the caller that wants it — this keeps
``agentshield`` itself, and this package's own ``__init__``, free of any
mandatory LLM SDK dependency.
"""
