"""The API's logging (api/app.py): uvicorn configures only its own loggers, so `juju.*` messages
below WARNING never appeared in `docker compose logs api`. The assistant's one line a question says
why a reply was kept or dropped; this makes sure it is seen."""
import logging

from fastapi.testclient import TestClient

from juju.api import app as app_module


def test_a_logger_is_given_one_stderr_handler_at_info_and_asking_twice_adds_no_more(capsys):
    probe = logging.getLogger("juju_probe")
    probe.handlers.clear()
    probe.propagate = False
    try:
        app_module.configure_logging(probe)
        app_module.configure_logging(probe)
        assert len(probe.handlers) == 1 and probe.level == logging.INFO
        probe.info("assistant: ok after 1 tool calls")
        probe.debug("not at debug")
        err = capsys.readouterr().err
        assert err == "INFO juju_probe assistant: ok after 1 tool calls\n"
    finally:
        probe.handlers.clear()


def test_the_handler_follows_stderr_so_a_replaced_stream_never_breaks_a_log_call(capsys):
    probe = logging.getLogger("juju_probe2")
    probe.handlers.clear()
    probe.propagate = False
    try:
        app_module.configure_logging(probe)
        probe.info("first")
        capsys.readouterr()
        probe.info("second")                   # the capture was reset between the two
        assert "second" in capsys.readouterr().err
    finally:
        probe.handlers.clear()


def test_the_server_turns_it_on_for_the_whole_juju_package_when_it_starts():
    juju = logging.getLogger("juju")
    before = (list(juju.handlers), juju.level)
    try:
        juju.handlers.clear()
        juju.setLevel(logging.NOTSET)
        assert not juju.isEnabledFor(logging.INFO) or logging.getLogger().isEnabledFor(logging.INFO)
        with TestClient(app_module.app):       # entering it runs the startup
            assert juju.isEnabledFor(logging.INFO)
            assert logging.getLogger("juju.live_assistant").isEnabledFor(logging.INFO)
            assert any(isinstance(h, app_module._CurrentStderr) for h in juju.handlers)
    finally:
        juju.handlers[:] = before[0]
        juju.setLevel(before[1])
