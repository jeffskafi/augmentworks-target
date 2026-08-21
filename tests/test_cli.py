from __future__ import annotations

import json

from augmentworks_target.cli import build_parser, main


def test_cli_has_no_literal_secret_option() -> None:
    parser = build_parser()
    option_strings = {
        option
        for action in parser._subparsers._group_actions[0]
        .choices[  # type: ignore[attr-defined]
            "verify"
        ]
        ._actions
        for option in action.option_strings
    }

    assert "--secret" not in option_strings
    assert "--secret-env" in option_strings


def test_lifecycle_cli_requires_key_id(tmp_path, capsys) -> None:
    fixture = tmp_path / "fixture.json"
    fixture.write_text(json.dumps({"synthetic": True}), encoding="utf-8")

    exit_code = main(["verify", "https://target.test", "--fixture", str(fixture)])

    captured = capsys.readouterr()
    assert exit_code == 2
    assert "--key-id is required" in captured.err
