from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class RepoPaths:
    workspace_root: Path
    nexusflow_repo: Path
    supermix_repo: Path
    supermix_runtime: Path
    neurodsl_repo: Path
    npu_easy_repo: Path
    cifar_repo: Path
    cifar_onnx: Path
    windhawk_dir: Path
    windhawk_program_data: Path
    single_file_lab_repo: Path
    dashboard_file: Path
    workflow_file: Path
    windhawk_mod_file: Path


def discover_paths(base_dir: Path | None = None) -> RepoPaths:
    project_root = (base_dir or Path(__file__).resolve().parents[1]).resolve()
    workspace_root = project_root.parent
    return RepoPaths(
        workspace_root=workspace_root,
        nexusflow_repo=workspace_root / "nexusflow",
        supermix_repo=workspace_root / "Supermix_27",
        supermix_runtime=workspace_root / "Supermix_27" / "runtime_python",
        neurodsl_repo=workspace_root / "NeuroDSL-Infinity-Studio2",
        npu_easy_repo=workspace_root / "npu_easy",
        cifar_repo=workspace_root / "CIFAR-100",
        cifar_onnx=workspace_root / "CIFAR-100" / "cifar100_model.onnx",
        windhawk_dir=Path(r"C:\Program Files\Windhawk"),
        windhawk_program_data=Path(r"C:\ProgramData\Windhawk"),
        single_file_lab_repo=workspace_root / "single-file-lab-studio",
        dashboard_file=project_root / "dashboard" / "omniforge_dashboard.html",
        workflow_file=project_root / "workflows" / "omniforge_bootstrap.nxf",
        windhawk_mod_file=project_root / "windhawk" / "omniforge_hotkey.wh.cpp",
    )

