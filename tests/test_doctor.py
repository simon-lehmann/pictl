"""Doctor checks shape + behaviour."""

from __future__ import annotations

from pathlib import Path

from lib import doctor


def test_run_returns_ok_and_checks(pictl_home: Path):
    out = doctor.run()
    assert "ok" in out
    assert "checks" in out
    assert isinstance(out["checks"], list)


def test_each_check_has_required_fields(pictl_home: Path):
    out = doctor.run()
    for c in out["checks"]:
        assert "name" in c
        assert "ok" in c
        assert "detail" in c


def test_python_version_check_passes_on_supported_runtime(pictl_home: Path):
    out = doctor.run()
    py = next(c for c in out["checks"] if c["name"] == "python_version")
    # CI runs on 3.10+ so this should always pass.
    assert py["ok"] is True


def test_data_dir_check_picks_up_test_home(pictl_home: Path):
    out = doctor.run()
    dd = next(c for c in out["checks"] if c["name"] == "data_dir")
    assert str(pictl_home) in dd["detail"]
    assert dd["ok"] is True
