---
applyTo: '**/*.{dart}'
---

# Dart / Flutter Debug Rules

Dart 调试核心策略：**静态分析 + DevTools 可视化 + 热重载迭代**。

## 调试脚本

```powershell
# ── 静态分析（首步必做） ──
# 分析整个项目
dart analyze

# 分析指定文件
dart analyze lib/suspected_file.dart

# 分析并输出到文件
dart analyze --fatal-infos > analysis-errors.log

# ── 单元测试调试 ──
# 运行所有测试
dart test

# 运行指定测试文件（适合迭代调试）
dart test test/specific_test.dart

# 运行测试并显示详细输出
dart test --reporter expanded

# 运行测试并保留输出（不缓存）
dart test --no-cache

# ── Flutter 特有 ──
# 以 debug 模式运行（支持热重载和 DevTools）
flutter run --debug

# 以 profile 模式运行（性能分析）
flutter run --profile

# 构建 web 调试版本
flutter build web --debug

# ── DevTools 启动 ──
# 启动独立的 DevTools
flutter pub global activate devtools
dart devtools

# 或通过 IDE 命令启动：Dart: Open DevTools

# ── 代码生成调试 ──
# 清理并重新生成（解决生成代码缓存问题）
dart run build_runner clean
dart run build_runner build --delete-conflicting-outputs

# Watch 模式（代码变更时自动重新生成）
dart run build_runner watch --delete-conflicting-outputs
```

## 常见问题与排查

### 编译错误

| 错误类型                | 常见原因                       | 排查方向                                   |
| ----------------------- | ------------------------------ | ------------------------------------------ |
| **Compile-time error**  | 类型不匹配、null safety 违反   | 检查类型注解、`?` 和 `!` 的使用            |
| **Analysis error**      | lint 规则违反、未使用的导入    | 运行 `dart analyze` 查看详细               |
| **Asset not found**     | pubspec.yaml 中 asset 声明遗漏 | 检查 pubspec.yaml → `flutter:` → `assets:` |
| **Missing entry point** | `main()` 函数未定义            | 检查 `lib/main.dart` 是否存在              |

### Null Safety 问题

```dart
// 运行时非空断言失败
// 检查 '!' 的使用是否合理，改用 ?. 或 ?? 替代

// 排查步骤
// 1. 确认变量是否可能为 null
// 2. 用 ?. 安全访问
// 3. 用 ?? 提供默认值
// 4. 用 late 延迟初始化（确保一定在使用前赋值）
// 5. 用 required 构造函数参数确保非空

// 打点验证
// DEBUG: [function] value=%s, isNull=%b
```

### Widget 不更新 / 重建问题

```dart
// 检查 key 是否正确使用
// 列表项必须有唯一 key
ListView.builder(
  itemBuilder: (context, index) => ListTile(key: ValueKey(item.id)),
);

// 检查 setState 是否被调用
// DEBUG: [Widget] setState called | reason=%s

// 检查 InheritedWidget / Provider 值变化
// 使用 context.watch() 而非 context.read() 来监听变化

// 检查 const 构造函数是否遗漏（导致不必要重建）
class MyWidget extends StatelessWidget {
  const MyWidget({super.key}); // 确保有 const 构造函数
}
```

### 异步问题

```dart
// Future 未 await
// 检查所有 async 调用前是否有 await

// Stream 未监听
// 检查 StreamSubscription 是否正确 cancel

// Isolate 通信问题
// 检查 SendPort / ReceivePort 配对是否正确

// 打点验证
// DEBUG: [function] async start | args=%o
// DEBUG: [function] async complete | result=%o
// DEBUG: [function] error | exception=%o
```

### 布局溢出 / RenderFlex 问题

```dart
// 用 Flutter DevTools 的 Layout Explorer 分析
// 检查 Expanded / Flexible 的使用
// 检查 SizedBox / ConstrainedBox 约束
// 使用 FittedBox 或 Flexible 自适应

// 临时调试：用不同颜色背景标记各 widget 区域
// Container(color: Colors.red.withOpacity(0.3))
```

### 网络请求调试

```dart
// 使用 dart:developer 的 log 方法（可在 DevTools 中查看）
import 'dart:developer' as developer;

developer.log('Request sent', name: 'api', value: {'url': url, 'method': method});

// 使用 http 拦截器
// 在 Dio 或 http 客户端中添加 LogInterceptor
// Dio().interceptors.add(LogInterceptor(requestBody: true, responseBody: true));
```

## 打点规范

```dart
// 使用 dart:developer 的 log（兼容 DevTools）
import 'dart:developer' as developer;

// 打点
developer.log('enter', name: 'MyClass.method', value: {'arg': arg});
developer.log('exit', name: 'MyClass.method', value: {'result': result});

// 临时 stdout 打点（用于终端调试）
// DEBUG: [MyClass.method] enter | arg=$arg
// DEBUG: [MyClass.method] exit | result=$result
```

## Flutter 特有调试技巧

```dart
// 1. 启用 Debug Paint 查看布局边界
// 在 main() 中添加：
// import 'package:flutter/rendering.dart';
// RendererBinding.instance.setDebugPaintEnabled(true);

// 2. 启用 Performance Overlay
// MaterialApp(showPerformanceOverlay: true);

// 3. 检查重建原因
// 在 build 方法中添加 print 标记

// 4. 慢速动画调试
// MaterialApp(debugShowCheckedModeBanner: false);
// 在 DevTools 中启用 Slow Animations
```

## 调试流程

```
① dart analyze → 修复静态分析问题
② dart test → 确认测试通过
③ flutter run --debug → 运行时验证
④ 使用 DevTools（Timeline / Inspector / Memory）→ 深入分析
⑤ 最小复现 Widget → 隔离布局/状态问题
⑥ 修复后重新运行 dart analyze + dart test 回归验证
```
