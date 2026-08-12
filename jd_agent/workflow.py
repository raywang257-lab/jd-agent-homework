from __future__ import annotations

import hashlib
import json
import os
import re
from difflib import SequenceMatcher
from typing import Any

from openai import APIConnectionError, APIStatusError, APITimeoutError, OpenAI
from pydantic import ValidationError

from .schemas import ContentIssue, FieldIssue, JDContent, JobInput, RiskAssessment, RiskIssue


PLATFORM_GUIDES = {
    "BOSS直聘": {
        "description": "简短直接，优先呈现薪资、地点、核心职责和沟通邀请。",
        "instruction": "使用简短直接的候选人沟通语气；职责和要求各优先保留3至5条；突出真实薪资、工作地点和岗位亮点。",
    },
    "猎聘": {
        "description": "面向中高端人才，强调职位使命、业务影响、能力门槛和发展空间。",
        "instruction": "面向中高端候选人；强调岗位使命、业务影响和可验证成果；语气专业克制，不使用夸张承诺。",
    },
    "智联招聘": {
        "description": "标准、完整、易检索，清晰区分职责、任职要求与加分项。",
        "instruction": "采用标准化招聘结构；保留完整的岗位概述、职责、要求、加分项和亮点；使用便于检索的准确职业关键词。",
    },
    "前程无忧": {
        "description": "正式稳健，信息完整，重点表达岗位职责、任职资格和工作条件。",
        "instruction": "使用正式稳健的职位说明语气；完整呈现职责、任职资格、工作条件和真实待遇；避免口语化和网络热词。",
    },
    "拉勾招聘": {
        "description": "适合互联网、产品和技术岗，突出团队协作、产品阶段和技术或业务挑战。",
        "instruction": "采用专业但有活力的互联网招聘语气；强调产品阶段、团队协作、技术或业务挑战；不得自行补充技术栈。",
    },
    "公司官网": {
        "description": "信息最完整、表达最正式，兼顾岗位价值和雇主形象。",
        "instruction": "生成完整正式的官网职位页内容；兼顾岗位价值和雇主形象；如输入没有公司故事、福利或承诺，不得虚构。",
    },
}
PLATFORM_OPTIONS = tuple(PLATFORM_GUIDES)


def platform_description(platform: str) -> str:
    guide = PLATFORM_GUIDES.get(platform, PLATFORM_GUIDES["BOSS直聘"])
    return guide["description"]


REQUIRED_FIELDS = {
    "job_title": "岗位名称",
    "location": "工作地点",
    "job_goal": "岗位目标",
    "responsibilities": "主要职责",
    "required_skills": "必备能力",
}

RECOMMENDED_FIELDS = {
    "experience": "经验要求",
    "education": "学历要求",
    "salary": "薪资范围",
    "selling_points": "团队或岗位亮点",
}

FIELD_QUESTIONS = {
    "job_title": "请输入具体的岗位名称，例如‘AI 产品经理’或‘Python 开发工程师’。",
    "department": "该岗位属于哪个部门、中心或业务团队？",
    "location": "该岗位的工作城市或具体区域在哪里？例如‘上海·徐汇区’。",
    "seniority": "该岗位的职级是初级、中级、高级、资深还是管理岗？",
    "experience": "该岗位要求几年相关经验，是否接受应届生或经验不限？",
    "education": "该岗位的最低学历或学位要求是什么？例如‘本科及以上’或‘学历不限’。",
    "salary": "该岗位的薪资范围和计薪周期是什么？例如‘30K–45K·14薪’或‘面议’。",
    "job_goal": "这个岗位需要为业务实现什么核心目标？",
    "responsibilities": "入职后需要负责哪些具体任务和产出？",
    "required_skills": "胜任该岗位必须具备哪些能力、工具经验或可验证成果？",
    "preferred_skills": "候选人拥有哪些额外经验会被优先考虑？",
    "selling_points": "该岗位在成长、项目、团队或业务方面有什么真实亮点？",
}

MONEY_PATTERN = re.compile(
    r"(?:\d+(?:[.,]\d+)?\s*(?:[kK]|千|万|元|人民币|美元|港币)|"
    r"月薪|年薪|时薪|日薪|薪资|工资|面议|\d+\s*薪)"
)
EDUCATION_PATTERN = re.compile(
    r"(?:博士|硕士|研究生|本科|大专|专科|高中|中专|初中|学历|学位|学士|学历不限|不限学历)"
)
EXPERIENCE_PATTERN = re.compile(
    r"(?:"
    r"\d+\s*(?:[-–—~至到]\s*\d+\s*)?年(?:以上|以下|左右|以内)?"
    r"|工作经验|从业经验|应届|校招|社招|经验不限|无经验"
    r")"
)
ROLE_PATTERN = re.compile(
    r"(?:经理|工程师|设计师|运营|销售|专员|主管|总监|顾问|分析师|研究员|"
    r"开发|测试|架构师|科学家|助理|实习生|负责人|会计|出纳|教师|医生|护士|"
    r"律师|编辑|文案|客服|采购|产品|算法|行政|人事|财务|法务|市场|HR|CEO|CTO|CFO)" ,
    re.IGNORECASE,
)
ORG_PATTERN = re.compile(
    r"(?:部|中心|团队|组|事业群|事业部|办公室|平台|研究院|研究所|科|室|业务线|"
    r"研发|人力资源|财务|法务|市场|销售|运营|产品|设计)"
)
LEVEL_PATTERN = re.compile(
    r"(?:实习|初级|中级|高级|资深|专家|负责人|管理岗|主管|经理|总监|"
    r"[PMLT]\s*\d+|[一二三四五六七八九十0-9]+级|职级不限|不限)" ,
    re.IGNORECASE,
)

KNOWN_LOCATIONS = (
    "北京|上海|天津|重庆|广州|深圳|杭州|南京|苏州|成都|武汉|西安|长沙|"
    "郑州|青岛|济南|厦门|福州|宁波|合肥|昆明|沈阳|大连|长春|哈尔滨|"
    "海口|三亚|南宁|贵阳|兰州|西宁|太原|石家庄|南昌|乌鲁木齐|拉萨|"
    "香港|澳门|台北|中国|新加坡|日本|美国|英国|澳大利亚|加拿大"
)
LOCATION_PATTERN = re.compile(
    rf"(?:{KNOWN_LOCATIONS}|[一-鿿]{{2,12}}(?:省|市|区|县|州|旗|乡|镇|园区|大厦)|"
    r"[A-Za-z][A-Za-z .'-]{1,40})"
)


def _field_issue(field: str, label: str, message: str) -> FieldIssue:
    return FieldIssue(field=field, label=label, message=message, question=FIELD_QUESTIONS[field])


def inspect_field_relevance(job: JobInput) -> list[FieldIssue]:
    """检查已填内容是否属于对应字段。

    这一层是可解释、离线可用的确定性校验，避免在位置、学历、
    薪资等结构化字段中接收明显错类的内容。
    """
    issues: list[FieldIssue] = []

    title = job.job_title.strip()
    if title and (len(title) > 30 or not ROLE_PATTERN.search(title)):
        issues.append(_field_issue("job_title", "岗位名称", "请只填具体职位，不要填地点、薪资或一段职责。"))

    department = job.department.strip()
    if department and (
        len(department) > 30
        or not ORG_PATTERN.search(department)
        or MONEY_PATTERN.search(department)
        or EDUCATION_PATTERN.search(department)
        or EXPERIENCE_PATTERN.search(department)
    ):
        issues.append(_field_issue("department", "所属部门", "请只填部门、中心或业务团队名称。"))

    location = job.location.strip()
    if location and (
        len(location) > 50
        or not LOCATION_PATTERN.search(location)
        or MONEY_PATTERN.search(location)
        or EDUCATION_PATTERN.search(location)
        or EXPERIENCE_PATTERN.search(location)
        or ROLE_PATTERN.search(location)
    ):
        issues.append(_field_issue("location", "工作地点", "这里只能填城市、国家或具体区域，不要填岗位、学历、经验或薪资。"))

    seniority = job.seniority.strip()
    if seniority and (len(seniority) > 20 or not LEVEL_PATTERN.search(seniority)):
        issues.append(_field_issue("seniority", "职级", "请只填职级或岗位级别，例如‘高级’、‘资深’或‘P7’。"))

    experience = job.experience.strip()
    if experience and (
        len(experience) > 30
        or not EXPERIENCE_PATTERN.search(experience)
        or MONEY_PATTERN.search(experience)
        or EDUCATION_PATTERN.fullmatch(experience)
    ):
        issues.append(_field_issue("experience", "经验要求", "请只填工作年限或经验类型，例如‘3年以上’。"))

    education = job.education.strip()
    if education and (
        len(education) > 30
        or not EDUCATION_PATTERN.search(education)
        or MONEY_PATTERN.search(education)
        or EXPERIENCE_PATTERN.search(education)
    ):
        issues.append(_field_issue("education", "学历要求", "这里只能填学历或学位，不要填地点、工作年限或薪资。"))

    salary = job.salary.strip()
    if salary and (
        len(salary) > 40
        or not MONEY_PATTERN.search(salary)
        or EDUCATION_PATTERN.search(salary)
        or EXPERIENCE_PATTERN.search(salary)
    ):
        issues.append(_field_issue("salary", "薪资范围", "这里必须是金额、计薪周期或‘面议’，不要填地点、学历或经验。"))

    goal = job.job_goal.strip()
    if goal and (len(goal) < 10 or not re.search(r"负责|推动|实现|建设|提升|达成|支持|确保|优化|管理|完成|规划|目标", goal)):
        issues.append(_field_issue("job_goal", "岗位目标", "请描述该岗位要推动或实现的业务结果，不要只填关键词。"))

    responsibilities = job.responsibilities.strip()
    if responsibilities and (
        len(responsibilities) < 10
        or not re.search(r"负责|制定|推动|协调|管理|建立|分析|优化|完成|跟进|维护|设计|开发|交付|运营", responsibilities)
    ):
        issues.append(_field_issue("responsibilities", "主要职责", "请用‘负责、推动、建立、交付’等动作描述具体任务。"))

    required_skills = job.required_skills.strip()
    skill_pattern = r"能力|经验|熟悉|掌握|了解|精通|具备|能够|技能|工具|证书|资格|专业|沟通|分析|管理|开发|设计|产品|技术|英语|RAG|Agent|Python"
    if required_skills and (len(required_skills) < 6 or not re.search(skill_pattern, required_skills, re.IGNORECASE)):
        issues.append(_field_issue("required_skills", "必备能力", "请填候选人必须具备的能力、工具经验或专业资格。"))

    preferred_skills = job.preferred_skills.strip()
    if preferred_skills and (len(preferred_skills) < 4 or not re.search(skill_pattern, preferred_skills, re.IGNORECASE)):
        issues.append(_field_issue("preferred_skills", "加分能力", "请填与候选人能力或相关项目经验有关的加分项。"))

    selling_points = job.selling_points.strip()
    if selling_points and (
        len(selling_points) < 4
        or not re.search(r"成长|发展|机会|参与|核心|福利|空间|团队|平台|业务|项目|技术|从\s*0\s*到\s*1", selling_points)
    ):
        issues.append(_field_issue("selling_points", "岗位亮点", "请填写真实的成长、项目、团队或业务亮点。"))

    return issues


def inspect_completeness(job: JobInput) -> tuple[list[str], list[str], list[str]]:
    missing_required = [label for key, label in REQUIRED_FIELDS.items() if not getattr(job, key).strip()]
    missing_recommended = [label for key, label in RECOMMENDED_FIELDS.items() if not getattr(job, key).strip()]
    missing_keys = [key for key in REQUIRED_FIELDS if not getattr(job, key).strip()]
    missing_keys += [key for key in RECOMMENDED_FIELDS if not getattr(job, key).strip()]
    questions = [FIELD_QUESTIONS[key] for key in missing_keys]
    if job.required_skills and len(job.required_skills.strip()) < 12:
        questions.append("必备能力较笼统：请补充工具、业务场景或可验证的产出要求。")
    return missing_required, missing_recommended, questions


def _content_issue(
    field: str,
    original_text: str,
    issue_type: str,
    severity: str,
    reason: str,
    follow_up_question: str = "",
    safe_rewrite: str = "",
    requires_confirmation: bool = False,
) -> ContentIssue:
    identity = "\n".join((field, original_text, issue_type))
    issue_id = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:12]
    return ContentIssue(
        issue_id=issue_id,
        field=field,
        original_text=original_text,
        issue_type=issue_type,
        severity=severity,
        reason=reason,
        follow_up_question=follow_up_question,
        safe_rewrite=safe_rewrite,
        requires_confirmation=requires_confirmation,
    )


def _content_units(value: str) -> list[str]:
    return [
        item.strip(" \t-*•0123456789.、)")
        for item in re.split(r"[\n；;。]+", value)
        if item.strip(" \t-*•0123456789.、)")
    ]


def diagnose_content_quality(job: JobInput) -> list[ContentIssue]:
    """逐条诊断招聘内容；需要新事实时只追问，不自动增强。"""
    issues: list[ContentIssue] = []
    requirements = job.required_skills.strip()

    generic_requirements = [
        (
            r"需求分析(?:能力(?:强)?)?",
            r"客户访谈|业务流程|需求优先级|PRD|原型|产品方案|已上线|上线案例",
            "不可验证",
            "没有说明需求分析发生在哪类场景，也没有可供简历筛选或面试验证的成果证据。",
            "候选人需要通过什么项目经历或可验证成果证明需求分析能力？可考虑客户访谈、流程梳理、需求优先级或产品方案，但未经确认不会写入 JD。",
        ),
        (
            r"项目推进(?:能力(?:强)?)?|项目管理能力(?:强)?",
            r"跨团队|算法|研发|业务团队|上线|交付|项目范围|关键冲突|最终结果",
            "表述空泛",
            "没有说明项目阶段、协作对象、责任范围和成功标准。",
            "候选人需要独立负责哪个阶段、协调哪些团队，并通过什么最终结果证明项目推进能力？",
        ),
        (
            r"沟通(?:协调)?能力(?:强)?",
            r"客户|跨团队|算法|研发|业务|决策|冲突|谈判|汇报",
            "不可验证",
            "没有说明沟通对象、决策场景或可以复盘的行为证据。",
            "沟通能力需要在哪些场景验证，例如客户访谈、跨团队决策或冲突处理？",
        ),
        (
            r"有责任心|责任心强|抗压能力强|具备抗压能力",
            r"$^",
            "不可验证",
            "属于主观人格评价，难以直接用于简历筛选，也容易产生不一致判断。",
            "希望候选人用哪段经历证明其能承担压力或履行责任？请补充具体工作场景和行为证据。",
        ),
        (
            r"(?:能够|能)与技术团队共同定义产品指标|定义产品指标",
            r"业务指标|模型效果|用户体验|指标设计|数据采集|效果复盘",
            "成果标准不明确",
            "没有说明指标类型，也没有说明候选人在指标设计、采集和复盘中的责任范围。",
            "需要定义业务指标、模型效果指标还是用户体验指标？候选人负责指标设计、数据采集还是效果复盘？",
        ),
    ]
    for unit in _content_units(requirements):
        for pattern, evidence_pattern, issue_type, reason, question in generic_requirements:
            if re.search(evidence_pattern, unit):
                continue
            for match in re.finditer(pattern, unit):
                issues.append(
                    _content_issue(
                        field="required_skills",
                        original_text=match.group(0),
                        issue_type=issue_type,
                        severity="medium",
                        reason=reason,
                        follow_up_question=question,
                        requires_confirmation=True,
                    )
                )

    for unit in _content_units(requirements):
        if re.match(r"^(?:负责|推动|制定|规划|协调|跟进|完成)", unit):
            issues.append(
                _content_issue(
                    field="required_skills",
                    original_text=unit,
                    issue_type="职责与要求混淆",
                    severity="medium",
                    reason="该表述描述入职后执行的动作，更像岗位职责，而不是候选人入职前应具备的资格。",
                    follow_up_question="这是入职后的任务，还是候选人必须证明做过的经历？若是任务，应移入岗位职责。",
                    requires_confirmation=True,
                )
            )

    outcome_pattern = re.compile(r"交付|上线|落地|完成|产出|结果|指标|增长|提升|优化|机制|方案|报告|验收")
    for unit in _content_units(job.responsibilities):
        if not outcome_pattern.search(unit):
            issues.append(
                _content_issue(
                    field="responsibilities",
                    original_text=unit,
                    issue_type="缺少预期产出",
                    severity="medium",
                    reason="描述了工作动作，但没有说明应交付什么结果或达到什么完成标准。",
                    follow_up_question="这项职责最终需要交付什么结果，例如产品方案、治理机制、上线版本或效果复盘？",
                    requires_confirmation=True,
                )
            )
        if re.search(r"^负责需求分析[，,]\s*协调", unit) and re.search(r"推动.+落地", unit):
            rewritten = re.sub(r"^负责需求分析", "开展需求分析", unit)
            rewritten = rewritten.replace("和业务团队", "与业务团队")
            issues.append(
                _content_issue(
                    field="responsibilities",
                    original_text=unit,
                    issue_type="安全改写",
                    severity="low",
                    reason="只调整动词和并列关系，不增加团队、规模、技术栈或结果等新事实。",
                    safe_rewrite=rewritten,
                    requires_confirmation=False,
                )
            )

    senior_role = bool(re.search(r"高级|资深|专家|负责人|总监", f"{job.job_title} {job.seniority}"))
    scale_evidence = re.compile(r"规模|人数|团队|预算|收入|增长|上线|交付|指标|结果|客户|业务链路")
    if senior_role and job.responsibilities.strip() and not scale_evidence.search(job.responsibilities):
        issues.append(
            _content_issue(
                field="responsibilities",
                original_text=job.responsibilities.strip(),
                issue_type="高级岗位责任边界不明确",
                severity="medium",
                reason="高级岗位职责没有说明责任规模、结果指标或完整业务边界。",
                follow_up_question="这是高级岗位：需要对哪些指标、业务链路或结果边界负责？",
                requires_confirmation=True,
            )
        )
    unique: dict[str, ContentIssue] = {}
    for issue in issues:
        unique.setdefault(issue.issue_id, issue)
    return list(unique.values())


def diagnose_requirement_quality(job: JobInput) -> list[str]:
    """兼容主动追问入口：从结构化诊断中提取需要 HR 补充的问题。"""
    return list(
        dict.fromkeys(
            issue.follow_up_question
            for issue in diagnose_content_quality(job)
            if issue.follow_up_question
        )
    )


def prioritise_follow_up_questions(job: JobInput, limit: int = 4) -> list[str]:
    """只返回当前最值得问的少量问题，避免把所有缺失项一次性丢给用户。"""
    questions: list[str] = []
    relevance_issues = inspect_field_relevance(job)
    required_issue_fields = set(REQUIRED_FIELDS)

    for issue in relevance_issues:
        if issue.field in required_issue_fields:
            questions.append(issue.question)
    for key in REQUIRED_FIELDS:
        if not getattr(job, key).strip():
            questions.append(FIELD_QUESTIONS[key])
    questions.extend(diagnose_requirement_quality(job))
    for issue in relevance_issues:
        if issue.field not in required_issue_fields:
            questions.append(issue.question)
    for key in RECOMMENDED_FIELDS:
        if not getattr(job, key).strip():
            questions.append(FIELD_QUESTIONS[key])
    if job.required_skills and len(job.required_skills.strip()) < 12:
        questions.append("必备能力较笼统：请补充工具、业务场景或可验证的产出要求。")
    return list(dict.fromkeys(questions))[: max(1, limit)]


_SALARY_NUMBER = r"(?:\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)"
_SALARY_UNIT = r"(?:[kK]|千|万|元|人民币|美元|港币)"
_SALARY_PERIOD = r"(?:\s*(?:[/／]\s*)?(?:每)?(?:月|年|日|天|小时)|\s*(?:月薪|年薪|时薪|日薪))?"
_SALARY_MONTHS = r"(?:[\s··・]*\d+\s*薪)?"

SALARY_RANGE_PATTERN = re.compile(
    rf"{_SALARY_NUMBER}\s*{_SALARY_UNIT}?\s*(?:[-–—~至到])\s*"
    rf"{_SALARY_NUMBER}\s*{_SALARY_UNIT}{_SALARY_PERIOD}{_SALARY_MONTHS}",
    re.IGNORECASE,
)
CONCRETE_SALARY_PATTERN = re.compile(
    rf"(?:{SALARY_RANGE_PATTERN.pattern}|"
    rf"{_SALARY_NUMBER}\s*{_SALARY_UNIT}{_SALARY_PERIOD}{_SALARY_MONTHS})",
    re.IGNORECASE,
)


def _format_salary_candidate(value: str) -> str:
    candidate = re.sub(r"\s+", "", value.strip(" ，,;；。"))
    candidate = re.sub(r"(?:-|—|~|至|到)", "–", candidate)
    return re.sub(r"k", "K", candidate, flags=re.IGNORECASE)


def find_salary_update_candidate(job: JobInput, text: str) -> str:
    """识别最终 JD 中与已确认岗位事实不同的具体薪资。

    这个结果只是待 HR 确认的候选值，不会自动改写结构化岗位事实。
    """
    source_salary = _normalise_text(job.salary)
    seen: set[str] = set()
    for match in CONCRETE_SALARY_PATTERN.finditer(text):
        candidate = _format_salary_candidate(match.group(0))
        candidate_key = _normalise_text(candidate)
        if not candidate_key or candidate_key in seen:
            continue
        seen.add(candidate_key)
        if candidate_key != source_salary:
            return candidate
    return ""


def synchronise_confirmed_salary(text: str, old_salary: str, confirmed_salary: str) -> str:
    """仅统一已确认的薪资表达，保留 JD 中其他手工修改。"""
    updated_text = text.replace(old_salary, confirmed_salary) if old_salary else text
    confirmed_key = _normalise_text(confirmed_salary)

    def replace_match(match: re.Match[str]) -> str:
        matched_salary = _format_salary_candidate(match.group(0))
        if _normalise_text(matched_salary) == confirmed_key:
            return confirmed_salary
        return match.group(0)

    return CONCRETE_SALARY_PATTERN.sub(replace_match, updated_text)


def detect_intake_conflicts(raw_text: str) -> list[str]:
    """在调用生成模型前，找出原始材料中可以明确解释的自相矛盾。"""
    text = raw_text.strip()
    conflicts: list[str] = []
    salary_ranges = list(dict.fromkeys(SALARY_RANGE_PATTERN.findall(text)))
    if len(salary_ranges) > 1:
        conflicts.append("原始材料出现多个薪资范围：" + "、".join(salary_ranges) + "。请确认最终版本。")

    work_modes = [label for keyword, label in (("远程", "远程办公"), ("混合", "混合办公"), ("现场", "现场办公")) if keyword in text]
    if len(set(work_modes)) > 1:
        conflicts.append("原始材料同时出现多种工作方式：" + "、".join(dict.fromkeys(work_modes)) + "。")

    if re.search(r"应届|经验不限|无经验", text) and re.search(r"[3-9]\s*年以上|\d{2,}\s*年以上", text):
        conflicts.append("经验要求同时出现‘应届/经验不限’和‘3年以上’类条件。")
    if re.search(r"学历不限|不限学历", text) and EDUCATION_PATTERN.search(text.replace("学历不限", "").replace("不限学历", "")):
        conflicts.append("学历要求同时出现‘学历不限’和具体学历门槛。")

    locations = list(dict.fromkeys(re.findall(KNOWN_LOCATIONS, text)))
    if len(locations) > 1:
        conflicts.append("原始材料提到多个工作城市：" + "、".join(locations[:4]) + "。请确认实际工作地。")
    return conflicts


def suggest_job_goal(raw_text: str) -> str:
    """只在原文有明确语言线索时，返回待用户确认的岗位目标候选。"""
    patterns = [
        r"(?:这个岗位要|该岗位需要|核心目标是)([^。！？\n]+)",
        r"(负责[^。！？\n]+完整链路)",
        r"(推动[^。！？\n]+(?:落地|建设|增长|提升|优化))",
    ]
    for pattern in patterns:
        match = re.search(pattern, raw_text)
        if match:
            return match.group(1).strip()
    return ""


def _label_value(raw_text: str, labels: str) -> str:
    match = re.search(rf"(?:{labels})\s*[:：]\s*([^\n]+)", raw_text, flags=re.IGNORECASE)
    return match.group(1).strip(" ，,;；。") if match else ""


def _demo_extract_job_input(raw_text: str, existing: JobInput | None = None) -> JobInput:
    data = existing.model_dump() if existing else JobInput(work_mode="", platform="BOSS直聘").model_dump()
    labelled_fields = {
        "job_title": r"岗位名称|职位名称|招聘岗位|岗位|职位",
        "department": r"所属部门|部门|团队",
        "location": r"工作地点|地点|城市",
        "seniority": r"职级|级别",
        "experience": r"经验要求|工作经验|经验",
        "education": r"学历要求|学历|学位",
        "salary": r"薪资范围|薪资|薪酬|月薪|年薪",
        "job_goal": r"岗位目标|职位目标|核心目标",
        "responsibilities": r"主要职责|岗位职责|职责",
        "required_skills": r"必备能力|任职要求|必备要求|职位要求",
        "preferred_skills": r"加分能力|加分项|优先条件",
        "selling_points": r"岗位亮点|团队亮点|亮点",
    }
    for field, labels in labelled_fields.items():
        extracted = _label_value(raw_text, labels)
        if extracted:
            data[field] = extracted

    if not data["job_title"]:
        title_match = re.search(rf"(?:招|招聘|寻找|需要)(?:一名|一位)?\s*([^\n，,。]{{2,24}}?(?:{ROLE_PATTERN.pattern[3:-1]}))", raw_text, re.IGNORECASE)
        if title_match:
            data["job_title"] = title_match.group(1).strip()
        else:
            heading_match = re.search(
                rf"(?m)^[ \t]*#\s*([^#\n（(]{{1,24}}?(?:{ROLE_PATTERN.pattern[3:-1]}))"
                r"(?:\s*[（(][^）)\n]+[）)])?\s*$",
                raw_text,
                re.IGNORECASE,
            )
            if heading_match:
                data["job_title"] = heading_match.group(1).strip()
    if not data["salary"]:
        salary_match = SALARY_RANGE_PATTERN.search(raw_text)
        if salary_match:
            data["salary"] = salary_match.group(0)
    if not data["education"]:
        education_match = EDUCATION_PATTERN.search(raw_text)
        if education_match:
            tail = raw_text[education_match.start() : education_match.start() + 12]
            data["education"] = re.match(r"[^\n，,。;；]+", tail).group(0).strip()
    if not data["experience"]:
        experience_match = EXPERIENCE_PATTERN.search(raw_text)
        if experience_match:
            experience = re.sub(r"\s+", "", experience_match.group(0))
            experience = re.sub(r"[–—~至到]", "-", experience)
            data["experience"] = experience
    for keyword, label in (("远程", "远程办公"), ("混合", "混合办公"), ("现场", "现场办公")):
        if keyword in raw_text:
            data["work_mode"] = label
            break
    return JobInput.model_validate(data)


def _job_input_schema() -> dict[str, Any]:
    schema = JobInput.model_json_schema()
    schema["additionalProperties"] = False
    schema["required"] = list(schema.get("properties", {}))
    return schema


def _extract_job_input_json(raw: str) -> JobInput:
    content = raw.strip()
    if content.startswith("```"):
        content = re.sub(r"^```(?:json)?\s*", "", content, flags=re.IGNORECASE)
        content = re.sub(r"\s*```$", "", content)
    return JobInput.model_validate_json(content)


def extract_job_input(raw_text: str, existing: JobInput | None = None) -> tuple[JobInput, str]:
    """从旧 JD、需求笔记或聊天记录中抽取事实；未出现的信息不允许猜测。"""
    if not raw_text.strip():
        raise ValueError("请先粘贴招聘需求、旧 JD 或沟通记录。")
    api_key = os.getenv("LLM_API_KEY", "").strip()
    if not api_key:
        return _demo_extract_job_input(raw_text, existing), "demo"

    client_kwargs: dict[str, Any] = {"api_key": api_key}
    if os.getenv("LLM_BASE_URL", "").strip():
        client_kwargs["base_url"] = os.environ["LLM_BASE_URL"].strip()
    client = OpenAI(**client_kwargs, timeout=45, max_retries=1)
    model = os.getenv("LLM_MODEL", "gpt-5-mini")
    existing_json = json.dumps(existing.model_dump(), ensure_ascii=False) if existing else "无"
    system = """你是招聘需求整理 Agent。从原始材料中抽取结构化岗位事实。

字段定义：
- job_goal：岗位需要实现的核心业务结果或负责的完整业务链路。
  ‘负责某产品从需求分析到上线评估的完整链路’可以作为岗位目标。
- responsibilities：入职后执行的具体动作和任务。
- required_skills：候选人入职前必须具备的能力和经验。
- experience：材料明确提出的相关工作年限或经验类型。
  ‘具备 1-3 年产品经理相关经验’应提取为‘1-3年’。
- department 和 seniority 是可选字段；材料没有明确提供时返回空字符串，不能根据岗位名称猜测。

同一句话可以为岗位目标提供依据，也可以包含职责信息。
只能提取材料明确支持的内容，不得推测或补写薪资、福利、经验、学历、技术栈和公司信息。
未提供的字符串字段返回空字符串。职责、能力等多项内容使用换行分隔。
如果提供了‘已有结果’，应保留其中已确认内容；只有新材料明确表示更正时才覆盖。
work_mode 仅使用‘现场办公’、‘混合办公’、‘远程办公’或空字符串。
platform 保留已有值；没有时使用‘BOSS直聘’。只返回符合 JSON Schema 的内容。"""
    user_content = f"已有结果：{existing_json}\n\n原始材料：\n{raw_text.strip()}"
    try:
        response = client.chat.completions.create(
            model=model,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user_content}],
            response_format={
                "type": "json_schema",
                "json_schema": {"name": "job_input", "strict": True, "schema": _job_input_schema()},
            },
        )
        result = _extract_job_input_json(response.choices[0].message.content or "{}")
        if existing and not result.platform:
            result.platform = existing.platform
        return result, f"llm:{model}"
    except (APIStatusError, APIConnectionError, APITimeoutError, ValidationError, ValueError) as error:
        reason = explain_llm_failure(error, model)
        return _demo_extract_job_input(raw_text, existing), f"fallback:{reason}"


def _split_items(value: str) -> list[str]:
    items = [part.strip(" -•\t") for part in re.split(r"[\n；;]+", value) if part.strip()]
    return items or [value.strip()] if value.strip() else []


def source_location_and_mode(job: JobInput) -> str:
    """位置与工作方式属于已确认事实，不允许由生成模型补写。"""
    return " · ".join(value for value in (job.location, job.work_mode) if value)


def enforce_source_facts(job: JobInput, jd: JDContent) -> JDContent:
    """将高风险事实字段强制恢复为结构化输入中的已确认值。"""
    return jd.model_copy(
        update={
            "job_title": job.job_title,
            "location_and_mode": source_location_and_mode(job),
            "salary_and_benefits": job.salary or "面议",
            "selling_points": _split_items(job.selling_points),
        }
    )


def _demo_generate(job: JobInput) -> JDContent:
    title = job.job_title or "待定岗位"
    goal = job.job_goal or "承担该岗位的核心业务工作，推动团队目标落地"
    responsibilities = _split_items(job.responsibilities) or ["根据业务目标制定计划并推动落地"]
    requirements = _split_items(job.required_skills) or ["具备与岗位匹配的专业能力"]
    if job.experience:
        requirements.insert(0, f"具备{job.experience}相关工作经验")
    if job.education:
        requirements.append(job.education)
    return JDContent(
        job_title=title,
        job_summary=f"我们正在寻找一名{title}，核心目标是{goal.rstrip('。')}。",
        responsibilities=responsibilities,
        requirements=requirements,
        preferred_qualifications=_split_items(job.preferred_skills),
        selling_points=_split_items(job.selling_points),
        location_and_mode=" · ".join(filter(None, [job.location or "地点待定", job.work_mode])),
        salary_and_benefits=job.salary or "面议",
    )


def _schema() -> dict[str, Any]:
    schema = JDContent.model_json_schema()
    schema["additionalProperties"] = False
    # 严格结构化输出要求 properties 中的每个键都出现在 required。
    # Pydantic 不会把带默认值的列表字段自动列为 required，因此在这里补齐。
    schema["required"] = list(schema.get("properties", {}))
    return schema


def _extract_jd_content(raw: str) -> JDContent:
    """接收严格 JSON，也兼容部分第三方网关返回的 Markdown 代码块。"""
    content = raw.strip()
    if content.startswith("```"):
        content = re.sub(r"^```(?:json)?\s*", "", content, flags=re.IGNORECASE)
        content = re.sub(r"\s*```$", "", content)
    try:
        return JDContent.model_validate_json(content)
    except ValidationError:
        start, end = content.find("{"), content.rfind("}")
        if start >= 0 and end > start:
            return JDContent.model_validate_json(content[start : end + 1])
        raise


def explain_llm_failure(error: Exception, model: str) -> str:
    """将模型网关错误转换为可操作且不暴露敏感信息的提示。"""
    detail = str(error).lower()
    if "no_channel" in detail or "没有可用的通道" in detail:
        return f"模型‘{model}’在当前网关没有可用通道；请使用网关 /models 返回的精确模型 ID。"
    if "401" in detail or "authentication" in detail or "invalid api key" in detail:
        return "模型密钥无效或已失效，请更新 LLM_API_KEY。"
    if "429" in detail or "rate limit" in detail or "insufficient" in detail:
        return "模型服务限流或额度不足，请检查账户额度后重试。"
    if "invalid schema" in detail or "response_format" in detail:
        return "模型网关拒绝了结构化输出 Schema，请检查模型的 JSON Schema 兼容性。"
    if isinstance(error, (APIConnectionError, APITimeoutError)):
        return "无法连接模型服务或请求超时，请检查 LLM_BASE_URL 和网络。"
    if isinstance(error, ValidationError):
        return f"模型‘{model}’没有返回符合 JD 结构的 JSON。"
    return f"模型‘{model}’调用失败，请检查模型 ID 和网关兼容性。"


def generate_jd(job: JobInput) -> tuple[JDContent, str]:
    api_key = os.getenv("LLM_API_KEY", "").strip()
    if not api_key:
        return enforce_source_facts(job, _demo_generate(job)), "demo"

    client_kwargs: dict[str, Any] = {"api_key": api_key}
    if os.getenv("LLM_BASE_URL", "").strip():
        client_kwargs["base_url"] = os.environ["LLM_BASE_URL"].strip()
    client = OpenAI(**client_kwargs, timeout=45, max_retries=1)
    model = os.getenv("LLM_MODEL", "gpt-5-mini")
    guide = PLATFORM_GUIDES.get(job.platform, PLATFORM_GUIDES["BOSS直聘"])
    system = f"""你是资深招聘专家。把输入改写为准确、可执行的中文招聘JD。
目标招聘平台：{job.platform}。
平台写作规则：{guide['instruction']}
职位名称、薪资福利、地点、工作方式、部门、职级和岗位亮点只能使用输入中的明确事实。
所有评价性表述也必须有输入依据。不得自行添加‘影响力大’‘行业领先’‘高速增长’
‘核心地位’‘发展空间大’‘团队氛围佳’等没有原始事实支持的判断。
如果岗位亮点信息不足，宁可保持简洁，不得进行营销性补写。
不得虚构薪资、福利、技术栈或公司承诺。区分必备项与加分项。
职责使用动词开头，要求尽量可验证。只返回符合JSON Schema的内容。"""
    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": json.dumps(job.model_dump(), ensure_ascii=False)},
            ],
            response_format={
                "type": "json_schema",
                "json_schema": {"name": "jd_content", "strict": True, "schema": _schema()},
            },
        )
        raw = response.choices[0].message.content or "{}"
        result = enforce_source_facts(job, _extract_jd_content(raw))
        return result, f"llm:{model}"
    except (APIStatusError, APIConnectionError, APITimeoutError, ValidationError, ValueError) as error:
        reason = explain_llm_failure(error, model)
        return enforce_source_facts(job, _demo_generate(job)), f"fallback:{reason}"


def render_jd(job: JobInput, jd: JDContent) -> str:
    def section(title: str, items: list[str], limit: int | None = None) -> list[str]:
        if not items:
            return []
        visible_items = items[:limit] if limit else items
        return [f"## {title}", *[f"{i}. {item}" for i, item in enumerate(visible_items, 1)], ""]

    platform = job.platform if job.platform in PLATFORM_GUIDES else "BOSS直聘"
    salary = job.salary or "面议"
    location_and_mode = source_location_and_mode(job)
    safe_selling_points = _split_items(job.selling_points)
    common_meta = [f"目标平台：{platform}"]
    if job.department:
        common_meta.append(f"部门：{job.department}")
    common_meta.append(f"工作地点与方式：{location_and_mode}")
    if job.seniority:
        common_meta.append(f"职级：{job.seniority}")
    common_meta.extend([f"薪资与福利：{salary}", ""])

    if platform == "BOSS直聘":
        lines = [f"# {job.job_title} ｜ {salary}", "", jd.job_summary, "", *common_meta]
        lines += section("你要负责", jd.responsibilities, 5)
        lines += section("我们希望你", jd.requirements, 5)
        lines += section("加分项", jd.preferred_qualifications, 3)
        lines += section("为什么值得加入", safe_selling_points, 3)
        lines += ["如果你与这个岗位匹配，欢迎直接沟通。"]
    elif platform == "猎聘":
        lines = [f"# {job.job_title}", "", *common_meta, "## 职位使命", jd.job_summary, ""]
        lines += section("核心职责", jd.responsibilities)
        lines += section("关键任职资格", jd.requirements)
        lines += section("优先条件", jd.preferred_qualifications)
        lines += section("职业机会", safe_selling_points)
    elif platform == "拉勾招聘":
        lines = [f"# 我们在找：{job.job_title}", "", jd.job_summary, "", *common_meta]
        lines += section("你将负责", jd.responsibilities)
        lines += section("我们希望你", jd.requirements)
        lines += section("加分项", jd.preferred_qualifications)
        lines += section("为什么加入", safe_selling_points)
    elif platform == "前程无忧":
        lines = [f"# {job.job_title}", "", *common_meta, "## 职位描述", jd.job_summary, ""]
        lines += section("岗位职责", jd.responsibilities)
        lines += section("任职资格", jd.requirements)
        lines += section("优先条件", jd.preferred_qualifications)
        lines += section("岗位亮点", safe_selling_points)
    elif platform == "公司官网":
        lines = [f"# {job.job_title}", "", *common_meta, "## 职位价值", jd.job_summary, ""]
        lines += section("岗位职责", jd.responsibilities)
        lines += section("任职要求", jd.requirements)
        lines += section("加分项", jd.preferred_qualifications)
        lines += section("岗位亮点", safe_selling_points)
    else:  # 智联招聘
        lines = [
            f"# {job.job_title}",
            "",
            *common_meta,
            "## 岗位概述",
            jd.job_summary,
            "",
        ]
        lines += section("岗位职责", jd.responsibilities)
        lines += section("任职要求", jd.requirements)
        lines += section("加分项", jd.preferred_qualifications)
        lines += section("岗位亮点", safe_selling_points)
    return "\n".join(lines).strip()


AGE_RISK_PATTERN = re.compile(
    r"(?:"
    r"\d{2}\s*(?:周?岁)?\s*(?:以下|以内|以下优先)"
    r"|年龄.{0,8}(?:不超过|不得超过|低于|小于)\s*\d{2}"
    r"|(?:90后|95后|00后)\s*(?:优先|限定|为主)"
    r"|(?:最好|尽量|原则上)\s*(?:不要|不宜|别)?\s*超过\s*\d{2}\s*(?:周?岁)?"
    r"|年轻(?:[、，,和且并]?\s*有活力)?的?候选人优先"
    r"|年轻人优先"
    r")",
    re.IGNORECASE,
)

RISK_RULES = [
    (AGE_RISK_PATTERN.pattern, "high", "合规性", "存在可能与岗位能力无直接关系的年龄限制", "删除年龄条件，改为可验证的能力、经验或工作成果要求。"),
    (r"男性优先|女性优先|限男|限女|只招男|只招女|未婚|未育|已婚已育", "high", "合规性", "包含性别或婚育状态限制", "删除性别和婚育状态要求。"),
    (r"本地户口|外地人不要|身高\s*1[.\d]+米以上|形象气质佳", "high", "合规性", "包含可能与岗位能力无关的身份或外观限制", "仅保留对完成工作确有必要的能力条件。"),
    (r"无条件加班|长期无偿加班|接受\s*996|必须随时加班", "high", "用工表述", "存在不合理的强制加班表述", "删除强制性措辞，如确有工作时段要求，如实说明班次和补偿机制。"),
    (r"保证晋升|保证加薪|保证年薪|绝不裁员|行业第一|绝对领先", "medium", "承诺与真实性", "存在无法验证或过度承诺", "改为有依据、可核实的事实描述。"),
    (r"面议", "low", "信息完整性", "薪资信息不透明", "如条件允许，补充薪资范围、币种和计薪周期。"),
]

RESPONSIBILITY_HEADERS = {"岗位职责", "你要负责", "你将负责", "核心职责"}
REQUIREMENT_HEADERS = {"任职要求", "任职资格", "我们希望你", "关键任职资格"}
UNVERIFIED_CLAIM_PATTERNS = [
    r"五险一金",
    r"年终奖",
    r"股票|期权",
    r"餐补|交通补贴|住房补贴",
    r"免费体检|带薪年假",
    r"上市公司|世界\s*500\s*强|头部企业|行业龙头",
]
UNSUPPORTED_PROMOTIONAL_PATTERNS = [
    r"薪资真实透明",
    r"发展空间大",
    r"无限发展空间",
    r"团队合作氛围佳",
    r"氛围(?:极佳|优秀|融洽|佳)",
    r"中心地段",
    r"交通便利",
    r"体现能力价值",
    r"高速增长",
    r"顶尖团队",
    r"行业领先",
    r"(?:技术|团队|工作)氛围(?:开放|自由|优秀|融洽|良好|佳)",
]


def inspect_risks(text: str) -> list[RiskIssue]:
    issues: list[RiskIssue] = []
    for pattern, level, category, reason, suggestion in RISK_RULES:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            issues.append(RiskIssue(level=level, text=match.group(0), reason=reason, suggestion=suggestion, category=category))
    responsibility_headers = r"^## (?:岗位职责|你要负责|你将负责|核心职责)$"
    requirement_headers = r"^## (?:任职要求|任职资格|我们希望你|关键任职资格)$"
    if not re.search(responsibility_headers, text, re.MULTILINE) or not re.search(requirement_headers, text, re.MULTILINE):
        issues.append(RiskIssue(level="high", text="结构缺失", reason="JD缺少职责或任职要求", suggestion="补充必要章节后再发送。", category="结构完整性"))
    return issues


def _normalise_text(value: str) -> str:
    return re.sub(r"[^0-9a-zA-Z一-鿿]+", "", value).lower()


def _section_items(text: str) -> tuple[list[str], list[str]]:
    responsibilities: list[str] = []
    requirements: list[str] = []
    current: list[str] | None = None
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if line.startswith("## "):
            title = line[3:].strip()
            current = responsibilities if title in RESPONSIBILITY_HEADERS else requirements if title in REQUIREMENT_HEADERS else None
            continue
        if current is not None and re.match(r"^(?:\d+[.\u3001)]|[-•])\s*", line):
            item = re.sub(r"^(?:\d+[.\u3001)]|[-•])\s*", "", line).strip()
            if item:
                current.append(item)
    return responsibilities, requirements


def _deduplicate_risk_issues(issues: list[RiskIssue]) -> list[RiskIssue]:
    unique: list[RiskIssue] = []
    seen: set[tuple[str, str, str]] = set()
    for issue in issues:
        key = (issue.level, issue.category, _normalise_text(issue.text))
        if key not in seen:
            seen.add(key)
            unique.append(issue)
    return unique


def assess_risks(job: JobInput, text: str) -> RiskAssessment:
    """结合原始岗位输入和最终 JD 进行可解释的发布前风险预估。"""
    issues = inspect_risks(text)
    source_text = "\n".join(
        str(value) for key, value in job.model_dump().items() if key != "platform" and value
    )
    normalised_source = _normalise_text(source_text)
    normalised_jd = _normalise_text(text)
    salary_update_candidate = find_salary_update_candidate(job, text)

    if salary_update_candidate:
        issues.append(
            RiskIssue(
                level="high",
                category="薪资事实待确认",
                text=salary_update_candidate,
                reason=(
                    "最终 JD 中出现了与已确认岗位信息不同的具体薪资，"
                    "直接编辑文案不会自动把金额变成已核实事实"
                ),
                suggestion="由 HR 点击页面上的薪资变更确认按钮，同步更新结构化薪资后再审批。",
            )
        )
    elif job.salary:
        if _normalise_text(job.salary) not in normalised_jd:
            issues.append(
                RiskIssue(
                    level="medium",
                    category="薪资一致性",
                    text="JD 中的薪资与已确认输入不一致",
                    reason="生成内容没有原样保留输入的薪资范围或计薪周期",
                    suggestion=f"人工核对并确认薪资应为‘{job.salary}’。",
                )
            )
    else:
        money_match = MONEY_PATTERN.search(text)
        if money_match and money_match.group(0) != "面议":
            issues.append(
                RiskIssue(
                    level="high",
                    category="真实性",
                    text=money_match.group(0),
                    reason="原始岗位信息没有提供薪资，但 JD 出现了具体金额",
                    suggestion="发布前由 HR 确认真实薪资；未确认时不要填入具体金额。",
                )
            )

    for pattern in UNVERIFIED_CLAIM_PATTERNS:
        for match in re.finditer(pattern, text, flags=re.IGNORECASE):
            if _normalise_text(match.group(0)) not in normalised_source:
                issues.append(
                    RiskIssue(
                        level="medium",
                        category="真实性",
                        text=match.group(0),
                        reason="该福利或公司信息没有出现在原始岗位输入中",
                        suggestion="请 HR 核实后保留；无法核实时删除。",
                    )
                )

    for pattern in UNSUPPORTED_PROMOTIONAL_PATTERNS:
        for match in re.finditer(pattern, text, flags=re.IGNORECASE):
            claim = match.group(0)
            if _normalise_text(claim) not in normalised_source:
                issues.append(
                    RiskIssue(
                        level="medium",
                        category="真实性",
                        text=claim,
                        reason="该评价或营销表述没有出现在原始岗位信息中",
                        suggestion="删除该表述，或由 HR 提供可核实依据。",
                    )
                )

    responsibilities, requirements = _section_items(text)
    work_mode_pattern = re.compile(r"现场办公|远程办公|混合办公|到岗办公|坐班")
    for requirement in requirements:
        work_mode_match = work_mode_pattern.search(requirement)
        if work_mode_match:
            issues.append(
                RiskIssue(
                    level="low",
                    category="内容结构",
                    text=requirement,
                    reason="工作方式属于岗位条件，不属于候选人的能力要求",
                    suggestion="从任职要求中删除，并只在‘工作地点与方式’中展示。",
                )
            )

    for responsibility in responsibilities:
        responsibility_key = _normalise_text(responsibility)
        for requirement in requirements:
            requirement_key = _normalise_text(requirement)
            if min(len(responsibility_key), len(requirement_key)) < 8:
                continue
            similarity = SequenceMatcher(None, responsibility_key, requirement_key).ratio()
            if similarity >= 0.78:
                issues.append(
                    RiskIssue(
                        level="medium",
                        category="内容重复",
                        text=f"职责‘{responsibility}’ / 要求‘{requirement}’",
                        reason="岗位职责与任职要求高度相似，容易混淆工作任务和候选人门槛",
                        suggestion="职责改为入职后的动作与产出，要求改为候选人已具备的能力与经验。",
                    )
                )

    all_items = [*responsibilities, *requirements]
    seen_items: dict[str, str] = {}
    for item in all_items:
        item_key = _normalise_text(item)
        if item_key in seen_items:
            issues.append(
                RiskIssue(
                    level="low",
                    category="内容重复",
                    text=item,
                    reason="同一条内容在 JD 中重复出现",
                    suggestion="合并重复条目，每条只表达一个清晰要点。",
                )
            )
        else:
            seen_items[item_key] = item

    years_match = re.search(r"(\d+)\s*年", job.experience)
    years = int(years_match.group(1)) if years_match else None
    if re.search(r"实习|初级", job.seniority) and years is not None and years >= 3:
        issues.append(
            RiskIssue(
                level="medium",
                category="条件矛盾",
                text=f"职级‘{job.seniority}’ / 经验‘{job.experience}’",
                reason="初级或实习职级与较高工作年限可能不匹配",
                suggestion="核对岗位职级，或降低工作年限门槛。",
            )
        )
    if re.search(r"高级|资深|专家|总监", job.seniority) and re.search(r"应届|无经验|经验不限", job.experience):
        issues.append(
            RiskIssue(
                level="medium",
                category="条件矛盾",
                text=f"职级‘{job.seniority}’ / 经验‘{job.experience}’",
                reason="高职级与无经验要求可能互相矛盾",
                suggestion="明确该岗位的实际责任级别和最低经验要求。",
            )
        )

    unclear_patterns = [
        (r"XXX|TBD|待补充|待确认|待定|暂缺", "存在未完成的占位内容"),
        (r"的的|了了|以及以及|负责负责|具备具备|要求要求", "存在明显的重复用词，可能是错别字"),
        (r"完成领导交办的?其他工作|有责任心|抗压能力强", "表述过于模糊，难以评估或验证"),
    ]
    for pattern, reason in unclear_patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            issues.append(
                RiskIssue(
                    level="low",
                    category="文本质量",
                    text=match.group(0),
                    reason=reason,
                    suggestion="人工校对，并改为明确、可验证的表述。",
                )
            )

    issues = _deduplicate_risk_issues(issues)
    if any(issue.level == "high" for issue in issues):
        overall_level = "高"
    elif any(issue.level == "medium" for issue in issues):
        overall_level = "中"
    else:
        overall_level = "低"
    return RiskAssessment(overall_level=overall_level, issues=issues)


def content_hash(text: str) -> str:
    return hashlib.sha256(text.strip().encode("utf-8")).hexdigest()
