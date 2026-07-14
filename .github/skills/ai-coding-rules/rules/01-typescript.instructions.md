---
applyTo: '**/*.{ts,tsx}'
---

# TypeScript Rules

- 类型声明优先用 interface，组件 props 用 interface 继承
- 泛型参数用语义化命名（TData、TResponse），避免单字母
- 优先 `unknown` 而非 `any`，减少类型断言 `as`
- 函数返回类型显式标注，不依赖类型推断

## 注释规范

- 公开 API 使用 JSDoc 格式（`/** */`）写文档注释
- 组件 props 使用 `@param` 标注每个属性的用途
- 复杂逻辑添加行内注释，解释"为什么做"而非"做了什么"
- 使用 `@deprecated` 标注已废弃 API，并注明替代方案
- 修改代码不得删除已有注释，逻辑变化时追加说明

## Testing

- 使用 vitest + @testing-library/react 编写测试
- 覆盖 Happy Path、Boundary Case、Exception Handling 三个维度
- 测试文件命名 `*.test.ts` 或 `*.test.tsx`
- Mock 外部依赖而非真实调用

## Lint

- 运行 `pnpm lint` 检查代码质量（等价于 `eslint @lania-pro-components/components --ext .ts,.tsx`）
- 运行 `pnpm typecheck` 确保类型无错误（等价于 `tsc --noEmit`，从项目根目录执行）
- 运行 `pnpm format` 格式化代码（等价于 `prettier --write`）

> 所有检查均从项目根目录执行，不要进入子目录运行。
