---
applyTo: '**/*.{ts,tsx}'
---

# TypeScript / TSX Debug Rules

TypeScript 调试核心策略：**类型系统优先，运行时验证兜底**。

## 调试脚本

### 类型检查

```powershell
# TypeScript 严格模式类型检查（首步必做）
pnpm exec tsc --noEmit --pretty

# 仅检查指定包
pnpm exec tsc --noEmit --pretty --project packages/components/tsconfig.json

# 仅检查指定文件（快速定位）
pnpm exec tsc --noEmit --pretty | Select-String "src/suspectedFile.ts"

# 生成类型检查报告
pnpm exec tsc --noEmit --pretty 2>&1 | Out-File -FilePath tsc-errors.log

# Watch 模式增量检查（适合迭代调试）
pnpm exec tsc --noEmit --pretty --watch

# 查看模块解析过程（排查 import 路径问题）
pnpm exec tsc --traceResolution --noEmit 2>&1 | Select-String "some-module"
```

### Source Map 调试

```powershell
# 确保 tsconfig 中 "sourceMap": true
# 在浏览器 DevTools / IDE 中设置断点，单步执行编译前源码
```

### 依赖类型声明检查

```powershell
# 检查第三方库是否有 @types 包
pnpm ls --depth=0 -r | Select-String "@types"

# 查看某个包的实际类型定义
Get-Content "node_modules/@types/react/index.d.ts" -Head 50

# 检查 tsconfig paths 配置
Get-Content tsconfig.json | ConvertFrom-Json | Select-Object -ExpandProperty compilerOptions | Select-Object paths, baseUrl

# 验证模块实际路径
node -e "console.log(require.resolve('some-module'))"
```

### Lint 检查

```powershell
# ESLint 检查并输出
pnpm exec eslint 'packages/**/*.{ts,tsx}' --max-warnings 0 --format stylish

# ESLint 自动修复
pnpm exec eslint 'packages/**/*.{ts,tsx}' --fix

# 输出 JSON 到文件便于分析
pnpm exec eslint 'packages/**/*.{ts,tsx}' --format json --output-file lint-output.json
```

### 单元测试调试

```powershell
# 运行所有测试
pnpm exec vitest run

# 运行指定测试文件
pnpm exec vitest run test/components/ActionButton.test.tsx

# 运行测试并显示覆盖率
pnpm exec vitest run --coverage

# 仅运行失败的测试
pnpm exec vitest run --reporter verbose --passWithNoTests

# 带 UI 界面的测试调试
pnpm exec vitest --ui
```

### 构建调试

```powershell
# 构建所有包
pnpm run build

# 构建指定包
pnpm --filter @lania-pro-components/components run build

# 清理构建产物后重新构建
pnpm run clean && pnpm run build

# 查看 rollup 构建详情（调试打包问题）
pnpm exec rollup -c rollup.config.js --bundleConfigAsCjs
```

### 日志与输出分析

```powershell
# 搜索代码中的临时调试日志
Select-String -Path "packages/**/*.{ts,tsx}" -Pattern "// DEBUG:|console\.\w+\(" -CaseSensitive | Group-Object Filename

# 搜索 TODO/FIXME 标记
Select-String -Path "packages/**/*.{ts,tsx}" -Pattern "TODO|FIXME|HACK|XXX" -CaseSensitive

# 搜索被注释掉的代码
Select-String -Path "packages/**/*.{ts,tsx}" -Pattern "^\s*//\s*(console|debugger|export|function|class)" -CaseSensitive
```

### 性能调试

```powershell
# 查找大文件（可能影响构建性能）
Get-ChildItem -Recurse -Include *.ts,*.tsx | Where-Object { $_.Length -gt 100KB } | Select-Object Name, Length | Sort-Object Length -Descending

# 统计各包文件数量（评估模块复杂度）
Get-ChildItem -Recurse -Directory packages | ForEach-Object { $files = Get-ChildItem $_.FullName -Recurse -Include *.ts,*.tsx; [PSCustomObject]@{Package = $_.Name; Files = $files.Count} } | Sort-Object Files -Descending
```

### 文件变更追踪

```powershell
# 查看 *.ts,*.tsx 最近修改
Get-ChildItem -Recurse -Include *.ts,*.tsx | Sort-Object LastWriteTime -Descending | Select-Object -First 20 Name, LastWriteTime

# Git 变更文件列表
git diff --name-only HEAD~1

# 查看某个文件的 Git 历史
git log --oneline --follow -- packages/components/src/ProTable/index.tsx
```

### 综合调试命令（一键式）

```powershell
# 快速诊断：类型检查 + Lint + 测试
function Invoke-QuickDiagnose {
    Write-Host "=== Type Check ===" -ForegroundColor Cyan
    pnpm exec tsc --noEmit --pretty 2>&1 | Out-Host
    Write-Host "=== Lint ===" -ForegroundColor Cyan
    pnpm exec eslint 'packages/**/*.{ts,tsx}' --max-warnings 0 2>&1 | Out-Host
    Write-Host "=== Test (failed only) ===" -ForegroundColor Cyan
    pnpm exec vitest run --reporter verbose 2>&1 | Out-Host
}
```

### 打点调试（Instrumentation / Probe）

```powershell
# 查找所有已有的 // DEBUG: 标记
Select-String -Path "packages/**/*.{ts,tsx,js,jsx}" -Pattern "// DEBUG:" -CaseSensitive
```

```typescript
// 入口打点
// DEBUG: [Component.method] enter | input=%o

// 类型断言验证（运行时确认类型符合预期）
// DEBUG: [Component.method] type check | isArray=%o, typeof value=%s

// 分支打点
// DEBUG: [Component.method] branch=if-case | condition=%o

// 返回值打点
// DEBUG: [Component.method] return | result=%o

// 性能耗时打点
// const start = performance.now();
// // ... code ...
// // DEBUG: [Component.method] took %d ms, performance.now() - start
```

### 最小复现测试文件

最小复现文件模板（TypeScript）：

```typescript
// debug-isolate.test.ts — 最小复现测试
import { describe, it, expect } from 'vitest';

// 1. 定义问题输入的精确类型
type Input = { /* ... */ };

// 2. 构造最小输入数据（仅保留触发问题所需的字段）
const minimalInput: Input = {/* ... */};

// 3. 定义预期行为
const expectedOutput = {/* ... */};

// 4. 执行并断言（逐步缩小输入范围）
describe('Bug reproduction: [问题简述]', () => {
  it('should reproduce the issue', () => {
    const result = someFunction(minimalInput);
    expect(result).toEqual(expectedOutput);
  });

  it('should pass when [某个条件]', () => {
    const modifiedInput = { ...minimalInput /* 改变一个条件 */ };
    const result = someFunction(modifiedInput);
    expect(result).not.toEqual(expectedOutput);
  });
});
```

### Git 二分定位

```powershell
# 对 TS 项目使用 vitest 作为 bisect 验证命令
git bisect start
git bisect bad
git bisect good <已知正常 commit>
git bisect run pnpm exec vitest run test/specific-test.tsx
git bisect reset
```

## 错误分类与排查策略

### 类型错误（编译期）

```powershell
# 定位错误位置
pnpm exec tsc --noEmit --pretty
```

- 检查类型定义是否匹配，泛型参数是否正确传递
- 检查第三方库类型声明（`@types/xxx`）是否存在或版本匹配

#### 类型推断不符合预期

```typescript
// 用 satisfies 验证类型但不改变推断结果
const result = someFunction(input) satisfies ExpectedType;

// 用临时类型变量暴露推断结果
const _debugType: ExpectedType = someFunction(input);
// ↑ 如果这行报错，说明推断类型与预期不匹配
```

#### 泛型约束错误

```typescript
// 明确标注泛型参数来缩小范围
function debugGeneric<T extends Constraint>(arg: T) {
  // 在调用处显式传入类型参数
}
// 调用：debugGeneric<ConcreteType>(arg);
```

#### 交叉类型/联合类型问题

```typescript
// 使用 Discriminated Union 区分分支
type Result = { status: 'success'; data: unknown } | { status: 'error'; message: string };

// 用类型谓词收窄类型
function isSuccess(r: Result): r is { status: 'success'; data: unknown } {
  return r.status === 'success';
}
```

#### Module Resolution 错误

```powershell
pnpm exec tsc --traceResolution --noEmit 2>&1 | Select-String "some-module"
```

- 检查 `tsconfig.json` 中 `paths` / `baseUrl` 配置
- 检查 `package.json` 中 `exports` / `types` 字段

#### 异步类型错误

```typescript
// 确保 Promise 类型链完整
async function debugAsync(): Promise<ResultType> {
  const data = await fetchData(); // 检查 fetchData 返回类型
  return processData(data); // 检查 processData 返回类型
}
// 用 ReturnType 检查函数返回类型
type FetchReturn = ReturnType<typeof fetchData>;
```

### 运行时错误

- 区分：TypeError（undefined 调用）、RangeError（递归溢出）、ReferenceError（未定义变量）
- 检查异步操作中 `await` 是否遗漏
- 检查可选链 `?.` 和空值合并 `??` 的使用是否合理

### 渲染问题（React）

- **不更新**：检查 props 引用是否变化（`React.memo` 依赖）、Context 值是否更新
- **无限重渲染**：检查 `useEffect` 依赖数组、`useCallback`/`useMemo` 依赖链
- **样式异常**：检查 CSS 类名冲突、CSS-in-JS 运行时值、样式优先级

#### React TSX 特有调试

```typescript
// 检查 props 类型是否匹配
type Props = {
  onSave: (data: Data) => void;
  items: Item[];
};

// 检查泛型组件类型
const GenericComponent = <T extends unknown>(props: Props<T>) => {
  const _check: Props<T> = props;
};
```

### 状态管理问题

- 追踪 store 变更时序
- 检查 watch/effect 依赖项
- 注意批量更新队列（React 18 auto-batching）

### 异步时序问题

- 检查竞态条件（请求覆盖、过期响应处理）
- 检查 cleanup 函数是否执行
- 检查 Promise 链是否有未 `await` 的调用

### 网络请求问题

- 检查请求/响应 payload 与预期是否一致
- 检查状态码和错误处理分支
- 检查 CORS 配置
- 检查请求时序（并发请求、取消请求）

### 构建/打包问题

- 检查 rollup/vite 配置
- 检查外部依赖声明（`external`/`peerDependencies`）
- 检查产物内容：`pnpm exec rollup -c rollup.config.js --bundleConfigAsCjs`

## 输出规范

除通用调试报告外，TS 调试需额外包含：

- `tsc --noEmit` 的错误行号和错误码
- 涉及的类型定义文件路径及行号
- `tsconfig.json` 中相关编译选项配置
