---
applyTo: '**/*.py'
---

# Python Rules

- 类型注解：函数参数和返回值必须标注类型
- 使用 `Pydantic` / `dataclass` 定义数据结构，避免裸 dict
- 异常处理：捕获具体异常类型，禁止 `except: pass`
- 使用 `pathlib` 而非 `os.path`
- 遵循 PEP 8 + PEP 257 规范

## Testing

- 使用 pytest 编写测试，函数名以 `test_` 开头
- 测试文件命名 `test_*.py`，放于 `tests/` 目录
- 使用 fixture 管理测试依赖和 Mock
- 覆盖正常路径、边界条件和异常分支
- 使用 `parametrize` 减少重复测试代码

## Lint

- 运行 `ruff check .` / `flake8` 检查代码质量
- 运行 `mypy .` 检查类型注解
- 运行 `black .` 格式化代码
