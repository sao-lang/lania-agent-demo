---
applyTo: '**/*.dart'
---

# Dart Rules

- 类型声明优先使用 `class` 和 `typedef`，避免 `dynamic`
- 使用 `final` / `const` 替代 `var`，不可变优先
- 遵循 Effective Dart 风格指南
- 使用 `sealed class` + `when` 处理状态/结果分支（而非手动 if-else）

## 注释规范

- 公开 API 使用 `///` 文档注释，而非 `//` 或 `/** */`
- 类、方法、顶级常量必须写文档注释
- 复杂逻辑需添加行内注释解释意图，不解释"是什么"
- 修改代码不得删除已有注释，逻辑变化时追加说明

## Testing

- 使用 `flutter_test` + `mockito` 编写测试
- 测试文件放于 `test/` 目录，命名 `*_test.dart`
- Widget 测试覆盖 UI 渲染和交互行为
- 使用 `group()` 组织相关测试用例
- 覆盖正常路径、边界条件和异常处理
- 运行 `flutter test` 执行所有测试

## Lint

- 运行 `dart format .` 格式化代码
- 运行 `dart analyze` 静态分析
- 运行 `flutter analyze`（Flutter 项目专用，包含更多规则）
- 确保无 warning 和 info 级别的问题
