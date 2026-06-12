# 跨文件上下文关联 (Cross-File Context Resolution)

## 功能概述

`_extract_context_snippet()` 在提取证据行附近日志上下文时，若目标行包含对其他文件的引用（堆栈跟踪、文件路径），自动追踪到关联文件提取上下文。

## 支持的引用格式

### Java/Kotlin 堆栈
```
at com.example.PilotService.java:150
Caused by: java.lang.NullPointerException at MainActivity.kt:42
```
→ 在 `log_contents` 中查找以 `PilotService.java` 或 `MainActivity.kt` 结尾的文件

### Native 堆栈
```
#00 pc 00012345 /system/lib/libfoo.so
#01 pc 00067890 /vendor/lib64/libbar.so
```
→ 在 `log_contents` 中查找以 `libfoo.so` 或 `libbar.so` 结尾的文件

### 通用文件:行引用
```
config.xml:456
helper.py:78
logcat.log:12345
```
→ 在 `log_contents` 中匹配对应文件名

## 输出格式

```
--- 附近日志 (2025_0407_19.36.56.zip/2025_0407_19.36.56/log/current_log_dir/pilot.log) ---
>>> L14230: FATAL: SIGSEGV in com.example.PilotService.java:150
    L14229: INFO: pilot started
    L14231: backtrace follows:

--- 关联文件上下文（跨文件追踪）---
  ↳ 2025_0407_19.36.56.zip/2025_0407_19.36.56/log/current_log_dir/PilotService.java (原因: Java堆栈引用 PilotService.java:150)
  >>> L150: void init() { throw new NullPointerException(); }
      L149: // called from native
      L151: }
```

## 实现细节

- `_resolve_cross_file_refs(line_text, log_contents, current_file)` 解析单行日志中的跨文件引用
- 返回最多 3 个关联文件引用 `[(ref_file, ref_line_num, reason), ...]`
- 使用去重集合 `seen` 避免同一文件重复引用
- 上下文行数与主文件一致（默认 ±5 行）
- 仅当 `log_contents` 中存在对应文件时才输出

## 限制

- 只解析单行内容，不跨行追踪调用链
- 文件名匹配使用 `endswith` 后缀匹配，不要求完整路径
- 嵌套引用不递归（只追踪一层）
