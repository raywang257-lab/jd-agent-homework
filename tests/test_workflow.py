from jd_agent.exporter import markdown_jd_to_docx
from jd_agent.schemas import JDContent, JobInput
from jd_agent.workflow import (
    PLATFORM_OPTIONS,
    _schema,
    assess_risks,
    content_hash,
    explain_llm_failure,
    inspect_completeness,
    inspect_field_relevance,
    inspect_risks,
    platform_description,
    render_jd,
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
