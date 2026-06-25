"""查询反思扩展兼容导出模块。

为仍然从 ``app.harness.reflection`` 导入反思能力的旧代码提供稳定入口，
实际实现位于 ``app.harness.extensions.query.reflection``。该文件不承载
额外逻辑，只负责兼容导出，避免重构期间出现导入路径断裂。
"""

from app.harness.extensions.query.reflection import ReflectionHarness

__all__ = ['ReflectionHarness']
