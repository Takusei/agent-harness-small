from unittest.mock import Mock

import pytest

from agent_harness import cli
from agent_harness.config import settings


def test_local_key_blocks_inherited_key_and_values_are_literal(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-inherited-key")
    monkeypatch.setenv("HARNESS_MAX_TURNS", "90")
    path = tmp_path / ".env"
    path.write_text('OPENAI_API_KEY=\nHARNESS_MAX_TURNS=4\nHARNESS_TASK="${HOME}"\n')
    config = settings(path)
    assert config["OPENAI_API_KEY"] == ""
    assert config["HARNESS_MAX_TURNS"] == "4"
    assert config["HARNESS_TASK"] == "${HOME}"
    # Loading does not mutate the process environment.
    assert settings(tmp_path / "missing")["OPENAI_API_KEY"] == "test-inherited-key"


def test_cli_overrides_env_and_numeric_defaults_are_converted():
    args = cli.parser(
        {
            "HARNESS_REPO": "fixtures/slugify",
            "HARNESS_MAX_TURNS": "4",
            "HARNESS_MAX_SECONDS": "12.5",
            "HARNESS_OUTPUT_BYTES": "2048",
        }
    ).parse_args(["--scripted", "slugify", "--max-turns", "7"])
    assert args.max_turns == 7
    assert args.max_seconds == 12.5
    assert args.output_bytes == 2048
    assert str(args.repo) == "fixtures/slugify"


def test_environment_cannot_enable_live_mode():
    with pytest.raises(SystemExit) as exc:
        cli.parser({"HARNESS_REPO": "fixture", "HARNESS_LIVE": "true"}).parse_args([])
    assert exc.value.code == 2


def test_blank_local_key_rejects_live_before_execution(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text(
        "OPENAI_API_KEY=\nHARNESS_MODEL=test-model\nHARNESS_REPO=fixture\n"
    )
    monkeypatch.setenv("OPENAI_API_KEY", "test-inherited-key")
    monkeypatch.setattr("sys.argv", ["agent-harness", "--live"])
    execute = Mock(side_effect=AssertionError("must not start execution"))
    monkeypatch.setattr(cli, "execute", execute)
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 2
    execute.assert_not_called()
