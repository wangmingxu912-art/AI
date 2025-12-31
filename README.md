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

### 3) 启动服务

```bash
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

然后打开 `http://localhost:8000`。

## 使用说明

- **上传 PDF**：会自动渲染所有页
- **框选**：在任意页上拖拽鼠标框选答案区域（bbox）
- **添加题目**：填写题号、满分（建议粘贴题干与 mark scheme），点击“添加该题”
- **评分**：点击“开始评分”

## 重要提示

- 如果你不提供题干/评分标准，模型会用“合理的 AQA 风格”进行近似评分，准确度会更差。
- 错误框（bbox）来自模型输出：如果模型无法可靠定位，会留空或不完整。