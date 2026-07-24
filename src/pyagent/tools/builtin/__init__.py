"""内置工具集合。

每个文件定义一个 @tool 装饰的工具类，ToolDiscovery 会自动扫描加载。
工具列表：
- read_file:  读取文件内容
- write_file: 写入文件
- edit_file:  精确替换编辑
- run_bash:   执行 shell 命令
- grep:       正则搜索文件内容
- find_files: 按名称模式查找文件
- list_dir:   列出目录内容
"""

from pyagent.tools.builtin.bash import RunBashTool
from pyagent.tools.builtin.edit import EditFileTool
from pyagent.tools.builtin.find import FindFilesTool
from pyagent.tools.builtin.grep import GrepTool
from pyagent.tools.builtin.ls import ListDirTool
from pyagent.tools.builtin.read import ReadFileTool
from pyagent.tools.builtin.write import WriteFileTool

__all__ = [
    "RunBashTool",
    "ReadFileTool",
    "WriteFileTool",
    "EditFileTool",
    "GrepTool",
    "FindFilesTool",
    "ListDirTool",
]
