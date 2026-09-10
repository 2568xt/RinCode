"""RinCode 的 Internal Module Entry Point，支持执行 ``python -m rincode``。

Python 以模块方式启动 Package 时会进入这里，再把控制权交给 `rincode.cli.commands.run`。该入口只负责
连接解释器与 CLI，不解析参数、不初始化 Agent Runtime，也不维护另一套命令实现；它应与安装后的
`rincode` Console Script 表现一致。
"""

from rincode.cli.commands import run

if __name__ == "__main__":
    run()
