# 平台检测与仓库过滤策略

## 概述

分析 bug 时，先判断问题来自眼镜端(glasses)还是主机端(host)，然后只使用对应平台的仓库，并根据日志类型进一步缩小代码搜索范围。减少发送给 LLM 的无用代码上下文。

## 平台检测规则

### 眼镜端 (Glasses) 特征 — 高权重 (score 3)

| 特征 | 说明 |
|---|---|
| `xrlinux` | xrlinux 系统标识 |
| `rockchip\|rk3568\|rk3588` | Rockchip 芯片 (眼镜端硬件) |
| `dove` | Dove 项目 (眼镜端) |
| `xr_codec` | XR Codec (眼镜端编解码) |
| `nrsdk\|nr.?sdk` | NRSDK (眼镜端SDK) |
| `leopard\|heron\|nreal-framework` | 眼镜相关项目名 |

### 眼镜端 — 中权重 (score 2)

- Linux 系统特征: `/dev/video|input|tty|fb`, `/sys/class|/sys/devices`, `dmesg|kernel:`, `systemd|systemctl`
- 硬件驱动: `drm|kms|fbdev|disp`, `v4l2|videodev`, `alsa|snd_|sound.*driver`, `i2c|spi|uart|gpio`
- Native 信号: `signal (11|6|7)`, `segfault|segv`

### 主机端 (Host) 特征 — 高权重 (score 3)

| 特征 | 说明 |
|---|---|
| `logcat\|adb logcat` | Android logcat 日志 |
| `android\.os\|android\.app` | Android SDK 包名 |
| `ActivityManager\|WindowManager` | Android 系统服务 |
| `ANR\|Application Not Responding` | ANR (应用无响应) |
| `com\.nreal\|com\.android` | 应用标识 |

### 主机端 — 中权重 (score 2)

- Android 组件: `Activity`, `Fragment`, `Service`, `AndroidManifest.xml`
- 构建系统: `gradle`, `androidx`, `com\.android`
- Java 异常: `NullPointerException`, `IllegalStateException`

### 判定逻辑

- 累计所有匹配特征的分数
- 眼镜端分数 > 主机端分数 → `glasses`，否则 `host`
- 置信度 = min(1.0, diff/max_total + 0.3)
- 无法判定 → `unknown`

## Manifest 解析

### 搜索顺序

1. 眼镜端: 搜索 `xrlinux*.xml` / `xrlinux_manifest.xml` / `release.xrl`
2. 主机端: 搜索 `android.xml` / `android_manifest.xml` / `release.xml` / `default.xml`
3. 找不到 manifest → 使用 `repositories/config.yaml` 中的 `platform_repos.fallback_repos`

### Manifest 格式

标准 repo manifest XML:
```xml
<manifest>
  <remote name="origin" fetch="https://github.com/nreal-ai/" />
  <default remote="origin" revision="main" />
  <project name="dove" path="dove" revision="main" />
</manifest>
```

## 日志类型 → 仓库映射

| 日志类型 | 识别模式 | 目标仓库 |
|---|---|---|
| `logcat` / `java` / `android_framework` | logcat 格式、Java 异常、Android 系统服务 | project, framework |
| `kernel` / `display_driver` / `audio_driver` | kernel:、drm、v4l2、alsa、/dev/ | dove, leopard, framework, heron, nrsdkrepo, xr_codec |
| `native` | signal 11/6/7、SIGSEGV、SIGABRT、tombstone | dove, leopard, heron, nrsdkrepo, framework, xr_codec, nrealUtil |
| `xr_device` | [dove/、[leopard/、[heron/、xr_codec、nrsdk | dove, leopard, heron, nrsdkrepo, xr_codec, nrealUtil, framework |

### 仓库交集逻辑

多种日志类型同时存在时，取各类型对应仓库的**交集**：
- logcat + kernel → `{project, framework} ∩ {dove, leopard, ...}` = `framework` (跨平台交互问题)
- 交集为空时，回退到各类型的**并集**

## 平台仓库配置 (config.yaml)

```yaml
platform_repos:
  glasses:
    manifest_patterns: ["xrlinux*.xml", "release.xrl"]
    fallback_repos:
      - nrealUtil
      - heron
      - xr_codec
      - nrsdkrepo
      - dove
      - leopard
      - sparrow
      - framework
  host:
    manifest_patterns: ["android.xml", "release.xml"]
    fallback_repos:
      - project
      - framework
      - dove
      - leopard
      - sparrow
```

## 代码模块

- `scripts/platform_detector.py` — 平台检测主逻辑
- `scripts/manifest_parser.py` — Manifest XML 解析
- `scripts/analyzer.py` 中新增方法:
  - `_resolve_platform_repos()` — 解析平台仓库
  - `_classify_log_types()` — 日志分类
  - `_get_repos_for_log_types()` — 按日志类型过滤仓库
