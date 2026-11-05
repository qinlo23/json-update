# Build on Windows: uv run --group build pyinstaller --clean json-update.spec
from pathlib import Path

root = Path(SPECPATH)
a = Analysis([str(root / 'main.py')], pathex=[str(root / 'src')],
             binaries=[], datas=[], hiddenimports=[], hookspath=[],
             hooksconfig={}, runtime_hooks=[], excludes=[], noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], name='JSON-Batch-Editor',
          debug=False, bootloader_ignore_signals=False, strip=False,
          upx=False, console=False)
