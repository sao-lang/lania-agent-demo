---
applyTo: '**/*.{py}'
---

# Python Debug Rules

Python 调试核心策略：**类型标注辅助静态检查 + logging 分级 + pdb 交互式调试 + 最小复现脚本**。

## 调试脚本

```powershell
# ── 静态类型检查 ──
# mypy 类型检查（需要安装 mypy）
pip install mypy
mypy src/ --strict

# 检查指定文件
mypy src/suspected_module.py --strict

# 宽松模式（仅检查标注了的代码）
mypy src/ --check-untyped-defs

# pyright 类型检查（更快）
pip install pyright
pyright src/

# ── Lint 检查 ──
# ruff（超快 linter）
pip install ruff
ruff check src/

# pylint
pip install pylint
pylint src/

# 综合检查（flake8）
pip install flake8
flake8 src/

# ── 单元测试调试 ──
# pytest 运行所有测试
python -m pytest

# 运行指定测试文件
python -m pytest tests/test_suspected.py -v

# 运行特定测试函数
python -m pytest tests/test_suspected.py::test_function -v

# 显示 print 输出（默认 pytest 会捕获 stdout）
python -m pytest -s tests/test_suspected.py

# 失败时进入 pdb
python -m pytest --pdb tests/test_suspected.py

# 显示测试覆盖率
python -m pytest --cov=src/ --cov-report=html

# ── pdb 调试器 ──
# 在代码中插入断点
# breakpoint()  # Python 3.7+ 内置

# 脚本运行进入 pdb
python -m pdb src/script.py

# 从 pytest 进入 pdb（测试失败时自动进入）
python -m pytest --pdb -x tests/test_suspected.py

# ── 性能分析 ──
# cProfile 性能分析
python -m cProfile -o profile.prof src/script.py

# 查看性能报告
python -m pstats profile.prof

# line_profiler（逐行分析，需要安装）
pip install line_profiler
kernprof -l -v src/script.py

# memory_profiler（内存分析，需要安装）
pip install memory_profiler
python -m memory_profiler src/script.py

# ── 依赖检查 ──
# 检查过时的包
pip list --outdated

# 检查依赖树
pipdeptree

# 检查包依赖冲突
pip check
```

## 常见问题与排查

### 类型标注错误

```python
# 使用 reveal_type 查看 mypy 推断的类型（mypy 专用）
reveal_type(some_variable)  # mypy 会输出：Revealed type is "..."
# 注意：reveal_type 仅在 mypy 运行时有效，实际代码中不会执行

# 用 cast 明确类型（仅在类型检查时生效）
from typing import cast
result = cast(ExpectedType, some_value)

# 用 TypedDict 精确描述字典结构
from typing import TypedDict
class Config(TypedDict):
    host: str
    port: int
    debug: bool
```

### None 相关错误

```python
# AttributeError: 'NoneType' object has no attribute 'xxx'
# 排查方向
# 1. 检查函数是否在某些路径下返回了 None
# 2. 检查可选参数是否未传值

# 安全访问模式
if obj is not None and obj.attr is not None:
    # 安全使用

# 用 Optional 明确标注
from typing import Optional
def get_user(id: int) -> Optional[User]:
    ...

# 打点验证
# DEBUG: [function] obj=%s, isNone=%s
```

### 异常处理问题

```python
# 过于宽泛的 except
try:
    risky_operation()
except Exception:  # 过于宽泛，掩盖了真正的错误
    pass

# 正确做法
try:
    risky_operation()
except SpecificError as e:
    # DEBUG: [function] SpecificError=%s
    raise  # 或重新抛出
except Exception as e:
    # DEBUG: [function] unexpected error=%s
    raise
```

### 异步 Python（asyncio）问题

```python
# 常见问题
# 1. 协程未 await → 返回的是 coroutine 对象而非结果
# 2. 事件循环未正确创建/关闭
# 3. 在同步代码中调用异步函数

# 打点验证
import asyncio

async def debug_async():
    # DEBUG: [debug_async] start | args=%s
    await asyncio.sleep(0)  # 让出控制权
    # DEBUG: [debug_async] resumed

# 调试工具
# asyncio.run() 会自动创建新的事件循环
# asyncio.create_task() 创建后台任务
# asyncio.gather() 并发执行多个协程

# 超时控制
try:
    result = await asyncio.wait_for(coro(), timeout=5.0)
except asyncio.TimeoutError:
    # DEBUG: [function] timeout
    pass
```

### 内存泄漏

```python
# 使用 tracemalloc 追踪内存分配
import tracemalloc

tracemalloc.start()
# ... 运行代码 ...
snapshot = tracemalloc.take_snapshot()
top_stats = snapshot.statistics('lineno')

for stat in top_stats[:10]:
    print(stat)  # DEBUG: [memory] %s
```

### 导入/模块问题

```powershell
# 检查模块搜索路径
python -c "import sys; print('\n'.join(sys.path))"

# 检查模块具体位置
python -c "import suspected_module; print(suspected_module.__file__)"

# 检查循环导入
python -X importtime src/script.py 2> import-time.log
```

## 打点规范

```python
# 1. print 打点（快速验证，定位后清理）
# DEBUG: print(f"[function] enter: input={input}")
# DEBUG: print(f"[function] exit: result={result}")

# 2. logging 打点（可分级控制，适合保留）
import logging
logger = logging.getLogger(__name__)
# DEBUG: logger.debug(f"[function] detail: {value}")
# logger.info(f"[function] info: {value}")

# 3. traceback 打点（获取完整调用栈）
import traceback
# DEBUG: traceback.print_stack()  # 打印当前调用栈
# DEBUG: traceback.print_exc()   # 打印异常回溯

# 4. inspect 打点（获取调用者信息）
import inspect
# DEBUG: caller = inspect.currentframe().f_back.f_code.co_name
```

## 最小复现脚本模板

```python
"""
最小复现脚本 — debug_repro.py
用法: python debug_repro.py
"""
import sys
import traceback

# 1. 最小输入数据
INPUT = {
    "key": "value",
    # 逐步删除字段直到问题消失
}

# 2. 预期行为
EXPECTED = {"status": "ok"}

# 3. 调用目标代码
def main():
    try:
        result = some_function(INPUT)
        assert result == EXPECTED, (
            f"Mismatch:\n"
            f"  expected: {EXPECTED}\n"
            f"  got:      {result}"
        )
        print("PASS: behavior matches expected")
    except Exception:
        print("FAIL: exception raised")
        traceback.print_exc()
        sys.exit(1)

if __name__ == "__main__":
    main()
```

## 调试流程

```
① mypy src/ --strict → 修复类型错误
② ruff check src/ → 修复 lint 问题
③ python -m pytest tests/ -v → 确认测试通过
④ 运行时异常：插入 breakpoint() → pdb 交互式调试
⑤ 逻辑问题：编写最小复现脚本隔离验证
⑥ 性能问题：cProfile / line_profiler 定位热点
⑦ 修复后：mypy + ruff + pytest 回归验证
```

### 常用 pdb 命令速查

| 命令           | 缩写       | 作用                     |
| -------------- | ---------- | ------------------------ |
| `list`         | `l`        | 显示当前行附近代码       |
| `next`         | `n`        | 执行下一行（不进入函数） |
| `step`         | `s`        | 进入函数内部             |
| `continue`     | `c`        | 继续执行到下一个断点     |
| `print expr`   | `p expr`   | 打印表达式值             |
| `pp expr`      | `pp`       | 漂亮打印表达式值         |
| `args`         | `a`        | 打印当前函数参数         |
| `where`        | `w`        | 打印调用栈               |
| `up`           | `u`        | 向上移动栈帧             |
| `down`         | `d`        | 向下移动栈帧             |
| `break lineno` | `b lineno` | 在指定行设置断点         |
| `quit`         | `q`        | 退出调试器               |
