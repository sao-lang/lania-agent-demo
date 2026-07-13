---
applyTo: '**/*'
---

# Debug Script Tools

提供一套标准化的通用调试脚本命令，按场景分类，语言无关。使用 PowerShell（Windows）语法。

> **语言专有调试脚本请参考对应语言的规则文件：**
>
> - TypeScript/TSX → `rules/02-ts-debug.instructions.md`
> - Dart/Flutter → `rules/03-dart-debug.instructions.md`
> - Rust → `rules/04-rust-debug.instructions.md`
> - Go → `rules/05-go-debug.instructions.md`
> - Python → `rules/06-python-debug.instructions.md`

## 1. 依赖检查

```powershell
# 查看依赖树
pnpm ls --depth=3

# 检查过时依赖
pnpm outdated

# 检查重复依赖
pnpm dedupe --check

# 检查依赖版本一致性
pnpm exec syncpack list-mismatches
```

## 2. 网络请求调试

```powershell
# 使用 curl 测试 API 端点
curl -X GET "http://localhost:3000/api/endpoint" -H "Content-Type: application/json" -w "\nHTTP Status: %{http_code}\n"

# POST 请求调试
curl -X POST "http://localhost:3000/api/endpoint" -H "Content-Type: application/json" -d '{"key":"value"}' -w "\nHTTP Status: %{http_code}\n"

# 查看响应头
curl -I "http://localhost:3000/api/endpoint"
```

## 3. 文件变更追踪

```powershell
# 查看近期修改的文件（按扩展名筛选）
Get-ChildItem -Recurse -Include *.ts,*.tsx,*.rs,*.go,*.py,*.dart | Sort-Object LastWriteTime -Descending | Select-Object -First 20 Name, LastWriteTime

# Git 变更文件列表
git diff --name-only HEAD~1

# 查看某个文件的 Git 历史
git log --oneline --follow -- <文件路径>
```

## 4. 性能调试

```powershell
# 查找大文件（可能影响构建/运行性能）
Get-ChildItem -Recurse -Include *.ts,*.tsx,*.rs,*.go,*.py,*.dart | Where-Object { $_.Length -gt 100KB } | Select-Object Name, Length | Sort-Object Length -Descending

# 统计各目录文件数量（评估模块复杂度）
Get-ChildItem -Recurse -Directory | ForEach-Object { $files = Get-ChildItem $_.FullName -Recurse -Include *.ts,*.tsx,*.rs,*.go,*.py,*.dart; [PSCustomObject]@{Directory = $_.Name; Files = $files.Count} } | Sort-Object Files -Descending | Select-Object -First 20
```

## 5. 打点调试（Instrumentation / Probe）

在可疑路径插入探测点，收集运行时执行轨迹和行为数据。

```powershell
# 查找所有已有的 // DEBUG: 标记
Select-String -Path "packages/**/*.*" -Pattern "// DEBUG:|# DEBUG:" -CaseSensitive | Group-Object Filename
```

通用打点规则：

- 使用 `// DEBUG:`（C 系语言）或 `# DEBUG:`（Python 等）作为前缀
- 包含模块名、函数名、关键变量值
- 定位后统一检索清理

## 6. 最小复现（Minimal Reproduction）

当问题边界不清晰时，编写独立的最小测试文件来隔离和复现问题。

通用原则：

- **逐步简化**：从完整输入逐步删除字段，直到刚好能复现问题为止
- **单一变量**：每次只改变一个条件
- **独立无依赖**：不依赖外部服务、数据库、网络
- **生成即清理**：验证后删除或转为正式测试用例

> 各语言的最小复现文件模板参见对应语言的规则文件（02-ts-debug / 03-dart-debug / 04-rust-debug / 05-go-debug / 06-python-debug）。

## 7. Git 二分定位（Bisect）

当问题由近期某次提交引入时，使用 Git bisect 定位引入 commit。

```powershell
# 启动二分查找
git bisect start

# 标记当前版本为 bad
git bisect bad

# 标记已知的正常版本为 good
git bisect good <已知正常 commit>

# 对每个检出版本运行测试（替换为项目的测试命令）
git bisect run <测试命令>

# 找到首个 bad commit 后结束
git bisect reset
```

## 8. 快照对比调试（Snapshot Diff）

```powershell
# 查看文件变更前后对比
git diff HEAD -- <文件路径>

# 查看两次提交间特定文件的差异
git diff <commit1> <commit2> -- <文件路径>

# 查看分支间的差异
git diff main..feature-branch --name-only

# 导出某次提交的完整变更
git show <commit> --stat
```

## 9. A/B 对比调试

```powershell
# 比较两个分支的行为差异
git checkout branch-a
# 构建/运行 ...
# ... 测试 ...

git checkout branch-b
# 构建/运行 ...
# ... 测试 ...
```

## 10. 依赖注入调试（Mock / Stub）

```powershell
# 搜索项目中已有的 mock 文件（了解项目 mock 模式）
Get-ChildItem -Recurse -Filter "*mock*" -Name
Get-ChildItem -Recurse -Filter "*__mocks__*" -Name
```

手动编写 mock 的通用模式：

- 将外部依赖（API、数据库、文件系统）替换为可控的 mock 实现
- 通过 mock 返回特定数据来验证不同分支行为
- 通过 mock 抛出异常来验证错误处理路径

## 11. 故障注入（Fault Injection）

通过人为制造故障来验证系统容错能力：

- **网络故障**：关闭服务/断开连接，观察重试和降级行为
- **数据异常**：传入非法/边界值，观察校验和错误处理
- **时序故障**：增加延迟/调整顺序，观察竞态处理
- **资源耗尽**：模拟内存/磁盘不足，观察 graceful degradation

## 使用原则

- 语言专有命令（编译检查、测试框架、lint 工具）优先参考对应语言的规则文件
- 一次性收集足够证据再分析，避免反复执行同一命令
- 长时间运行的命令（watch/UI 模式）使用 `mode=async` 执行
- 脚本输出较长时重定向到文件，再用 `read_file` 分析
- 打点日志在问题定位后统一清理
