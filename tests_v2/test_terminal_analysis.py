import pytest

from daemon_v2.analysis.terminal import (
    is_pasted_prompt_command,
    is_pulse_inspection_command,
    is_test_command,
    terminal_labels,
    useful_command_lines,
)


@pytest.mark.parametrize(
    "command",
    [
        "python -m pytest",
        "python3 -m pytest tests_v2",
        ".venv/bin/python -m pytest tests_v2/test_routes.py",
        "pytest",
        "npm test",
        "swift test",
        "make test",
    ],
)
def test_recognizes_supported_test_commands(command):
    assert is_test_command(command)


@pytest.mark.parametrize(
    "command",
    ["echo pytest", "npm testSomething", "make tester"],
)
def test_rejects_test_command_false_positives(command):
    assert not is_test_command(command)


@pytest.mark.parametrize(
    "command",
    [
        "curl http://127.0.0.1:5000/trace/today",
        "curl http://127.0.0.1:5000/trace/today.md | head",
        "curl http://127.0.0.1:5000/trace/days | python -m json.tool",
        "curl http://127.0.0.1:5000/days",
        "curl http://127.0.0.1:5000/day/2026-07-05 | rg Session",
    ],
)
def test_recognizes_pulse_inspection_commands(command):
    assert is_pulse_inspection_command(command)


def test_recognizes_pasted_prompt_but_keeps_legitimate_single_marker():
    prompt = (
        "Pulse_V2 — extraction terminal\n"
        "Contexte : conserver le comportement actuel.\n"
        "Objectif : déplacer les fonctions pures.\n"
        "À faire : adapter les imports.\n"
        "Validation attendue : tous les tests passent."
    )

    assert is_pasted_prompt_command(prompt)
    assert not is_pasted_prompt_command("echo 'Contexte : build local'")


def test_keeps_useful_lines_from_multiline_inspection_command():
    command = (
        "curl http://127.0.0.1:5000/trace/today.md | head\n"
        "make test\n"
        "curl http://127.0.0.1:5000/days"
    )

    assert useful_command_lines(command) == ["make test"]


def test_terminal_labels_have_stable_order():
    activity = {
        "details": {
            "command": (
                "python -m daemon_v2.main\n"
                "git status\n"
                "make test"
            ),
            "exit_code": 1,
        }
    }

    assert terminal_labels(activity) == ["test", "git", "pulse", "erreur"]


@pytest.mark.parametrize(
    ("exit_code", "has_error"),
    [
        (0, False),
        (1, True),
        (None, False),
        (False, False),
        (True, False),
        ("1", False),
    ],
)
def test_terminal_error_label_preserves_exit_code_contract(exit_code, has_error):
    labels = terminal_labels(
        {"details": {"command": "echo useful", "exit_code": exit_code}}
    )

    assert ("erreur" in labels) is has_error
