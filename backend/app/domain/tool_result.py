"""Tool return types shared across all tools in app/tools/.

ToolError is returned (not raised) from every tool on failure so the agent
loop can catch it and continue rather than crashing.  The retryable flag
lets the loop decide whether to retry or surface a graceful message.
"""

from pydantic import BaseModel, Field


class ToolError(BaseModel):
    error: str = Field(description="Human-readable error description.")
    retryable: bool = Field(
        default=False,
        description="True if the caller may retry the same call; False for terminal failures.",
    )
