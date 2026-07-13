---
applyTo: '**/*.{ts,tsx}'
---

# TypeScript Rules

- 类型声明优先用 interface，组件 props 用 interface 继承
- 泛型参数用语义化命名（TData、TResponse），避免单字母
- 优先 `unknown` 而非 `any`，减少类型断言 `as`
- 函数返回类型显式标注，不依赖类型推断

## Testing

- 使用 vitest + @testing-library/react 编写测试
- 覆盖 Happy Path、Boundary Case、Exception Handling 三个维度
- 测试文件命名 `*.test.ts` 或 `*.test.tsx`
- Mock 外部依赖而非真实调用

## Lint

- 运行 `pnpm lint` / `eslint` 检查代码质量
- 运行 `tsc --noEmit` 确保类型无错误
- 运行 `prettier --write` 格式化代码
