"""根目录薄入口脚本。

``app/main.py`` 使用包内相对导入（``from .engine import Engine``），PyInstaller
不能直接把包内模块当入口，因此在根目录放这层薄包装：只负责把控制权交给
``app.main.main()``，业务逻辑全部留在 ``app`` 包内。

源码运行等价于：``python -m app.main``；打包后由 PyInstaller 把本脚本作为入口。
"""

import sys

from app.main import main

if __name__ == "__main__":
    sys.exit(main())
