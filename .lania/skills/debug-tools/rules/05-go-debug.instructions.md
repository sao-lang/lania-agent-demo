---
applyTo: '**/*.{go}'
---

# Go Debug Rules

Go 调试核心策略：**静态检查 + 单元测试 + pprof 性能分析 + delve 运行时调试**。

## 调试脚本

```powershell
# ── 静态检查（首步必做） ──
# 编译检查
go build ./...

# Vet 静态分析
go vet ./...

# 显示更详细的 vet 检查
go vet -v ./...

# ── 单元测试调试 ──
# 运行所有测试
go test ./...

# 运行指定包的测试
go test ./internal/suspected/...

# 运行指定测试函数
go test -run TestFunctionName ./...

# 显示详细输出（打印 t.Log 内容）
go test -v -run TestFunctionName ./...

# 显示测试覆盖率
go test -coverprofile=coverage.out ./...
go tool cover -html=coverage.out -o coverage.html

# ── Race 检测（并发问题） ──
# 测试时启用 race 检测
go test -race ./...

# 运行时启用 race 检测
go run -race main.go

# ── Delve 调试器 ──
# 安装 delve
go install github.com/go-delve/delve/cmd/dlv@latest

# 以 debug 模式启动
dlv debug main.go

# 附加到运行中的进程
dlv attach <PID>

# 远程调试
dlv debug --headless --listen=:2345 --api-version=2

# ── 性能分析 ──
# 生成 CPU profile
go test -cpuprofile=cpu.prof -bench=. ./...

# 生成 Memory profile
go test -memprofile=mem.prof -bench=. ./...

# 生成 Block profile（检测锁竞争）
go test -blockprofile=block.prof -bench=. ./...

# ── 格式化与 Lint ──
# 格式化
gofmt -l -s ./

# 格式化（直接修改文件）
gofmt -l -s -w ./

# golangci-lint（需要安装）
golangci-lint run ./...
```

## 常见问题与排查

### 接口实现错误

```go
// 编译时检查接口是否被正确实现
// 用断言方式让编译器提前检查
var _ SomeInterface = &MyStruct{} // 编译报错说明 MyStruct 未实现 SomeInterface

// 确认接口方法的签名完全匹配（参数、返回值类型、数量）
// 检查方法接收者是否应为指针（*T）而非值（T）
```

### nil 引用 / 空指针

```go
// 排查方向
// 1. 检查 map/slice/channel 是否用 make 初始化
// 2. 检查指针类型字段是否在结构体初始化时赋值
// 3. 检查函数返回值是否可能为 nil

// 打点验证
// DEBUG: [function] ptr=%v, isNil=%t

// 安全访问模式
if obj != nil && obj.Field != nil {
    // 安全使用
}
```

### 并发问题（Goroutine + Channel）

```go
// Deadlock 检测
// 1. 运行时：go test -race ./... 检测数据竞争
// 2. 编译时：检查 channel 是否在正确位置 close
// 3. 检查 goroutine 泄漏（sync.WaitGroup 未 Done）

// 打点验证
// DEBUG: [function] goroutine started | id=%d
// DEBUG: [function] channel send | value=%v
// DEBUG: [function] channel receive | value=%v

// 超时控制模式（防止死锁）
select {
case result := <-ch:
    // 正常处理
case <-time.After(5 * time.Second):
    // DEBUG: [function] timeout waiting for channel
}
```

### 错误处理遗漏

```go
// 未检查的 error 返回值
// 使用 go vet 检查未处理的错误
// go vet -vettool=$(go env GOPATH)/bin/errcheck

// 打点验证
// DEBUG: [function] error=%v
if err != nil {
    // 不要吞掉错误
    return fmt.Errorf("context: %w", err)
}
```

### 内存泄漏

```powershell
# 使用 pprof 分析
go tool pprof -http=:8080 cpu.prof
go tool pprof -http=:8080 mem.prof
```

```go
// 常见原因
// 1. Goroutine 未退出（channel 未关闭、select 无 default）
// 2. 全局 map/slice 无限增长
// 3. time.Ticker 未 Stop
// 4. defer 中持有大对象引用

// 打点验证
// DEBUG: [function] goroutine count=%d
// runtime.NumGoroutine() 返回当前 goroutine 数量
```

### Interface{} / any 类型断言

```go
// 安全类型断言（使用 comma-ok 模式）
if val, ok := someInterface.(ConcreteType); ok {
    // 安全使用 val
} else {
    // DEBUG: [function] type assertion failed | actualType=%T
}

// 避免在热点路径中使用频繁的类型断言（考虑类型 switch）
```

### 测试调试

```go
// 表驱动测试 + 子测试（便于定位单个 case）
func TestSomething(t *testing.T) {
    tests := []struct {
        name string
        input Input
        want  Output
    }{
        {"case 1: normal", input1, output1},
        {"case 2: edge", input2, output2},
    }
    for _, tt := range tests {
        t.Run(tt.name, func(t *testing.T) {
            got := Something(tt.input)
            if !reflect.DeepEqual(got, tt.want) {
                // DEBUG: [TestSomething] mismatch | got=%v, want=%v
                t.Errorf("Something() = %v, want %v", got, tt.want)
            }
        })
    }
}
```

## 打点规范

```go
// 1. fmt.Printf 打点（快速验证）
// DEBUG: fmt.Printf("[function] entry: input=%+v\n", input)
// DEBUG: fmt.Printf("[function] exit: result=%+v\n", result)

// 2. log 包打点（带时间戳，适合长期观察）
// DEBUG: log.Printf("[function] state=%s, value=%d", state, value)

// 3. testing.T.Log 打点（测试用，仅 -v 时显示）
// t.Logf("DEBUG: [function] key=%s", key)
```

## 调试流程

```
① go vet ./... → 修复静态问题
② go build ./... → 确认编译通过
③ go test -race ./... → 修复并发和数据竞争问题
④ go run -race main.go → 运行时并发检测
⑤ 复杂逻辑用 dlv debug 单步执行
⑥ 性能问题用 pprof 分析 hot path
⑦ 修复后：go vet + go test -race + go build 回归验证
```
