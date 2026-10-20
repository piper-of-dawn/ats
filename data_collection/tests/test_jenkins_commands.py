"""Execute Jenkins shell blocks with a fake Docker CLI; no daemon or secrets needed."""

import json
import os
from pathlib import Path
import re
import subprocess

import pytest

PROJECT = Path(__file__).resolve().parents[1]


def _scripts():
    text = (PROJECT / "Jenkinsfile").read_text()
    return re.findall(r"'''(#!/bin/sh\n.*?)'''", text, re.DOTALL)


def _environment(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    executable = bin_dir / "docker"
    executable.write_text(r'''#!/usr/bin/env python3
import json, os, sys
args = sys.argv[1:]
login_has_password = bool(sys.stdin.read()) if args[0] == 'login' else False
with open(os.environ['TEST_DOCKER_CALLS'], 'a') as stream:
    stream.write(json.dumps({'argv': args, 'has_runtime_key': bool(os.getenv('MASSIVE_API_KEY')),
                            'has_registry_token': bool(os.getenv('DOCKERHUB_TOKEN')),
                            'docker_config': os.getenv('DOCKER_CONFIG'),
                            'login_has_password': login_has_password})+'\n')
if args[0] == 'pull':
    sys.exit(int(os.getenv('TEST_PULL_EXIT', '0')))
''')
    executable.chmod(0o755)
    tickers = "AAPL\nMSFT\n"
    (tmp_path / ".collection-tickers.txt").write_text(tickers)
    calls_file = tmp_path / "calls.jsonl"
    env = dict(os.environ, PATH=str(bin_dir) + os.pathsep + os.environ["PATH"],
               TEST_DOCKER_CALLS=str(calls_file),
               STATE_DIR=str(tmp_path / "state"), RUN_ID="initial", DATASETS="reference,news",
               START_DATE="2026-10-01", END_DATE="2026-10-20",
               COLLECTION_IMAGE="piperofthedawn/ats:data-collection", COLLECTION_CONTAINER="test-collector")
    for name in ("MASSIVE_API_KEY", "DOCKERHUB_TOKEN", "DOCKERHUB_USERNAME", "DOCKER_CONFIG"):
        env.pop(name, None)
    return env, calls_file, tickers


@pytest.mark.parametrize("authenticated", [False, True])
@pytest.mark.parametrize("optional_parameters_set", [False, True])
def test_jenkins_shell_commands_pull_and_run_with_scoped_credentials(tmp_path, authenticated, optional_parameters_set):
    env, calls_file, tickers = _environment(tmp_path)
    if not optional_parameters_set:
        for name in ("DATASETS", "START_DATE", "END_DATE"):
            env.pop(name)
    if authenticated:
        env["COLLECTION_IMAGE"] = "piperofthedawn/ats@sha256:" + "a" * 64
    scripts = _scripts()
    assert len(scripts) == 4
    for index, script in enumerate(scripts):
        subprocess.run(["/bin/sh", "-n"], input=script, text=True, check=True)
        stage_env = dict(env)
        if index == 1 and authenticated:
            stage_env.update(DOCKERHUB_USERNAME="test-user", DOCKERHUB_TOKEN="do-not-log-registry-token")
        if index == 2:
            stage_env["MASSIVE_API_KEY"] = "do-not-log-this-secret"
        result = subprocess.run(["/bin/sh"], input=script, text=True, cwd=tmp_path,
                                env=stage_env, capture_output=True, check=True)
        assert "do-not-log-this-secret" not in result.stdout + result.stderr
        assert "do-not-log-registry-token" not in result.stdout + result.stderr
    assert (tmp_path / "state/runs/initial/tickers.txt").read_text() == tickers
    calls = [json.loads(line) for line in calls_file.read_text().splitlines()]
    commands = [c["argv"][0] for c in calls]
    assert "build" not in commands
    assert commands.index("pull") < commands.index("run")
    pull = next(c for c in calls if c["argv"][0] == "pull")
    assert pull["argv"] == ["pull", env["COLLECTION_IMAGE"]]
    assert not pull["has_runtime_key"]
    logins = [c for c in calls if c["argv"][0] == "login"]
    assert len(logins) == int(authenticated)
    if authenticated:
        assert logins[0]["argv"] == ["login", "--username", "test-user", "--password-stdin"]
        assert logins[0]["login_has_password"]
        assert not Path(logins[0]["docker_config"]).exists()
    run = next(c for c in calls if c["argv"][0] == "run")
    assert run["has_runtime_key"]
    assert not run["has_registry_token"]
    argv = run["argv"]
    assert "--pull=never" in argv
    assert env["COLLECTION_IMAGE"] in argv
    assert argv[argv.index("--env") + 1] == "MASSIVE_API_KEY"
    if optional_parameters_set:
        assert argv[argv.index("--datasets") + 1] == "reference,news"
        assert argv[argv.index("--start") + 1] == "2026-10-01"
        assert argv[argv.index("--end") + 1] == "2026-10-20"
    else:
        assert not {"--datasets", "--start", "--end"}.intersection(argv)
    assert argv[argv.index("--output") + 1] == "/data/runs/initial/stocks.jsonl"
    assert all("do-not-log-this-secret" not in arg for arg in argv)
    assert "do-not-log-registry-token" not in calls_file.read_text()
    assert commands.count("stop") == 2

    # A changed universe must not overwrite an existing run's input file.
    (tmp_path / ".collection-tickers.txt").write_text("GOOG\n")
    result = subprocess.run(["/bin/sh"], input=scripts[0], text=True, cwd=tmp_path, env=env, capture_output=True)
    assert result.returncode != 0
    assert (tmp_path / "state/runs/initial/tickers.txt").read_text() == tickers


def test_jenkins_pull_failure_stops_and_removes_login_config(tmp_path):
    env, calls_file, _ = _environment(tmp_path)
    env.update(TEST_PULL_EXIT="1", DOCKERHUB_USERNAME="test-user", DOCKERHUB_TOKEN="registry-secret")
    result = subprocess.run(["/bin/sh"], input=_scripts()[1], text=True, cwd=tmp_path,
                            env=env, capture_output=True)
    assert result.returncode != 0
    calls = [json.loads(line) for line in calls_file.read_text().splitlines()]
    assert [call["argv"][0] for call in calls] == ["login", "pull"]
    assert not Path(calls[0]["docker_config"]).exists()
