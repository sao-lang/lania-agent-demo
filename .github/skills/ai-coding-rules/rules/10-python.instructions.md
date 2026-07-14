---
applyTo: '**/*.py'
---

# Python Rules

- 类型注解：函数参数和返回值必须标注类型
- 使用 `Pydantic` / `dataclass` 定义数据结构，避免裸 dict
- 异常处理：捕获具体异常类型，禁止 `except: pass`
- 使用 `pathlib` 而非 `os.path`
- 遵循 PEP 8 规范

## 注释规范

- 遵循 PEP 257 文档注释规范
- 公开模块、类、函数使用 `"""docstring"""` 写文档注释
- docstring 格式：首行简述，空行后详述参数/返回值/异常
- 复杂逻辑添加行内注释，解释"为什么做"而非"做了什么"
- 修改代码不得删除已有注释，逻辑变化时追加说明

## Testing

- 使用 `pytest` 编写测试，函数名以 `test_` 开头
- 测试文件命名 `test_*.py`，放于 `tests/` 目录
- 使用 fixture 管理测试依赖和 Mock
- 覆盖正常路径、边界条件和异常分支
- 使用 `parametrize` 减少重复测试代码
- 运行 `pytest` 或 `pytest tests/` 执行测试
- 运行 `pytest --cov` 检查测试覆盖率

## Lint

- 运行 `ruff check .` 检查代码质量（替代 flake8，速度更快）
- 运行 `ruff format .` 格式化代码（替代 black，与 ruff 无缝集成）
- 运行 `mypy .` 检查类型注解
- 确保 `ruff check` 无 error 和 warning
