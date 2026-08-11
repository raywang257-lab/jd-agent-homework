# 招聘 JD Agent

这是“AI Agent 实战题—题目一”的可运行交付物。它完成：

1. 根据岗位信息生成结构化 JD。
2. 检查关键信息并给出追问。
3. 支持人工编辑与版本级审批。
4. 发布前检查可疑限制和过度承诺。
5. 导出真实 Word 文件。
6. 经人工审批后发送带 Word 附件的真实邮件。
7. 保存生成、审批、下载和发送日志。

## 快速启动

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
streamlit run app.py
```

浏览器默认打开 `http://localhost:8501`。

## 配置真实 AI

在 `.env` 填写：

```text
LLM_API_KEY=...
LLM_BASE_URL=...
LLM_MODEL=...
```

`LLM_BASE_URL` 可留空，表示使用 SDK 默认接口。未填写 API Key 时，系统会明确标记为“演示生成器（非 AI）”，只用来检查界面、审批、Word 和日志流程。

## 配置真实邮件

在 `.env` 填写 SMTP 信息。`ALLOWED_RECIPIENTS` 是必填白名单，演示时建议只填写自己的测试邮箱。未审批的内容、审批后被修改的内容、非白名单收件人均无法发送。

## 运行测试

```bash
pytest -q
```

## Demo 流程

1. 在“岗位输入”页删除一个必填字段，展示生成被拦截和自动追问。
2. 补齐信息并生成 JD。
3. 在结果中加入“30岁以下”，展示风险检查，然后删除。
4. 填写确认人并审批当前版本。
5. 下载 Word，并向白名单邮箱发送真实邮件。
6. 打开审计日志和收件箱展示闭环。

## 当前局限

- 风险检查当前为明确可解释的规则集，不等于法律意见。
- OpenAI-compatible 服务必须支持 `json_schema` 响应格式；否则需在 `workflow.py` 中改用对应厂商的结构化输出方式。
- SMTP 服务器接受邮件不代表收件端一定不会将其归入垃圾邮件。
