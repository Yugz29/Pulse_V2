import subprocess

from daemon_v2.app_watcher import frontmost_application


def test_reads_frontmost_application_name_without_macos():
    responses = iter(
        [
            subprocess.CompletedProcess(
                args=[], returncode=0, stdout="[ ASN:0x0-0x123 ]\n", stderr=""
            ),
            subprocess.CompletedProcess(
                args=[],
                returncode=0,
                stdout='{ "LSDisplayName"="Visual Studio Code" }\n',
                stderr="",
            ),
        ]
    )

    def runner(*args, **kwargs):
        return next(responses)

    assert frontmost_application(runner) == "Visual Studio Code"


def test_returns_none_when_no_frontmost_application():
    def runner(*args, **kwargs):
        return subprocess.CompletedProcess(
            args=[], returncode=0, stdout="[ NULL ]\n", stderr=""
        )

    assert frontmost_application(runner) is None
