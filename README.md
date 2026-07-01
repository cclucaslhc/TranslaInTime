# TranslaInTime

TranslaInTime 是一个本地实时语音翻译字幕工具。它可以从麦克风采集语音，使用 Faster-Whisper 做语音识别/翻译，再通过 Argos Translate 做本地离线文本翻译，最后在浏览器或 Qt 桌面窗口中显示字幕。

当前项目默认面向“英语语音 -> 中文字幕”的低延迟使用场景，同时也支持切换源语言、目标语言、分段长度和速度优先模式。

## 功能特点

- 本地运行：音频在本机处理，不依赖云端语音 API。
- 双入口：支持 Web 字幕页和 Qt 桌面版。
- GPU 优先：默认自动检测 CUDA，GPU 不可用时回退 CPU。
- 低延迟字幕：按短音频窗口持续识别，适合会议、演示和英语内容听译。
- 麦克风检测：Web 页面提供本地采集和后端接收电平，便于排查输入问题。
- 离线翻译：目标语言为中文时，可安装 Argos `en -> zh` 语言包实现本地翻译。
- 去重历史：自动过滤高度重复的字幕，保留最近字幕历史。

## 环境要求

- Windows 10/11
- Python 3.10
- PowerShell
- 可用麦克风
- 可选：NVIDIA GPU、较新的 NVIDIA 驱动、CUDA 12/cuDNN 9 兼容环境

项目脚本会自动创建 `.venv` 虚拟环境，并使用清华 PyPI 镜像安装 Python 依赖。首次运行会下载 Whisper 模型，耗时取决于网络和模型大小。

## 快速开始：Web 版

在项目根目录运行：

```powershell
.\run.ps1
```

启动完成后打开：

<http://127.0.0.1:7860>

在浏览器中允许麦克风权限，然后点击“开始”。如果只是想确认麦克风是否被浏览器和后端正确接收，可以先使用页面里的麦克风检测功能。

## 快速开始：Qt 桌面版

```powershell
.\launch_qt_desktop.ps1
```

桌面版会在后台启动 Qt 窗口，默认使用：

- 源语言：英语
- 目标语言：中文
- 模型：`small`
- 设备：`auto`
- 计算类型：`int8_float16`
- 分段：`1.2` 秒

## 常用启动参数

Web 版可以通过 PowerShell 参数调整运行方式：

```powershell
.\run.ps1 -ModelSize base -TargetLanguage zh
.\run.ps1 -ModelSize medium -ComputeType float16
.\run.ps1 -Device cpu
.\run.ps1 -HostName 127.0.0.1 -Port 7860
.\run.ps1 -UseHfMirror
```

参数说明：

| 参数 | 默认值 | 说明 |
| --- | --- | --- |
| `-ModelSize` | `small` | Whisper 模型大小，常用 `tiny`、`base`、`small`、`medium` |
| `-Device` | `auto` | `auto` 会优先 CUDA，失败后回退 CPU；也可指定 `cpu` 或 `cuda` |
| `-ComputeType` | `int8_float16` | GPU 上偏速度的默认配置；CPU 会自动改用 `int8` |
| `-TargetLanguage` | `zh` | 默认目标字幕语言 |
| `-UseHfMirror` | 关闭 | 设置 `HF_ENDPOINT=https://hf-mirror.com` 下载模型 |

模型越小，启动和推理越快；模型越大，准确率通常更好但延迟更高。演示和实时字幕建议先用 `base` 或 `small`。

## 安装中文翻译语言包

如果目标语言是中文，建议先安装 Argos Translate 的英语到中文语言包：

```powershell
.\.venv\Scripts\python.exe scripts\install_argos_pair.py --from en --to zh
```

未安装语言包时，程序仍会显示 Whisper 生成的英文翻译，并在日志中提示缺少 `en -> zh` 包。

## 检查 GPU

安装依赖后可以运行：

```powershell
.\.venv\Scripts\python.exe scripts\diagnose_gpu.py
```

如果输出中的 `CUDA device count` 大于 0，说明 CTranslate2 可以看到 CUDA 设备。若 CUDA 加载失败，应用会自动回退 CPU，并在页面或日志中显示原因。

## 打包桌面版 EXE

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts\package_qt.ps1
```

打包产物位于：

```text
dist\TranslaInTime\TranslaInTime.exe
```

创建或刷新桌面快捷方式：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts\create_exe_shortcut.ps1
```

## 快捷键

Web 版：

- `Space`：开始/停止
- `Esc`：停止
- `C`：清空字幕历史
- `M`：麦克风检测

Qt 桌面版：

- `Space`：开始/停止
- `Esc`：停止
- `Ctrl+L`：清空字幕历史

## 常见问题

### 浏览器打不开页面

确认 `.\run.ps1` 仍在运行，并访问 <http://127.0.0.1:7860>。如果修改了端口，请使用启动日志中显示的地址。

### 浏览器没有麦克风权限

检查浏览器地址栏的麦克风权限，或在系统设置中确认浏览器允许访问麦克风。建议优先使用 Chrome 或 Edge。

### 字幕一直没有结果

先使用麦克风检测确认本地和后端电平都在变化。若电平过低，请靠近麦克风或调高系统输入音量。

### 中文翻译没有生效

运行 `scripts\install_argos_pair.py --from en --to zh` 安装语言包。安装完成后重新启动应用。

### GPU 没有被使用

运行 `scripts\diagnose_gpu.py` 检查 CUDA 可见性。驱动、CUDA 运行库或 CTranslate2 wheel 不匹配时会回退 CPU，这是预期的保护行为。

### 模型下载慢

默认使用 Hugging Face 官方源。可以用 `.\run.ps1 -UseHfMirror` 尝试 `hf-mirror.com`，但如果镜像出现元数据兼容性问题，请切回默认源。

## 项目结构

```text
app/main.py                 FastAPI 后端和 WebSocket 音频处理
static/                     Web 字幕页面
desktop_core.py             Qt 桌面版实时翻译引擎
desktop_qt_app.py           Qt 桌面界面
run.ps1                     Web 版启动脚本
launch_qt_desktop.ps1       Qt 桌面版启动脚本
scripts/                    安装、诊断和打包辅助脚本
requirements.txt            Python 依赖
```

## 说明

TranslaInTime 目前更重视本地可运行性和低延迟体验，不追求逐字级严格对齐。Web 版通过浏览器采集 16 kHz PCM 音频并发送给后端；后端按短窗口处理音频、过滤静音和重复结果，再返回字幕。Qt 版直接在桌面进程中采集麦克风并显示字幕。
