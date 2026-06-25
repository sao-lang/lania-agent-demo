# 远程 Knowledge / Sandbox Provider 运维手册

## 1. 适用范围

本文覆盖以下远程 provider：

- `KNOWLEDGE_CAPABILITY_PROVIDER=remote_http`
- `SANDBOX_EXECUTOR_PROVIDER=remote_http`

目标是统一说明鉴权失败、超时、限流、上游故障、断路器与本地 fallback 的运行行为。

## 2. 配置项

### 2.1 Knowledge Capability

- `KNOWLEDGE_CAPABILITY_BASE_URL`
- `KNOWLEDGE_CAPABILITY_TIMEOUT_SECONDS`
- `KNOWLEDGE_CAPABILITY_AUTH_TOKEN`
- `KNOWLEDGE_CAPABILITY_ALLOW_LOCAL_FALLBACK`

### 2.2 Sandbox Executor

- `SANDBOX_EXECUTOR_BASE_URL`
- `SANDBOX_EXECUTOR_TIMEOUT_SECONDS`
- `SANDBOX_EXECUTOR_AUTH_TOKEN`
- `SANDBOX_EXECUTOR_ALLOW_LOCAL_FALLBACK`

### 2.3 通用断路器

- `REMOTE_PROVIDER_CIRCUIT_BREAKER_THRESHOLD`
- `REMOTE_PROVIDER_CIRCUIT_BREAKER_COOLDOWN_SECONDS`

## 3. 失败分类

### 3.1 鉴权失败

命中条件：

- HTTP `401`
- HTTP `403`

当前行为：

- 不走本地 fallback。
- `knowledge` 返回显式 `auth_failed` 错误。
- `sandbox` 返回 `permission_error`，默认 `abort`。

排查重点：

- token 是否缺失或过期。
- 调用方和 worker 是否使用同一鉴权方案。
- 反向代理是否吞掉 `Authorization` 头。

### 3.2 超时

命中条件：

- 请求超时
- HTTP `408`

当前行为：

- 若开启 `*_ALLOW_LOCAL_FALLBACK=true`，自动回退本地 provider。
- 同时累计断路器失败计数。
- 未开启 fallback 时直接抛出 timeout / dependency 错误。

### 3.3 限流

命中条件：

- HTTP `429`

当前行为：

- 若允许本地 fallback，则回退本地 provider。
- 失败计数进入断路器。

排查重点：

- worker 并发是否超过上游限额。
- 是否需要拆分租户级限流或加队列削峰。

### 3.4 上游故障

命中条件：

- HTTP `5xx`
- 网络不可达

当前行为：

- 若允许本地 fallback，则回退本地 provider。
- 连续失败达到阈值后，断路器在冷却窗口内直接短路到 fallback。

## 4. 断路器行为

- 连续失败次数达到 `REMOTE_PROVIDER_CIRCUIT_BREAKER_THRESHOLD` 后，断路器打开。
- 在 `REMOTE_PROVIDER_CIRCUIT_BREAKER_COOLDOWN_SECONDS` 冷却窗口内：
  - 若允许本地 fallback，则直接走本地 fallback。
  - 若不允许 fallback，则直接返回 `circuit_open` 错误。
- 任意一次远程成功响应会清空连续失败计数并关闭断路器。

## 5. 观测面

### 5.1 Health

- `/api/v1/knowledge/health`
- `/api/v1/sandbox/health`
- `/api/v1/health`

其中主 `/health` 会暴露：

- provider 类型
- base URL 是否已配置
- auth 是否已配置
- timeout
- 是否允许本地 fallback
- 断路器阈值和冷却时间

### 5.2 Trace

`knowledge` 远程 provider 会记录：

- `knowledge_remote_request_completed`
- `knowledge_remote_request_failed`
- `knowledge_remote_fallback_applied`
- `knowledge_remote_circuit_open`

## 6. 建议值

- 生产环境保持 `*_ALLOW_LOCAL_FALLBACK=true`，除非必须强制远程依赖不可降级。
- `REMOTE_PROVIDER_CIRCUIT_BREAKER_THRESHOLD` 建议从 `3` 起步。
- `REMOTE_PROVIDER_CIRCUIT_BREAKER_COOLDOWN_SECONDS` 建议从 `30` 秒起步。
- `TIMEOUT_SECONDS` 建议略小于入口层超时，避免请求堆积。
