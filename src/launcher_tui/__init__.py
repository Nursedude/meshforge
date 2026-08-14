"""MeshForge TUI Launcher Package.

The TUI runs in FLAT-import mode: ``main.py`` is executed as a script and
imports its siblings absolutely (``from backend import ...``) after
injecting this directory into ``sys.path``. This __init__ performs the
same injection so package-path imports (``from launcher_tui.handlers.x
import ...`` — used by launcher.py and tests) resolve their flat internal
imports too.

Q5 (audit W18): this file used to import ``.backend`` and ``.main``
relatively, creating a SECOND module object for each (launcher_tui.backend
vs flat backend — two distinct DialogBackend classes), and defined a
``main()`` function that shadowed the ``launcher_tui.main`` submodule so
``python -m launcher_tui`` ran a bare launcher with NO crash hook, stderr
redirect, or terminal-restore cleanup. It now imports nothing; __main__
routes through the real ``main.py:main()``.
"""

import os as _os
import sys as _sys

_here = _os.path.dirname(_os.path.abspath(__file__))
for _p in (_os.path.dirname(_here), _here):  # src/, src/launcher_tui/
    if _p not in _sys.path:
        _sys.path.insert(0, _p)

__all__ = []
