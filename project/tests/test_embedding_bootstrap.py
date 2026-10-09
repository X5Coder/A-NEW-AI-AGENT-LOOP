"""Embedding bootstrap local-state checks (deterministic, no network).

Covers _local_state(): missing / incomplete / ready classification of the
project/embedding_model/ folder. Never touches the real folder or network.
"""
import sys
from pathlib import Path

project_root = Path(__file__).resolve().parents[1]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from core.embedding.bootstrap import MARKER, _local_state


def _make(folder, weights=False, configs=False, marker=False):
    folder.mkdir(parents=True, exist_ok=True)
    if weights:
        (folder / "model.safetensors").write_bytes(b"fake-weights")
    if configs:
        (folder / "config.json").write_text("{}", encoding="utf-8")
        (folder / "tokenizer.json").write_text("{}", encoding="utf-8")
    if marker:
        (folder / MARKER).write_text("complete\n", encoding="utf-8")
    return folder


def test_missing_dir(tmp_path):
    assert _local_state(tmp_path / "nope") == "missing"


def test_empty_dir_is_missing(tmp_path):
    assert _local_state(_make(tmp_path / "empty")) == "missing"


def test_configs_without_weights_is_incomplete(tmp_path):
    assert _local_state(_make(tmp_path / "c", configs=True)) == "incomplete"


def test_weights_without_marker_is_incomplete(tmp_path):
    assert _local_state(_make(tmp_path / "w", weights=True, configs=True)) == "incomplete"


def test_marker_without_files_is_not_ready(tmp_path):
    assert _local_state(_make(tmp_path / "m", marker=True)) != "ready"


def test_full_folder_is_ready(tmp_path):
    assert _local_state(_make(tmp_path / "r", weights=True, configs=True, marker=True)) == "ready"
