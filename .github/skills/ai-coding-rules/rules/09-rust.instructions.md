---
applyTo: '**/*.rs'
---

# Rust Rules

- 优先使用 `enum` + `match` 处理状态/错误分支
- 使用 `Result<T, E>` 而非 `panic!` / `unwrap()`
- 泛型约束用 `where` 子句提升可读性
- 遵循 Rustfmt 和 Clippy 规范

## 注释规范

- 公开 API 使用 `///` 文档注释（可生成 rustdoc）
- 结构体字段必须加 `///` 文档注释
- 复杂函数需在函数上方写文档注释说明用途、参数和返回值
- 使用 `// SAFETY:` 标注 unsafe 代码块的 safety 前提
- 修改代码不得删除已有注释，逻辑变化时追加说明

## Testing

- 使用 `#[cfg(test)]` 模块 + `#[test]` 属性编写单元测试
- 集成测试放于 `tests/` 目录
- 覆盖正常路径、边界条件和错误处理
- 使用 `assert_eq!` / `assert!` 断言，必要时用 `?` 操作符传播错误
- 使用 `cargo test` 执行所有测试
- 使用 `cargo test -- --nocapture` 查看测试中的 println! 输出

## Lint

- 运行 `cargo fmt` 格式化代码
- 运行 `cargo clippy` 静态分析（等价于增强版 lint）
- 运行 `cargo check` 快速检查编译错误（比 `cargo build` 更快）
- 确保 `cargo clippy` 无 warning
