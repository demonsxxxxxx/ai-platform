from tools.frontend_official_entry_switch_plan import (
    build_switch_plan,
    run_preflight_checks,
)


OLD_COMMAND = (
    "python3 /home/xinlin.jiang/ai-platform-phaseb/services/ai-platform/tools/"
    "serve_lambchat_thin_shell.py --host 0.0.0.0 --port 18001 "
    "--root /home/xinlin.jiang/lambchat-poc/frontend-dist-ai-platform "
    "--api-base http://127.0.0.1:8020"
)


def test_switch_plan_requires_operator_approval_and_records_rollback():
    plan = build_switch_plan(
        old_pid=25816,
        old_command=OLD_COMMAND,
        new_server_script="/home/xinlin.jiang/frontend-pr111-smoke/tools/serve_ai_platform_frontend.py",
        new_root="/home/xinlin.jiang/frontend-pr111-smoke/dist",
        api_base="http://127.0.0.1:8020",
        port=18001,
        expected_commit="5e3a747e031e7f1a1ce7c525d19a0ca2d64519ed",
        log_path="/home/xinlin.jiang/frontend-pr111-smoke/official-18001.log",
    )

    assert plan["schema_version"] == "ai-platform.frontend-official-entry-switch-plan.v1"
    assert plan["requires_operator_approval"] is True
    assert plan["does_not_execute"] is True
    assert plan["target"]["port"] == 18001
    assert plan["target"]["expected_commit"] == "5e3a747e031e7f1a1ce7c525d19a0ca2d64519ed"
    assert plan["preflight_checks"] == [
        "confirm_old_pid_matches_command",
        "confirm_new_root_has_index",
        "confirm_build_provenance_matches_expected_commit",
        "confirm_api_health_ok",
    ]
    assert plan["switch_commands"] == [
        "kill 25816",
        "nohup python3 /home/xinlin.jiang/frontend-pr111-smoke/tools/serve_ai_platform_frontend.py --host 0.0.0.0 --port 18001 --root /home/xinlin.jiang/frontend-pr111-smoke/dist --api-base http://127.0.0.1:8020 > /home/xinlin.jiang/frontend-pr111-smoke/official-18001.log 2>&1 &",
    ]
    assert plan["rollback_commands"] == [
        "pkill -f 'serve_ai_platform_frontend.py --host 0.0.0.0 --port 18001'",
        f"nohup {OLD_COMMAND} > /home/xinlin.jiang/frontend-pr111-smoke/official-18001.rollback.log 2>&1 &",
    ]
    assert "tools/frontend_static_proxy_smoke.py --base-url http://127.0.0.1:18001" in plan["post_switch_smoke_commands"][0]
    assert "tools/verify_company_login_pair.sh" in plan["manual_company_login_gate"]


def test_switch_plan_rejects_untrusted_shell_arguments():
    try:
        build_switch_plan(
            old_pid=25816,
            old_command=OLD_COMMAND,
            new_server_script="/tmp/server.py; rm -rf /",
            new_root="/home/xinlin.jiang/frontend-pr111-smoke/dist",
            api_base="http://127.0.0.1:8020",
            port=18001,
            expected_commit="5e3a747e031e7f1a1ce7c525d19a0ca2d64519ed",
            log_path="/home/xinlin.jiang/frontend-pr111-smoke/official-18001.log",
        )
    except ValueError as exc:
        assert "unsafe shell argument" in str(exc)
    else:
        raise AssertionError("unsafe shell argument should be rejected")


def test_preflight_checks_accept_current_old_entry_and_new_dist(tmp_path, monkeypatch):
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<html>AI Platform</html>", encoding="utf-8")
    (dist / "ai-platform-build-provenance.json").write_text(
        '{"schema_version":"ai-platform.frontend-build-provenance.v1","git":{"commit":"5e3a747e031e7f1a1ce7c525d19a0ca2d64519ed","dirty":false}}',
        encoding="utf-8",
    )

    def fake_fetch_json(url: str, timeout: int):
        assert url == "http://127.0.0.1:8020/api/ai/health"
        assert timeout == 8
        return {"status_code": 200, "body": {"status": "ok"}}

    monkeypatch.setattr("tools.frontend_official_entry_switch_plan._fetch_json", fake_fetch_json)

    result = run_preflight_checks(
        old_pid=25816,
        expected_old_command=OLD_COMMAND,
        observed_processes=[
            {"pid": 25816, "args": OLD_COMMAND},
            {
                "pid": 29264,
                "args": "python3 /home/xinlin.jiang/frontend-pr111-smoke/tools/serve_ai_platform_frontend.py --host 0.0.0.0 --port 18003",
            },
        ],
        new_root=str(dist),
        api_base="http://127.0.0.1:8020",
        expected_commit="5e3a747e031e7f1a1ce7c525d19a0ca2d64519ed",
        timeout=8,
    )

    assert result["status"] == "pass"
    assert result["failed_checks"] == []
    assert result["checks"]["old_pid_matches_command"]["ok"] is True
    assert result["checks"]["new_root_has_index"]["ok"] is True
    assert result["checks"]["build_provenance_matches_expected_commit"]["ok"] is True
    assert result["checks"]["api_health_ok"]["ok"] is True


def test_preflight_checks_fail_when_old_entry_changed(tmp_path, monkeypatch):
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<html>AI Platform</html>", encoding="utf-8")
    (dist / "ai-platform-build-provenance.json").write_text(
        '{"schema_version":"ai-platform.frontend-build-provenance.v1","git":{"commit":"5e3a747e031e7f1a1ce7c525d19a0ca2d64519ed","dirty":false}}',
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "tools.frontend_official_entry_switch_plan._fetch_json",
        lambda *_args, **_kwargs: {"status_code": 200, "body": {"status": "ok"}},
    )

    result = run_preflight_checks(
        old_pid=25816,
        expected_old_command=OLD_COMMAND,
        observed_processes=[{"pid": 25816, "args": "python3 other.py --port 18001"}],
        new_root=str(dist),
        api_base="http://127.0.0.1:8020",
        expected_commit="5e3a747e031e7f1a1ce7c525d19a0ca2d64519ed",
    )

    assert result["status"] == "fail"
    assert "old_pid_matches_command" in result["failed_checks"]
