# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['desktop_client.py'],
    pathex=[],
    binaries=[],
    datas=[('frontend\\templates', 'frontend\\templates'), ('frontend\\static', 'frontend\\static'), ('app\\db\\alembic', 'app\\db\\alembic')],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='kabootar',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['build\\windows\\kabootar.ico'],
)
