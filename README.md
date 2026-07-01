# TranslaInTime

本地实时语音翻译字幕 Demo：浏览器采集麦克风，FastAPI WebSocket 接收 16 kHz PCM，`faster-whisper` 用 RTX 5070 Ti 做语音识别/英文翻译，页面实时显示字幕，不播放声音。

## 快速启动

```powershell
.\run.ps1
```

打开 <http://127.0.0.1:7860>，允许浏览器使用麦克风，然后点击“开始”。

## Qt 桌面版

```powershell
.\launch_qt_desktop.ps1
```

打包 exe：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts\package_qt.ps1
```

打包产物位于 `dist\TranslaInTime\TranslaInTime.exe`。

创建或刷新指向 exe 的桌面快捷方式：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts\create_exe_shortcut.ps1
```

默认配置：

- ASR 模型：`small`
- 设备：`auto`，优先 CUDA，失败后退 CPU
- 计算：`int8_float16`，速度优先
- 目标字幕：中文
- 分段：`1.2` 秒，默认开启速度优先和字幕去重
- CUDA 运行库：通过 `nvidia-cublas-cu12`、`nvidia-cudnn-cu12` 等 wheel 装在虚拟环境里，后端启动时自动加入 DLL 搜索路径
- 模型下载：默认使用 Hugging Face 官方源；本机实测 `hf-mirror.com` 对新版 `huggingface-hub` 元数据响应不兼容，所以不默认强制镜像
- pip 源：清华 PyPI 镜像

## 中文翻译包

目标语言为中文时，流程是“语音 -> Whisper 英文 pivot -> Argos 英中本地翻译”。首次使用中文翻译前建议安装 Argos 语言包：

```powershell
.\.venv\Scripts\python.exe scripts\install_argos_pair.py --from en --to zh
```

如果不安装，页面仍会显示 Whisper 的英文翻译，并在日志中提示缺少 `en->zh` 包。

## GPU 检查

```powershell
.\.venv\Scripts\python.exe scripts\diagnose_gpu.py
```

如果输出 `CUDA device count` 大于 0，CTranslate2 能看到 CUDA。若 Faster-Whisper 加载 CUDA 失败，服务会自动退回 CPU，并在页面日志中显示原因。

## 常用启动参数

```powershell
.\run.ps1 -ModelSize base -TargetLanguage en
.\run.ps1 -ModelSize medium -ComputeType float16
.\run.ps1 -Device cpu
.\run.ps1 -UseHfMirror
```

模型越小延迟越低；`tiny`/`base` 更适合低延迟演示，`small` 是速度和质量的折中，`medium` 准确率更好但首轮下载和推理更慢。

## 低延迟与快捷键

页面默认使用速度优先模式：目标不是英文时，后端优先走“Whisper 一次翻译到英文 pivot -> Argos 本地翻译到目标语言”，避免为了显示原文再额外跑一次 ASR。需要更完整原文时，可以关闭页面里的“速度优先”。

快捷键：

- `Space`：开始/停止
- `Esc`：停止
- `C`：清空字幕历史和重复计数

字幕历史最多保留 24 条。后端会先过滤重叠窗口造成的高相似重复，前端也会再做一次轻量去重并累计过滤数量。

## 说明

这个 Demo 优先速度和本地开源可运行性，不追求逐字级流式对齐。浏览器端每隔几秒把麦克风 PCM 推给后端，后端按短窗口处理并返回最新字幕。CTranslate2 的新版 CUDA 路线需要 CUDA 12/cuDNN 9 环境；RTX 50 系列建议保持新驱动。当前机器的 NVIDIA 驱动已能看到 RTX 5070 Ti，并且 `small` 模型已完成 CUDA 加载预热。
