"""AI Agent — core orchestrator with tool calling and autonomy levels."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

from .providers.base import LLMMessage, LLMProvider, LLMTool

logger = logging.getLogger(__name__)


class AutonomyLevel(str, Enum):
    SUGGEST = "suggest"    # Только советы
    CONFIRM = "confirm"    # Советы + подтверждение
    AUTO = "auto"          # Полная автоматизация


@dataclass
class AgentConfig:
    """Configuration for the AI agent."""
    system_prompt: str = ""
    autonomy: AutonomyLevel = AutonomyLevel.CONFIRM
    max_tool_rounds: int = 10
    temperature: float = 0.3
    max_tokens: int = 4096


@dataclass
class ToolResult:
    """Result of a tool execution."""
    success: bool
    data: Any = None
    error: str | None = None
    needs_confirmation: bool = False  # For confirm mode
    confirmation_message: str = ""


@dataclass
class AgentTurn:
    """One turn of the agent conversation."""
    user_message: str
    assistant_response: str
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    tool_results: list[dict[str, Any]] = field(default_factory=list)


class TradingAgent:
    """AI Trading Agent — orchestrates LLM + tools."""

    def __init__(
        self,
        provider: LLMProvider,
        config: AgentConfig | None = None,
    ):
        self.provider = provider
        self.config = config or AgentConfig()
        self.tools: dict[str, tuple[LLMTool, Callable]] = {}
        self.history: list[LLMMessage] = []
        self._setup_system_prompt()

    def _setup_system_prompt(self) -> None:
        """Set up the system prompt with trading context."""
        if not self.config.system_prompt:
            self.config.system_prompt = (
                "Ты — AI Trading Assistant для крипто-трейдера. "
                "Ты помогаешь с анализом рынка, поиском сигналов, риск-менеджментом и обучением. "
                "Отвечай на русском языке. Будь конкретным и используй данные из инструментов. "
                "Никогда не принимай решения без явного запроса пользователя (если режим не 'auto')."
            )
        self.history = [LLMMessage(role="system", content=self.config.system_prompt)]

    def register_tool(
        self,
        name: str,
        description: str,
        parameters: dict[str, Any],
        handler: Callable[..., Any],
    ) -> None:
        """Register a tool that the LLM can call."""
        tool = LLMTool(name=name, description=description, parameters=parameters)
        self.tools[name] = (tool, handler)

    def _get_tools(self) -> list[LLMTool]:
        return [t for t, _ in self.tools.values()]

    async def _execute_tool(self, name: str, args: dict[str, Any]) -> ToolResult:
        """Execute a registered tool."""
        if name not in self.tools:
            return ToolResult(success=False, error=f"Unknown tool: {name}")
        _, handler = self.tools[name]
        try:
            result = handler(**args)
            if isinstance(result, ToolResult):
                return result
            return ToolResult(success=True, data=result)
        except Exception as e:
            logger.exception("Tool %s failed", name)
            return ToolResult(success=False, error=str(e))

    async def chat(self, user_message: str) -> AgentTurn:
        """Process a user message and return the agent's response."""
        self.history.append(LLMMessage(role="user", content=user_message))

        turn = AgentTurn(user_message=user_message, assistant_response="")
        tools = self._get_tools()

        for _ in range(self.config.max_tool_rounds):
            response = await self.provider.chat(
                messages=self.history,
                tools=tools if tools else None,
                temperature=self.config.temperature,
                max_tokens=self.config.max_tokens,
            )

            # If no tool calls, we're done
            if not response.tool_calls:
                turn.assistant_response = response.content
                self.history.append(LLMMessage(role="assistant", content=response.content))
                break

            # Process tool calls
            self.history.append(LLMMessage(
                role="assistant",
                content=response.content,
                tool_calls=response.tool_calls,
            ))

            for tc in response.tool_calls:
                func = tc.get("function", {})
                name = func.get("name", "")
                try:
                    args = json.loads(func.get("arguments", "{}"))
                except json.JSONDecodeError:
                    args = {}

                tool_result = await self._execute_tool(name, args)
                turn.tool_calls.append({"name": name, "args": args})
                turn.tool_results.append({
                    "name": name,
                    "success": tool_result.success,
                    "data": tool_result.data,
                    "error": tool_result.error,
                })

                # Check autonomy level
                if (
                    self.config.autonomy == AutonomyLevel.CONFIRM
                    and tool_result.needs_confirmation
                ):
                    turn.assistant_response = tool_result.confirmation_message
                    self.history.append(LLMMessage(
                        role="tool",
                        content=json.dumps({
                            "needs_confirmation": True,
                            "message": tool_result.confirmation_message,
                        }),
                        tool_call_id=tc.get("id"),
                        name=name,
                    ))
                    break

                # Add tool result to history
                self.history.append(LLMMessage(
                    role="tool",
                    content=json.dumps(tool_result.data if tool_result.success else {"error": tool_result.error}),
                    tool_call_id=tc.get("id"),
                    name=name,
                ))
            else:
                continue
            break

        # Trim history if too long (keep system + last 50 messages)
        if len(self.history) > 52:
            self.history = self.history[:1] + self.history[-50:]

        return turn

    async def stream_chat(self, user_message: str):
        """Stream a chat response (for Web UI)."""
        self.history.append(LLMMessage(role="user", content=user_message))
        tools = self._get_tools()

        async for chunk in self.provider.stream(
            messages=self.history,
            tools=tools if tools else None,
            temperature=self.config.temperature,
            max_tokens=self.config.max_tokens,
        ):
            yield chunk

    def reset(self) -> None:
        """Reset conversation history."""
        self.history = []
        self._setup_system_prompt()
