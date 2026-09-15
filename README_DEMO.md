# CityPercept AI Demo 落地指南

## 1. Demo 要解决的问题

用户上传一张街景图片，系统调用多模态模型完成识别与评分，网页读取结构化结果并动态展示。

完整链路如下：

```text
上传图片
  -> 保存为临时文件
  -> 调用 openai_image_labeler.py
  -> 渲染 Prompt
  -> 调用 GPT 或 Gemini
  -> 按 JSON Schema 校验输出
  -> 生成 annotation.json
  -> 网页读取评分
  -> 展示指标、分析依据和综合结果
```

## 2. 项目文件职责

| 文件 | 作用 |
| --- | --- |
| `citypercept_app.py` | Streamlit 网页入口，负责上传、调用评分流程和结果展示 |
| `run_demo.ps1` | Windows 一键启动脚本 |
| `requirements.txt` | 网页和模型调用所需依赖 |
| `llm标注结果/scripts/openai_image_labeler.py` | AI 评分 Workflow，负责模型请求和结构化输出 |
| `llm标注结果/prompt.md` | 评分规则、专业定义和输出要求 |
| `llm标注结果/schemas/annotation_schema.json` | 强制模型输出的 JSON 结构 |
| `llm标注结果/.env` | 当前模型、接口地址和运行参数 |
| `llm标注结果/.env.example` | 配置模板，不包含真实密钥 |

不要提交或公开 `llm标注结果/.env`，因为它包含 API Key。

## 3. 第一次运行

### 3.1 安装依赖

在项目根目录执行：

```powershell
python -m venv .demo_venv
.\.demo_venv\Scripts\python.exe -m pip install -r requirements.txt
```

### 3.2 配置模型

打开 `llm标注结果/.env`，确认以下三项：

```dotenv
OPENAI_API_KEY=你的密钥
OPENAI_BASE_URL=你的接口地址
OPENAI_MODEL=你的视觉模型名称
```

如果要切换 GPT 和 Gemini，只改 `OPENAI_MODEL` 与对应的 `OPENAI_BASE_URL`，网页代码不需要修改。

如果暂时没有模型密钥，可以把 `OPENAI_API_KEY` 留空。此时网页会显示明确标注的演示结果，便于检查上传、结果结构、表格和下载功能，但不会调用真实模型。

### 3.3 启动网页

推荐直接运行：

```powershell
powershell -ExecutionPolicy Bypass -File .\run_demo.ps1
```

启动脚本会自动寻找可用的 Python 环境；如果 `8501` 已被占用，
会自动改用后续可用端口。终端会打印实际访问地址，例如：

```text
http://127.0.0.1:8501
```

也可以手动启动：

```powershell
.\.demo_venv\Scripts\python.exe -m streamlit run .\citypercept_app.py
```

## 4. 网页中最重要的三个步骤

### 第一步：接收图片

Streamlit 通过 `st.file_uploader` 接收图片，再把文件内容转换为二进制数据。

### 第二步：调用已有评分流程

网页不重新实现评分，而是调用已有的 `openai_image_labeler.py`。

```python
annotation = run_ai_analysis(
    uploaded_file.getvalue(),
    uploaded_file.name,
)
```

这一步执行完成后，评分流程已经生成 `annotation.json`。

### 第三步：读取结构化结果并展示

网页把 `evaluations` 转换成以维度名称为键的字典，然后按名称读取分数：

```python
evaluations = {
    item["dimension"]: item
    for item in annotation["evaluations"]
}

walkability_score = evaluations["M2 步行友好感"]["score"]
```

网页展示的是模型返回的真实结果，不再写死 `4.2 / 5` 之类的分数。

## 5. annotation.json 的结构

关键字段如下：

```json
{
  "image_overview": "整体街景分析",
  "evaluations": [
    {
      "dimension": "M2 步行友好感",
      "score": "4",
      "visual_evidence": "画面中的直接视觉依据",
      "reasoning": "按照评分锚点给出的理由"
    }
  ],
  "score_summary": {
    "valid_count": 11,
    "mean_score": 3.9,
    "highest": {
      "dimension": "M2 步行友好感",
      "score": "4"
    },
    "lowest": {
      "dimension": "Y3 情绪愉悦度",
      "score": "3"
    },
    "na_dimensions": []
  }
}
```

## 6. 落地时如何排查问题

### 页面能打开，但点击分析失败

查看 `llm标注结果/.env`，重点检查 API Key、接口地址和模型名称。

如果日志中出现 `401 Unauthorized`，表示接口认证失败。优先检查密钥是否过期、接口地址是否与密钥所属平台一致。认证错误不会继续重试，页面会直接给出提示。

如果 `OPENAI_BASE_URL` 使用公网 `http://` 地址，API Key 在网络传输中不具备加密保护。正式部署必须改用服务商提供的 `https://` 地址。

### 模型返回后网页显示失败

检查 `llm标注结果/schemas/annotation_schema.json` 是否包含网页需要读取的评分维度。

### 想增加安全感指标

必须同时修改三个地方：

1. `prompt.md` 中增加指标定义和 1-5 分锚点。
2. `annotation_schema.json` 中增加维度名称，并把数量从 11 改为 12。
3. `openai_image_labeler.py` 的 `DIMENSION_EXPORT_SPECS` 中增加对应字段。

只修改网页文案不会产生真实的安全感评分。

## 7. 从 Demo 到正式产品

当前版本适合作品集、课程项目和个人演示：

```text
Streamlit
  -> Python 评分脚本
  -> 多模态模型
  -> 结构化 JSON
```

正式多用户产品应升级为：

```text
Web 前端
  -> FastAPI 接口
  -> 图片对象存储
  -> 任务队列
  -> 异步 AI Worker
  -> 数据库
  -> 前端查询任务状态和结果
```

升级时应保留当前已经验证过的 Prompt、Schema、评分规则和字段映射，只替换任务调度和数据存储方式。

## 8. 可用于项目介绍的技术描述

设计并实现街景智能分析 Demo，完成图片上传、Prompt 模板渲染、多模态模型调用、JSON Schema 结构化约束、评分解析与可视化展示。通过统一输出契约连接模型、评分脚本和网页前端，实现单张街景图像的自动识别、结构化评分和空间品质分析。
