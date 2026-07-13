---
applyTo: '**/*.test.{ts,tsx}'
---

# Testing Rules

- 测试覆盖三个维度：Happy Path、Boundary Case、Exception Handling
- 优先使用组件库自带的测试工具（vitest + @testing-library/react）
- Mock 外部依赖而非真实调用
- 测试命名：`describe('Component')` / `it('should ... when ...')`
