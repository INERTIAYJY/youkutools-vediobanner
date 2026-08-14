# -*- mode: python ; coding: utf-8 -*-

import shutil
from pathlib import Path


ROOT = Path(SPECPATH).resolve().parent
ffmpeg_dir = ROOT / "tools" / "ffmpeg" / "bin"
datas = []
if ffmpeg_dir.exists() and any(ffmpeg_dir.iterdir()):
    datas.append((str(ffmpeg_dir), "tools/ffmpeg/bin"))


# This is a QWidgets-only app: prune every Qt module family the GUI never
# imports so the bundle stays small. QtCore/QtGui/QtWidgets are required.
QT_EXCLUDES = [
    "PySide6.Qt3DAnimation",
    "PySide6.Qt3DCore",
    "PySide6.Qt3DExtras",
    "PySide6.Qt3DInput",
    "PySide6.Qt3DLogic",
    "PySide6.Qt3DRender",
    "PySide6.QtBluetooth",
    "PySide6.QtCharts",
    "PySide6.QtConcurrent",
    "PySide6.QtDataVisualization",
    "PySide6.QtDBus",
    "PySide6.QtDesigner",
    "PySide6.QtHelp",
    "PySide6.QtLocation",
    "PySide6.QtMultimedia",
    "PySide6.QtMultimediaWidgets",
    "PySide6.QtNetwork",
    "PySide6.QtNetworkAuth",
    "PySide6.QtNfc",
    "PySide6.QtOpenGL",
    "PySide6.QtOpenGLWidgets",
    "PySide6.QtPdf",
    "PySide6.QtPdfWidgets",
    "PySide6.QtPositioning",
    "PySide6.QtPrintSupport",
    "PySide6.QtQml",
    "PySide6.QtQmlModels",
    "PySide6.QtQmlWorkerScript",
    "PySide6.QtQuick",
    "PySide6.QtQuick3D",
    "PySide6.QtQuickWidgets",
    "PySide6.QtRemoteObjects",
    "PySide6.QtSensors",
    "PySide6.QtSerialPort",
    "PySide6.QtSql",
    "PySide6.QtSvg",
    "PySide6.QtSvgWidgets",
    "PySide6.QtTest",
    "PySide6.QtTextToSpeech",
    "PySide6.QtVirtualKeyboard",
    "PySide6.QtWebChannel",
    "PySide6.QtWebEngineCore",
    "PySide6.QtWebEngineWidgets",
    "PySide6.QtWebSockets",
    "PySide6.QtXml",
]

a = Analysis(
    [str(ROOT / "src" / "batch_ab_video" / "main.py")],
    pathex=[str(ROOT / "src")],
    binaries=[],
    datas=datas,
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=QT_EXCLUDES,
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="BatchABVideo",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="BatchABVideo",
)


# PySide6's base hook copies every Qt DLL regardless of Analysis.excludes,
# so prune the modules this QWidgets-only app never imports. Verified safe by
# a launch check after each rebuild.
def _prune_unused_qt(dist_root: Path) -> None:
    qt_dir = dist_root / "_internal" / "PySide6"
    if not qt_dir.is_dir():
        return
    for dll in [
        "Qt6Network.dll",
        "Qt6OpenGL.dll",
        "Qt6Pdf.dll",
        "Qt6Qml.dll",
        "Qt6QmlMeta.dll",
        "Qt6QmlModels.dll",
        "Qt6QmlWorkerScript.dll",
        "Qt6Quick.dll",
        "Qt6Svg.dll",
        "Qt6VirtualKeyboard.dll",
    ]:
        (qt_dir / dll).unlink(missing_ok=True)
    plugins = qt_dir / "plugins"
    for rel in [
        "iconengines/qsvgicon.dll",
        "imageformats/qpdf.dll",
        "imageformats/qsvg.dll",
        "networkinformation/qnetworklistmanager.dll",
        "platforminputcontexts/qtvirtualkeyboardplugin.dll",
        "qmltooling",
        "scenegraph",
        "virtualkeyboard",
    ]:
        target = plugins / rel
        if target.is_dir():
            shutil.rmtree(target, ignore_errors=True)
        else:
            target.unlink(missing_ok=True)


_prune_unused_qt(ROOT / "dist" / "BatchABVideo")
