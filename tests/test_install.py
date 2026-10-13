import importlib.util
from pathlib import Path

from pto import config

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("pto_install", ROOT / "scripts" / "install.py")
install = importlib.util.module_from_spec(spec)
spec.loader.exec_module(install)

CFG_TEXT = (ROOT / "config.yaml").read_text(encoding="utf-8")


def test_every_configured_model_has_a_download_source():
    missing = [m for m in install.needed_models(config.load()) if m not in install.MODEL_URLS]
    assert not missing, f"add these to scripts/install.py MODEL_URLS: {missing}"


def test_gpu_vendor_prefers_discrete_cards():
    assert install.pick_vendor(["AMD Radeon RX 7800 XT", "Intel(R) UHD Graphics 770"]) == "amd"
    assert install.pick_vendor(["Intel(R) UHD Graphics", "NVIDIA GeForce RTX 4070"]) == "nvidia"
    assert install.pick_vendor(["Intel(R) Arc(TM) A770 Graphics"]) == "intel"
    assert install.pick_vendor([]) == "nvidia"


def test_fix_paths_moves_folders_off_missing_drives_and_keeps_comments(tmp_path):
    text, changes = install.fix_paths(CFG_TEXT, tmp_path, drive_exists=lambda p: False)
    cfg = config.validate_text(text)
    assert cfg.paths.data_dir == tmp_path / "data"
    assert cfg.paths.comfyui_dir == tmp_path / "ComfyUI_windows_portable"
    assert len(changes) == 2 and "# db, designs, logs" in text
    assert text.count("\n") == CFG_TEXT.count("\n")  # only the two values changed

    same, none = install.fix_paths(CFG_TEXT, tmp_path, drive_exists=lambda p: True)
    assert same == CFG_TEXT and none == []
