from __future__ import annotations

import json
import os
import uuid
from typing import Any

import streamlit as st
from dotenv import load_dotenv

from jd_agent.emailer import send_jd_email
from jd_agent.exporter import markdown_jd_to_docx
from jd_agent.schemas import ContentIssue, JDContent, JobInput, OptimizationDecision
from jd_agent.storage import init_db, log_event, recent_events
from jd_agent.workflow import (
    FIELD_QUESTIONS,
    PLATFORM_OPTIONS,
    assess_risks,
    content_hash,
    diagnose_content_quality,
    detect_intake_conflicts,
    extract_job_input,
    find_salary_update_candidate,
    generate_jd,
    inspect_completeness,
    inspect_field_relevance,
    platform_description,
    prioritise_follow_up_questions,
    render_jd,
    suggest_job_goal,
    synchronise_confirmed_salary,
)


load_dotenv()
init_db()

INPUT_TAB = "① 需求澄清"
RESULT_TAB = "② 生成、复核与发布"
LOG_TAB = "③ 当前案例审计"
WORK_MODES = ["待确认", "现场办公", "混合办公", "远程办公"]

EXAMPLE_REQUIREMENT = """我们想招一名高级 AI 产品经理，在上海现场办公，属于企业智能产品部。
这个岗位要负责企业 AI Agent 产品从需求分析到上线评估的完整链路，包括规划企业知识库和 Agent 工作流，协调算法、研发和业务团队推动产品落地，建立效果评估与持续优化机制。
候选人需要3年以上 B 端产品经验，本科及以上，具备需求分析、项目推进和与技术团队定义产品指标的能力。有 RAG、模型评估或 Agent 产品上线经验加分。
薪资范围是30K–45K·14薪。岗位亮点是参与核心 AI 产品从0到1建设。"""

FIELD_STATE_KEYS = {
    "job_title": "field_job_title",
    "department": "field_department",
    "location": "field_location",
    "work_mode": "field_work_mode",
    "seniority": "field_seniority",
    "experience": "field_experience",
    "education": "field_education",
    "salary": "field_salary",
    "job_goal": "field_job_goal",
    "responsibilities": "field_responsibilities",
    "required_skills": "field_required_skills",
    "preferred_skills": "field_preferred_skills",
    "selling_points": "field_selling_points",
    "platform": "field_platform",
}

st.set_page_config(page_title="招聘协作 Agent", page_icon=":material/person_search:", layout="wide")

DEFAULTS: dict[str, Any] = {
    "run_id": str(uuid.uuid4()),
    "workflow_tab": INPUT_TAB,
    "next_tab": "",
    "input_mode": "Agent 智能整理",
    "intake_raw": "",
    "intake_supplement": "",
    "intake_done": False,
    "intake_mode": "",
    "intake_conflicts": [],
    "intake_conflicts_confirmed": False,
    "suggested_job_goal": "",
    "jd_text": "",
    "jd_draft": "",
    "result_view": "预览",
    "previous_result_view": "预览",
    "next_result_view": "",
    "approved_hash": "",
    "generator_mode": "",
    "generated_job_title": "",
    "generated_platform": "",
    "result_platform": "BOSS直聘",
    "generated_job": {},
    "generated_content": {},
    "optimization_decisions": {},
    "quality_flash": "",
    "reviewer": "",
    "approval_confirmed": False,
    "recipient": "",
    "flash_message": "",
    "field_job_title": "",
    "field_department": "",
    "field_location": "",
    "field_work_mode": "待确认",
    "field_seniority": "",
    "field_experience": "",
    "field_education": "",
    "field_salary": "",
    "field_job_goal": "",
    "field_responsibilities": "",
    "field_required_skills": "",
    "field_preferred_skills": "",
    "field_selling_points": "",
    "field_platform": "BOSS直聘",
}
for key, value in DEFAULTS.items():
    st.session_state.setdefault(key, value)


def current_job() -> JobInput:
    work_mode = st.session_state.field_work_mode
    return JobInput(
        job_title=st.session_state.field_job_title,
        department=st.session_state.field_department,
        location=st.session_state.field_location,
        work_mode="" if work_mode == "待确认" else work_mode,
        seniority=st.session_state.field_seniority,
        experience=st.session_state.field_experience,
        education=st.session_state.field_education,
        salary=st.session_state.field_salary,
        job_goal=st.session_state.field_job_goal,
        responsibilities=st.session_state.field_responsibilities,
        required_skills=st.session_state.field_required_skills,
        preferred_skills=st.session_state.field_preferred_skills,
        selling_points=st.session_state.field_selling_points,
        platform=st.session_state.field_platform,
    )


def populate_job_fields(job: JobInput) -> None:
    for field, state_key in FIELD_STATE_KEYS.items():
        value = getattr(job, field)
        if field == "work_mode":
            value = value if value in WORK_MODES[1:] else "待确认"
        elif field == "platform":
            value = value if value in PLATFORM_OPTIONS else "BOSS直聘"
        st.session_state[state_key] = value


def load_example() -> None:
    st.session_state.intake_raw = EXAMPLE_REQUIREMENT


def reset_case() -> None:
    for key in list(st.session_state):
        if key.startswith("quality_supplement_"):
            del st.session_state[key]
    for key, value in DEFAULTS.items():
        st.session_state[key] = str(uuid.uuid4()) if key == "run_id" else value.copy() if isinstance(value, (dict, list)) else value


def reset_approval() -> None:
    st.session_state.approved_hash = ""


def confirm_salary_source_update(candidate: str) -> None:
    """将最终 JD 中的具体薪资显式回写为 HR 已确认的岗位事实。"""
    if not candidate or not st.session_state.generated_job:
        st.session_state.flash_message = "没有可确认的薪资变更。"
        return

    source_job = JobInput.model_validate(st.session_state.generated_job)
    old_salary = source_job.salary or "面议"
    updated_job = source_job.model_copy(update={"salary": candidate})
    st.session_state.generated_job = updated_job.model_dump()
    st.session_state.field_salary = candidate

    if st.session_state.generated_content:
        content = JDContent.model_validate(st.session_state.generated_content)
        st.session_state.generated_content = content.model_copy(
            update={"salary_and_benefits": candidate}
        ).model_dump()

    # 保留用户对其他段落的手工修改；只同步旧薪资占位值。
    updated_text = synchronise_confirmed_salary(
        st.session_state.jd_text,
        old_salary,
        candidate,
    )
    st.session_state.jd_text = updated_text
    st.session_state.jd_draft = updated_text
    st.session_state.approved_hash = ""
    st.session_state.approval_confirmed = False
    st.session_state.next_tab = RESULT_TAB
    st.session_state.next_result_view = "预览"
    log_event(
        "salary_fact_confirmed",
        st.session_state.run_id,
        {
            "old_salary": old_salary,
            "new_salary": candidate,
            "old_salary_hash": content_hash(old_salary),
            "new_salary_hash": content_hash(candidate),
            "content_hash": content_hash(updated_text),
        },
    )
    st.session_state.flash_message = (
        f"已将薪资从‘{old_salary}’更新为‘{candidate}’。"
        "请重新检查风险并确认当前版本。"
    )


def on_result_view_change() -> None:
    if (
        st.session_state.result_view == "编辑"
        and st.session_state.previous_result_view != "编辑"
    ):
        st.session_state.jd_draft = st.session_state.jd_text
    st.session_state.previous_result_view = st.session_state.result_view


def confirm_suggested_job_goal() -> None:
    suggestion = st.session_state.suggested_job_goal.strip()
    if suggestion:
        st.session_state.field_job_goal = suggestion
        st.session_state.suggested_job_goal = ""


def record_optimization_decision(issue_data: dict[str, Any], decision: str) -> None:
    issue = ContentIssue.model_validate(issue_data)
    revised_text = issue.original_text
    source_excerpt = issue.original_text

    if decision == "accepted":
        if issue.safe_rewrite:
            revised_text = issue.safe_rewrite
        else:
            supplement_key = f"quality_supplement_{issue.issue_id}"
            revised_text = str(st.session_state.get(supplement_key, "")).strip()
            source_excerpt = revised_text
            if not revised_text:
                st.session_state.quality_flash = "请先填写 HR 已确认的补充事实，再保存。"
                return

        field_state_key = FIELD_STATE_KEYS.get(issue.field)
        if not field_state_key:
            st.session_state.quality_flash = "该问题暂不支持自动写回，请保留原文后手动修改。"
            return
        current_text = str(st.session_state.get(field_state_key, ""))
        if issue.original_text not in current_text:
            st.session_state.quality_flash = "原文已经变化，请重新查看最新诊断。"
            return
        st.session_state[field_state_key] = current_text.replace(issue.original_text, revised_text, 1)

    optimization_decision = OptimizationDecision(
        issue_id=issue.issue_id,
        original_text=issue.original_text,
        revised_text=revised_text,
        decision="accepted" if decision == "accepted" else "rejected",
        source_excerpt=source_excerpt,
    )
    decisions = dict(st.session_state.optimization_decisions)
    decisions[issue.issue_id] = optimization_decision.model_dump()
    st.session_state.optimization_decisions = decisions
    st.session_state.quality_flash = (
        "已采纳并写回结构化字段。" if decision == "accepted" else "已记录：保留原文。"
    )
    log_event(
        "content_optimization_decided",
        st.session_state.run_id,
        {
            "issue_id": issue.issue_id,
            "field": issue.field,
            "issue_type": issue.issue_type,
            "decision": optimization_decision.decision,
            "original_hash": content_hash(issue.original_text),
            "revised_hash": content_hash(revised_text),
        },
    )


def mask_email(value: str) -> str:
    if "@" not in value:
        return "已脱敏"
    name, domain = value.split("@", 1)
    return (name[:2] + "***@" + domain) if name else "***@" + domain


def redact_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    safe = dict(metadata)
    if safe.get("recipient"):
        safe["recipient"] = mask_email(str(safe["recipient"]))
    if safe.get("reviewer"):
        reviewer = str(safe["reviewer"])
        safe["reviewer"] = reviewer[:1] + "**"
    if "message_id" in safe:
        safe["message_id"] = "已记录（界面不展示）"
    if "error" in safe:
        safe["error"] = "操作失败（详情仅保留在服务器日志）"
    return safe


if st.session_state.next_tab:
    st.session_state.workflow_tab = st.session_state.next_tab
    st.session_state.next_tab = ""

if st.session_state.next_result_view:
    st.session_state.result_view = st.session_state.next_result_view
    st.session_state.previous_result_view = st.session_state.next_result_view
    st.session_state.next_result_view = ""

st.title("招聘协作 Agent")
st.caption("原始需求 → 自动抽取 → 主动澄清 → 渠道化 JD → 风险门禁 → 人工发布")

if os.getenv("LLM_API_KEY", "").strip():
    st.badge(
        f"LLM 配置已载入 · {os.getenv('LLM_MODEL', 'gpt-5-mini')}",
        icon=":material/key:",
        color="blue",
    )
    st.caption("配置已载入不等于鉴权成功；实际调用结果会在需求整理和 JD 生成阶段明确显示。")
else:
    st.warning("未配置 LLM_API_KEY：需求抽取和 JD 生成将使用明确标记的离线演示模式。", icon=":material/warning:")

with st.sidebar:
    st.header("当前案例")
    st.write(f"运行 ID：`{st.session_state.run_id[:8]}`")
    if not st.session_state.intake_done and not st.session_state.jd_text:
        st.info("待整理需求", icon=":material/pending:")
    elif not st.session_state.jd_text:
        st.info("需求澄清中", icon=":material/forum:")
    elif st.session_state.approved_hash == content_hash(st.session_state.jd_text):
        st.success("已审批，可发布", icon=":material/check_circle:")
    else:
        st.warning("已生成，待复核", icon=":material/rate_review:")
    st.button("开始新案例", icon=":material/restart_alt:", width="stretch", on_click=reset_case)

input_tab, result_tab, log_tab = st.tabs(
    [INPUT_TAB, RESULT_TAB, LOG_TAB],
    key="workflow_tab",
    on_change="rerun",
)

if input_tab.open:
    with input_tab:
        st.subheader("先把原始材料交给 Agent")
        st.write("粘贴业务方的招聘需求、旧 JD、会议笔记或聊天记录。Agent 会先整理事实，再只追问当前最关键的信息。")

        st.segmented_control(
            "输入方式",
            ["Agent 智能整理", "直接校对字段"],
            key="input_mode",
            required=True,
            width="stretch",
        )

        if st.session_state.input_mode == "Agent 智能整理":
            with st.container(border=True):
                st.text_area(
                    "原始招聘材料",
                    key="intake_raw",
                    height=220,
                    placeholder="例如：我们要招一名上海的高级 AI 产品经理……\n也可以直接粘贴旧 JD 或与业务负责人的聊天记录。",
                )
                if st.session_state.intake_done:
                    st.text_area(
                        "补充回答或更正",
                        key="intake_supplement",
                        height=100,
                        placeholder="例如：最终工作地是上海；薪资以30K–45K·14薪为准。",
                    )
                with st.container(horizontal=True):
                    st.button("载入示例", icon=":material/lightbulb:", on_click=load_example)
                    parse_clicked = st.button(
                        "重新整理补充内容" if st.session_state.intake_done else "让 Agent 整理需求",
                        icon=":material/auto_awesome:",
                        type="primary",
                    )

            if parse_clicked:
                source = st.session_state.intake_raw.strip()
                if st.session_state.intake_supplement.strip():
                    source += "\n\n补充回答：\n" + st.session_state.intake_supplement.strip()
                existing = current_job() if st.session_state.intake_done else None
                try:
                    with st.skeleton(height=180):
                        extracted_job, extraction_mode = extract_job_input(source, existing)
                    populate_job_fields(extracted_job)
                    st.session_state.intake_done = True
                    st.session_state.intake_mode = extraction_mode
                    st.session_state.intake_conflicts = detect_intake_conflicts(source)
                    st.session_state.intake_conflicts_confirmed = False
                    st.session_state.optimization_decisions = {}
                    st.session_state.suggested_job_goal = (
                        suggest_job_goal(source) if not extracted_job.job_goal.strip() else ""
                    )
                    filled_count = sum(
                        bool(str(value).strip())
                        for key, value in extracted_job.model_dump().items()
                        if key not in {"platform", "work_mode"}
                    )
                    log_event(
                        "intake_extracted",
                        st.session_state.run_id,
                        {"mode": extraction_mode, "filled_fields": filled_count, "conflicts": len(st.session_state.intake_conflicts)},
                    )
                    st.rerun()
                except ValueError as exc:
                    st.error(str(exc), icon=":material/error:")

        job = current_job()
        missing_required, missing_recommended, _ = inspect_completeness(job)
        field_issues = inspect_field_relevance(job)
        follow_up_questions = prioritise_follow_up_questions(job, limit=4)
        if st.session_state.suggested_job_goal:
            follow_up_questions = [
                question for question in follow_up_questions if question != FIELD_QUESTIONS["job_goal"]
            ]
        conflicts = st.session_state.intake_conflicts
        filled_fields = sum(
            bool(str(value).strip())
            for key, value in job.model_dump().items()
            if key not in {"platform", "work_mode"}
        )

        if st.session_state.intake_done:
            with st.chat_message("assistant", avatar=":material/person_search:"):
                if st.session_state.intake_mode.startswith("llm:"):
                    mode_label = "真实 AI 抽取"
                elif st.session_state.intake_mode.startswith("fallback:"):
                    mode_label = "AI 失败后的离线规则抽取"
                else:
                    mode_label = "离线规则抽取"
                st.write(f"我已通过 **{mode_label}** 整理出 **{filled_fields}** 个原文明确提供的岗位字段。")
                if st.session_state.intake_mode.startswith("fallback:"):
                    reason = st.session_state.intake_mode.removeprefix("fallback:")
                    st.warning(f"模型调用未成功：{reason}当前结果由离线规则产生，不是真实 AI 抽取。")
                if conflicts:
                    st.warning("发现需要人工确认的冲突：\n\n" + "\n".join(f"- {item}" for item in conflicts))
                if st.session_state.suggested_job_goal:
                    st.info(f"我找到一个可能的岗位目标：{st.session_state.suggested_job_goal}")
                    st.button(
                        "确认作为岗位目标",
                        icon=":material/check:",
                        on_click=confirm_suggested_job_goal,
                    )
                if follow_up_questions:
                    st.write("下一步只需要回答这些关键问题：")
                    for index, question in enumerate(follow_up_questions, 1):
                        st.write(f"{index}. {question}")
                elif not conflicts and not field_issues:
                    st.success("关键信息已足够生成 JD。", icon=":material/check_circle:")
                st.caption("你可以在上方补充回答后重新整理，也可在下方直接修正抽取结果。")

        show_fields = st.session_state.input_mode == "直接校对字段" or st.session_state.intake_done
        with st.expander(
            "已抽取的结构化信息" if st.session_state.intake_done else "结构化岗位信息",
            expanded=show_fields,
            icon=":material/edit_note:",
        ):
            left, right = st.columns(2)
            with left:
                st.text_input("岗位名称 *", key="field_job_title", help="只填具体职位，例如 AI 产品经理。")
                st.text_input("所属部门", key="field_department")
                st.text_input("工作地点 *", key="field_location", help="只填城市、国家或具体区域。")
                st.selectbox("工作方式", WORK_MODES, key="field_work_mode")
                st.text_input("职级", key="field_seniority")
                st.caption("所属部门和职级均为可选项；原始材料未提供时可以留空。")
                st.text_input("经验要求", key="field_experience")
                st.text_input("学历要求", key="field_education")
                st.text_input("薪资范围", key="field_salary")
            with right:
                st.text_area("岗位目标 *", key="field_job_goal", height=100)
                st.text_area("主要职责 *", key="field_responsibilities", height=130)
                st.text_area("必备能力 *", key="field_required_skills", height=130)
                st.text_area("加分能力", key="field_preferred_skills", height=90)
                st.text_area("岗位亮点", key="field_selling_points", height=90)
                st.selectbox("招聘平台", PLATFORM_OPTIONS, key="field_platform")
                st.caption(platform_description(st.session_state.field_platform))

        if show_fields:
            job = current_job()
            missing_required, missing_recommended, _ = inspect_completeness(job)
            field_issues = inspect_field_relevance(job)

            content_issues = diagnose_content_quality(job)
            decisions = st.session_state.optimization_decisions
            unresolved_issues = [issue for issue in content_issues if issue.issue_id not in decisions]

            st.subheader("内容质量诊断与优化")
            st.caption("系统先判断内容是否具体、可验证和位置正确；需要新增事实时只追问，不会自行补写。")
            if st.session_state.quality_flash:
                st.info(st.session_state.quality_flash)
                st.session_state.quality_flash = ""

            vague_count = sum(issue.issue_type == "表述空泛" for issue in content_issues)
            unverifiable_count = sum(
                issue.issue_type in {"不可验证", "成果标准不明确"}
                for issue in content_issues
            )
            mixed_count = sum(issue.issue_type == "职责与要求混淆" for issue in content_issues)
            output_count = sum(issue.issue_type == "缺少预期产出" for issue in content_issues)
            quality_columns = st.columns(4)
            quality_columns[0].metric("空泛要求", vague_count)
            quality_columns[1].metric("不可验证要求", unverifiable_count)
            quality_columns[2].metric("职责要求混淆", mixed_count)
            quality_columns[3].metric("缺少成果描述", output_count)

            if content_issues:
                st.write(f"发现 **{len(content_issues)}** 个诊断项，其中 **{len(unresolved_issues)}** 个尚未处理。")
                decision_models = [
                    OptimizationDecision.model_validate(value)
                    for value in decisions.values()
                ]
                accepted_count = sum(decision.decision == "accepted" for decision in decision_models)
                rejected_count = sum(decision.decision == "rejected" for decision in decision_models)
                if decision_models:
                    st.caption(
                        f"本案例已记录 {len(decision_models)} 项决定：采纳 {accepted_count} 项，保留原文 {rejected_count} 项。"
                    )
                field_labels = {
                    "responsibilities": "岗位职责",
                    "required_skills": "必备能力",
                    "preferred_skills": "加分能力",
                    "selling_points": "岗位亮点",
                }
                for issue in content_issues:
                    decision_data = decisions.get(issue.issue_id)
                    with st.container(border=True):
                        st.write(
                            f"**{field_labels.get(issue.field, issue.field)} · {issue.issue_type}**"
                        )
                        st.caption(f"严重程度：{issue.severity}")
                        st.write("**原文**")
                        st.code(issue.original_text, language=None)
                        st.write(f"**问题**：{issue.reason}")
                        if issue.follow_up_question:
                            st.write(f"**建议追问**：{issue.follow_up_question}")
                        if issue.safe_rewrite:
                            st.write("**安全改写**")
                            st.code(issue.safe_rewrite, language=None)
                            st.caption("依据：仅重新组织原始表达，没有增加新的岗位事实。")

                        if decision_data:
                            decision = OptimizationDecision.model_validate(decision_data)
                            if decision.decision == "accepted":
                                st.success("已采纳并写回结构化字段。", icon=":material/check_circle:")
                            else:
                                st.info("已决定保留原文。", icon=":material/keep:")
                        else:
                            if not issue.safe_rewrite:
                                st.text_area(
                                    "补充 HR 已确认的事实",
                                    key=f"quality_supplement_{issue.issue_id}",
                                    placeholder="只填写业务方或 HR 已确认的信息；保存后将替换上方原文。",
                                    height=90,
                                    persist_state="session",
                                )
                            with st.container(horizontal=True):
                                st.button(
                                    "采纳安全改写" if issue.safe_rewrite else "使用补充内容",
                                    key=f"quality_accept_{issue.issue_id}",
                                    icon=":material/check:",
                                    type="primary",
                                    on_click=record_optimization_decision,
                                    args=(issue.model_dump(), "accepted"),
                                )
                                st.button(
                                    "保留原文",
                                    key=f"quality_reject_{issue.issue_id}",
                                    on_click=record_optimization_decision,
                                    args=(issue.model_dump(), "rejected"),
                                )
            else:
                st.success("未发现明确的空泛、不可验证或职责混淆问题。", icon=":material/check_circle:")
            st.caption("生成后的无来源新增内容由结果页真实性检查继续核验。优化决定在审计日志中按内容哈希记录。")

            with st.container(border=True):
                st.subheader("生成前确认")
                if missing_required:
                    st.error("必填信息缺失：" + "、".join(missing_required))
                if field_issues:
                    for issue in field_issues:
                        st.error(f"{issue.label}：{issue.message}")
                if missing_recommended:
                    st.caption("建议继续补充：" + "、".join(missing_recommended))
                if conflicts:
                    st.checkbox("我已人工核对上述冲突，并已在结构化字段中保留最终版本。", key="intake_conflicts_confirmed")
                if not missing_required and not field_issues and (not conflicts or st.session_state.intake_conflicts_confirmed):
                    st.success("岗位事实已可用，可以进入生成。", icon=":material/check_circle:")

                generation_blocked = bool(
                    missing_required
                    or field_issues
                    or (conflicts and not st.session_state.intake_conflicts_confirmed)
                )
                if st.button(
                    f"生成 {job.platform} JD",
                    icon=":material/arrow_forward:",
                    type="primary",
                    disabled=generation_blocked,
                    width="stretch",
                ):
                    try:
                        with st.skeleton(height=220):
                            jd, mode = generate_jd(job)
                        rendered_jd = render_jd(job, jd)
                        st.session_state.jd_text = rendered_jd
                        st.session_state.jd_draft = rendered_jd
                        st.session_state.generator_mode = mode
                        st.session_state.generated_job_title = job.job_title
                        st.session_state.generated_platform = job.platform
                        st.session_state.result_platform = job.platform
                        st.session_state.generated_job = job.model_dump()
                        st.session_state.generated_content = jd.model_dump()
                        st.session_state.approved_hash = ""
                        st.session_state.result_view = "预览"
                        st.session_state.previous_result_view = "预览"
                        optimization_summary = {
                            "optimization_decision_count": len(st.session_state.optimization_decisions),
                            "optimization_accepted_count": sum(
                                value.get("decision") == "accepted"
                                for value in st.session_state.optimization_decisions.values()
                            ),
                            "optimization_rejected_count": sum(
                                value.get("decision") == "rejected"
                                for value in st.session_state.optimization_decisions.values()
                            ),
                        }
                        if mode.startswith("fallback:"):
                            reason = mode.removeprefix("fallback:")
                            log_event(
                                "llm_fallback",
                                st.session_state.run_id,
                                {
                                    "reason": reason,
                                    "job_title": job.job_title,
                                    "platform": job.platform,
                                    **optimization_summary,
                                },
                            )
                            st.session_state.flash_message = f"AI 生成失败：{reason}当前为明确标记的离线回退结果。"
                        else:
                            log_event(
                                "jd_generated",
                                st.session_state.run_id,
                                {
                                    "mode": mode,
                                    "job_title": job.job_title,
                                    "platform": job.platform,
                                    **optimization_summary,
                                },
                            )
                            st.session_state.flash_message = "JD 已生成，请预览、检查风险并完成审批。"
                        st.session_state.next_tab = RESULT_TAB
                        st.rerun()
                    except Exception as exc:
                        log_event("generation_failed", st.session_state.run_id, {"error": str(exc)})
                        st.error("生成流程发生未预期错误。错误已写入当前案例日志。")
        else:
            st.caption("先让 Agent 整理原始材料；结构化结果和生成操作会在完成抽取后出现。")

if result_tab.open:
    with result_tab:
        if not st.session_state.jd_text:
            st.info("请先在‘需求澄清’中生成 JD。", icon=":material/info:")
        else:
            if st.session_state.flash_message:
                st.success(st.session_state.flash_message, icon=":material/check_circle:")
                st.session_state.flash_message = ""

            if st.session_state.generator_mode.startswith("llm:"):
                mode_label = "真实 AI"
            elif st.session_state.generator_mode.startswith("fallback:"):
                mode_label = "AI 失败后的离线回退"
            else:
                mode_label = "离线演示生成器"
            result_info_col, platform_col = st.columns([2, 1], vertical_alignment="bottom")
            with result_info_col:
                st.caption(f"生成方式：{mode_label} · 当前版本：{st.session_state.generated_platform}")
                if st.session_state.generator_mode.startswith("fallback:"):
                    reason = st.session_state.generator_mode.removeprefix("fallback:")
                    st.warning(f"模型调用未成功：{reason}当前 JD 是离线回退结果，请勿将其当作真实 AI 生成结果。")
                st.write("选择目标发布平台，可从同一份已生成内容快速生成对应版本。")
            with platform_col:
                with st.container(border=True):
                    st.selectbox(
                        "发布平台",
                        PLATFORM_OPTIONS,
                        key="result_platform",
                        help="平台切换只调整发布文案，不会修改已确认的岗位事实。",
                        persist_state="session",
                    )
                    st.caption(platform_description(st.session_state.result_platform))
                    platform_generate_clicked = st.button(
                        f"生成{st.session_state.result_platform}发布文案",
                        icon=":material/campaign:",
                        type="primary",
                        width="stretch",
                    )

            if platform_generate_clicked:
                target_platform = st.session_state.result_platform
                if target_platform not in PLATFORM_OPTIONS:
                    st.error("发布平台无效，请重新选择。")
                else:
                    source_job = JobInput.model_validate(st.session_state.generated_job)
                    previous_platform = st.session_state.generated_platform
                    platform_job = source_job.model_copy(update={"platform": target_platform})
                    try:
                        if not st.session_state.generated_content:
                            raise ValueError("当前会话缺少 canonical JD 内容，请返回第一步重新生成。")
                        platform_jd = JDContent.model_validate(st.session_state.generated_content)
                        platform_text = render_jd(platform_job, platform_jd)
                        st.session_state.jd_text = platform_text
                        st.session_state.jd_draft = platform_text
                        st.session_state.generated_platform = target_platform
                        st.session_state.generated_job = platform_job.model_dump()
                        st.session_state.approved_hash = ""
                        st.session_state.approval_confirmed = False
                        st.session_state.next_result_view = "预览"
                        log_event(
                            "platform_version_generated",
                            st.session_state.run_id,
                            {
                                "from_platform": previous_platform,
                                "to_platform": target_platform,
                                "mode": "deterministic_render",
                                "content_hash": content_hash(platform_text),
                            },
                        )
                        st.session_state.flash_message = (
                            f"已生成{target_platform}发布文案。请重新检查风险并确认当前版本。"
                        )
                        st.rerun()
                    except Exception as exc:
                        log_event(
                            "platform_generation_failed",
                            st.session_state.run_id,
                            {"platform": target_platform, "error": str(exc)},
                        )
                        st.error("平台文案生成失败。错误已写入当前案例日志。")

            st.segmented_control(
                "结果视图",
                ["预览", "编辑"],
                key="result_view",
                required=True,
                on_change=on_result_view_change,
            )
            if st.session_state.result_view == "预览":
                with st.container(border=True):
                    st.markdown(st.session_state.jd_text)
            else:
                with st.form("jd_edit_form"):
                    st.text_area(
                        "编辑最终 JD",
                        key="jd_draft",
                        height=420,
                        help="只有点击保存后才会替换正式版本；保存后原审批立即失效。",
                    )
                    with st.container(horizontal=True):
                        save_edit = st.form_submit_button(
                            "保存修改",
                            type="primary",
                            icon=":material/save:",
                        )
                        cancel_edit = st.form_submit_button("取消")

                if save_edit:
                    draft = st.session_state.jd_draft.strip()
                    if not draft:
                        st.error("JD 不能为空。")
                    else:
                        old_hash = content_hash(st.session_state.jd_text)
                        st.session_state.jd_text = draft
                        reset_approval()
                        st.session_state.next_result_view = "预览"
                        log_event(
                            "jd_edited",
                            st.session_state.run_id,
                            {
                                "old_content_hash": old_hash,
                                "new_content_hash": content_hash(draft),
                            },
                        )
                        st.rerun()

                if cancel_edit:
                    # 下次进入编辑时，on_result_view_change 会从正式版本重建草稿。
                    st.session_state.next_result_view = "预览"
                    st.rerun()

            risk_job = JobInput.model_validate(st.session_state.generated_job)
            salary_update_candidate = find_salary_update_candidate(
                risk_job,
                st.session_state.jd_text,
            )
            if salary_update_candidate:
                with st.container(border=True):
                    st.warning(
                        "检测到你在最终 JD 中修改了具体薪资。"
                        "这是岗位事实变更，需要 HR 明确确认，不能只作为文案编辑。",
                        icon=":material/currency_yen:",
                    )
                    st.write(f"**原已确认薪资：** {risk_job.salary or '面议'}")
                    st.write(f"**JD 中的新薪资：** {salary_update_candidate}")
                    st.button(
                        f"确认更新薪资为 {salary_update_candidate}",
                        type="primary",
                        icon=":material/published_with_changes:",
                        on_click=confirm_salary_source_update,
                        args=(salary_update_candidate,),
                    )
                    st.caption(
                        "确认后会同步更新结构化岗位信息、当前 JD 和后续平台版本，"
                        "并让原审批失效。"
                    )
            assessment = assess_risks(risk_job, st.session_state.jd_text)
            st.subheader("风险门禁")
            with st.container(border=True):
                blocking_count = sum(issue.level == "high" for issue in assessment.issues)
                review_count = sum(issue.level == "medium" for issue in assessment.issues)
                quality_count = sum(issue.level == "low" for issue in assessment.issues)
                if blocking_count:
                    st.write("**规则检测结果：存在发布阻断项**")
                elif review_count:
                    st.write("**规则检测结果：存在人工核实项**")
                else:
                    st.write("**规则检测结果：未命中已知中高风险规则**")
                blocking_col, review_col, quality_col = st.columns(3)
                blocking_col.metric("发布阻断项", blocking_count)
                review_col.metric("人工核实项", review_count)
                quality_col.metric("文本优化项", quality_count)
                if assessment.overall_level == "高":
                    st.error(
                        "当前版本包含发布阻断项。请修改 JD 并重新检查，"
                        "高风险内容不能审批、下载或发送。"
                    )
                elif assessment.overall_level == "中":
                    st.warning("存在需要核实或优化的内容。")
                else:
                    st.info("未命中已知规则不等于没有风险；发布前仍需人工核对语义风险和事实依据。")
                for index, issue in enumerate(assessment.issues, 1):
                    severity = {"high": "🔴", "medium": "🟠", "low": "🟡"}.get(issue.level, "⚪")
                    st.write(f"{severity} **{index}. {issue.category} · {issue.text}**")
                    st.write(f"原因：{issue.reason}")
                    st.caption("建议：" + issue.suggestion)
                if not assessment.issues:
                    st.write("当前确定性规则未检出问题。")

            st.subheader("人工审批")
            st.text_input("主审批人姓名", key="reviewer")
            st.checkbox("我已核对原始需求、JD 事实、风险提示和后续收件人。", key="approval_confirmed")

            high_risk = assessment.overall_level == "高"
            if high_risk:
                st.error(
                    "当前版本包含发布阻断项。请修改 JD 并重新检查，"
                    "高风险内容不能审批、下载或发送。"
                )

            approval_ready = bool(
                st.session_state.reviewer.strip()
                and st.session_state.approval_confirmed
                and not high_risk
            )
            if st.button("确认当前版本", icon=":material/verified:", disabled=not approval_ready):
                st.session_state.approved_hash = content_hash(st.session_state.jd_text)
                metadata: dict[str, Any] = {
                    "reviewer": st.session_state.reviewer.strip(),
                    "content_hash": st.session_state.approved_hash,
                    "risk_level": assessment.overall_level,
                    "risk_issue_count": len(assessment.issues),
                }
                log_event("jd_approved", st.session_state.run_id, metadata)
                st.success("当前内容版本已确认。")

            approved = bool(
                not high_risk
                and st.session_state.approved_hash == content_hash(st.session_state.jd_text)
            )
            if approved:
                st.success("审批有效：可导出或发送。", icon=":material/check_circle:")
                docx_bytes = markdown_jd_to_docx(st.session_state.jd_text)
                st.download_button(
                    "下载最终 Word",
                    data=docx_bytes,
                    file_name=f"{st.session_state.generated_job_title or '招聘岗位'}_{st.session_state.generated_platform or '通用'}_JD.docx",
                    mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    icon=":material/download:",
                    on_click=lambda: log_event(
                        "word_downloaded",
                        st.session_state.run_id,
                        {"job_title": st.session_state.generated_job_title, "platform": st.session_state.generated_platform},
                    ),
                )
                st.text_input("收件人（必须在 ALLOWED_RECIPIENTS 白名单中）", key="recipient")
                if st.button("发送真实邮件", icon=":material/send:", type="primary", disabled=not st.session_state.recipient.strip()):
                    try:
                        message_id = send_jd_email(
                            st.session_state.recipient,
                            st.session_state.generated_job_title or "招聘岗位",
                            st.session_state.jd_text,
                            docx_bytes,
                        )
                        log_event("email_sent", st.session_state.run_id, {"recipient": st.session_state.recipient, "message_id": message_id})
                        st.success(f"邮件服务器已接受邮件。回执：{message_id}")
                    except Exception as exc:
                        log_event("email_failed", st.session_state.run_id, {"recipient": st.session_state.recipient, "error": str(exc)})
                        st.error(f"发送失败：{exc}")
            else:
                st.warning("当前版本未完成有效审批，Word 下载和邮件发送均被禁用。")

if log_tab.open:
    with log_tab:
        st.subheader("当前案例审计日志")
        st.caption(f"只展示当前运行 ID `{st.session_state.run_id[:8]}` 的记录；审批人、收件人和邮件回执在界面中脱敏。")
        events = recent_events(st.session_state.run_id)
        if not events:
            st.info("当前案例尚无日志。")
        for event in events:
            with st.expander(f"{event['created_at']} · {event['event']}"):
                metadata = json.loads(event["metadata"])
                st.json(redact_metadata(metadata))
