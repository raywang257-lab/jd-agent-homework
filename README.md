# 招聘 JD Agent

[![Streamlit App](https://static.streamlit.io/badges/streamlit_badge_black_white.svg)](https://raywang257-jd-agent.streamlit.app)

公开在线 Demo：[https://raywang257-jd-agent.streamlit.app](https://raywang257-jd-agent.streamlit.app)

这是“AI Agent 实战题—题目一”的可运行交付物。它完成：

1. 根据岗位信息生成结构化 JD。
2. 检查关键信息并给出追问。
3. 支持人工编辑与版本级审批。
4. 发布前检查可疑限制和过度承诺。
5. 导出真实 Word 文件。
6. 经人工审批后发送带 Word 附件的真实邮件。
7. 保存生成、审批、下载和发送日志。
8. 支持粘贴原始需求、旧 JD 或沟通记录，自动抽取岗位事实并主动澄清。
9. 当前版本对高风险 JD 实施硬阻断，不能审批、下载或发送。
10. 职位名称、地点、工作方式、薪资和岗位亮点由结构化输入强制控制，模型不能擅自改写。
11. 风险检查覆盖任意两位数年龄限制及无依据营销表述。
12. 在复核阶段可自由切换目标发布平台，并重新生成对应平台文案；切换后必须重新审批。
13. 平台切换使用同一份 canonical 内容进行确定性渲染，不再重复调用模型。
14. Agent 会对缺乏场景和验证证据的泛化任职要求提出补充问题。
15. 生成前提供逐条内容质量诊断，区分安全改写与必须由 HR 补充的新事实，并记录采纳或保留决定。

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

1. 粘贴一段不完整的招聘需求或加载示例，让 Agent 自动抽取。
2. 展示冲突检查和最多 4 个关键追问，补充回答后重新整理。
3. 校对结构化结果，选择招聘平台并生成 JD。系统会自动进入复核阶段。
4. 在结果中加入“30岁以下”，展示高风险发布门禁。
5. 删除风险内容，填写确认人并审批当前版本。
6. 下载 Word，并向白名单邮箱发送真实邮件。
7. 打开当前案例审计日志和收件箱展示闭环。

## 当前局限

- 风险检查当前为明确可解释的规则集，不等于法律意见。
- OpenAI-compatible 服务必须支持 `json_schema` 响应格式；否则需在 `workflow.py` 中改用对应厂商的结构化输出方式。
- SMTP 服务器接受邮件不代表收件端一定不会将其归入垃圾邮件。
