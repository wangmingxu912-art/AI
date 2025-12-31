# AQA A-Level 手写卷子自动评分（PDF → 图片 → GPT-5.2 评分）

这是一个最小可用的网页应用：

- **上传手写 PDF**，后端把每一页渲染成 PNG 并展示
- **框选每题答案区域**（bbox），裁剪出每题图
- **空白题自动 0 分**（不调用模型）
- **非空白题调用 GPT-5.2（thinking/高推理）评分**，并返回错误区域 bbox
- **前端在裁剪图上叠加错误框**，并汇总总分

## 运行方式

### 1) 安装依赖

```bash
python -m pip install -r requirements.txt
```

### 2) 配置 OpenAI Key

```bash
export OPENAI_API_KEY="你的key"
```

可选：

```bash
export OPENAI_MODEL="gpt-5.2"
export OPENAI_REASONING_EFFORT="high"
```

### （可选）演示模式：不调用 OpenAI，返回假评分/错误框

如果你只是想先确认“上传→渲染→框选→叠加显示→汇总总分”这条链路好不好用，可以开启演示模式：

```bash
export APP_MOCK_GPT=1
```

### 自动识别题目（推荐：按题干编号 OCR 分题）

现在默认会优先用 OCR 识别题干编号（例如 `1`、`2(a)`、`Q3`）来分题；如果 OCR 识别不到，再回退到“按空白行切分连续答案块”的方式。

本地运行如果没有 OCR 依赖，需要安装 tesseract：

```bash
sudo apt-get update && sudo apt-get install -y tesseract-ocr tesseract-ocr-eng
```

### 3) 启动服务

```bash
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

然后打开 `http://localhost:8000`。

## 部署到网上（推荐 Docker）

这个项目是“前端静态页 + 后端 FastAPI”同一个服务，**最简单就是用 Docker 部署到 Render / Railway / Fly.io** 这类平台。

### 方式 A：任意支持 Docker 的平台（通用）

- **Build**：使用仓库根目录的 `Dockerfile`
- **启动**：容器默认会执行 `uvicorn ... --port ${PORT}`（平台会注入 `PORT`）
- **必须配置的环境变量**：
  - `OPENAI_API_KEY`
- **可选环境变量**：
  - `OPENAI_MODEL`（默认 `gpt-5.2`）
  - `OPENAI_REASONING_EFFORT`（默认 `high`）

### 方式 B：自己服务器（Docker）

```bash
docker build -t aqa-grader .
docker run -p 8000:8000 -e OPENAI_API_KEY="你的key" aqa-grader
```

## 使用说明

- **上传 PDF**：会自动渲染所有页
- **自动识别题目**：点击“自动识别题目”，系统会把连续书写内容按空白行自动切分成 Q1、Q2…
- **填写满分/标准**：可统一设置默认满分，也可逐题修改，并粘贴题干/mark scheme（越完整越准）
- **评分**：点击“开始评分”

## 重要提示

- 如果你不提供题干/评分标准，模型会用“合理的 AQA 风格”进行近似评分，准确度会更差。
- 错误框（bbox）来自模型输出：如果模型无法可靠定位，会留空或不完整。