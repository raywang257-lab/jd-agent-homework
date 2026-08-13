import pytest

import jd_agent.workflow as workflow
from jd_agent.exporter import markdown_jd_to_docx
from jd_agent.schemas import JDContent, JobInput
from jd_agent.workflow import (
    PLATFORM_OPTIONS,
    _schema,
    assess_risks,
    content_hash,
    detect_intake_conflicts,
    diagnose_content_quality,
    diagnose_requirement_quality,
    explain_llm_failure,
    enforce_source_facts,
    find_salary_update_candidate,
    generate_jd,
    inspect_completeness,
    inspect_field_relevance,
    inspect_risks,
    platform_description,
    prioritise_follow_up_questions,
    render_jd,
    suggest_job_goal,
    _demo_extract_job_input,
)


def test_required_fields_are_reported():
    missing_required, _, questions = inspect_completeness(JobInput())
    assert "岗位名称" in missing_required
    assert "工作地点" in missing_required
    assert questions


def complete_job(**overrides):
    data = {
        "job_title": "AI 产品经理",
        "department": "企业智能产品部",
        "location": "上海·徐汇区",
        "work_mode": "现场办公",
        "seniority": "高级",
        "experience": "3年以上",
        "education": "本科及以上",
        "salary": "30K–45K·14薪",
        "job_goal": "负责并推动企业 AI Agent 产品落地。",
        "responsibilities": "负责产品规划，推动研发与业务团队交付。",
        "required_skills": "具备 B 端产品经验与需求分析能力。",
        "preferred_skills": "有 RAG 或 Agent 项目经验。",
        "selling_points": "参与核心 AI 项目从 0 到 1 建设。",
    }
    data.update(overrides)
    return JobInput(**data)


def test_valid_job_fields_have_no_relevance_issues():
    assert inspect_field_relevance(complete_job()) == []


def test_location_rejects_salary_content():
    issues = inspect_field_relevance(complete_job(location="30K–45K"))
    assert any(issue.field == "location" for issue in issues)


def test_education_rejects_location_content():
    issues = inspect_field_relevance(complete_job(education="上海市"))
    assert any(issue.field == "education" for issue in issues)


def test_salary_requires_money_content():
    issues = inspect_field_relevance(complete_job(salary="本科及以上"))
    assert any(issue.field == "salary" for issue in issues)


def test_no_channel_error_has_actionable_message():
    message = explain_llm_failure(RuntimeError("503 no_channel"), "claude")
    assert "没有可用通道" in message
    assert "/models" in message


def test_strict_schema_requires_every_property():
    schema = _schema()
    assert set(schema["required"]) == set(schema["properties"])


def test_risky_age_requirement_is_flagged():
    issues = inspect_risks("# 测试\n## 岗位职责\n1. 工作\n## 任职要求\n1. 30岁以下")
    assert any(issue.level == "high" and "年龄" in issue.reason for issue in issues)


@pytest.mark.parametrize(
    "expression",
    [
        "30岁以下",
        "32岁以下",
        "年龄不得超过38岁",
        "年龄低于35岁",
        "90后优先",
        "年轻人优先",
        "最好不要超过35岁",
        "年轻、有活力的候选人优先",
    ],
)
def test_age_restriction_variants_are_blocked(expression):
    text = (
        "# 测试岗位\n"
        "## 岗位职责\n"
        "1. 推动业务落地\n"
        "## 任职要求\n"
        f"1. {expression}"
    )
    assessment = assess_risks(complete_job(), text)
    assert assessment.overall_level == "高"
    assert any(issue.category == "合规性" for issue in assessment.issues)


def test_content_hash_changes_after_edit():
    assert content_hash("版本A") != content_hash("版本B")


def test_docx_export_is_a_zip_container():
    payload = markdown_jd_to_docx("# AI 产品经理\n## 岗位职责\n1. 推动产品落地")
    assert payload[:2] == b"PK"
    assert len(payload) > 1_000


def sample_jd():
    return JDContent(
        job_title="AI 产品经理",
        job_summary="负责企业 AI Agent 产品的规划与落地。",
        responsibilities=["负责产品规划", "推动研发与业务团队交付"],
        requirements=["具备 B 端产品经验", "具备需求分析能力"],
        preferred_qualifications=["有 Agent 项目经验"],
        selling_points=["参与核心 AI 产品建设"],
        location_and_mode="上海·徐汇区 · 现场办公",
        salary_and_benefits="30K–45K·14薪",
    )


def test_every_platform_has_a_description():
    assert len(PLATFORM_OPTIONS) >= 5
    assert all(platform_description(platform) for platform in PLATFORM_OPTIONS)


def test_platform_rendering_is_distinct():
    boss = render_jd(complete_job(platform="BOSS直聘"), sample_jd())
    liepin = render_jd(complete_job(platform="猎聘"), sample_jd())
    assert boss != liepin
    assert "## 你要负责" in boss
    assert "## 关键任职资格" in liepin


def test_all_platform_versions_pass_structure_check():
    for platform in PLATFORM_OPTIONS:
        text = render_jd(complete_job(platform=platform), sample_jd())
        issues = inspect_risks(text)
        assert not any(issue.text == "结构缺失" for issue in issues), platform


def test_discriminatory_requirement_is_high_risk():
    text = render_jd(complete_job(), sample_jd()) + "\n仅限35岁以下，男性优先。"
    assessment = assess_risks(complete_job(), text)
    assert assessment.overall_level == "高"
    assert any(issue.category == "合规性" for issue in assessment.issues)


def test_unverified_benefit_is_flagged():
    text = render_jd(complete_job(), sample_jd()) + "\n五险一金，年终奖。"
    assessment = assess_risks(complete_job(), text)
    assert assessment.overall_level == "中"
    assert any(issue.category == "真实性" and issue.text == "五险一金" for issue in assessment.issues)


def test_unsupported_promotional_claims_are_flagged():
    text = render_jd(complete_job(), sample_jd()) + "\n上海中心地段，团队合作氛围佳，发展空间大。"
    assessment = assess_risks(complete_job(), text)
    claims = {issue.text for issue in assessment.issues if issue.category == "真实性"}
    assert assessment.overall_level == "中"
    assert {"中心地段", "团队合作氛围佳", "发展空间大"}.issubset(claims)


def test_unsupported_technical_culture_claim_is_flagged():
    text = render_jd(complete_job(), sample_jd()) + "\n技术氛围开放。"
    assessment = assess_risks(complete_job(), text)
    assert any(issue.text == "技术氛围开放" for issue in assessment.issues)


def test_source_promotional_claim_is_not_flagged_when_hr_provided_it():
    job = complete_job(selling_points="团队合作氛围佳")
    text = render_jd(job, sample_jd()) + "\n团队合作氛围佳"
    assessment = assess_risks(job, text)
    assert not any(issue.text == "团队合作氛围佳" for issue in assessment.issues)


def test_concrete_salary_edit_from_negotiable_requires_confirmation():
    job = complete_job(salary="面议")
    text = render_jd(job, sample_jd()).replace("面议", "30k - 45k · 14 薪")

    assert find_salary_update_candidate(job, text) == "30K–45K·14薪"
    assessment = assess_risks(job, text)
    assert assessment.overall_level == "高"
    assert any(issue.category == "薪资事实待确认" for issue in assessment.issues)


def test_confirmed_salary_is_not_treated_as_a_new_fact():
    job = complete_job(salary="30K–45K·14薪")
    text = render_jd(job, sample_jd())

    assert find_salary_update_candidate(job, text) == ""
    assert not any(
        issue.category == "薪资事实待确认"
        for issue in assess_risks(job, text).issues
    )


def test_generated_facts_are_forced_back_to_source():
    job = complete_job(
        location="上海",
        work_mode="现场办公",
        salary="30K–45K·14薪",
        selling_points="参与核心 AI 产品从 0 到 1 建设",
    )
    hallucinated = sample_jd().model_copy(
        update={
            "job_title": "首席 AI 产品经理",
            "location_and_mode": "上海中心地段 · 弹性办公",
            "salary_and_benefits": "高薪透明，发展空间大",
            "selling_points": ["团队氛围佳", "交通便利"],
        }
    )
    safe = enforce_source_facts(job, hallucinated)
    assert safe.job_title == job.job_title
    assert safe.location_and_mode == "上海 · 现场办公"
    assert safe.salary_and_benefits == job.salary
    assert safe.selling_points == ["参与核心 AI 产品从 0 到 1 建设"]


def test_generate_jd_enforces_contract_on_model_output(monkeypatch):
    job = complete_job(location="上海", work_mode="现场办公", salary="30K–45K·14薪")
    hallucinated = sample_jd().model_copy(
        update={
            "job_title": "首席 AI 产品经理",
            "location_and_mode": "上海中心地段 · 弹性办公",
            "salary_and_benefits": "高薪透明，发展空间大",
            "selling_points": ["团队合作氛围佳", "交通便利"],
        }
    )

    class FakeCompletions:
        def create(self, **kwargs):
            message = type("Message", (), {"content": hallucinated.model_dump_json()})()
            choice = type("Choice", (), {"message": message})()
            return type("Response", (), {"choices": [choice]})()

    class FakeOpenAI:
        def __init__(self, **kwargs):
            self.chat = type("Chat", (), {"completions": FakeCompletions()})()

    monkeypatch.setenv("LLM_API_KEY", "test-key")
    monkeypatch.setenv("LLM_MODEL", "test-model")
    monkeypatch.setenv("LLM_API_MODE", "chat")
    monkeypatch.delenv("LLM_BASE_URL", raising=False)
    monkeypatch.setattr(workflow, "OpenAI", FakeOpenAI)

    result, mode = generate_jd(job)
    assert mode == "llm:test-model"
    assert result.job_title == job.job_title
    assert result.location_and_mode == "上海 · 现场办公"
    assert result.salary_and_benefits == job.salary
    assert result.selling_points == [job.selling_points]


def test_generate_jd_supports_streaming_responses_mode(monkeypatch):
    job = complete_job(location="上海", work_mode="现场办公", salary="30K–45K·14薪")
    model_output = sample_jd()

    class FakeEvent:
        type = "response.output_text.delta"

        def __init__(self, delta):
            self.delta = delta

    class FakeResponses:
        def create(self, **kwargs):
            assert kwargs["stream"] is True
            assert isinstance(kwargs["input"], list)
            assert kwargs["text"]["format"]["type"] == "json_schema"
            return [FakeEvent(model_output.model_dump_json())]

    class FakeOpenAI:
        def __init__(self, **kwargs):
            self.responses = FakeResponses()

    monkeypatch.setenv("LLM_API_KEY", "test-key")
    monkeypatch.setenv("LLM_MODEL", "test-responses-model")
    monkeypatch.setenv("LLM_API_MODE", "responses")
    monkeypatch.setenv("LLM_BASE_URL", "https://example.com/v1")
    monkeypatch.setattr(workflow, "OpenAI", FakeOpenAI)

    result, mode = generate_jd(job)

    assert mode == "llm:test-responses-model"
    assert result.job_title == job.job_title
    assert result.location_and_mode == "上海 · 现场办公"
    assert result.salary_and_benefits == job.salary


def test_rendering_ignores_hallucinated_critical_facts():
    job = complete_job(location="上海", work_mode="现场办公", salary="30K–45K·14薪")
    hallucinated = sample_jd().model_copy(
        update={
            "job_title": "首席 AI 产品经理",
            "location_and_mode": "海外 · 弹性办公",
            "salary_and_benefits": "100K，发展空间大",
            "selling_points": ["团队合作氛围佳"],
        }
    )
    text = render_jd(job, hallucinated)
    assert text.startswith("# AI 产品经理 ｜ 30K–45K·14薪")
    assert "工作地点与方式：上海 · 现场办公" in text
    assert "首席 AI 产品经理" not in text
    assert "100K" not in text
    assert "团队合作氛围佳" not in text


def test_work_mode_in_requirements_is_flagged_as_wrong_section():
    jd = sample_jd().model_copy(update={"requirements": ["本科及以上学历，愿意现场办公"]})
    assessment = assess_risks(complete_job(), render_jd(complete_job(), jd))
    assert any(
        issue.category == "内容结构" and "现场办公" in issue.text
        for issue in assessment.issues
    )


def test_seniority_and_experience_conflict_is_flagged():
    job = complete_job(seniority="初级", experience="5年以上")
    assessment = assess_risks(job, render_jd(job, sample_jd()))
    assert any(issue.category == "条件矛盾" for issue in assessment.issues)


def test_responsibility_requirement_duplicate_is_flagged():
    jd = sample_jd().model_copy(
        update={
            "responsibilities": ["负责企业 AI 产品需求分析与项目推进"],
            "requirements": ["负责企业 AI 产品需求分析与项目推进"],
        }
    )
    assessment = assess_risks(complete_job(), render_jd(complete_job(), jd))
    assert any(issue.category == "内容重复" for issue in assessment.issues)


def test_intake_extracts_labelled_job_facts_without_inventing_missing_fields():
    raw = """
    岗位：AI 产品经理
    地点：上海
    薪资：30K-45K·14薪
    岗位目标：负责并推动企业 AI Agent 产品落地
    职责：负责产品规划并推动研发交付
    任职要求：具备 B 端产品经验和需求分析能力
    """
    job = _demo_extract_job_input(raw)
    assert job.job_title == "AI 产品经理"
    assert job.location == "上海"
    assert job.salary == "30K-45K·14薪"
    assert job.education == ""
    assert job.selling_points == ""


def test_intake_extracts_experience_range_from_unlabelled_jd():
    raw = """
    # 产品经理（Product Manager）

    ## 任职要求
    1. 本科及以上学历；
    2. 具备 1-3 年产品经理相关经验，有完整产品项目经验优先；
    """
    job = _demo_extract_job_input(raw)
    assert job.job_title == "产品经理"
    assert job.experience == "1-3年"
    assert job.department == ""
    assert job.seniority == ""


def test_department_and_seniority_are_optional_and_not_queried():
    job = complete_job(department="", seniority="")
    missing_required, missing_recommended, questions = inspect_completeness(job)
    assert "所属部门" not in missing_required
    assert "所属部门" not in missing_recommended
    assert not any("部门" in question or "职级" in question for question in questions)


def test_optional_department_and_seniority_are_omitted_from_rendered_jd():
    text = render_jd(complete_job(department="", seniority=""), sample_jd())
    assert "部门：待定" not in text
    assert "职级：待定" not in text


def test_intake_conflicts_are_explained():
    conflicts = detect_intake_conflicts("上海或北京，远程或现场，薪资30K-40K，另一版35K-45K")
    assert any("多个薪资" in item for item in conflicts)
    assert any("多种工作方式" in item for item in conflicts)
    assert any("多个工作城市" in item for item in conflicts)


def test_follow_up_questions_are_limited_and_prioritise_required_fields():
    questions = prioritise_follow_up_questions(JobInput(), limit=4)
    assert len(questions) == 4
    assert any("岗位名称" in question for question in questions)
    assert not any("薪资范围" in question for question in questions)


def test_generic_requirements_trigger_verifiability_questions():
    job = complete_job(
        required_skills="具备产品能力、需求分析能力、项目推进能力和沟通能力。",
        responsibilities="负责产品工作。",
    )
    questions = diagnose_requirement_quality(job)
    assert any("需求分析能力" in question and "可验证" in question for question in questions)
    assert any("项目" in question and "最终结果" in question for question in questions)
    assert any("沟通能力" in question and "场景" in question for question in questions)
    assert any("高级岗位" in question and "结果边界" in question for question in questions)


def test_specific_requirements_do_not_trigger_generic_questions():
    job = complete_job(
        required_skills=(
            "能独立完成企业客户访谈、业务流程梳理和需求优先级判断；"
            "有跨算法、研发和业务团队推动产品上线交付的经验。"
        ),
        responsibilities="负责完整业务链路，并对产品上线和用户采用指标负责。",
    )
    assert diagnose_requirement_quality(job) == []


def test_content_quality_returns_structured_explainable_issues():
    job = complete_job(
        required_skills="具备需求分析能力；有责任心、抗压能力强；负责推进项目。",
        responsibilities="规划企业知识库和 Agent 工作流。",
    )
    issues = diagnose_content_quality(job)
    by_type = {issue.issue_type: issue for issue in issues}
    assert "不可验证" in by_type
    assert "职责与要求混淆" in by_type
    assert "缺少预期产出" in by_type
    assert all(issue.issue_id and issue.reason for issue in issues)
    assert all(not issue.safe_rewrite for issue in issues)


def test_safe_rewrite_only_reorganizes_source_words():
    original = "负责需求分析，协调算法、研发和业务团队推动产品落地"
    job = complete_job(responsibilities=original)
    issues = diagnose_content_quality(job)
    rewrite = next(issue for issue in issues if issue.issue_type == "安全改写")
    assert rewrite.original_text == original
    assert rewrite.safe_rewrite == "开展需求分析，协调算法、研发与业务团队推动产品落地"
    assert rewrite.requires_confirmation is False


def test_job_goal_suggestion_requires_explicit_goal_language():
    raw = "这个岗位要负责企业 AI Agent 产品从需求分析到上线评估的完整链路。"
    assert suggest_job_goal(raw) == "负责企业 AI Agent 产品从需求分析到上线评估的完整链路"
    assert suggest_job_goal("日常与研发和业务团队沟通。") == ""
