# -*- mode: python ; coding: utf-8 -*-
"""MBESQC PyInstaller spec file."""

import sys
from pathlib import Path

block_cipher = None

ROOT = Path(SPECPATH)
SHARED = ROOT.parent.parent / "_shared"

a = Analysis(
    [str(ROOT / 'desktop' / '__main__.py')],
    pathex=[str(ROOT), str(SHARED)],
    binaries=[],
    datas=[
        # Include _shared geoview_pyside6 package
        (str(SHARED / 'geoview_pyside6'), 'geoview_pyside6'),
    ],
    hiddenimports=[
        'desktop',
        'desktop.main',
        'desktop.app_controller',
        'desktop.panels.dashboard_panel',
        'desktop.panels.project_detail_panel',
        'desktop.panels.upload_panel',
        'desktop.panels.analysis_panel',
        'desktop.panels.project_form_panel',
        'desktop.panels.dqr_panel',
        'desktop.services.data_service',
        'desktop.services.om_client',
        'desktop.services.analysis_service',
        'desktop.services.export_service',
        'desktop.services.caris_batch_service',
        'desktop.services.dqr_service',
        'desktop.widgets.toast',
        'desktop.widgets.drop_zone',
        'desktop.widgets.qc_unlock_grid',
        'mbes_qc',
        'mbes_qc.runner',
        'mbes_qc.dqr_ppt',
        'pds_toolkit',
        'pds_toolkit.gsf_reader',
        'pds_toolkit.hvf_reader',
        'pds_toolkit.models',
        'geoview_pyside6',
        'geoview_pyside6.app_base',
        'geoview_pyside6.constants',
        'numpy',
        'matplotlib',
        'matplotlib.backends.backend_agg',
        'openpyxl',
        'pptx',
        'docx',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'tkinter', 'unittest', 'test', 'PyQt5', 'PyQt6',
        # AI/ML — not used by MBESQC
        'torch', 'torchvision', 'torchaudio',
        'tensorflow', 'keras',
        'onnxruntime', 'sklearn', 'scikit-learn',
        # Heavy libs not needed
        'cv2', 'llvmlite', 'numba',
        'notebook', 'jupyterlab', 'jupyter', 'ipykernel',
        'pyarrow', 'babel',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='MBESQC',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    icon=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='MBESQC',
)
