"""查询恢复扩展兼容导出模块。

为仍然依赖 ``app.harness.recovery`` 导入路径的调用方保留兼容入口，实际
恢复逻辑已经迁移到 ``app.harness.extensions.query.recovery``。模块本身
不做任何运行时决策，只承担向后兼容职责。
"""

from app.harness.extensions.query.recovery import RecoveryManager

__all__ = ['RecoveryManager']
