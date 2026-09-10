"""Desktop model changes affect the live project, never saved defaults."""

import asyncio
import json
from types import SimpleNamespace

import pytest

from rincode.cli import desktop_models as models
from rincode.config import loader
from rincode.tui_rpc.errors import ModelNotAvailableError, ModelSwitchInTurnError


@pytest.fixture
def setup(monkeypatch, tmp_path):
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps(
            {
                "agents": {
                    "defaults": {
                        "model": "old-model",
                        "provider": "openai",
                    }
                }
            }
        )
    )
    monkeypatch.setattr(loader, "_current_config_path", path)
    monkeypatch.setattr(models, "has_active_turns", lambda: False)

    async def catalog(_params):
        return {
            "model": "old-model",
            "provider": "openai",
            "providers": [
                {"slug": "openai", "name": "OpenAI", "authenticated": True, "models": ["old-model", "new-model"]},
                {"slug": "anthropic", "name": "Anthropic", "authenticated": False, "models": ["other-model"]},
            ],
        }

    monkeypatch.setattr(models, "model_options", catalog)
    calls = []

    def make_provider(config):
        calls.append(("build", config.agents.defaults.model, config.agents.defaults.provider))
        return object()

    monkeypatch.setattr(models, "make_provider", make_provider)

    async def ready():
        pass

    def selector(model="old-model"):
        loop = SimpleNamespace(model=model, provider=object())

        def replace(provider, *, model):
            calls.append(("replace", model))
            loop.provider = provider
            loop.model = model

        loop.replace_provider = replace
        return models.DesktopModelSelection(lambda: loop, ready), loop

    return SimpleNamespace(path=path, calls=calls, selector=selector)


async def test_options_reports_live_model_and_includes_it_in_candidates(setup):
    selection, _ = setup.selector("live-model")
    result = await selection.options({})
    assert result["model"] == "live-model"
    assert result["provider"] == "openai"
    assert result["scope"] == "project_runtime"
    assert result["providers"][0]["models"][0] == "live-model"
    assert result["providers"][0]["configured"] is True
    assert result["providers"][1]["configured"] is False
    assert setup.calls == []


async def test_switch_builds_before_replacement_without_changing_disk(setup):
    selection, loop = setup.selector()
    original = setup.path.read_bytes()
    old_provider = loop.provider
    result = await selection.select({"model": "new-model", "provider": "openai"})
    assert result == {"applied": True, "model": "new-model", "provider": "openai", "scope": "project_runtime"}
    assert setup.calls == [("build", "new-model", "openai"), ("replace", "new-model")]
    assert loop.provider is not old_provider
    assert loop.model == "new-model"
    assert setup.path.read_bytes() == original
    assert (await selection.options({}))["model"] == "new-model"


@pytest.mark.parametrize("error_type", [SystemExit, RuntimeError, ValueError])
async def test_failed_build_preserves_live_selection_and_hides_details(setup, monkeypatch, error_type):
    selection, loop = setup.selector()
    old_provider = loop.provider
    original = setup.path.read_bytes()
    private_detail = "private-provider-diagnostic"

    def fail(_config):
        raise error_type(private_detail)

    monkeypatch.setattr(models, "make_provider", fail)
    with pytest.raises(ModelNotAvailableError) as caught:
        await selection.select({"model": "new-model", "provider": "openai"})
    assert private_detail not in str(caught.value)
    assert private_detail not in json.dumps(caught.value.data)
    assert caught.value.__suppress_context__ is True
    assert loop.model == "old-model"
    assert loop.provider is old_provider
    assert (await selection.options({}))["model"] == "old-model"
    assert setup.path.read_bytes() == original
    assert setup.calls == []


async def test_busy_project_rejects_switch_before_build(setup, monkeypatch):
    selection, loop = setup.selector()
    monkeypatch.setattr(models, "has_active_turns", lambda: True)
    with pytest.raises(ModelSwitchInTurnError):
        await selection.select({"model": "new-model", "provider": "openai"})
    assert loop.model == "old-model"
    assert setup.calls == []


@pytest.mark.parametrize(
    "provider,model",
    [
        ("anthropic", "other-model"),
        ("unknown", "new-model"),
        ("openai", "unknown-model"),
    ],
)
async def test_unconfigured_or_unknown_candidates_rejected(setup, provider, model):
    selection, loop = setup.selector()
    with pytest.raises(ModelNotAvailableError):
        await selection.select({"model": model, "provider": provider})
    assert loop.model == "old-model"
    assert setup.calls == []


async def test_projects_and_restarted_runtime_use_independent_selections(setup):
    project_a, _ = setup.selector()
    project_b, _ = setup.selector()
    await project_a.select({"model": "new-model", "provider": "openai"})
    assert (await project_a.options({}))["model"] == "new-model"
    assert (await project_b.options({}))["model"] == "old-model"
    configured = models.load_runtime_config(None, None)
    restarted, _ = setup.selector(configured.agents.defaults.model)
    assert (await restarted.options({}))["model"] == "old-model"


async def test_runtime_is_ready_before_factory_is_used(setup):
    gate = asyncio.Event()
    entered = asyncio.Event()
    _, loop = setup.selector()
    factory_calls = []

    async def ready():
        entered.set()
        await gate.wait()

    def factory():
        factory_calls.append(True)
        return loop

    selection = models.DesktopModelSelection(factory, ready)
    task = asyncio.create_task(selection.select({"model": "new-model", "provider": "openai"}))
    await asyncio.wait_for(entered.wait(), 1)
    assert not task.done()
    assert factory_calls == []
    assert setup.calls == []
    gate.set()
    result = await asyncio.wait_for(task, 1)
    assert result["model"] == "new-model"
