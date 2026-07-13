---
applyTo: 'graphify-out/**'
---

# Graphify Rules

收到全局分析指令时：

- 存在 `graphify-out/` 或 `graph.json` → 图谱先行，用 CALLS edges / God nodes 定位边界
- 不存在 → 退回到 import/require 静态分析，不提及/索要 graphify
