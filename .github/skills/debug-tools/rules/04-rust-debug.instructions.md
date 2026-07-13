---
applyTo: '**/*.{rs}'
---

# Rust Debug Rules

Rust 调试核心策略：**编译器错误信息优先，单元测试隔离，运行时用 `dbg!` 和 `tracing`**。

## 调试脚本

```powershell
# ── 编译检查（首步必做） ──
# 检查编译错误
cargo check

# 详细编译检查（显示所有警告）
cargo check --all-targets

# 完整编译（生成二进制后可运行）
cargo build

# 显示完整的编译错误信息
cargo check 2>&1

# ── 单元测试调试 ──
# 运行所有测试
cargo test

# 运行指定测试（按名称过滤）
cargo test test_name

# 运行测试并显示 stdout（默认测试会捕获 stdout）
cargo test -- --nocapture

# 运行测试且不并行执行（方便打点调试，避免输出交错）
cargo test -- --test-threads=1

# ── Clippy Lint 检查 ──
cargo clippy --all-targets -- -D warnings

# ── 格式化检查 ──
cargo fmt --check

# ── 文档测试 ──
cargo test --doc

# ── Release 模式调试 ──
cargo build --release
# 注：release 模式会优化掉部分 debug 信息，如需性能调试可在 Cargo.toml 中：
# [profile.release]
# debug = true
```

## 常见问题与排查

### 借用检查器（Borrow Checker）错误

```rust
// 经典：cannot borrow `x` as mutable more than once at a time
// 排查方向：
//   1. 检查是否存在多个可变引用
//   2. 考虑使用 RefCell / Mutex 实现内部可变性
//   3. 考虑重构为所有权转移而非借用
//   4. 检查生命周期标注是否正确

// 调试技巧：用 clone() 临时绕过借用检查（打点用，提交前清理）
// DEBUG: let cloned = value.clone();
```

### 生命周期标注问题

```rust
// 典型错误：lifetime mismatch / missing lifetime specifier
// 排查方向：
//   1. 确认返回值的生命周期与哪个输入参数关联
//   2. 使用 'static 仅在值确实为全局时使用
//   3. 考虑使用 Arc 替代生命周期标注

// 调试技巧：添加显式生命周期标注来让编译器指出不匹配点
fn debug_lifetime<'a, 'b>(x: &'a str, y: &'b str) -> &'a str {
    // 如果这里报错，说明返回的引用与 'a 不匹配
    x
}
```

### 所有权（Ownership）问题

```rust
// 典型：use of moved value
// 排查方向：
//   1. 检查是否在 move 后继续使用原变量
//   2. 考虑使用 & 引用替代所有权转移
//   3. 考虑实现 Clone / Copy trait
//   4. 检查集合操作（Vec::push、HashMap::insert）是否转移了所有权

// 打点验证
// DEBUG: [function] value moved, remaining=%?
```

### Panic / 运行时崩溃

```powershell
# 获取完整 panic 回溯（默认只显示少量信息）
$env:RUST_BACKTRACE = "1"
cargo run

# 完整回溯
$env:RUST_BACKTRACE = "full"
cargo run
```

```rust
// 在代码中捕获 panic 信息
use std::panic;

let result = panic::catch_unwind(|| {
    // 可能 panic 的代码
});
match result {
    Ok(val) => { /* 正常 */ }
    Err(e) => {
        // DEBUG: [function] panic caught | err=%?
        // 可以在这里分析 panic 原因
    }
}
```

### Unsafe 代码问题

```rust
// 排查流程：
//   1. 检查裸指针操作是否正确（null、dangling、alignment）
//   2. 检查 FFI 调用是否满足 ABI 约定
//   3. 用 Miri 检测未定义行为
//   4. 用 AddressSanitizer 检测内存错误

// Miri 检测（需要 nightly）
// cargo +nightly miri test

// 打点 unsafe 块
// SAFETY: [说明为什么这个 unsafe 是安全的]
// DEBUG: [function] unsafe block | ptr=%p, len=%d
unsafe {
    // ...
}
```

### 并发问题

```rust
// 检查 Send / Sync trait 实现
// 使用 Arc<Mutex<T>> 或 Arc<RwLock<T>> 共享状态
// 使用 channels（crossbeam、tokio::sync::mpsc）传递消息

// 死锁排查
//   1. 检查锁的获取顺序是否一致
//   2. 考虑使用 std::sync::TryLock 替代 Lock（非阻塞尝试）
//   3. 检查是否在持有锁时又尝试获取同一把锁

// 打点验证
// DEBUG: [function] lock acquired | thread=%?
// DEBUG: [function] lock released | thread=%
```

### 异步 Rust 问题

```rust
// 检查 Future 是否被 poll（未 .await 的 Future 不会执行）
// 检查 async block 中是否使用了非 Send 类型（多线程运行时要求 Future: Send）
// 检查阻塞操作是否在异步上下文中使用（应改用 tokio::fs、tokio::io 等）

// 打点验证
// DEBUG: [async_fn] start | args=%?
// DEBUG: [async_fn] after await point 1 | state=%?
// DEBUG: [async_fn] complete | result=%?
```

## 调试宏规范

```rust
// 1. dbg! 宏 — 最快捷的运行时打印（自动包含文件、行号、表达式和值）
// 注意：dbg! 在 release 模式下也会存在（除非用 cfg(debug_assertions) 包裹）
// DEBUG:
let value = dbg!(some_expression);

// 2. 条件编译调试日志（生产代码中推荐方式）
#[cfg(debug_assertions)]
{
    // DEBUG: [function] debug info | val=%?
}

// 3. 使用 tracing / log crate
// Cargo.toml 中添加 tracing
// use tracing::{debug, info, warn, error};
// DEBUG: tracing::debug!(target: "my_module", "message: {:?}", value);
```

## 调试构建配置

```toml
# Cargo.toml — 调试优化配置
[profile.dev]
# 默认已有 debug = true
opt-level = 0   # 无优化，保留所有调试信息

[profile.release]
debug = 1       # release 也保留行号信息（用于性能调试）
opt-level = 3   # 完整优化
```

## 调试流程

```
① cargo check → 修复编译错误
② cargo clippy → 修复 lint 警告
③ cargo test → 确认测试通过
④ 运行时使用 RUST_BACKTRACE=full 获取完整回溯
⑤ 用 dbg! 宏打点定位运行时逻辑问题
⑥ unsafe 块用 Miri 检测未定义行为
⑦ 修复后：cargo check + cargo clippy + cargo test 回归验证
```
