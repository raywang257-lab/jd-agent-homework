from __future__ import annotations

import json
import os
import uuid

import streamlit as st
from dotenv import load_dotenv

from jd_agent.emailer import send_jd_email
from jd_agent.exporter import markdown_jd_to_docx
from jd_agent.schemas import JobInput
from jd_agent.storage import init_db, log_event, recent_events
from jd_agent.workflow import (
    PLATFORM_OPTIONS,
    assess_risks,
    content_hash,
    generate_jd,
    inspect_completeness,
    inspect_field_relevance,
    platform_description,
    render_jd,
)


load_dotenv()
init_db()

st.set_page_config(page_title="招聘 JD Agent", page_icon="🧠", layout="wide")

DEFAULTS = {
    "run_id": str(uuid.uuid4()),
    "jd_text": "",
    "jd_editor": "",
    "approved_hash": "",
    "generator_mode": "",
    "generated_job_title": "",
    "generated_platform": "",
    "generated_job": {},
}
for key, value in DEFAULTS.items():
    if key not in st.session_state:
        st.session_state[key] = value


def reset_approval() -> None:
    st.session_state.approved_hash = ""


st.title("招聘 JD Agent")
st.caption("岗位信息 → 完整性检查 → JD 生成 → 风险检查 → 人工审批 → Word / 真实邮件")

if os.getenv("LLM_API_KEY", "").strip():
    st.success(f"真实 AI 模式：{os.getenv('LLM_MODEL', 'gpt-5-mini')}")
else:
    st.warning("演示生成器模式：未配置 LLM_API_KEY。当前只用于验证产品流程，不会冒充 AI 结果。")

with st.sidebar:
    st.header("流程状态")
    st.write(f"运行 ID：`{st.session_state.run_id[:8]}`")
    if not st.session_state.jd_text:
        st.info("待输入")
    elif st.session_state.approved_hash == content_hash(st.session_state.jd_text):
        st.success("已人工确认")
    else:
        st.warning("已生成，待人工确认")
    if st.button("开始新案例", width="stretch"):
        st.session_state.run_id = str(uuid.uuid4())
        st.session_state.jd_text = ""
        st.session_state.jd_editor = ""
        st.session_state.approved_hash = ""
        st.session_state.generator_mode = ""
        st.session_state.generated_job_title = ""
        st.session_state.generated_platform = ""
        st.session_state.generated_job = {}
        st.rerun()

input_tab, result_tab, log_tab = st.tabs(["① 岗位输入", "② 生成、审批与发送", "③ 审计日志"])

with input_tab:
    st.subheader("岗位信息")
    left, right = st.columns(2)
    with left:
        job_title = st.text_input("岗位名称 *", value="AI 产品经理", help="只填具体职位，例如 AI 产品经理。")
        department = st.text_input("所属部门", value="企业智能产品部", help="只填部门、中心或业务团队名称。")
        location = st.text_input("工作地点 *", value="上海", help="只填城市、国家或具体区域，例如上海·徐汇区。")
        work_mode = st.selectbox("工作方式", ["现场办公", "混合办公", "远程办公"])
        seniority = st.text_input("职级", value="高级", help="例如初级、高级、资深或 P7。")
        experience = st.text_input("经验要求", value="3年以上", help="只填工作年限或经验类型。")
        education = st.text_input("学历要求", value="本科及以上", help="只填学历或学位，例如本科及以上。")
        salary = st.text_input("薪资范围", value="30K–45K·14薪", help="只填金额和计薪周期，例如 30K–45K·14薪。")
    with right:
        job_goal = st.text_area("岗位目标 *", value="负责企业 AI Agent 产品从需求分析到上线评估的完整链路。")
        responsibilities = st.text_area(
            "主要职责 *",
            value="负责企业知识库和 Agent 工作流规划\n协调算法、研发和业务团队推动产品落地\n建立产品效果评估与持续优化机制",
            height=110,
        )
        required_skills = st.text_area(
            "必备能力 *",
            value="B端产品经验\n需求分析与项目推进能力\n能与技术团队定义可验证的产品指标",
            height=110,
        )
        preferred_skills = st.text_area("加分能力", value="有 RAG、模型评估或 Agent 产品上线经验")
        selling_points = st.text_area("岗位亮点", value="参与核心 AI 产品从 0 到 1 建设")
        platform = st.selectbox("招聘平台", PLATFORM_OPTIONS, help="Agent 会按平台的内容习惯生成和排版 JD。")
        st.caption(platform_description(platform))

    job = JobInput(
        job_title=job_title,
        department=department,
        location=location,
        work_mode=work_mode,
        seniority=seniority,
        experience=experience,
        education=education,
        salary=salary,
        job_goal=job_goal,
        responsibilities=responsibilities,
        required_skills=required_skills,
        preferred_skills=preferred_skills,
        selling_points=selling_points,
        platform=platform,
    )
    missing_required, missing_recommended, questions = inspect_completeness(job)
    field_issues = inspect_field_relevance(job)
    follow_up_questions = [*questions, *[issue.question for issue in field_issues]]
    follow_up_questions = list(dict.fromkeys(follow_up_questions))
    with st.expander("完整性与内容相关性检查", expanded=bool(missing_required or missing_recommended or field_issues)):
        if not missing_required and not missing_recommended and not field_issues:
            st.success("所有岗位信息都已填写，且与对应字段要求匹配。")
        if missing_required:
            st.error("必填信息缺失：" + "、".join(missing_required))
        if missing_recommended:
            st.warning("建议补充：" + "、".join(missing_recommended))
        for issue in field_issues:
            st.error(f"{issue.label}：{issue.message}")
        if follow_up_questions:
            st.write("Agent 追问：")
            for question in follow_up_questions:
                st.write(f"- {question}")

    generation_blocked = bool(missing_required or field_issues)
    if st.button("检查并生成 JD", type="primary", disabled=generation_blocked, width="stretch"):
        try:
            jd, mode = generate_jd(job)
            rendered_jd = render_jd(job, jd)
            st.session_state.jd_text = rendered_jd
            st.session_state.jd_editor = rendered_jd
            st.session_state.generator_mode = mode
            st.session_state.generated_job_title = job.job_title
            st.session_state.generated_platform = job.platform
            st.session_state.generated_job = job.model_dump()
            st.session_state.approved_hash = ""
            if mode.startswith("fallback:"):
                reason = mode.removeprefix("fallback:")
                log_event(
                    "llm_fallback",
                    st.session_state.run_id,
                    {"reason": reason, "job_title": job.job_title, "platform": job.platform},
                )
                st.warning(f"AI 生成未完成：{reason} 已改用明确标注的离线回退结果。")
            else:
                log_event("jd_generated", st.session_state.run_id, {"mode": mode, "job_title": job.job_title, "platform": job.platform})
                st.success("生成完成，请进入第二个标签页审核。")
        except Exception as exc:
            log_event("generation_failed", st.session_state.run_id, {"error": str(exc)})
            st.error("生成流程发生未预期错误。错误已写入审计日志，请检查配置后重试。")

with result_tab:
    if not st.session_state.jd_text:
        st.info("请先在“岗位输入”中生成 JD。")
    else:
        if st.session_state.generator_mode.startswith("llm:"):
            mode_label = "真实 AI"
        elif st.session_state.generator_mode.startswith("fallback:"):
            mode_label = "AI 失败后的离线回退（非 AI 结果）"
        else:
            mode_label = "演示生成器（非 AI）"
        st.caption(f"生成方式：{mode_label} · 目标平台：{st.session_state.generated_platform or '未记录'}")
        edited_text = st.text_area(
            "编辑最终 JD（任何修改都会使原审批失效）",
            height=520,
            key="jd_editor",
        )
        if edited_text != st.session_state.jd_text:
            st.session_state.jd_text = edited_text
            reset_approval()

        risk_job_data = st.session_state.get("generated_job") or job.model_dump()
        risk_job = JobInput.model_validate(risk_job_data)
        assessment = assess_risks(risk_job, st.session_state.jd_text)
        st.subheader("风险预估")
        with st.container(border=True):
            level_col, score_col, count_col = st.columns(3)
            level_col.metric("总体风险等级", assessment.overall_level)
            score_col.metric("风险分", f"{assessment.score}/100")
            count_col.metric("发现问题", len(assessment.issues))
            if assessment.overall_level == "高":
                st.error("存在高风险表述，建议人工修订并再次检查后发布。")
            elif assessment.overall_level == "中":
                st.warning("存在需要核实或优化的内容。")
            else:
                st.success("未发现明确的中高风险，仍应由发布人完成最终核对。")

            for index, issue in enumerate(assessment.issues, 1):
                severity = {"high": "🔴", "medium": "🟠", "low": "🟡"}.get(issue.level, "⚪")
                st.write(f"{severity} **{index}. {issue.category} · {issue.text}**")
                st.write(f"原因：{issue.reason}")
                st.caption("建议：" + issue.suggestion)
            if not assessment.issues:
                st.write("未检出明确问题。")
        st.caption("风险预估只提示证据、原因和建议，不会自动修改 JD 或替代人工审核。")

        st.subheader("人工审批")
        reviewer = st.text_input("确认人姓名")
        confirmed = st.checkbox("我已检查岗位信息、JD 内容、风险提示和后续收件人。")
        if st.button("确认当前版本", disabled=not (reviewer.strip() and confirmed)):
            st.session_state.approved_hash = content_hash(st.session_state.jd_text)
            log_event(
                "jd_approved",
                st.session_state.run_id,
                {
                    "reviewer": reviewer.strip(),
                    "content_hash": st.session_state.approved_hash,
                    "risk_level": assessment.overall_level,
                    "risk_score": assessment.score,
                    "risk_issue_count": len(assessment.issues),
                },
            )
            st.success("当前内容版本已确认。")

        approved = st.session_state.approved_hash == content_hash(st.session_state.jd_text)
        if approved:
            st.success("审批有效：可导出或发送。")
            docx_bytes = markdown_jd_to_docx(st.session_state.jd_text)
            st.download_button(
                "下载最终 Word",
                data=docx_bytes,
                file_name=f"{st.session_state.generated_job_title or '招聘岗位'}_{st.session_state.generated_platform or '通用'}_JD.docx",
                mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                on_click=lambda: log_event(
                    "word_downloaded",
                    st.session_state.run_id,
                    {"job_title": st.session_state.generated_job_title, "platform": st.session_state.generated_platform},
                ),
            )
            recipient = st.text_input("收件人（必须在 ALLOWED_RECIPIENTS 白名单中）")
            if st.button("发送真实邮件", type="primary", disabled=not recipient.strip()):
                try:
                    message_id = send_jd_email(
                        recipient,
                        st.session_state.generated_job_title or "招聘岗位",
                        st.session_state.jd_text,
                        docx_bytes,
                    )
                    log_event("email_sent", st.session_state.run_id, {"recipient": recipient, "message_id": message_id})
                    st.success(f"邮件服务器已接受邮件。回执：{message_id}")
                except Exception as exc:
                    log_event("email_failed", st.session_state.run_id, {"recipient": recipient, "error": str(exc)})
                    st.error(f"发送失败：{exc}")
        else:
            st.warning("当前版本尚未确认，Word 下载和邮件发送均被禁用。")

with log_tab:
    st.subheader("最近运行日志")
    events = recent_events()
    if not events:
        st.info("尚无日志。")
    for event in events:
        with st.expander(f"{event['created_at']} · {event['event']} · {event['run_id'][:8]}"):
            st.json(json.loads(event["metadata"]))
