"""Model selection for one desktop project's live backend; never writes config."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from rincode.cli._helpers import load_runtime_config, make_provider
from rincode.tui_rpc.errors import (
    ConfigValidationError,
    InternalError,
    ModelNotAvailableError,
    ModelSwitchInTurnError,
)
from rincode.tui_rpc.methods.model import model_options
from rincode.tui_rpc.methods.turn import has_active_turns


class DesktopModelSelection:
    """A selection lasts until this project's backend is disconnected.

    Sessions share the same AgentLoop, including newly created and resumed ones.
    The configured model directory supplies candidates, not a remote availability
    guarantee. The live loop, rather than session.info's disk defaults, supplies
    the selected model.
    """

    def __init__(
        self,
        agent_loop_factory: Callable[[], Any],
        await_runtime_ready: Callable[[], Awaitable[None]],
    ) -> None:
        self._agent_loop_factory = agent_loop_factory
        self._await_runtime_ready = await_runtime_ready
        self._provider: str | None = None

    async def _loop(self) -> Any:
        await self._await_runtime_ready()
        loop = self._agent_loop_factory()
        if loop is None:
            raise InternalError(data={"public_message": "项目模型尚未就绪，请稍后重试"})
        return loop

    def _selection(self, loop: Any) -> dict:
        if self._provider is None:
            runtime = load_runtime_config(None, None)
            self._provider = runtime.get_provider_name(loop.model) or ""
        return {"model": loop.model, "provider": self._provider, "scope": "project_runtime"}

    async def options(self, params: dict) -> dict:
        loop = await self._loop()
        selection = self._selection(loop)
        catalog = await model_options({})
        providers = []
        for item in catalog["providers"]:
            models = list(item["models"])
            if item["slug"] == selection["provider"] and loop.model not in models:
                models.insert(0, loop.model)
            providers.append(
                {
                    "slug": item["slug"],
                    "name": item["name"],
                    "configured": bool(item["authenticated"]),
                    "models": models,
                }
            )
        return {**selection, "providers": providers}

    async def select(self, params: dict) -> dict:
        model = params.get("model")
        provider = params.get("provider")
        if not isinstance(model, str) or not model.strip() or not isinstance(provider, str) or not provider:
            raise ConfigValidationError(data={"public_message": "请选择模型及其提供商"})

        options = await self.options({})
        if has_active_turns():
            raise ModelSwitchInTurnError(data={"public_message": "请先等待当前任务完成或停止，再切换模型"})
        entry = next((p for p in options["providers"] if p["slug"] == provider), None)
        if not entry or not entry["configured"] or model not in entry["models"]:
            raise ModelNotAvailableError(data={"public_message": "该模型尚未配置，请刷新模型列表"})

        loop = self._agent_loop_factory()
        if model != loop.model or provider != self._provider:
            runtime = load_runtime_config(None, None)
            runtime.agents.defaults.model = model
            runtime.agents.defaults.provider = provider
            try:
                replacement = make_provider(runtime)
            except (SystemExit, RuntimeError, ValueError):
                # Provider construction errors may include configuration details.
                raise ModelNotAvailableError(
                    data={"public_message": "无法加载该模型配置，当前选择未改变"},
                ) from None
            # No await between the active-turn check, construction and replacement:
            # a new turn cannot interleave with a half-applied selection.
            loop.replace_provider(replacement, model=model)
            self._provider = provider
        return {"applied": True, **self._selection(loop)}


def register_desktop_model_methods(dispatcher, *, agent_loop_factory, await_runtime_ready) -> None:
    selection = DesktopModelSelection(agent_loop_factory, await_runtime_ready)
    dispatcher.register("desktop.model.options", selection.options)
    dispatcher.register("desktop.model.select", selection.select)
