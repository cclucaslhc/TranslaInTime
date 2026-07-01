const SAMPLE_RATE = 16000;
const MAX_HISTORY = 24;

const startButton = document.querySelector("#startButton");
const monitorButton = document.querySelector("#monitorButton");
const stopButton = document.querySelector("#stopButton");
const clearButton = document.querySelector("#clearButton");
const statusEl = document.querySelector("#status");
const runtimeEl = document.querySelector("#runtime");
const translationEl = document.querySelector("#translation");
const originalEl = document.querySelector("#original");
const metricsEl = document.querySelector("#metrics");
const logEl = document.querySelector("#log");
const historyEl = document.querySelector("#history");
const sourceLanguageEl = document.querySelector("#sourceLanguage");
const targetLanguageEl = document.querySelector("#targetLanguage");
const chunkSecondsEl = document.querySelector("#chunkSeconds");
const speedModeEl = document.querySelector("#speedMode");
const localMeterEl = document.querySelector("#localMeter");
const serverMeterEl = document.querySelector("#serverMeter");
const localLevelTextEl = document.querySelector("#localLevelText");
const serverLevelTextEl = document.querySelector("#serverLevelText");
const localStatsEl = document.querySelector("#localStats");
const serverStatsEl = document.querySelector("#serverStats");

let socket;
let audioContext;
let sourceNode;
let processorNode;
let mediaStream;
let monitorOnly = false;
let duplicateCount = 0;
let localPacketCount = 0;
let maxLocalPeak = 0;
let lastSignature = "";
const historyItems = [];

function getBackendBase() {
  if (window.location.protocol === "file:") return "127.0.0.1:7860";
  return window.location.host || "127.0.0.1:7860";
}

function setStatus(text, mode = "idle") {
  statusEl.textContent = text;
  statusEl.dataset.mode = mode;
}

function addLog(text) {
  const item = document.createElement("div");
  item.textContent = `${new Date().toLocaleTimeString()}  ${text}`;
  logEl.prepend(item);
  while (logEl.children.length > 8) logEl.lastChild.remove();
}

function signature(text) {
  return (text || "").toLocaleLowerCase().replace(/[\s\p{P}\p{S}_]+/gu, "");
}

function isNearDuplicate(text) {
  const current = signature(text);
  if (current.length < 2) return true;
  if (!lastSignature) {
    lastSignature = current;
    return false;
  }
  if (current === lastSignature) return true;
  if (
    Math.min(current.length, lastSignature.length) >= 8 &&
    (current.includes(lastSignature) || lastSignature.includes(current))
  ) {
    if (current.length > lastSignature.length) lastSignature = current;
    return true;
  }
  lastSignature = current;
  return false;
}

function updateMetrics() {
  metricsEl.textContent = `重复过滤 ${duplicateCount} 条`;
}

function updateMeter(fillEl, labelEl, peak, dbfs) {
  const percent = Math.min(100, Math.max(0, peak * 140));
  fillEl.style.width = `${percent}%`;
  const levelName = peak < 0.006 ? "很安静" : peak < 0.04 ? "偏小" : peak < 0.8 ? "正常" : "过载";
  labelEl.textContent = `${levelName} ${dbfs.toFixed(1)} dBFS`;
}

function updateLocalLevel(audio) {
  localPacketCount += 1;
  let sum = 0;
  let peak = 0;
  for (let i = 0; i < audio.length; i += 1) {
    const value = Math.abs(audio[i]);
    if (value > peak) peak = value;
    sum += audio[i] * audio[i];
  }
  const rms = Math.sqrt(sum / Math.max(1, audio.length));
  const dbfs = 20 * Math.log10(Math.max(rms, 1e-6));
  maxLocalPeak = Math.max(maxLocalPeak, peak);
  updateMeter(localMeterEl, localLevelTextEl, peak, dbfs);
  localStatsEl.textContent = `本地包 ${localPacketCount} · Peak ${peak.toFixed(3)} · Max ${maxLocalPeak.toFixed(3)}`;
}

function resetMicMeters() {
  localPacketCount = 0;
  maxLocalPeak = 0;
  localMeterEl.style.width = "0%";
  serverMeterEl.style.width = "0%";
  localLevelTextEl.textContent = "未开始";
  serverLevelTextEl.textContent = "未收到";
  localStatsEl.textContent = "本地包 0 · Peak 0.000";
  serverStatsEl.textContent = "后端包 0 · 0 ms";
}

function renderHistory() {
  historyEl.innerHTML = "";
  if (historyItems.length === 0) {
    const empty = document.createElement("div");
    empty.className = "history-empty";
    empty.textContent = "暂无历史";
    historyEl.append(empty);
    return;
  }

  for (const item of historyItems) {
    const row = document.createElement("article");
    row.className = "history-item";

    const time = document.createElement("time");
    time.textContent = item.time;

    const text = document.createElement("div");
    text.className = "history-text";
    text.textContent = item.translation;

    const original = document.createElement("div");
    original.className = "history-original";
    original.textContent = item.original ? `原文：${item.original}` : "";

    row.append(time, text, original);
    historyEl.append(row);
  }
}

function addHistory(result) {
  const translation = (result.translation || "").trim();
  if (!translation || isNearDuplicate(translation)) {
    duplicateCount += 1;
    updateMetrics();
    return false;
  }

  historyItems.unshift({
    time: new Date().toLocaleTimeString(),
    translation,
    original: (result.original || "").trim(),
  });
  while (historyItems.length > MAX_HISTORY) historyItems.pop();
  renderHistory();
  return true;
}

function clearHistory() {
  historyItems.length = 0;
  duplicateCount = 0;
  lastSignature = "";
  updateMetrics();
  renderHistory();
}

function downsampleTo16k(input, inputRate) {
  if (inputRate === SAMPLE_RATE) return new Float32Array(input);
  const ratio = inputRate / SAMPLE_RATE;
  const outputLength = Math.floor(input.length / ratio);
  const output = new Float32Array(outputLength);
  for (let i = 0; i < outputLength; i += 1) {
    const start = Math.floor(i * ratio);
    const end = Math.min(Math.floor((i + 1) * ratio), input.length);
    let sum = 0;
    for (let j = start; j < end; j += 1) sum += input[j];
    output[i] = sum / Math.max(1, end - start);
  }
  return output;
}

function sendConfig() {
  if (!socket || socket.readyState !== WebSocket.OPEN) return;
  socket.send(
    JSON.stringify({
      type: "config",
      sourceLanguage: sourceLanguageEl.value,
      targetLanguage: targetLanguageEl.value,
      chunkSeconds: Number(chunkSecondsEl.value),
      speedMode: speedModeEl.checked,
      monitorOnly,
      dedupe: true,
      dedupeSimilarity: 0.86,
    }),
  );
}

function connectSocket() {
  const protocol = window.location.protocol === "https:" ? "wss" : "ws";
  const backendBase = getBackendBase();
  setStatus("连接后端中", "idle");
  socket = new WebSocket(`${protocol}://${backendBase}/ws/audio`);
  socket.binaryType = "arraybuffer";

  const connectTimer = window.setTimeout(() => {
    if (socket && socket.readyState !== WebSocket.OPEN) {
      setStatus("连接超时", "error");
      addLog(`WebSocket 连接超时：ws://${backendBase}/ws/audio`);
    }
  }, 3000);

  socket.addEventListener("open", () => {
    window.clearTimeout(connectTimer);
    sendConfig();
    setStatus("监听中", "live");
  });

  socket.addEventListener("message", (event) => {
    const data = JSON.parse(event.data);
    if (data.type === "ready") {
      addLog(`服务端就绪，模型 ${data.model}，分段 ${data.chunkSeconds}s${data.monitorOnly ? "，仅检测麦克风" : ""}`);
      return;
    }
    if (data.type === "input_level") {
      updateMeter(serverMeterEl, serverLevelTextEl, data.peak, data.dbfs);
      serverStatsEl.textContent = `后端包 ${data.packetCount} · ${data.receivedMs} ms · RMS ${data.rms}`;
      if (monitorOnly) setStatus("检测中", "live");
      return;
    }
    if (data.type === "level") {
      setStatus("声音偏小", "idle");
      addLog(`后端判定音量偏小，peak=${data.peak ?? 0}`);
      return;
    }
    if (data.type === "processing") {
      setStatus("识别中", "live");
      runtimeEl.textContent = `正在识别 ${data.audioMs} ms 音频 · peak ${data.peak}`;
      if (!translationEl.textContent || translationEl.textContent === "开始说话") {
        translationEl.textContent = "正在识别英文语音";
      }
      return;
    }
    if (data.type === "empty_result") {
      setStatus("无文本", "idle");
      translationEl.textContent = "未识别到文本，请继续说完整英文句子";
      originalEl.textContent = `检测语言：${data.detectedLanguage || "unknown"} · peak ${data.peak}`;
      addLog(`Whisper 返回空文本，latency=${data.latencyMs}ms，detected=${data.detectedLanguage || "unknown"}，peak=${data.peak}`);
      return;
    }
    if (data.type === "duplicate") {
      duplicateCount += 1;
      updateMetrics();
      if (data.latencyMs) setStatus(`${data.latencyMs} ms`, "live");
      return;
    }
    if (data.type === "error") {
      setStatus("出错", "error");
      addLog(data.message);
      return;
    }
    if (data.type === "result") {
      if (!addHistory(data)) return;
      setStatus(`${data.latencyMs} ms`, "live");
      runtimeEl.textContent = `${data.model} · ${data.device} · ${data.computeType} · ${data.detectedLanguage || "auto"}→${data.targetLanguage}`;
      if (data.translation) translationEl.textContent = data.translation;
      originalEl.textContent = data.original ? `原文：${data.original}` : "";
      if (data.loadWarning) addLog(data.loadWarning);
      if (data.translateWarning) addLog(data.translateWarning);
    }
  });

  socket.addEventListener("close", () => {
    window.clearTimeout(connectTimer);
    if (mediaStream) {
      setStatus("后端断开", "error");
      addLog(`WebSocket 已断开，请确认服务地址 http://${backendBase}`);
    } else {
      setStatus("已断开", "idle");
    }
  });

  socket.addEventListener("error", () => {
    window.clearTimeout(connectTimer);
    setStatus("连接失败", "error");
    addLog(`无法连接后端：ws://${backendBase}/ws/audio`);
  });
}

async function pingBackend() {
  try {
    const response = await fetch(`http://${getBackendBase()}/health`, { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const data = await response.json();
    addLog(`后端在线，CUDA 设备 ${data.cudaDevices}`);
  } catch (error) {
    addLog(`后端健康检查失败：${error.message}`);
  }
}

async function start(nextMonitorOnly = false) {
  if (mediaStream) return;
  monitorOnly = nextMonitorOnly;
  startButton.disabled = true;
  monitorButton.disabled = true;
  stopButton.disabled = false;
  resetMicMeters();
  translationEl.textContent = monitorOnly ? "麦克风检测中" : "正在请求麦克风权限";
  originalEl.textContent = "";

  connectSocket();
  mediaStream = await navigator.mediaDevices.getUserMedia({
    audio: {
      channelCount: 1,
      echoCancellation: true,
      noiseSuppression: true,
      autoGainControl: true,
    },
  });

  audioContext = new AudioContext({ latencyHint: "interactive" });
  sourceNode = audioContext.createMediaStreamSource(mediaStream);
  processorNode = audioContext.createScriptProcessor(2048, 1, 1);
  processorNode.onaudioprocess = (event) => {
    event.outputBuffer.getChannelData(0).fill(0);
    if (!socket || socket.readyState !== WebSocket.OPEN) return;
    const input = event.inputBuffer.getChannelData(0);
    updateLocalLevel(input);
    const pcm16k = downsampleTo16k(input, audioContext.sampleRate);
    socket.send(pcm16k.buffer);
  };

  sourceNode.connect(processorNode);
  processorNode.connect(audioContext.destination);
  translationEl.textContent = monitorOnly ? "请对着麦克风说话，观察上方电平" : "开始说话";
}

async function stop() {
  startButton.disabled = false;
  monitorButton.disabled = false;
  stopButton.disabled = true;
  if (processorNode) processorNode.disconnect();
  if (sourceNode) sourceNode.disconnect();
  if (audioContext) await audioContext.close();
  if (mediaStream) mediaStream.getTracks().forEach((track) => track.stop());
  if (socket && socket.readyState === WebSocket.OPEN) socket.close();
  processorNode = undefined;
  sourceNode = undefined;
  audioContext = undefined;
  mediaStream = undefined;
  socket = undefined;
  monitorOnly = false;
  setStatus("已停止", "idle");
}

function isTypingTarget(target) {
  return ["INPUT", "SELECT", "TEXTAREA", "BUTTON"].includes(target.tagName);
}

startButton.addEventListener("click", () => {
  start(false).catch((error) => {
    addLog(error.message);
    setStatus("启动失败", "error");
    startButton.disabled = false;
    monitorButton.disabled = false;
    stopButton.disabled = true;
  });
});
monitorButton.addEventListener("click", () => {
  start(true).catch((error) => {
    addLog(error.message);
    setStatus("检测失败", "error");
    startButton.disabled = false;
    monitorButton.disabled = false;
    stopButton.disabled = true;
  });
});
stopButton.addEventListener("click", stop);
clearButton.addEventListener("click", clearHistory);

for (const control of [sourceLanguageEl, targetLanguageEl, chunkSecondsEl, speedModeEl]) {
  control.addEventListener("change", sendConfig);
}

document.addEventListener("keydown", (event) => {
  if (isTypingTarget(event.target)) return;
  if (event.code === "Space") {
    event.preventDefault();
    if (mediaStream) stop();
    else startButton.click();
  } else if (event.key === "Escape") {
    event.preventDefault();
    stop();
  } else if (event.key.toLocaleLowerCase() === "c") {
    clearHistory();
  } else if (event.key.toLocaleLowerCase() === "m") {
    event.preventDefault();
    if (mediaStream) stop();
    else monitorButton.click();
  }
});

updateMetrics();
renderHistory();
resetMicMeters();
pingBackend();
