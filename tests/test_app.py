from __future__ import annotations

from pathlib import Path

from streamlit.testing.v1 import AppTest

from jd_agent import storage, workflow
from jd_agent.schemas import JDContent
from jd_agent.workflow import content_hash


APP_PATH = Path(__file__).resolve().parents[1] / "app.py"
INPUT_TAB = "① 需求澄清"
RESULT_TAB = "② 生成、复核与发布"
LOG_TAB = "③ 当前案例审计"


def generated_jd() -> JDContent:
    return JDContent(
        job_title="AI 产品经理",
        job_summary="负责企业 AI Agent 产品从需求分析到上线评估的完整链路。",
        responsibilities=["规划企业知识库和 Agent 工作流", "推动研发与业务团队交付"],
        requirements=["具备三年以上 B 端产品经验", "具备需求分析和项目推进能力"],
        preferred_qualifications=["有 RAG 或 Agent 产品经验"],
        selling_points=["参与核心 AI 产品从 0 到 1 建设"],
        location_and_mode="上海 · 现场办公",
        salary_and_benefits="30K–45K·14薪",
    )


def complete_field_state() -> dict[str, object]:
    return {
        "input_mode": "直接校对字段",
        "field_job_title": "AI 产品经理",
        "field_department": "企业智能产品部",
        "field_location": "上海",
        "field_work_mode": "现场办公",
        "field_seniority": "高级",
        "field_experience": "3年以上",
        "field_education": "本科及以上",
        "field_salary": "30K–45K·14薪",
        "field_job_goal": "负责企业 AI Agent 产品从需求分析到上线评估的完整链路。",
        "field_responsibilities": "规划企业知识库和 Agent 工作流\n推动研发与业务团队交付",
        "field_required_skills": "具备 B 端产品经验、需求分析和项目推进能力",
        "field_preferred_skills": "有 RAG 或 Agent 产品经验",
        "field_selling_points": "参与核心 AI 产品从 0 到 1 建设",
        "field_platform": "BOSS直聘",
    }


def find_by_label(elements, label: str):
    return next(element for element in elements if element.label == label)


def build_app(monkeypatch, tmp_path) -> AppTest:
    monkeypatch.setattr(storage, "DB_PATH", tmp_path / "audit.db")
    monkeypatch.setattr(workflow, "generate_jd", lambda job: (generated_jd(), "demo"))
    app = AppTest.from_file(str(APP_PATH), default_timeout=10).run()
    for key, value in complete_field_state().items():
        app.session_state[key] = value
    return app.run()


def generate_result(app: AppTest) -> AppTest:
    generate = next(button for button in app.button if button.label.startswith("生成 "))
    return generate.click().run()


def test_generation_enters_result_and_updates_sidebar(monkeypatch, tmp_path):
    app = generate_result(build_app(monkeypatch, tmp_path))

    assert app.session_state["workflow_tab"] == RESULT_TAB
    assert app.session_state["jd_text"].startswith("# AI 产品经理")
    assert any("已生成，待复核" in warning.value for warning in app.warning)


def test_quality_supplement_is_written_back_and_audited(monkeypatch, tmp_path):
    app = build_app(monkeypatch, tmp_path)
    supplements = [area for area in app.text_area if area.label == "补充 HR 已确认的事实"]
    accept_buttons = [button for button in app.button if button.label == "使用补充内容"]
    assert supplements
    assert accept_buttons

    confirmed = "能通过企业客户访谈和业务流程梳理形成需求优先级方案，并提供已上线案例"
    supplements[0].set_value(confirmed)
    app = accept_buttons[0].click().run()

    assert confirmed in app.session_state["field_required_skills"]
    assert app.session_state["optimization_decisions"]
    assert any(
        event["event"] == "content_optimization_decided"
        and '"decision": "accepted"' in event["metadata"]
        for event in storage.recent_events(app.session_state["run_id"])
    )


def test_switching_to_edit_preserves_generated_jd(monkeypatch, tmp_path):
    app = generate_result(build_app(monkeypatch, tmp_path))
    expected = app.session_state["jd_text"]

    view = find_by_label(app.segmented_control, "结果视图")
    app = view.set_value("编辑").run()

    editor = find_by_label(app.text_area, "编辑最终 JD")
    assert editor.value == expected
    assert app.session_state["jd_text"] == expected


def test_result_platform_can_generate_channel_specific_copy(monkeypatch, tmp_path):
    app = generate_result(build_app(monkeypatch, tmp_path))
    app.session_state["approved_hash"] = content_hash(app.session_state["jd_text"])

    def fail_if_model_is_called(job):
        raise AssertionError("平台切换不应再次调用生成模型")

    monkeypatch.setattr(workflow, "generate_jd", fail_if_model_is_called)

    platform = find_by_label(app.selectbox, "发布平台")
    app = platform.set_value("猎聘").run()
    app.session_state["next_tab"] = RESULT_TAB
    app = find_by_label(app.button, "生成猎聘发布文案").click().run()

    assert app.session_state["generated_platform"] == "猎聘"
    assert app.session_state["generated_job"]["platform"] == "猎聘"
    assert app.session_state["generated_content"]
    assert "## 关键任职资格" in app.session_state["jd_text"]
    assert app.session_state["approved_hash"] == ""
    assert app.session_state["approval_confirmed"] is False
    assert any(
        event["event"] == "platform_version_generated"
        for event in storage.recent_events(app.session_state["run_id"])
    )


def test_saving_edit_invalidates_approval(monkeypatch, tmp_path):
    app = generate_result(build_app(monkeypatch, tmp_path))
    app.session_state["approved_hash"] = content_hash(app.session_state["jd_text"])
    app = find_by_label(app.segmented_control, "结果视图").set_value("编辑").run()

    revised = app.session_state["jd_text"] + "\n\n人工补充说明。"
    # AppTest 当前不会像浏览器一样持续保留可选 tabs 的打开状态，
    # 因此在提交表单的这次脚本运行前显式保持结果页。
    app.session_state["next_tab"] = RESULT_TAB
    find_by_label(app.segmented_control, "结果视图").set_value("编辑")
    find_by_label(app.text_area, "编辑最终 JD").set_value(revised)
    app = find_by_label(app.button, "保存修改").click().run()

    assert app.session_state["jd_text"] == revised
    assert app.session_state["approved_hash"] == ""
    assert app.session_state["result_view"] == "预览"
    assert any(event["event"] == "jd_edited" for event in storage.recent_events(app.session_state["run_id"]))


def test_high_risk_cannot_be_approved(monkeypatch, tmp_path):
    app = generate_result(build_app(monkeypatch, tmp_path))
    risky = app.session_state["jd_text"] + "\n\n## 其他要求\n1. 30岁以下"
    app.session_state["jd_text"] = risky
    app.session_state["jd_draft"] = risky
    app.session_state["reviewer"] = "测试审批人"
    app.session_state["approval_confirmed"] = True
    # 即使旧版本错误地留下了同内容哈希，高风险门禁也必须优先阻断发布。
    app.session_state["approved_hash"] = content_hash(risky)
    app = app.run()

    approve = find_by_label(app.button, "确认当前版本")
    assert approve.disabled is True
    assert not app.download_button


def test_salary_edit_can_be_confirmed_and_then_submitted(monkeypatch, tmp_path):
    app = generate_result(build_app(monkeypatch, tmp_path))
    old_text = app.session_state["jd_text"]
    updated_text = old_text.replace("30K–45K·14薪", "36K-50K·14薪")
    app.session_state["generated_job"]["salary"] = "面议"
    app.session_state["generated_content"]["salary_and_benefits"] = "面议"
    app.session_state["field_salary"] = "面议"
    app.session_state["jd_text"] = updated_text
    app.session_state["jd_draft"] = updated_text
    app.session_state["reviewer"] = "测试审批人"
    app.session_state["approval_confirmed"] = True
    app.session_state["approved_hash"] = content_hash(updated_text)
    app = app.run()

    approve = find_by_label(app.button, "确认当前版本")
    assert approve.disabled is True
    confirm_salary = next(
        button for button in app.button
        if button.label.startswith("确认更新薪资为 ")
    )
    app.session_state["next_tab"] = RESULT_TAB
    app = confirm_salary.click().run()

    assert app.session_state["generated_job"]["salary"] == "36K–50K·14薪"
    assert app.session_state["generated_content"]["salary_and_benefits"] == "36K–50K·14薪"
    assert app.session_state["field_salary"] == "36K–50K·14薪"
    assert "36K–50K·14薪" in app.session_state["jd_text"]
    assert app.session_state["approved_hash"] == ""
    assert app.session_state["approval_confirmed"] is False
    assert any(
        event["event"] == "salary_fact_confirmed"
        for event in storage.recent_events(app.session_state["run_id"])
    )

    app.session_state["reviewer"] = "测试审批人"
    app.session_state["approval_confirmed"] = True
    app.session_state["next_tab"] = RESULT_TAB
    app = app.run()
    approve = find_by_label(app.button, "确认当前版本")
    assert approve.disabled is False
    app.session_state["next_tab"] = RESULT_TAB
    app = approve.click().run()

    assert app.session_state["approved_hash"] == content_hash(app.session_state["jd_text"])
    assert app.download_button


def test_new_case_clears_previous_state(monkeypatch, tmp_path):
    app = generate_result(build_app(monkeypatch, tmp_path))
    old_run_id = app.session_state["run_id"]
    app = find_by_label(app.button, "开始新案例").click().run()

    assert app.session_state["run_id"] != old_run_id
    assert app.session_state["workflow_tab"] == INPUT_TAB
    assert app.session_state["jd_text"] == ""
    assert app.session_state["jd_draft"] == ""
    assert app.session_state["intake_raw"] == ""


def test_audit_view_only_renders_current_run(monkeypatch, tmp_path):
    app = build_app(monkeypatch, tmp_path)
    current_run = app.session_state["run_id"]
    storage.log_event("current_only", current_run, {"marker": "CURRENT_MARKER"})
    storage.log_event("other_only", "another-run", {"marker": "OTHER_MARKER"})
    app.session_state["workflow_tab"] = LOG_TAB
    app = app.run()

    rendered = "\n".join(str(element.value) for element in [*app.markdown, *app.json])
    assert "CURRENT_MARKER" in rendered
    assert "OTHER_MARKER" not in rendered
