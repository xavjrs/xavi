"""The cross-platform launcher (run.py) and the start scripts."""

import importlib.util
import os

import pytest

from conftest import ROOT


@pytest.fixture
def run_module():
    spec = importlib.util.spec_from_file_location("xavi_run", os.path.join(ROOT, "run.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_every_mode_points_at_real_files(run_module):
    assert set(run_module.MODES) == {"web", "app", "terminal"}
    for mode, (requirements, command, port) in run_module.MODES.items():
        assert os.path.exists(os.path.join(ROOT, requirements)), mode
        script = next(part for part in command if part.endswith(".py") or part == "-m")
        if script != "-m":
            assert os.path.exists(os.path.join(ROOT, script)), mode


def test_unknown_mode_prints_help_and_exits(run_module, capsys):
    with pytest.raises(SystemExit) as stop:
        run_module.main(["nonsense"])
    assert stop.value.code == 1 and "python run.py web" in capsys.readouterr().out


def test_old_python_is_refused_with_a_download_link(run_module, monkeypatch, capsys):
    monkeypatch.setattr(run_module.sys, "version_info", (3, 8, 0))
    with pytest.raises(SystemExit):
        run_module.main(["web"])
    assert "python.org" in capsys.readouterr().out


def test_private_environment_lives_inside_the_project(run_module):
    assert run_module.venv_python().startswith(os.path.join(ROOT, ".venv"))


def test_requirements_are_bounded_and_include_what_each_mode_imports():
    for name in ("requirements.txt", "web/requirements.txt"):
        lines = [l.strip() for l in open(os.path.join(ROOT, name), encoding="utf-8") if l.strip() and not l.startswith("#")]
        assert all(("<" in l or "gunicorn" in l or "tzdata" in l) for l in lines), name       # a ceiling on every real dependency
    web = open(os.path.join(ROOT, "web", "requirements.txt"), encoding="utf-8").read()
    assert all(pkg in web for pkg in ("flask", "gunicorn", "yfinance", "requests", "tzdata"))
    assert "streamlit" in open(os.path.join(ROOT, "requirements.txt"), encoding="utf-8").read()


@pytest.mark.parametrize("script", ["start_web.bat", "start_app.bat", "start_web.sh", "start_app.sh"])
def test_start_scripts_call_the_launcher(script):
    text = open(os.path.join(ROOT, script), encoding="utf-8").read()
    assert "run.py" in text and ("3.10" in text)
    assert "py -3.13 web" not in text                         # no hard-coded interpreter any more
