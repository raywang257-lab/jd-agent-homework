"""数据模型定义 -- 包含原有模型和新增智能模型"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# 原有模型
# ---------------------------------------------------------------------------

class JobInput(BaseModel):
    """结构化岗位输入"""

    job_title: str = ""
    department: str = ""
    location: str = ""
    work_mode: str = ""
    seniority: str = ""
    experience: str = ""
    education: str = ""
    salary: str = ""
    job_goal: str = ""
    responsibilities: str = ""
    required_skills: str = ""
    preferred_skills: str = ""
    selling_points: str = ""
    platform: str = "BOSS直聘"


class JDContent(BaseModel):
    """JD 正文内容（渠道无关的 canonical 内容）"""

    job_title: str = ""
    department: str = ""
    location: str = ""
    work_mode: str = ""
    seniority: str = ""
    experience: str = ""
    education: str = ""
    salary_and_benefits: str = ""
    job_goal: str = ""
    responsibilities: list[str] = Field(default_factory=list)
    required_skills: list[str] = Field(default_factory=list)
    preferred_skills: list[str] = Field(default_factory=list)
    selling_points: list[str] = Field(default_factory=list)
    platform: str = ""


class ContentIssue(BaseModel):
    """内容质量诊断项"""

    issue_id: str
    field: str
    issue_type: str
    severity: str
    original_text: str
    safe_rewrite: str = ""
    reason: str
    follow_up_question: str = ""


class OptimizationDecision(BaseModel):
    """内容优化决定记录"""

    issue_id: str
    original_text: str
    revised_text: str
    decision: str
    source_excerpt: str


class RiskIssue(BaseModel):
    """风险检查项"""

    level: str  # high / medium / low
    category: str
    text: str
    reason: str
    suggestion: str


class RiskAssessment(BaseModel):
    """风险评估结果"""

    issues: list[RiskIssue] = Field(default_factory=list)
    overall_level: str = "低"


class FieldIssue(BaseModel):
    """字段相关性检查项"""

    label: str
    message: str


# ---------------------------------------------------------------------------
# 新增智能模型
# ---------------------------------------------------------------------------

class QualityScore(BaseModel):
    """JD 质量评分"""

    score: int = 0
    completeness: int = 0
    specificity: int = 0
    risk: int = 0
    quality: int = 0
    optimization: int = 0
    breakdown: dict[str, Any] = Field(default_factory=dict)


class SmartSuggestion(BaseModel):
    """智能字段推荐"""

    field: str
    label: str
    value: str
    confidence: float = 0.5
    reason: str = ""


class KeywordInfo(BaseModel):
    """关键词信息"""

    keyword: str
    category: str  # skill / requirement / benefit / location / education
    frequency: int = 1


class SkillGapItem(BaseModel):
    """技能缺口项"""

    skill: str
    category: str
    in_jd: bool = False
    importance: str = "medium"  # high / medium / low
    note: str = ""


class JDTemplate(BaseModel):
    """JD 模板"""

    template_id: str
    name: str
    job_title: str
    platform: str
    content: str
    created_at: str
    tags: list[str] = Field(default_factory=list)


class SmartTip(BaseModel):
    """智能提示"""

    tip_id: str
    level: str  # info / warning / success / danger
    title: str
    content: str
    action: str = ""


class SalaryBenchmark(BaseModel):
    """薪资基准"""

    job_title: str
    location: str
    suggested_range: str
    confidence: str = "medium"  # high / medium / low
    source: str = "行业经验基准"
    notes: str = ""


class CaseSummary(BaseModel):
    """历史案例摘要"""

    run_id: str
    job_title: str
    platform: str
    created_at: str
    event_count: int
    status: str = "未知"


class ComparisonItem(BaseModel):
    """对比项"""

    field: str
    label: str
    original: str
    generated: str
    match: bool = True
