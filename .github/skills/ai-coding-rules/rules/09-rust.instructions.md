---
applyTo: '**/*.rs'
---

# Rust Rules

- 优先使用 `enum` + `match` 处理状态/错误分支
- 使用 `Result<T, E>` 而非 `panic!` / `unwrap()`
- 结构体字段加文档注释 `///`
- 泛型约束用 `where` 子句提升可读性
- 遵循 Rustfmt 和 Clippy 规范

## Testing

- 使用 `#[cfg(test)]` 模块 + `#[test]` 属性编写单元测试
- 集成测试放于 `tests/` 目录
- 覆盖正常路径、边界条件和错误处理
- 使用 `assert_eq!` / `assert!` 断言，必要时用 `?` 操作符传播错误

## Lint

- 运行 `cargo fmt` 格式化代码
- 运行 `cargo clippy` 静态分析
