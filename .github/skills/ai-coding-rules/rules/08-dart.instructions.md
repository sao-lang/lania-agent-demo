---
applyTo: '**/*.dart'
---

# Dart Rules

- 类型声明优先使用 `class` 和 `typedef`，避免 `dynamic`
- 使用 `final` / `const` 替代 `var`，不可变优先
- 遵循 Effective Dart 风格指南
- 使用 `///` 文档注释，而非 `//` 或 `/** */`

## Testing

- 使用 flutter_test + mockito 编写测试
- 测试文件放于 `test/` 目录，命名 `*_test.dart`
- Widget 测试覆盖 UI 渲染和交互行为
- 使用 `group()` 组织相关测试用例

## Lint

- 运行 `dart format .` 格式化代码
- 运行 `dart analyze` 静态分析
