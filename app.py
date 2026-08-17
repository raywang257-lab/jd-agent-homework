from __future__ import annotations

import json
import os
import uuid
from typing import Any

import streamlit as st
from dotenv import load_dotenv

from jd_agent.emailer import send_jd_email
from jd_agent.exporter import jd_to_markdown, jd_to_pdf, jd_to_plain_text, markdown_jd_to_docx
from jd_agent.schemas import ContentIssue, JDContent, JobInput, OptimizationDecision
from jd_agent.smart_features import (
    batch_split_requirements,
    get_completion_stats,
    quick_quality_check,
    render_keyword_cloud,
    render_progress_bar,
    render_quality_gauge,
)
from jd_agent.storage import (
    all_cases,
    case_events,
    case_stats,
    delete_template,
    init_db,
    load_templates,
    log_event,
    recent_events,
    save_template,
)
from jd_agent.workflow import (
    FIELD_QUESTIONS,
    PLATFORM_OPTIONS,
    analyze_skill_gaps,
    assess_risks,
    calculate_quality_score,
    compare_requirement_vs_jd,
    content_hash,
    diagnose_content_quality,
    detect_intake_conflicts,
    extract_job_input,
    extract_keywords,
    find_salary_update_candidate,
    generate_jd,
    generate_smart_tips,
    inspect_completeness,
    inspect_field_relevance,
    platform_description,
    prioritise_follow_up_questions,
    render_jd,
    salary_benchmark,
    suggest_field_values,
    suggest_job_goal,
    synchronise_confirmed_salary,
)

load_dotenv()
init_db()

INPUT_TAB = "① 需求澄清"
RESULT_TAB = "② 生成、复核与发布"
ANALYSIS_TAB = "③ 智能分析"
LOG_TAB = "④ 当前案例审计"
HISTORY_TAB = "⑤ 历史案例"
WORK_MODES = ["待确认", "现场办公", "混合办公", "远程办公"]

EXAMPLE_REQUIREMENT = """我们想招一名高级 AI 产品经理，在上海现场办公，属于企业智能产品部。
这个岗位要负责企业 AI Agent 产品从需求分析到上线评估的完整链路，包括规划企业知识库和 Agent 工作流，协调算法、研发和业务团队推动产品落地，建立效果评估与持续优化机制。
候选人需要3年以上 B 端产品经验，本科及以上，具备需求分析、项目推进和与技术团队定义产品指标的能力。有 RAG、模型评估或 Agent 产品上线经验加分。
薪资范围是30K–45K·14薪。岗位亮点是参与核心 AI 产品从0到1建设。"""

BATCH_EXAMPLE = """岗位1：高级前端工程师，北京，3年以上经验，精通 React 和 TypeScript，有 SSR 经验优先，薪资 25K-40K·15薪。

岗位2：数据分析师，上海，2年以上经验，熟练 SQL 和 Python，有 BI 报表经验优先，薪资 18K-30K·14薪。

岗位3：后端开发工程师，深圳，5年以上经验，精通 Go 和微服务架构，有高并发经验优先，薪资 30K-50K·16薪。"""

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
    "batch_mode": False,
    "batch_raw": "",
    "batch_results": [],
    "batch_current_index": 0,
    "intake_raw": "",
    "intake_supplement": "",
    "intake_done": False,
    "intake_mode": "",
    "polish_style": "专业清晰",
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
    "template_search": "",
    "template_name": "",
    "history_search": "",
}
for key, value in DEFAULTS.items():
    st.session_state.setdefault(key, value)


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------

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


def load_batch_example() -> None:
    st.session_state.batch_raw = BATCH_EXAMPLE


def reset_case() -> None:
    for key in list(st.session_state):
        if key.startswith("quality_supplement_"):
            del st.session_state[key]
    for key, value in DEFAULTS.items():
        st.session_state[key] = str(uuid.uuid4()) if key == "run_id" else value.copy() if isinstance(value, (dict, list)) else value


def reset_approval() -> None:
    st.session_state.approved_hash = ""


def confirm_salary_source_update(candidate: str) -> None:
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
        f"已将薪资从'{old_salary}'更新为'{candidate}'。"
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


def apply_smart_suggestion(field: str, value: str) -> None:
    """应用智能推荐到对应字段"""
    state_key = FIELD_STATE_KEYS.get(field)
    if state_key:
        current = str(st.session_state.get(state_key, "")).strip()
        if current:
            st.session_state[state_key] = current + "\n" + value
        else:
            st.session_state[state_key] = value
        st.session_state.quality_flash = f"已将推荐内容追加到{field}字段。"


def save_current_as_template() -> None:
    """将当前 JD 保存为模板"""
    name = st.session_state.template_name.strip()
    if not name:
        st.session_state.flash_message = "请先输入模板名称。"
        return
    if not st.session_state.jd_text:
        st.session_state.flash_message = "当前没有 JD 内容可保存。"
        return
    save_template(
        name=name,
        job_title=st.session_state.generated_job_title or st.session_state.field_job_title,
        platform=st.session_state.generated_platform or st.session_state.field_platform,
        content=st.session_state.jd_text,
        tags=[],
    )
    st.session_state.template_name = ""
    st.session_state.flash_message = f"模板「{name}」已保存。"


def load_template_to_fields(template_id: str) -> None:
    """加载模板到当前会话"""
    from jd_agent.storage import get_template

    template = get_template(template_id)
    if not template:
        return
    st.session_state.jd_text = template["content"]
    st.session_state.jd_draft = template["content"]
    st.session_state.generated_job_title = template["job_title"]
    st.session_state.generated_platform = template["platform"]
    st.session_state.result_platform = template["platform"]
    st.session_state.approved_hash = ""
    st.session_state.next_tab = RESULT_TAB
    st.session_state.flash_message = f"已加载模板「{template['name']}」"


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


# ---------------------------------------------------------------------------
# 页面渲染
# ---------------------------------------------------------------------------

if st.session_state.next_tab:
    st.session_state.workflow_tab = st.session_state.next_tab
    st.session_state.next_tab = ""

if st.session_state.next_result_view:
    st.session_state.result_view = st.session_state.next_result_view
    st.session_state.previous_result_view = st.session_state.next_result_view
    st.session_state.next_result_view = ""

st.title("招聘协作 Agent")
st.caption("原始需求 → 自动抽取 → 主动澄清 → 渠道化 JD → 风险门禁 → 智能分析 → 人工发布")

# LLM 状态提示
if os.getenv("LLM_API_KEY", "").strip():
    st.badge(
        f"LLM 配置已载入 · {os.getenv('LLM_MODEL', 'gpt-5-mini')}",
        icon=":material/key:",
        color="blue",
    )
    st.caption("配置已载入不等于鉴权成功；实际调用结果会在需求整理和 JD 生成阶段明确显示。")
else:
    st.warning("未配置 LLM_API_KEY：需求抽取和 JD 生成将使用明确标记的离线演示模式。", icon=":material/warning:")

# 全局闪现消息
if st.session_state.flash_message:
    st.toast(st.session_state.flash_message)
    st.session_state.flash_message = ""

# ---------------------------------------------------------------------------
# 侧边栏
# ---------------------------------------------------------------------------

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

    st.divider()

    # 全局统计
    st.subheader("全局统计")
    stats = case_stats()
    stat_cols = st.columns(2)
    stat_cols[0].metric("总案例数", stats["total_cases"])
    stat_cols[1].metric("总事件数", stats["total_events"])
    stat_cols[0].metric("已存模板", stats["total_templates"])
    stat_cols[1].metric("已发布", stats["status_counts"].get("已发布", 0))

    if stats["status_counts"]:
        st.caption("案例状态分布")
        for status, count in stats["status_counts"].items():
            st.progress(count / max(stats["total_cases"], 1), text=f"{status}: {count}")

    st.divider()

    # 模板快速访问
    st.subheader("模板库")
    templates = load_templates(st.session_state.template_search)
    st.text_input("搜索模板", key="template_search", placeholder="按名称或岗位搜索", label_visibility="collapsed")
    if templates:
        for t in templates[:5]:
            with st.container(border=True):
                st.write(f"**{t['name']}**")
                st.caption(f"{t['job_title']} · {t['platform']} · {t['created_at'][:10]}")
                btn_cols = st.columns(2)
                btn_cols[0].button(
                    "加载",
                    key=f"load_tpl_{t['template_id']}",
                    icon=":material/folder_open:",
                    on_click=load_template_to_fields,
                    args=(t["template_id"],),
                )
                btn_cols[1].button(
                    "删除",
                    key=f"del_tpl_{t['template_id']}",
                    icon=":material/delete:",
                    on_click=delete_template,
                    args=(t["template_id"],),
                )
        if len(templates) > 5:
            st.caption(f"还有 {len(templates) - 5} 个模板，请在历史案例标签页查看完整列表。")
    else:
        st.caption("暂无保存的模板")


# ---------------------------------------------------------------------------
# 标签页
# ---------------------------------------------------------------------------

input_tab, result_tab, analysis_tab, log_tab, history_tab = st.tabs(
    [INPUT_TAB, RESULT_TAB, ANALYSIS_TAB, LOG_TAB, HISTORY_TAB],
    key="workflow_tab",
    on_change="rerun",
)

# ===========================================================================
# Tab 1: 需求澄清
# ===========================================================================

if input_tab.open:
    with input_tab:
        st.subheader("先把原始材料交给 Agent")
        st.write("粘贴业务方的招聘需求、旧 JD、会议笔记或聊天记录。Agent 会先整理事实，再只追问当前最关键的信息。")

        # 输入方式选择
        mode_cols = st.columns([2, 1])
        with mode_cols[0]:
            st.segmented_control(
                "输入方式",
                ["Agent 智能整理", "直接校对字段"],
                key="input_mode",
                required=True,
                width="stretch",
            )
        with mode_cols[1]:
            st.checkbox("批量模式", key="batch_mode", help="一次性粘贴多个岗位需求，Agent 会自动拆分并逐个处理。")

        # 批量模式
        if st.session_state.batch_mode:
            with st.container(border=True):
                st.text_area(
                    "批量招聘材料（每个岗位用空行分隔）",
                    key="batch_raw",
                    height=200,
                    placeholder="例如：\n岗位1：高级前端工程师，北京...\n\n岗位2：数据分析师，上海...",
                )
                with st.container(horizontal=True):
                    st.button("载入批量示例", icon=":material/lightbulb:", on_click=load_batch_example)
                    batch_parse_clicked = st.button("拆分并逐个处理", icon=":material/splitscreen:", type="primary")

            if batch_parse_clicked:
                raw = st.session_state.batch_raw.strip()
                if not raw:
                    st.error("请先粘贴批量招聘材料。")
                else:
                    parts = batch_split_requirements(raw)
                    if len(parts) <= 1:
                        st.warning("未能识别出多个独立岗位需求，请确保每个岗位之间用空行分隔。")
                    else:
                        st.session_state.batch_results = parts
                        st.session_state.batch_current_index = 0
                        st.success(f"已拆分为 {len(parts)} 个岗位需求，点击下方逐个处理。")

            if st.session_state.batch_results:
                batch_items = st.session_state.batch_results
                total = len(batch_items)
                current_idx = st.session_state.batch_current_index

                st.info(f"批量处理进度：{current_idx + 1} / {total}")

                # 进度条
                st.progress((current_idx) / total if total > 0 else 0)

                # 当前项预览
                st.text_area(
                    f"当前岗位需求 #{current_idx + 1}",
                    value=batch_items[current_idx],
                    height=120,
                    disabled=True,
                )

                batch_cols = st.columns(3)
                with batch_cols[0]:
                    if st.button("← 上一个", disabled=current_idx == 0, icon=":material/arrow_back:"):
                        st.session_state.batch_current_index = max(0, current_idx - 1)
                        st.rerun()
                with batch_cols[1]:
                    if st.button("载入到当前案例", icon=":material/input:", type="primary"):
                        st.session_state.intake_raw = batch_items[current_idx]
                        st.session_state.batch_mode = False
                        st.session_state.input_mode = "Agent 智能整理"
                        st.rerun()
                with batch_cols[2]:
                    if st.button("下一个 →", disabled=current_idx >= total - 1, icon=":material/arrow_forward:"):
                        st.session_state.batch_current_index = min(total - 1, current_idx + 1)
                        st.rerun()

                st.caption("载入后可在下方正常使用 Agent 智能整理流程。")

        # 单个需求处理
        if not st.session_state.batch_mode:
            if st.session_state.input_mode == "Agent 智能整理":
                with st.container(border=True):
                    st.text_area(
                        "原始招聘材料",
                        key="intake_raw",
                        height=220,
                        placeholder="例如：我们要招一名上海的高级 AI 产品经理……\n也可以直接粘贴旧 JD 或与业务负责人的聊天记录。",
                    )
                    st.segmented_control(
                        "润色风格",
                        ["专业清晰", "简洁直接", "正式稳健"],
                        key="polish_style",
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
                            "重新拆解并润色" if st.session_state.intake_done else "自动拆解并润色",
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
                            extracted_job, extraction_mode = extract_job_input(
                                source,
                                existing,
                                st.session_state.polish_style,
                            )
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
                            {
                                "mode": extraction_mode,
                                "polish_style": st.session_state.polish_style,
                                "filled_fields": filled_count,
                                "conflicts": len(st.session_state.intake_conflicts),
                            },
                        )
                        st.rerun()
                    except ValueError as exc:
                        st.error(str(exc), icon=":material/error:")

                if st.session_state.intake_done:
                    st.markdown(
                        f":green-badge[已完成] 已自动拆解，并按 **{st.session_state.polish_style}** 风格润色。"
                        "润色只整理原文事实，结构化字段仍需人工复核。"
                    )

        job = current_job()

        # 智能进度仪表盘
        if st.session_state.intake_done or st.session_state.input_mode == "直接校对字段":
            completion = get_completion_stats(job)
            with st.container(border=True):
                st.subheader("智能进度仪表盘")
                prog_cols = st.columns([3, 1])
                with prog_cols[0]:
                    st.markdown(
                        render_progress_bar(completion["filled"], completion["total"], "字段完成度"),
                        unsafe_allow_html=True,
                    )
                    if completion["missing_required"]:
                        st.caption(f"🔴 缺少必填：{'、'.join(completion['missing_required_list'])}")
                    if completion["missing_recommended"]:
                        st.caption(f"🟡 建议补充：{'、'.join(completion['missing_recommended_list'][:3])}{'...' if len(completion['missing_recommended_list']) > 3 else ''}")
                with prog_cols[1]:
                    quick = quick_quality_check(job)
                    score = quick["quality_score"].score
                    st.metric("质量评分", f"{score}/100")
                    st.caption(render_quality_gauge(score))

        # 智能推荐面板
        if st.session_state.intake_done and st.session_state.field_job_title:
            suggestions = suggest_field_values(st.session_state.field_job_title)
            if suggestions:
                with st.expander("AI 智能推荐（基于岗位名称）", expanded=False, icon=":material/tips_and_updates:"):
                    st.caption("根据岗位名称自动推荐常见职责、技能和亮点。点击「应用」可追加到对应字段。")
                    for sug in suggestions:
                        with st.container(border=True):
                            sug_cols = st.columns([4, 1])
                            with sug_cols[0]:
                                st.write(f"**{sug.label}**")
                                st.code(sug.value, language=None)
                                st.caption(f"推荐理由：{sug.reason} · 置信度：{sug.confidence:.0%}")
                            with sug_cols[1]:
                                st.button(
                                    "应用",
                                    key=f"apply_sug_{sug.field}",
                                    icon=":material/add_circle:",
                                    on_click=apply_smart_suggestion,
                                    args=(sug.field, sug.value),
                                )

        # 关键词提取
        if st.session_state.intake_done and st.session_state.intake_raw:
            keywords = extract_keywords(st.session_state.intake_raw)
            if keywords:
                with st.expander("关键词提取", expanded=False, icon=":material/tag:"):
                    st.markdown(render_keyword_cloud([k.model_dump() for k in keywords]), unsafe_allow_html=True)

        # 技能缺口分析
        if st.session_state.intake_done and st.session_state.field_job_title:
            gaps = analyze_skill_gaps(job)
            if gaps:
                with st.expander(f"技能缺口分析（{len(gaps)} 项潜在缺口）", expanded=False, icon=":material/psychology:"):
                    st.caption("系统对比同类岗位的典型技能要求，提示当前 JD 中可能遗漏的关键能力。")
                    high_gaps = [g for g in gaps if g.importance == "high"]
                    med_gaps = [g for g in gaps if g.importance == "medium"]
                    if high_gaps:
                        st.write("**高优先级缺口：**")
                        for gap in high_gaps:
                            st.write(f"- 🔴 **{gap.skill}** ({gap.category}) — {gap.note}")
                    if med_gaps:
                        st.write("**中优先级缺口：**")
                        for gap in med_gaps:
                            st.write(f"- 🟡 **{gap.skill}** ({gap.category}) — {gap.note}")

        # 薪资基准
        if st.session_state.intake_done and st.session_state.field_job_title:
            benchmark = salary_benchmark(
                st.session_state.field_job_title,
                st.session_state.field_location,
                st.session_state.field_experience,
            )
            with st.expander("薪资基准建议", expanded=False, icon=":material/currency_yen:"):
                bench_cols = st.columns(2)
                with bench_cols[0]:
                    st.metric("建议薪资范围", benchmark.suggested_range)
                    st.caption(f"置信度：{benchmark.confidence} · 来源：{benchmark.source}")
                with bench_cols[1]:
                    if st.session_state.field_salary:
                        st.metric("当前薪资", st.session_state.field_salary)
                    else:
                        st.metric("当前薪资", "未填写")
                st.info(benchmark.notes)
                if not st.session_state.field_salary and benchmark.suggested_range:
                    if st.button("采纳建议薪资", icon=":material/check:", key="adopt_salary"):
                        st.session_state.field_salary = benchmark.suggested_range

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
                    st.caption("以下是内容优化建议，不会阻止生成 JD。")
                    for issue in field_issues:
                        st.warning(f"{issue.label}：{issue.message}")
                if missing_recommended:
                    st.caption("建议继续补充：" + "、".join(missing_recommended))
                if conflicts:
                    st.checkbox("我已人工核对上述冲突，并已在结构化字段中保留最终版本。", key="intake_conflicts_confirmed")
                if not missing_required and (not conflicts or st.session_state.intake_conflicts_confirmed):
                    st.success("岗位事实已可用，可以进入生成。", icon=":material/check_circle:")

                generation_blocked = bool(
                    missing_required
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


# ===========================================================================
# Tab 2: 生成、复核与发布
# ===========================================================================

if result_tab.open:
    with result_tab:
        if not st.session_state.jd_text:
            st.info("请先在'需求澄清'中生成 JD。", icon=":material/info:")
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

            # 质量评分展示
            risk_job = JobInput.model_validate(st.session_state.generated_job) if st.session_state.generated_job else current_job()
            assessment = assess_risks(risk_job, st.session_state.jd_text)
            content_issues = diagnose_content_quality(risk_job)
            quality_score = calculate_quality_score(
                risk_job,
                st.session_state.jd_text,
                assessment,
                content_issues,
                st.session_state.optimization_decisions,
            )

            with st.container(border=True):
                st.subheader("JD 质量评分")
                score_cols = st.columns(6)
                score_cols[0].metric("总分", f"{quality_score.score}/100")
                score_cols[1].metric("完整度", f"{quality_score.completeness}/30")
                score_cols[2].metric("具体性", f"{quality_score.specificity}/25")
                score_cols[3].metric("风险", f"{quality_score.risk}/20")
                score_cols[4].metric("内容质量", f"{quality_score.quality}/15")
                score_cols[5].metric("优化处理", f"{quality_score.optimization}/10")

                # 评分条形图
                score_bar = st.columns(5)
                categories = [
                    ("完整度", quality_score.completeness, 30),
                    ("具体性", quality_score.specificity, 25),
                    ("风险", quality_score.risk, 20),
                    ("内容质量", quality_score.quality, 15),
                    ("优化处理", quality_score.optimization, 10),
                ]
                for i, (label, val, max_val) in enumerate(categories):
                    with score_bar[i]:
                        st.caption(label)
                        st.progress(val / max_val if max_val > 0 else 0, text=f"{val}/{max_val}")

            # 智能提示
            tips = generate_smart_tips(risk_job, st.session_state.jd_text, assessment, quality_score)
            if tips:
                with st.container(border=True):
                    st.subheader("智能提示")
                    for tip in tips:
                        if tip.level == "danger":
                            st.error(f"**{tip.title}** — {tip.content}")
                        elif tip.level == "warning":
                            st.warning(f"**{tip.title}** — {tip.content}")
                        elif tip.level == "success":
                            st.success(f"**{tip.title}** — {tip.content}")
                        else:
                            st.info(f"**{tip.title}** — {tip.content}")

            st.segmented_control(
                "结果视图",
                ["预览", "编辑", "对比"],
                key="result_view",
                required=True,
                on_change=on_result_view_change,
            )
            if st.session_state.result_view == "预览":
                with st.container(border=True):
                    st.markdown(st.session_state.jd_text)
            elif st.session_state.result_view == "编辑":
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
                    st.session_state.next_result_view = "预览"
                    st.rerun()
            elif st.session_state.result_view == "对比":
                # 原始需求 vs 生成 JD 对比
                comparison = compare_requirement_vs_jd(
                    st.session_state.intake_raw,
                    risk_job,
                    st.session_state.jd_text,
                )
                with st.container(border=True):
                    st.subheader("原始需求 vs 生成 JD")
                    st.caption("对比关键字段在原始材料和最终 JD 中的一致性。")
                    for item in comparison:
                        cmp_cols = st.columns([1, 2, 2, 1])
                        with cmp_cols[0]:
                            icon = "✅" if item.match else "⚠️"
                            st.write(f"{icon} **{item.label}**")
                        with cmp_cols[1]:
                            st.caption("原始需求")
                            st.text(item.original)
                        with cmp_cols[2]:
                            st.caption("生成 JD")
                            st.text(item.generated)
                        with cmp_cols[3]:
                            if not item.match and item.original != "(未提取)" and item.generated != "(未填写)":
                                st.caption("不一致")

            # 薪资变更检测
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

            # 风险门禁
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

            # 人工审批
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
                    "quality_score": quality_score.score,
                }
                log_event("jd_approved", st.session_state.run_id, metadata)
                st.success("当前内容版本已确认。")

            approved = bool(
                not high_risk
                and st.session_state.approved_hash == content_hash(st.session_state.jd_text)
            )
            if approved:
                st.success("审批有效：可导出或发送。", icon=":material/check_circle:")

                # 多格式导出
                st.subheader("导出与发布")
                docx_bytes = markdown_jd_to_docx(st.session_state.jd_text)

                export_cols = st.columns(4)
                with export_cols[0]:
                    st.download_button(
                        "下载 Word",
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
                with export_cols[1]:
                    st.download_button(
                        "下载 Markdown",
                        data=jd_to_markdown(st.session_state.jd_text).encode("utf-8"),
                        file_name=f"{st.session_state.generated_job_title or '招聘岗位'}_{st.session_state.generated_platform or '通用'}_JD.md",
                        mime="text/markdown",
                        icon=":material/code:",
                        on_click=lambda: log_event(
                            "markdown_downloaded",
                            st.session_state.run_id,
                            {"job_title": st.session_state.generated_job_title},
                        ),
                    )
                with export_cols[2]:
                    st.download_button(
                        "下载纯文本",
                        data=jd_to_plain_text(st.session_state.jd_text).encode("utf-8"),
                        file_name=f"{st.session_state.generated_job_title or '招聘岗位'}_{st.session_state.generated_platform or '通用'}_JD.txt",
                        mime="text/plain",
                        icon=":material/text_snippet:",
                        on_click=lambda: log_event(
                            "text_downloaded",
                            st.session_state.run_id,
                            {"job_title": st.session_state.generated_job_title},
                        ),
                    )
                with export_cols[3]:
                    try:
                        pdf_bytes = jd_to_pdf(st.session_state.jd_text)
                        st.download_button(
                            "下载 PDF",
                            data=pdf_bytes,
                            file_name=f"{st.session_state.generated_job_title or '招聘岗位'}_{st.session_state.generated_platform or '通用'}_JD.pdf",
                            mime="application/pdf",
                            icon=":material/picture_as_pdf:",
                            on_click=lambda: log_event(
                                "pdf_downloaded",
                                st.session_state.run_id,
                                {"job_title": st.session_state.generated_job_title},
                            ),
                        )
                    except Exception:
                        st.button("PDF 不可用", disabled=True, icon=":material/picture_as_pdf:")

                # 保存为模板
                with st.container(border=True):
                    st.caption("保存为模板供后续复用")
                    tpl_cols = st.columns([3, 1])
                    tpl_cols[0].text_input("模板名称", key="template_name", placeholder="例如：AI 产品经理标准 JD")
                    tpl_cols[1].button("保存模板", icon=":material/save:", on_click=save_current_as_template)

                # 邮件发送
                st.subheader("邮件发布")
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
                st.warning("当前版本未完成有效审批，导出和邮件发送均被禁用。")


# ===========================================================================
# Tab 3: 智能分析 (NEW)
# ===========================================================================

if analysis_tab.open:
    with analysis_tab:
        st.subheader("智能分析仪表盘")

        job = current_job()
        if st.session_state.generated_job:
            job = JobInput.model_validate(st.session_state.generated_job)

        jd_text = st.session_state.jd_text or ""

        if not st.session_state.intake_done and not jd_text:
            st.info("请先在'需求澄清'中整理需求或生成 JD，分析数据将在此展示。", icon=":material/analytics:")
        else:
            # 质量评分
            assessment = assess_risks(job, jd_text or " ")
            content_issues = diagnose_content_quality(job)
            quality_score = calculate_quality_score(
                job, jd_text or " ", assessment, content_issues, st.session_state.optimization_decisions
            )

            # 1. 质量评分总览
            with st.container(border=True):
                st.subheader("质量评分总览")
                score_cols = st.columns(6)
                score_cols[0].metric("总分", f"{quality_score.score}/100")
                score_cols[1].metric("完整度", f"{quality_score.completeness}/30")
                score_cols[2].metric("具体性", f"{quality_score.specificity}/25")
                score_cols[3].metric("风险", f"{quality_score.risk}/20")
                score_cols[4].metric("内容质量", f"{quality_score.quality}/15")
                score_cols[5].metric("优化处理", f"{quality_score.optimization}/10")

                breakdown = quality_score.breakdown
                detail_cols = st.columns(4)
                detail_cols[0].metric("已填字段", f"{breakdown.get('filled_fields', 0)}/{breakdown.get('total_fields', 14)}")
                detail_cols[1].metric("内容问题", f"{breakdown.get('total_issues', 0)}")
                detail_cols[2].metric("已处理", f"{breakdown.get('resolved_issues', 0)}")
                detail_cols[3].metric("JD 长度", f"{breakdown.get('jd_length', 0)} 字")

            # 2. 完成度分析
            completion = get_completion_stats(job)
            with st.container(border=True):
                st.subheader("字段完成度")
                st.markdown(
                    render_progress_bar(completion["filled"], completion["total"], "整体完成度"),
                    unsafe_allow_html=True,
                )
                comp_cols = st.columns(2)
                with comp_cols[0]:
                    if completion["missing_required_list"]:
                        st.write("**缺失必填字段：**")
                        for f in completion["missing_required_list"]:
                            st.write(f"- 🔴 {f}")
                    else:
                        st.success("所有必填字段已完整", icon=":material/check_circle:")
                with comp_cols[1]:
                    if completion["missing_recommended_list"]:
                        st.write("**建议补充字段：**")
                        for f in completion["missing_recommended_list"]:
                            st.write(f"- 🟡 {f}")

            # 3. 关键词云
            kw_source = st.session_state.intake_raw + "\n" + jd_text
            keywords = extract_keywords(kw_source)
            with st.container(border=True):
                st.subheader("关键词提取")
                if keywords:
                    st.markdown(render_keyword_cloud([k.model_dump() for k in keywords]), unsafe_allow_html=True)
                    # 分类统计
                    cat_counts: dict[str, int] = {}
                    for kw in keywords:
                        cat_counts[kw.category] = cat_counts.get(kw.category, 0) + 1
                    cat_cols = st.columns(len(cat_counts))
                    cat_labels = {"skill": "技能", "requirement": "要求", "location": "地点", "education": "学历", "salary": "薪资"}
                    for i, (cat, count) in enumerate(cat_counts.items()):
                        cat_cols[i].metric(cat_labels.get(cat, cat), count)
                else:
                    st.caption("暂无关键词")

            # 4. 技能缺口分析
            gaps = analyze_skill_gaps(job)
            with st.container(border=True):
                st.subheader("技能缺口分析")
                if gaps:
                    st.caption(f"对比同类岗位典型技能库，发现 {len(gaps)} 项潜在缺口：")
                    high_gaps = [g for g in gaps if g.importance == "high"]
                    med_gaps = [g for g in gaps if g.importance == "medium"]
                    if high_gaps:
                        gap_cols = st.columns(len(high_gaps))
                        for i, gap in enumerate(high_gaps):
                            with gap_cols[i]:
                                st.metric(gap.skill, "高优先级")
                                st.caption(gap.note)
                    if med_gaps:
                        for gap in med_gaps:
                            st.write(f"- 🟡 **{gap.skill}** ({gap.category}) — {gap.note}")
                else:
                    st.success("未检测到明显技能缺口", icon=":material/check_circle:")

            # 5. 薪资基准
            if job.job_title:
                benchmark = salary_benchmark(job.job_title, job.location, job.experience)
                with st.container(border=True):
                    st.subheader("薪资基准")
                    bench_cols = st.columns(3)
                    bench_cols[0].metric("建议薪资范围", benchmark.suggested_range)
                    bench_cols[1].metric("当前薪资", job.salary or "未填写")
                    bench_cols[2].metric("置信度", benchmark.confidence)
                    st.caption(f"来源：{benchmark.source}")
                    st.info(benchmark.notes)

            # 6. 风险分布
            with st.container(border=True):
                st.subheader("风险分布")
                risk_cols = st.columns(3)
                high_count = sum(i.level == "high" for i in assessment.issues)
                med_count = sum(i.level == "medium" for i in assessment.issues)
                low_count = sum(i.level == "low" for i in assessment.issues)
                risk_cols[0].metric("🔴 高风险", high_count)
                risk_cols[1].metric("🟠 中风险", med_count)
                risk_cols[2].metric("🟡 低风险", low_count)
                if assessment.issues:
                    st.caption("风险详情：")
                    for issue in assessment.issues:
                        severity = {"high": "🔴", "medium": "🟠", "low": "🟡"}.get(issue.level, "⚪")
                        st.write(f"{severity} **{issue.category}** — {issue.text}")

            # 7. 智能提示汇总
            tips = generate_smart_tips(job, jd_text, assessment, quality_score)
            if tips:
                with st.container(border=True):
                    st.subheader("智能建议汇总")
                    for tip in tips:
                        if tip.level == "danger":
                            st.error(f"**{tip.title}** — {tip.content}")
                        elif tip.level == "warning":
                            st.warning(f"**{tip.title}** — {tip.content}")
                        elif tip.level == "success":
                            st.success(f"**{tip.title}** — {tip.content}")
                        else:
                            st.info(f"**{tip.title}** — {tip.content}")


# ===========================================================================
# Tab 4: 当前案例审计
# ===========================================================================

if log_tab.open:
    with log_tab:
        st.subheader("当前案例审计日志")
        st.caption(f"只展示当前运行 ID `{st.session_state.run_id[:8]}` 的记录；审批人、收件人和邮件回执在界面中脱敏。")
        events = recent_events(st.session_state.run_id)
        if not events:
            st.info("当前案例尚无日志。")
        else:
            # 事件统计
            event_types: dict[str, int] = {}
            for event in events:
                event_types[event["event"]] = event_types.get(event["event"], 0) + 1
            stat_cols = st.columns(min(len(event_types), 5))
            for i, (etype, count) in enumerate(event_types.items()):
                stat_cols[i % len(stat_cols)].metric(etype, count)

            st.divider()

            for event in events:
                with st.expander(f"{event['created_at']} · {event['event']}"):
                    metadata = json.loads(event["metadata"])
                    st.json(redact_metadata(metadata))


# ===========================================================================
# Tab 5: 历史案例 (NEW)
# ===========================================================================

if history_tab.open:
    with history_tab:
        st.subheader("历史案例")
        st.caption("浏览所有历史招聘案例，支持按岗位名称、平台或状态搜索。")

        search_cols = st.columns([3, 1])
        search_cols[0].text_input("搜索案例", key="history_search", placeholder="输入岗位名称、平台或状态...")
        search_cols[1].button("刷新", icon=":material/refresh:")

        cases = all_cases(search=st.session_state.history_search)
        if not cases:
            st.info("暂无历史案例记录。")
        else:
            st.write(f"共找到 **{len(cases)}** 个案例")

            # 状态统计
            status_counts: dict[str, int] = {}
            for case in cases:
                status_counts[case.get("status", "未知")] = status_counts.get(case.get("status", "未知"), 0) + 1
            if status_counts:
                status_cols = st.columns(len(status_counts))
                for i, (status, count) in enumerate(status_counts.items()):
                    status_cols[i].metric(status, count)

            st.divider()

            for case in cases:
                with st.container(border=True):
                    case_cols = st.columns([3, 2, 2, 1])
                    with case_cols[0]:
                        status_icon = {
                            "需求澄清中": "🔵",
                            "待审批": "🟡",
                            "已审批": "🟢",
                            "已发布": "✅",
                        }.get(case.get("status", ""), "⚪")
                        st.write(f"{status_icon} **{case.get('job_title', '未知岗位')}**")
                        st.caption(f"ID: {case['run_id'][:8]} · {case['created_at']}")
                    with case_cols[1]:
                        st.caption("平台")
                        st.write(case.get("platform", "未指定"))
                    with case_cols[2]:
                        st.caption("状态")
                        st.write(case.get("status", "未知"))
                    with case_cols[3]:
                        st.caption("事件数")
                        st.write(case.get("event_count", 0))

                    # 查看案例详情
                    with st.expander("查看案例事件"):
                        case_evts = case_events(case["run_id"])
                        for evt in case_evts:
                            evt_time = evt["created_at"]
                            evt_type = evt["event"]
                            evt_meta = json.loads(evt["metadata"])
                            st.write(f"**{evt_time}** · `{evt_type}`")
                            st.json(redact_metadata(evt_meta))
                            st.caption("---")
