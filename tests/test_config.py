import copy
from pathlib import Path

import pytest
import yaml

from app.config.loader import ConfigError, load_task_config


def raw_config():
    return yaml.safe_load(Path("configs/tasks/narrative-travel-a2.yaml").read_text(encoding="utf-8"))


def write_config(tmp_path, data):
    path = tmp_path / "task.yaml"
    path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return path


def test_valid_config_loads():
    config = load_task_config("configs/tasks/narrative-travel-a2.yaml")
    assert config.total_max == 10
    assert config.error_type_keys
    assert config.code_map["AUX_MOVE"].type == "助动词"


def test_unknown_error_code_type_is_rejected(tmp_path):
    data = raw_config()
    data["error_codes"][0]["type"] = "不存在的类型"
    with pytest.raises(ConfigError):
        load_task_config(write_config(tmp_path, data))


def test_content_bands_need_fallback(tmp_path):
    data = raw_config()
    data["content"]["bands"] = data["content"]["bands"][:-1]
    with pytest.raises(ConfigError):
        load_task_config(write_config(tmp_path, data))


def test_length_caps_must_be_sorted(tmp_path):
    data = raw_config()
    data["length"]["caps"] = list(reversed(data["length"]["caps"]))
    with pytest.raises(ConfigError):
        load_task_config(write_config(tmp_path, data))
