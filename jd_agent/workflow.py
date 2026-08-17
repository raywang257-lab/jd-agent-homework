"""核心工作流 -- 需求抽取、JD 生成、风险检查、质量诊断及智能增强"""

from __future__ import annotations

import hashlib
import json
import os
import re
from difflib import SequenceMatcher
from typing import Any

from openai import APIConnectionError, APIStatusError, APITimeoutError, OpenAI
from pydantic import ValidationError

from .schemas import (
    ComparisonItem,
    ContentIssue,
    FieldIssue,
    JDContent,
    JobInput,
    KeywordInfo,
    QualityScore,
    RiskAssessment,
    RiskIssue,
    SalaryBenchmark,
    SkillGapItem,
    SmartSuggestion,
    SmartTip,
)

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

PLATFORM_OPTIONS = ["BOSS直聘", "智联招聘", "前程无忧", "拉勾", "猎聘", "脉脉"]

PLATFORM_DESCRIPTIONS: dict[str, str] = {
    "BOSS直聘": "直聊模式，适合快速触达候选人；标题和薪资是点击率核心。",
    "智联招聘": "传统综合平台，适合中高端岗位；正文结构化程度要求高。",
    "前程无忧": "覆盖面广，适合批量招聘；关键词匹配影响搜索曝光。",
    "拉勾": "互联网垂直平台，技术岗效果佳；福利和成长空间是亮点。",
    "猎聘": "中高端人才为主，适合管理岗和稀缺岗；职级和薪资需明确。",
    "脉脉": "职场社交推荐，适合内推和被动候选人；亮点和成长性更关键。",
}

PLATFORM_INSTRUCTIONS: dict[str, str] = {
    "BOSS直聘": "简短直接，突出真实薪资、地点、核心职责和沟通邀请。",
    "智联招聘": "采用标准结构，完整区分岗位概述、职责、要求、加分项和亮点。",
    "前程无忧": "使用正式稳健的职位说明语气，完整呈现职责、资格和工作条件。",
    "拉勾": "采用专业但有活力的互联网招聘语气，强调产品或技术挑战，不补写技术栈。",
    "猎聘": "面向中高端候选人，强调职位使命、业务影响和可验证成果。",
    "脉脉": "适合职场社交传播，突出真实机会与成长性，不使用夸张承诺。",
}

FIELD_QUESTIONS: dict[str, str] = {
    "job_title": "岗位名称是什么？（例如：高级 AI 产品经理）",
    "department": "这个岗位属于哪个部门或团队？",
    "location": "工作地点在哪个城市？是否支持远程？",
    "work_mode": "工作方式是现场办公、混合办公还是远程办公？",
    "seniority": "职级范围是什么？（例如：P7/P8、高级/资深/专家）",
    "experience": "需要几年相关经验？",
    "education": "学历要求是什么？",
    "salary": "薪资范围是多少？包含几薪？",
    "job_goal": "这个岗位要解决什么核心问题？衡量成功的指标是什么？",
    "responsibilities": "主要工作职责有哪些？",
    "required_skills": "必备的能力和技术有哪些？",
    "preferred_skills": "有哪些加分项？",
    "selling_points": "这个岗位和团队最吸引人的点是什么？",
    "platform": "主要在哪个招聘平台发布？",
}

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
    "preferred_skills": "加分能力",
    "selling_points": "团队或岗位亮点",
}

MONEY_PATTERN = re.compile(
    r"(?:\d+(?:[.,]\d+)?\s*(?:[kK]|千|万|元|人民币|美元|港币)|"
    r"月薪|年薪|时薪|日薪|薪资|工资|面议|\d+\s*薪)"
)
EDUCATION_PATTERN = re.compile(
    r"(?:博士|硕士|研究生|本科|大专|专科|高中|中专|初中|学历|学位|学士|学历不限|不限学历)"
)
EXPERIENCE_PATTERN = re.compile(
    r"(?:\d+\s*(?:[-–—~至到]\s*\d+\s*)?年(?:以上|以下|左右|以内)?|"
    r"工作经验|从业经验|应届|校招|社招|经验不限|无经验)"
)
ROLE_PATTERN = re.compile(
    r"(?:经理|工程师|设计师|运营|销售|专员|主管|总监|顾问|分析师|研究员|"
    r"开发|测试|架构师|科学家|助理|实习生|负责人|会计|出纳|教师|医生|护士|"
    r"律师|编辑|文案|客服|采购|产品|算法|行政|人事|财务|法务|市场|HR|CEO|CTO|CFO)",
    re.IGNORECASE,
)
ORG_PATTERN = re.compile(
    r"(?:部|中心|团队|组|事业群|事业部|办公室|平台|研究院|研究所|科|室|业务线|"
    r"研发|人力资源|财务|法务|市场|销售|运营|产品|设计)"
)
LEVEL_PATTERN = re.compile(
    r"(?:实习|初级|中级|高级|资深|专家|负责人|管理岗|主管|经理|总监|"
    r"[PMLT]\s*\d+|[一二三四五六七八九十0-9]+级|职级不限|不限)",
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

# 模糊表述关键词
VAGUE_PHRASES = [
    "熟悉", "了解", "良好", "较强", "优秀", "相关", "等", "之类",
    "一定的", "基本的", "深入理解", "熟练掌握",
]

# 不可验证表述
UNVERIFIABLE_PHRASES = [
    "具有良好沟通能力", "团队合作精神", "责任心强", "抗压能力强",
    "积极主动", "自我驱动", "学习能力强", "结果导向",
]

# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------

def content_hash(text: str) -> str:
    """计算文本内容哈希"""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def platform_description(platform: str) -> str:
    return PLATFORM_DESCRIPTIONS.get(platform, "通用招聘平台")


def _split_items(text: str) -> list[str]:
    """将多行/分号/编号文本拆分为列表"""
    if not text:
        return []
    lines = re.split(r"[\n;；]+", text)
    items = []
    for line in lines:
        cleaned = re.sub(r"^[\d一二三四五六七八九十]+[.、)]\s*", "", line.strip())
        cleaned = re.sub(r"^[•·\-\*]\s*", "", cleaned)
        if cleaned:
            items.append(cleaned)
    return items


def _clean_intake_item(text: str) -> str:
    """清理原始 JD 中的 Markdown/编号，但不增加新事实。"""
    cleaned = re.sub(r"^\s*(?:[-*•·]|\d+[.、)]|[一二三四五六七八九十]+[、.)])\s*", "", text)
    return cleaned.strip().rstrip("；;。")


def _markdown_sections(source: str) -> tuple[str, dict[str, list[str]]]:
    """从常见 Markdown JD 中提取标题和分区条目。"""
    section_aliases = {
        "responsibilities": ("岗位职责", "职位职责", "主要职责", "工作职责", "工作内容"),
        "required_skills": ("任职要求", "岗位要求", "职位要求", "必备能力", "任职资格"),
        "preferred_skills": ("加分项", "优先条件", "加分能力"),
        "selling_points": ("福利待遇", "岗位亮点", "团队亮点", "我们提供", "薪酬福利"),
    }
    title = ""
    current = ""
    sections: dict[str, list[str]] = {key: [] for key in section_aliases}

    for raw_line in source.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        heading = re.match(r"^#{1,6}\s+(.+?)\s*$", line)
        if heading:
            heading_text = heading.group(1).strip()
            if not title:
                candidate = re.sub(r"\s*[（(][^）)]*[）)]\s*$", "", heading_text).strip()
                if candidate and not any(alias in candidate for aliases in section_aliases.values() for alias in aliases):
                    title = candidate
            current = ""
            for field, aliases in section_aliases.items():
                if any(alias in heading_text for alias in aliases):
                    current = field
                    break
            continue
        if current:
            item = _clean_intake_item(line)
            if item:
                sections[current].append(item)

    return title, sections


def auto_polish_job(job: JobInput, style: str = "专业清晰") -> tuple[JobInput, set[str]]:
    """保守润色事实层：仅清理格式、去重并统一少量等义表达。"""
    data = job.model_dump()
    changed: set[str] = set()
    list_fields = ("responsibilities", "required_skills", "preferred_skills", "selling_points")

    for field in list_fields:
        original = str(data.get(field, ""))
        seen: set[str] = set()
        polished_items: list[str] = []
        for raw_item in re.split(r"[\n；;]+", original):
            item = _clean_intake_item(raw_item)
            if not item:
                continue
            if field == "responsibilities":
                item = re.sub(r"^负责需求分析(?=[，,])", "开展需求分析", item)
                item = item.replace("算法、研发和业务团队", "算法、研发与业务团队")
            normalized = re.sub(r"\s+", "", item).rstrip("。")
            if normalized in seen:
                continue
            seen.add(normalized)
            polished_items.append(item)
        polished = "\n".join(polished_items)
        if polished != original.strip():
            data[field] = polished
            changed.add(field)

    polished_job = JobInput(**data)
    return polished_job, changed


def _schema() -> dict[str, Any]:
    """返回严格的 JDContent JSON Schema，供兼容网关和测试复用。"""
    schema = JDContent.model_json_schema()
    schema["additionalProperties"] = False
    schema["required"] = list(schema.get("properties", {}))
    return schema


def explain_llm_failure(error: Exception, model: str) -> str:
    """将常见模型网关错误转换成可操作且不泄露密钥的提示。"""
    detail = str(error).lower()
    if "no_channel" in detail or "没有可用" in detail:
        return f"模型‘{model}’在当前网关没有可用通道；请使用网关 /models 返回的精确模型 ID。"
    if "401" in detail or "authentication" in detail or "invalid api key" in detail:
        return "模型密钥无效或已失效，请更新 LLM_API_KEY。"
    if "429" in detail or "rate limit" in detail:
        return "模型服务限流或额度不足，请稍后重试。"
    return f"模型‘{model}’调用失败，请检查模型 ID、网关地址和接口模式。"


# ---------------------------------------------------------------------------
# LLM 调用
# ---------------------------------------------------------------------------

def _call_llm(system_prompt: str, user_prompt: str, max_tokens: int = 2000) -> tuple[str, str]:
    """调用 LLM，返回 (response_text, mode)。失败时抛出 ValueError。"""
    api_key = os.getenv("LLM_API_KEY", "").strip()
    if not api_key:
        raise ValueError("未配置 LLM_API_KEY")

    base_url = os.getenv("LLM_BASE_URL", "").strip() or None
    model = os.getenv("LLM_MODEL", "gpt-5-mini")
    api_mode = os.getenv("LLM_API_MODE", "chat").strip().lower()

    client = OpenAI(api_key=api_key, base_url=base_url)

    if api_mode == "responses":
        stream = client.responses.create(
            model=model,
            input=[
                {
                    "role": "system",
                    "content": [{"type": "input_text", "text": system_prompt}],
                },
                {
                    "role": "user",
                    "content": [{"type": "input_text", "text": user_prompt}],
                },
            ],
            max_output_tokens=max_tokens,
            stream=True,
        )
        parts = [
            event.delta
            for event in stream
            if getattr(event, "type", "") == "response.output_text.delta"
            and getattr(event, "delta", "")
        ]
    elif api_mode == "chat":
        stream = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            max_tokens=max_tokens,
            temperature=0.3,
            stream=True,
        )
        parts = []
        for chunk in stream:
            if not getattr(chunk, "choices", None):
                continue
            content = getattr(chunk.choices[0].delta, "content", "")
            if content:
                parts.append(content)
    else:
        raise ValueError("LLM_API_MODE 只能是 chat 或 responses")

    text = "".join(parts).strip()
    if not text:
        raise ValueError("模型流式请求未返回文本内容")
    return text, f"llm:{model}"


def _create_structured_output(
    client: OpenAI,
    model: str,
    system: str,
    user_content: str,
    schema_name: str,
    schema: dict[str, Any],
) -> str:
    """兼容 Chat Completions 与 Responses 的严格结构化输出。"""
    api_mode = os.getenv("LLM_API_MODE", "chat").strip().lower()
    if api_mode == "responses":
        stream = client.responses.create(
            model=model,
            input=[
                {"role": "system", "content": [{"type": "input_text", "text": system}]},
                {"role": "user", "content": [{"type": "input_text", "text": user_content}]},
            ],
            text={
                "format": {
                    "type": "json_schema",
                    "name": schema_name,
                    "strict": True,
                    "schema": schema,
                }
            },
            max_output_tokens=4096,
            stream=True,
        )
        content = "".join(
            getattr(event, "delta", "")
            for event in stream
            if getattr(event, "type", "") == "response.output_text.delta"
        ).strip()
        if not content:
            raise ValueError("Responses 接口未返回可解析的文本。")
        return content
    if api_mode != "chat":
        raise ValueError("LLM_API_MODE 只能是 chat 或 responses。")
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user_content},
        ],
        response_format={
            "type": "json_schema",
            "json_schema": {"name": schema_name, "strict": True, "schema": schema},
        },
    )
    return response.choices[0].message.content or "{}"


def _extract_jd_content(raw: str) -> JDContent:
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


# ---------------------------------------------------------------------------
# 需求抽取
# ---------------------------------------------------------------------------

def extract_job_input(
    source: str,
    existing: JobInput | None = None,
    polish_style: str = "专业清晰",
) -> tuple[JobInput, str]:
    """从原始文本抽取结构化岗位信息，返回 (JobInput, mode)"""
    source = source.strip()
    if not source:
        raise ValueError("请提供原始招聘材料")

    api_key = os.getenv("LLM_API_KEY", "").strip()
    if api_key:
        try:
            extracted, mode = _extract_with_llm(source, existing, polish_style)
            polished, _ = auto_polish_job(extracted, polish_style)
            return polished, mode
        except Exception as exc:
            extracted = _extract_with_rules(source, existing)
            polished, _ = auto_polish_job(extracted, polish_style)
            return polished, f"fallback:{exc}"

    extracted = _extract_with_rules(source, existing)
    polished, _ = auto_polish_job(extracted, polish_style)
    return polished, "offline:rules"


def _extract_with_llm(
    source: str,
    existing: JobInput | None,
    polish_style: str = "专业清晰",
) -> tuple[JobInput, str]:
    """使用 LLM 抽取"""
    existing_json = existing.model_dump() if existing else {}

    system_prompt = (
        "你是招聘需求分析专家。从用户提供的原始招聘材料中抽取结构化岗位信息。"
        "只抽取原文明确提到的信息，不要臆测。如果原文没有提到某个字段，留空。"
        "返回 JSON 格式，字段包括：job_title, department, location, work_mode, "
        "seniority, experience, education, salary, job_goal, responsibilities, "
        "required_skills, preferred_skills, selling_points, platform。"
        "work_mode 取值：现场办公/混合办公/远程办公。"
        "responsibilities/required_skills/preferred_skills/selling_points 用换行分隔多条。"
        "如果提供了 existing 信息，在用户未提及新值时保留已有值。"
        f"按‘{polish_style}’风格整理，但只能改写原文已有事实，不得新增事实、条件或承诺。"
    )
    user_prompt = f"已有信息：{json.dumps(existing_json, ensure_ascii=False)}\n\n原始材料：\n{source}"

    text, mode = _call_llm(system_prompt, user_prompt)
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)

    data = json.loads(text)
    job = JobInput(
        **{
            key: str(value).strip()
            for key, value in data.items()
            if key in JobInput.model_fields
        }
    )
    return job, mode


def _extract_with_rules(source: str, existing: JobInput | None) -> JobInput:
    """离线规则抽取"""
    data: dict[str, Any] = {}
    if existing:
        data = existing.model_dump()

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
        match = re.search(rf"(?:{labels})\s*[:：]\s*([^\n]+)", source, flags=re.IGNORECASE)
        if match:
            data[field] = match.group(1).strip(" ，,;；。")

    lines = source.split("\n")
    full_text = source

    markdown_title, markdown_sections = _markdown_sections(source)
    if markdown_title:
        data["job_title"] = markdown_title
    for field, items in markdown_sections.items():
        if items:
            data[field] = "\n".join(items)

    # 岗位名称
    title_match = re.search(r"(?:招|招聘|招募|寻找)\s*(?:一名|一个|一位)?\s*(.+?)(?:[，,。；;]|\s+负责|\s+要求|\s+岗位)", full_text)
    if title_match and not data.get("job_title"):
        data["job_title"] = title_match.group(1).strip()

    # 工作地点
    city_match = re.search(r"(北京|上海|广州|深圳|杭州|成都|南京|武汉|西安|苏州|长沙|重庆|天津|青岛|大连|厦门|无锡|宁波|福州|合肥|济南|郑州|哈尔滨|沈阳|昆明|贵阳|南昌|太原|兰州|石家庄|呼和浩特|海口|银川|西宁|拉萨|乌鲁木齐|南宁|香港|澳门|台湾)", full_text)
    if city_match:
        data["location"] = city_match.group(1)

    # 工作方式
    if "现场办公" in full_text or "坐班" in full_text:
        data["work_mode"] = "现场办公"
    elif "混合办公" in full_text or "hybrid" in full_text.lower():
        data["work_mode"] = "混合办公"
    elif "远程办公" in full_text or "remote" in full_text.lower() or "居家" in full_text:
        data["work_mode"] = "远程办公"

    # 经验要求
    exp_range_match = re.search(r"(\d+)\s*[-–—~至到]\s*(\d+)\s*年", full_text)
    exp_match = re.search(r"(\d+)\s*年以上", full_text)
    if exp_range_match:
        data["experience"] = f"{exp_range_match.group(1)}-{exp_range_match.group(2)}年"
    elif exp_match:
        data["experience"] = f"{exp_match.group(1)}年以上"

    # 学历要求
    for edu in ["博士", "硕士", "研究生", "本科", "大专", "不限"]:
        if edu in full_text and ("学历" in full_text or "以上" in full_text or "毕业" in full_text):
            normalized_edu = edu if edu != "研究生" else "硕士"
            data["education"] = f"{normalized_edu}及以上" if f"{edu}及以上" in full_text else normalized_edu
            break

    # 薪资
    salary_match = re.search(r"(\d+[\-–到]\d+[Kk万])(?:[·]?(\d+)薪)?", full_text)
    if salary_match:
        salary = salary_match.group(0)
        data["salary"] = salary

    # 部门
    dept_match = re.search(r"(?:属于|隶属|所在)\s*(.+?)(?:部|组|中心|团队|部门)", full_text)
    if dept_match:
        data["department"] = dept_match.group(0).replace("属于", "").replace("隶属", "").replace("所在", "").strip()

    # 职责
    resp_match = re.search(r"(?:负责|工作内容|岗位职责|主要职责)[:：]?\s*(.+?)(?:任职要求|岗位要求|要求|加分|薪资|亮点|我们提供|$)", full_text, re.DOTALL)
    if resp_match and not data.get("responsibilities"):
        data["responsibilities"] = resp_match.group(1).strip()[:500]

    # 必备技能
    skills_match = re.search(r"(?:要求|任职要求|岗位要求|必备|需要|必须)[:：]?\s*(.+?)(?:加分|优先|薪资|亮点|我们提供|$)", full_text, re.DOTALL)
    if skills_match and not data.get("required_skills"):
        data["required_skills"] = skills_match.group(1).strip()[:500]

    # 加分项
    pref_match = re.search(r"(?:加分|优先|preferred|nice.?to.?have)[:：]?\s*(.+?)(?:薪资|亮点|我们提供|$)", full_text, re.DOTALL | re.IGNORECASE)
    if pref_match and not data.get("preferred_skills"):
        data["preferred_skills"] = pref_match.group(1).strip()[:300]

    # 亮点
    highlight_match = re.search(r"(?:亮点|吸引力|我们提供|福利待遇|优势)[:：]?\s*(.+?)(?:$)", full_text, re.DOTALL)
    if highlight_match and not data.get("selling_points"):
        data["selling_points"] = highlight_match.group(1).strip()[:300]

    # 岗位目标
    goal_match = re.search(r"(?:目标|职责目标|核心|主要)(?:是|为|包括)?\s*(.+?)(?:负责|要求|薪资|亮点|$)", full_text, re.DOTALL)
    if goal_match and not data.get("job_goal"):
        data["job_goal"] = goal_match.group(1).strip()[:200]

    return JobInput(**data)


def _demo_extract_job_input(raw_text: str, existing: JobInput | None = None) -> JobInput:
    """兼容旧调用名：使用当前离线规则完成事实抽取。"""
    return _extract_with_rules(raw_text, existing)


# ---------------------------------------------------------------------------
# 冲突检测
# ---------------------------------------------------------------------------

def detect_intake_conflicts(source: str) -> list[str]:
    """检测原始材料中的潜在冲突信息"""
    conflicts: list[str] = []

    # 薪资冲突
    salaries = re.findall(
        r"\d+(?:\.\d+)?\s*[Kk万]?\s*[-–—~至到]\s*"
        r"\d+(?:\.\d+)?\s*[Kk万](?:\s*[·x×*]?\s*\d+\s*薪)?",
        source,
    )
    if len(set(salaries)) > 1:
        conflicts.append(f"原文出现多个薪资范围：{'、'.join(dict.fromkeys(salaries))}，请确认最终薪资。")

    # 地点冲突
    cities = re.findall(r"(北京|上海|广州|深圳|杭州|成都|南京|武汉|西安|苏州|长沙|重庆)", source)
    if len(set(cities)) > 1:
        conflicts.append(f"原文出现多个工作城市：{'、'.join(dict.fromkeys(cities))}，请确认最终工作地点。")

    # 工作方式冲突
    modes = []
    if "现场" in source or "坐班" in source:
        modes.append("现场办公")
    if "混合" in source:
        modes.append("混合办公")
    if "远程" in source or "remote" in source.lower():
        modes.append("远程办公")
    if len(modes) > 1:
        conflicts.append(f"原文同时出现多种工作方式：{', '.join(modes)}，请确认最终工作方式。")

    # 经验冲突
    exps = re.findall(r"(\d+)\s*年以上", source)
    if len(set(exps)) > 1:
        conflicts.append(f"原文出现多个经验要求：{', '.join(set(exps))}年以上，请确认最终经验要求。")

    return conflicts


# ---------------------------------------------------------------------------
# JD 生成
# ---------------------------------------------------------------------------

def generate_jd(job: JobInput) -> tuple[JDContent, str]:
    """生成 JD 内容，返回 (JDContent, mode)"""
    api_key = os.getenv("LLM_API_KEY", "").strip()
    if api_key:
        try:
            jd, mode = _generate_jd_with_llm(job)
            return enforce_source_facts(job, jd), mode
        except Exception as exc:
            return enforce_source_facts(job, _generate_jd_offline(job)), f"fallback:{explain_llm_failure(exc, os.getenv('LLM_MODEL', 'gpt-5-mini'))}"

    return enforce_source_facts(job, _generate_jd_offline(job)), "offline:template"


def _generate_jd_with_llm(job: JobInput) -> tuple[JDContent, str]:
    """使用 LLM 生成 JD"""
    system_prompt = (
        "你是资深招聘文案专家。根据结构化岗位信息生成一份专业、清晰、有吸引力的 JD。"
        "要求：1) 职责和技能用列表形式；2) 语言专业但不生硬；"
        "3) 亮点要有具体吸引力；4) 不要添加未提供的信息。"
        "返回 JSON 格式的 JDContent，字段包括："
        "job_title, department, location, work_mode, seniority, experience, education, "
        "salary_and_benefits, job_goal, responsibilities(list), required_skills(list), "
        "preferred_skills(list), selling_points(list), platform。"
    )
    user_prompt = f"岗位信息：\n{json.dumps(job.model_dump(), ensure_ascii=False, indent=2)}"

    client_kwargs: dict[str, Any] = {"api_key": os.environ["LLM_API_KEY"]}
    if os.getenv("LLM_BASE_URL", "").strip():
        client_kwargs["base_url"] = os.environ["LLM_BASE_URL"].strip()
    client = OpenAI(**client_kwargs)
    model = os.getenv("LLM_MODEL", "gpt-5-mini")
    raw = _create_structured_output(client, model, system_prompt, user_prompt, "jd_content", _schema())
    return _extract_jd_content(raw), f"llm:{model}"


def _generate_jd_offline(job: JobInput) -> JDContent:
    """离线模板生成 JD"""
    responsibilities = _split_items(job.responsibilities) if job.responsibilities else []
    if not responsibilities and job.job_goal:
        responsibilities = [f"负责{job.job_goal}"]

    required_skills = _split_items(job.required_skills) if job.required_skills else []
    preferred_skills = _split_items(job.preferred_skills) if job.preferred_skills else []
    selling_points = _split_items(job.selling_points) if job.selling_points else []

    salary_str = job.salary or "面议"

    return JDContent(
        job_title=job.job_title,
        department=job.department,
        location=job.location,
        work_mode=job.work_mode,
        seniority=job.seniority,
        experience=job.experience,
        education=job.education,
        salary_and_benefits=salary_str,
        job_goal=job.job_goal,
        responsibilities=responsibilities,
        required_skills=required_skills,
        preferred_skills=preferred_skills,
        selling_points=selling_points,
        platform=job.platform,
    )


def enforce_source_facts(job: JobInput, jd: JDContent) -> JDContent:
    """把生成内容中的关键事实恢复为 HR 已确认的 JobInput 值。"""
    return jd.model_copy(
        update={
            "job_title": job.job_title,
            "department": job.department,
            "location": job.location,
            "work_mode": job.work_mode,
            "seniority": job.seniority,
            "experience": job.experience,
            "education": job.education,
            "salary_and_benefits": job.salary or "面议",
            "selling_points": _split_items(job.selling_points),
            "location_and_mode": " · ".join(value for value in (job.location, job.work_mode) if value),
            "requirements": list(jd.requirements or jd.required_skills),
            "required_skills": list(jd.requirements or jd.required_skills),
            "preferred_qualifications": list(jd.preferred_qualifications or jd.preferred_skills),
            "preferred_skills": list(jd.preferred_qualifications or jd.preferred_skills),
            "platform": job.platform,
        }
    )


def render_jd(job: JobInput, jd: JDContent) -> str:
    """按招聘平台渲染文案，并强制使用 JobInput 中的关键事实。"""
    def section(title: str, items: list[str], limit: int | None = None) -> list[str]:
        if not items:
            return []
        visible = items[:limit] if limit else items
        return [f"## {title}", *[f"{index}. {item}" for index, item in enumerate(visible, 1)], ""]

    platform = job.platform if job.platform in PLATFORM_OPTIONS else "BOSS直聘"
    salary = job.salary or "面议"
    location_and_mode = " · ".join(value for value in (job.location, job.work_mode) if value)
    summary = jd.job_summary or jd.job_goal or job.job_goal
    requirements = list(jd.requirements or jd.required_skills)
    preferred = list(jd.preferred_qualifications or jd.preferred_skills)
    selling_points = _split_items(job.selling_points)
    common_meta = [f"目标平台：{platform}"]
    if job.department:
        common_meta.append(f"部门：{job.department}")
    common_meta.append(f"工作地点与方式：{location_and_mode}")
    if job.seniority:
        common_meta.append(f"职级：{job.seniority}")
    common_meta.extend([f"薪资与福利：{salary}", ""])

    if platform == "BOSS直聘":
        lines = [f"# {job.job_title} ｜ {salary}", "", summary, "", *common_meta]
        lines += section("你要负责", jd.responsibilities, 5)
        lines += section("我们希望你", requirements, 5)
        lines += section("加分项", preferred, 3)
        lines += section("为什么值得加入", selling_points, 3)
        lines += ["如果你与这个岗位匹配，欢迎直接沟通。"]
    elif platform == "猎聘":
        lines = [f"# {job.job_title}", "", *common_meta, "## 职位使命", summary, ""]
        lines += section("核心职责", jd.responsibilities)
        lines += section("关键任职资格", requirements)
        lines += section("优先条件", preferred)
        lines += section("职业机会", selling_points)
    elif platform == "拉勾":
        lines = [f"# 我们在找：{job.job_title}", "", summary, "", *common_meta]
        lines += section("你将负责", jd.responsibilities)
        lines += section("我们希望你", requirements)
        lines += section("加分项", preferred)
        lines += section("为什么加入", selling_points)
    elif platform == "前程无忧":
        lines = [f"# {job.job_title}", "", *common_meta, "## 职位描述", summary, ""]
        lines += section("岗位职责", jd.responsibilities)
        lines += section("任职资格", requirements)
        lines += section("优先条件", preferred)
        lines += section("岗位亮点", selling_points)
    elif platform == "脉脉":
        lines = [f"# {job.job_title}｜机会介绍", "", summary, "", *common_meta]
        lines += section("你会负责", jd.responsibilities)
        lines += section("我们关注", requirements)
        lines += section("加分经历", preferred)
        lines += section("岗位机会", selling_points)
    else:
        lines = [f"# {job.job_title}", "", *common_meta, "## 岗位概述", summary, ""]
        lines += section("岗位职责", jd.responsibilities)
        lines += section("任职要求", requirements)
        lines += section("加分项", preferred)
        lines += section("岗位亮点", selling_points)
    return "\n".join(lines).strip()


# ---------------------------------------------------------------------------
# 完整性检查
# ---------------------------------------------------------------------------

def _field_issue(field: str, label: str, message: str) -> FieldIssue:
    return FieldIssue(field=field, label=label, message=message, question=FIELD_QUESTIONS[field])


def inspect_field_relevance(job: JobInput) -> list[FieldIssue]:
    """确定性检查字段是否放错位置，防止错类信息进入事实层。"""
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
        or not re.search(r"负责|制定|推动|协调|管理|建立|分析|优化|完成|跟进|维护|设计|开发|交付|运营|规划", responsibilities)
    ):
        issues.append(_field_issue("responsibilities", "主要职责", "请用动作描述具体任务和产出。"))

    skill_pattern = r"能力|经验|熟悉|掌握|了解|精通|具备|能够|技能|工具|证书|资格|专业|沟通|分析|管理|开发|设计|产品|技术|英语|RAG|Agent|Python"
    required_skills = job.required_skills.strip()
    if required_skills and (len(required_skills) < 6 or not re.search(skill_pattern, required_skills, re.IGNORECASE)):
        issues.append(_field_issue("required_skills", "必备能力", "请填候选人必须具备的能力、工具经验或专业资格。"))

    preferred_skills = job.preferred_skills.strip()
    if preferred_skills and (len(preferred_skills) < 4 or not re.search(skill_pattern, preferred_skills, re.IGNORECASE)):
        issues.append(_field_issue("preferred_skills", "加分能力", "请填与候选人能力或相关项目经验有关的加分项。"))

    selling_points = job.selling_points.strip()
    if selling_points and (
        len(selling_points) < 4
        or not re.search(r"成长|发展|机会|参与|核心|福利|空间|团队|平台|业务|项目|技术|建设|从\s*0\s*到\s*1", selling_points)
    ):
        issues.append(_field_issue("selling_points", "岗位亮点", "请填写真实的成长、项目、团队或业务亮点。"))
    return issues


def inspect_completeness(job: JobInput) -> tuple[list[str], list[str], list[str]]:
    """返回缺失必填项、缺失建议项和对应追问。"""
    missing_required = [label for key, label in REQUIRED_FIELDS.items() if not getattr(job, key).strip()]
    missing_recommended = [label for key, label in RECOMMENDED_FIELDS.items() if not getattr(job, key).strip()]
    missing_keys = [key for key in REQUIRED_FIELDS if not getattr(job, key).strip()]
    missing_keys += [key for key in RECOMMENDED_FIELDS if not getattr(job, key).strip()]
    questions = [FIELD_QUESTIONS[key] for key in missing_keys]
    if job.required_skills and len(job.required_skills.strip()) < 12:
        questions.append("必备能力较笼统：请补充工具、业务场景或可验证的产出要求。")
    return missing_required, missing_recommended, questions


# ---------------------------------------------------------------------------
# 追问问题优先级
# ---------------------------------------------------------------------------

def prioritise_follow_up_questions(job: JobInput, limit: int = 4) -> list[str]:
    """只返回当前最值得问的少量问题。"""
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
    return list(dict.fromkeys(questions))[: max(1, limit)]


# ---------------------------------------------------------------------------
# 岗位目标建议
# ---------------------------------------------------------------------------

def suggest_job_goal(source: str) -> str:
    """只在原文有明确目标语言时返回原文支持的候选。"""
    if not source:
        return ""

    # 从原文中提取可能的岗位目标描述
    goal_patterns = [
        r"(?:这个岗位要|该岗位需要|核心目标是)(负责[^。！？\n]+)",
        r"(负责[^。！？\n]+完整链路)",
        r"(推动[^。！？\n]+(?:落地|建设|增长|提升|优化))",
    ]

    for pattern in goal_patterns:
        match = re.search(pattern, source)
        if match:
            return match.group(1).strip()

    return ""


# ---------------------------------------------------------------------------
# 风险评估
# ---------------------------------------------------------------------------

AGE_RISK_PATTERN = re.compile(
    r"(?:\d{2}\s*(?:周?岁)?\s*(?:以下|以内|以下优先)"
    r"|年龄.{0,8}(?:不超过|不得超过|低于|小于)\s*\d{2}"
    r"|(?:90后|95后|00后)\s*(?:优先|限定|为主)"
    r"|(?:最好|尽量|原则上)\s*(?:不要|不宜|别)?\s*超过\s*\d{2}\s*(?:周?岁)?"
    r"|年轻(?:[、，,和且并]?\s*有活力)?的?候选人优先|年轻人优先)",
    re.IGNORECASE,
)
RISK_RULES = [
    (AGE_RISK_PATTERN.pattern, "high", "合规性", "存在可能与岗位能力无直接关系的年龄限制", "删除年龄条件，改为可验证的能力、经验或成果要求。"),
    (r"男性优先|女性优先|限男|限女|只招男|只招女|未婚|未育|已婚已育", "high", "合规性", "包含性别或婚育状态限制", "删除性别和婚育状态要求。"),
    (r"本地户口|外地人不要|形象气质佳", "high", "合规性", "包含可能与岗位能力无关的身份或外观限制", "仅保留完成工作所需的能力条件。"),
    (r"无条件加班|长期无偿加班|接受\s*996|必须随时加班", "high", "用工表述", "存在不合理的强制加班表述", "删除强制性措辞并如实说明班次与补偿。"),
    (r"保证晋升|保证加薪|保证年薪|绝不裁员|行业第一|绝对领先", "medium", "承诺与真实性", "存在无法验证或过度承诺", "改为有依据、可核实的事实描述。"),
    (r"面议", "low", "信息完整性", "薪资信息不透明", "如条件允许，补充薪资范围、币种和计薪周期。"),
]
RESPONSIBILITY_HEADERS = {"岗位职责", "你要负责", "你将负责", "核心职责", "你会负责"}
REQUIREMENT_HEADERS = {"任职要求", "任职资格", "我们希望你", "关键任职资格", "我们关注"}
UNVERIFIED_CLAIM_PATTERNS = [r"五险一金", r"年终奖", r"股票|期权", r"餐补|交通补贴|住房补贴", r"免费体检|带薪年假", r"上市公司|世界\s*500\s*强|头部企业|行业龙头"]
UNSUPPORTED_PROMOTIONAL_PATTERNS = [
    r"薪资真实透明", r"发展空间大", r"无限发展空间", r"团队合作氛围佳",
    r"氛围(?:极佳|优秀|融洽|佳)", r"中心地段", r"交通便利", r"体现能力价值",
    r"高速增长", r"顶尖团队", r"行业领先", r"(?:技术|团队|工作)氛围(?:开放|自由|优秀|融洽|良好|佳)",
]


def inspect_risks(text: str) -> list[RiskIssue]:
    issues: list[RiskIssue] = []
    for pattern, level, category, reason, suggestion in RISK_RULES:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            issues.append(RiskIssue(level=level, text=match.group(0), reason=reason, suggestion=suggestion, category=category))
    responsibility_headers = r"^## (?:岗位职责|你要负责|你将负责|核心职责|你会负责)$"
    requirement_headers = r"^## (?:任职要求|任职资格|我们希望你|关键任职资格|我们关注)$"
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
        if current is not None and re.match(r"^(?:\d+[.、)]|[-•])\s*", line):
            item = re.sub(r"^(?:\d+[.、)]|[-•])\s*", "", line).strip()
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


def assess_risks(job: JobInput, jd_text: str) -> RiskAssessment:
    """结合已确认事实与最终 JD 执行发布前门禁。"""
    issues = inspect_risks(jd_text)
    source_text = "\n".join(str(value) for key, value in job.model_dump().items() if key != "platform" and value)
    normalised_source = _normalise_text(source_text)
    normalised_jd = _normalise_text(jd_text)
    salary_update_candidate = find_salary_update_candidate(job, jd_text)
    if salary_update_candidate:
        issues.append(RiskIssue(level="high", category="薪资事实待确认", text=salary_update_candidate, reason="最终 JD 出现与已确认岗位信息不同的具体薪资。", suggestion="由 HR 确认并同步结构化薪资后再审批。"))
    elif job.salary and _normalise_text(job.salary) not in normalised_jd:
        issues.append(RiskIssue(level="medium", category="薪资一致性", text="JD 中的薪资与已确认输入不一致", reason="生成内容没有原样保留输入薪资。", suggestion=f"人工核对薪资应为‘{job.salary}’。"))
    elif not job.salary:
        money_match = MONEY_PATTERN.search(jd_text)
        if money_match and money_match.group(0) != "面议":
            issues.append(RiskIssue(level="high", category="真实性", text=money_match.group(0), reason="原始岗位信息没有提供薪资，但 JD 出现具体金额。", suggestion="由 HR 确认真实薪资后再发布。"))

    for pattern in UNVERIFIED_CLAIM_PATTERNS:
        for match in re.finditer(pattern, jd_text, flags=re.IGNORECASE):
            if _normalise_text(match.group(0)) not in normalised_source:
                issues.append(RiskIssue(level="medium", category="真实性", text=match.group(0), reason="该福利或公司信息没有出现在原始岗位输入中。", suggestion="请 HR 核实后保留；无法核实时删除。"))
    for pattern in UNSUPPORTED_PROMOTIONAL_PATTERNS:
        for match in re.finditer(pattern, jd_text, flags=re.IGNORECASE):
            claim = match.group(0)
            if _normalise_text(claim) not in normalised_source:
                issues.append(RiskIssue(level="medium", category="真实性", text=claim, reason="该评价或营销表述没有出现在原始岗位信息中。", suggestion="删除该表述，或由 HR 提供可核实依据。"))

    responsibilities, requirements = _section_items(jd_text)
    for requirement in requirements:
        if re.search(r"现场办公|远程办公|混合办公|到岗办公|坐班", requirement):
            issues.append(RiskIssue(level="low", category="内容结构", text=requirement, reason="工作方式不属于候选人的能力要求。", suggestion="只在工作地点与方式中展示。"))
    for responsibility in responsibilities:
        responsibility_key = _normalise_text(responsibility)
        for requirement in requirements:
            requirement_key = _normalise_text(requirement)
            if min(len(responsibility_key), len(requirement_key)) >= 8 and SequenceMatcher(None, responsibility_key, requirement_key).ratio() >= 0.78:
                issues.append(RiskIssue(level="medium", category="内容重复", text=f"职责‘{responsibility}’ / 要求‘{requirement}’", reason="职责与任职要求高度相似。", suggestion="区分入职后的任务和候选人已有能力。"))

    years_match = re.search(r"(\d+)\s*年", job.experience)
    years = int(years_match.group(1)) if years_match else None
    if re.search(r"实习|初级", job.seniority) and years is not None and years >= 3:
        issues.append(RiskIssue(level="medium", category="条件矛盾", text=f"职级‘{job.seniority}’ / 经验‘{job.experience}’", reason="初级职级与较高工作年限可能不匹配。", suggestion="核对职级或年限门槛。"))
    if re.search(r"高级|资深|专家|总监", job.seniority) and re.search(r"应届|无经验|经验不限", job.experience):
        issues.append(RiskIssue(level="medium", category="条件矛盾", text=f"职级‘{job.seniority}’ / 经验‘{job.experience}’", reason="高职级与无经验要求矛盾。", suggestion="明确实际责任级别和最低经验。"))

    issues = _deduplicate_risk_issues(issues)
    overall = "高" if any(i.level == "high" for i in issues) else "中" if any(i.level == "medium" for i in issues) else "低"
    return RiskAssessment(overall_level=overall, issues=issues)


# ---------------------------------------------------------------------------
# 薪资同步
# ---------------------------------------------------------------------------

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
    rf"(?:{SALARY_RANGE_PATTERN.pattern}|{_SALARY_NUMBER}\s*{_SALARY_UNIT}{_SALARY_PERIOD}{_SALARY_MONTHS})",
    re.IGNORECASE,
)


def _format_salary_candidate(value: str) -> str:
    candidate = re.sub(r"\s+", "", value.strip(" ，,;；。"))
    candidate = re.sub(r"(?:-|—|~|至|到)", "–", candidate)
    return re.sub(r"k", "K", candidate, flags=re.IGNORECASE)


def find_salary_update_candidate(job: JobInput, jd_text: str) -> str:
    """识别最终 JD 中与已确认事实不同的具体薪资。"""
    source_salary = _normalise_text(job.salary)
    seen: set[str] = set()
    for match in CONCRETE_SALARY_PATTERN.finditer(jd_text):
        candidate = _format_salary_candidate(match.group(0))
        candidate_key = _normalise_text(candidate)
        if not candidate_key or candidate_key in seen:
            continue
        seen.add(candidate_key)
        if candidate_key != source_salary:
            return candidate
    return ""


def synchronise_confirmed_salary(jd_text: str, old_salary: str, new_salary: str) -> str:
    """仅统一已确认的薪资表达，保留其他人工修改。"""
    updated = jd_text.replace(old_salary, new_salary) if old_salary else jd_text
    confirmed_key = _normalise_text(new_salary)

    def replace_match(match: re.Match[str]) -> str:
        candidate = _format_salary_candidate(match.group(0))
        return new_salary if _normalise_text(candidate) == confirmed_key else match.group(0)

    return CONCRETE_SALARY_PATTERN.sub(replace_match, updated)


# ---------------------------------------------------------------------------
# 内容质量诊断
# ---------------------------------------------------------------------------

def diagnose_content_quality(job: JobInput) -> list[ContentIssue]:
    """逐条诊断招聘内容；需要新事实时只追问，不自动增强。"""
    issues: list[ContentIssue] = []
    def add(field: str, original: str, issue_type: str, severity: str, reason: str, question: str = "", safe: str = "") -> None:
        issue_id = hashlib.sha256(f"{field}\n{original}\n{issue_type}".encode()).hexdigest()[:12]
        issues.append(ContentIssue(issue_id=issue_id, field=field, issue_type=issue_type, severity=severity, original_text=original, safe_rewrite=safe, reason=reason, follow_up_question=question))

    generic_requirements = [
        (r"需求分析(?:能力(?:强)?)?", r"客户访谈|业务流程|需求优先级|PRD|原型|产品方案|已上线|上线案例", "不可验证", "没有说明需求分析场景或可验证成果。", "需求分析能力需要通过什么项目经历或可验证成果证明？"),
        (r"项目推进(?:能力(?:强)?)?|项目管理能力(?:强)?", r"跨团队|算法|研发|业务团队|上线|交付|项目范围|关键冲突|最终结果", "表述空泛", "没有说明项目阶段、协作对象和成功标准。", "项目的最终结果是什么，并需要协调哪些团队？"),
        (r"沟通(?:协调)?能力(?:强)?", r"客户|跨团队|算法|研发|业务|决策|冲突|谈判|汇报", "不可验证", "没有说明沟通对象或行为证据。", "沟通能力需要在哪些场景验证？"),
        (r"有责任心|责任心强|抗压能力强|具备抗压能力", r"$^", "不可验证", "属于主观人格评价，难以一致验证。", "希望候选人用哪段具体经历证明这项能力？"),
    ]
    for unit in [item.strip(" \t-*•0123456789.、)") for item in re.split(r"[\n；;。]+", job.required_skills) if item.strip()]:
        for pattern, evidence, issue_type, reason, question in generic_requirements:
            if re.search(pattern, unit) and not re.search(evidence, unit):
                match = re.search(pattern, unit)
                add("required_skills", match.group(0), issue_type, "medium", reason, question)
        if re.match(r"^(?:负责|推动|制定|规划|协调|跟进|完成)", unit):
            add("required_skills", unit, "职责与要求混淆", "medium", "该表述描述入职后的动作，而不是候选人已有资格。", "这是入职任务还是候选人必须证明做过的经历？")

    outcome_pattern = re.compile(r"交付|上线|落地|完成|产出|结果|指标|增长|提升|优化|机制|方案|报告|验收")
    responsibility_units = [item.strip(" \t-*•0123456789.、)") for item in re.split(r"[\n；;。]+", job.responsibilities) if item.strip()]
    for unit in responsibility_units:
        if not outcome_pattern.search(unit):
            add("responsibilities", unit, "缺少预期产出", "medium", "描述了工作动作，但没有说明交付结果。", "这项职责最终需要交付什么结果？")
        if re.search(r"^负责需求分析[，,]\s*协调", unit) and re.search(r"推动.+落地", unit):
            rewritten = re.sub(r"^负责需求分析", "开展需求分析", unit).replace("和业务团队", "与业务团队")
            add("responsibilities", unit, "安全改写", "low", "只调整动词和并列关系，不增加事实。", safe=rewritten)

    senior_role = bool(re.search(r"高级|资深|专家|负责人|总监", f"{job.job_title} {job.seniority}"))
    if senior_role and job.responsibilities.strip() and not re.search(r"规模|人数|团队|预算|收入|增长|上线|交付|指标|结果|客户|业务链路", job.responsibilities):
        add("responsibilities", job.responsibilities.strip(), "高级岗位责任边界不明确", "medium", "高级岗位未说明责任规模或结果边界。", "这是高级岗位：需要对哪些指标、业务链路或结果边界负责？")

    unique: dict[str, ContentIssue] = {}
    for issue in issues:
        unique.setdefault(issue.issue_id, issue)
    return list(unique.values())


def diagnose_requirement_quality(job: JobInput) -> list[str]:
    """从诊断中提取需要 HR 补充的问题。"""
    return list(dict.fromkeys(issue.follow_up_question for issue in diagnose_content_quality(job) if issue.follow_up_question))


# ===========================================================================
# 新增智能功能
# ===========================================================================

def calculate_quality_score(
    job: JobInput,
    jd_text: str,
    assessment: RiskAssessment,
    content_issues: list[ContentIssue],
    decisions: dict[str, Any],
) -> QualityScore:
    """计算 JD 质量评分（0-100）"""
    # 1. 完整性（30分）
    missing_required, missing_recommended, _ = inspect_completeness(job)
    total_fields = len(REQUIRED_FIELDS) + len(RECOMMENDED_FIELDS)
    filled_count = sum(
        bool(getattr(job, field, "").strip())
        for field in [*REQUIRED_FIELDS, *RECOMMENDED_FIELDS]
    )
    completeness = int(30 * filled_count / total_fields)

    # 2. 具体性（25分）-- 基于内容长度和具体度
    specificity = 0
    if job.responsibilities:
        specificity += min(8, len(job.responsibilities) // 50)
    if job.required_skills:
        specificity += min(7, len(job.required_skills) // 40)
    if job.job_goal and len(job.job_goal) > 20:
        specificity += 5
    if job.selling_points:
        specificity += min(5, len(job.selling_points) // 30)
    specificity = min(25, specificity)

    # 3. 风险（20分）-- 风险越低分越高
    if assessment.overall_level == "低":
        risk = 20
    elif assessment.overall_level == "中":
        risk = 10
    else:
        risk = 0

    # 4. 内容质量（15分）-- 问题越少分越高
    total_issues = len(content_issues)
    resolved_issues = len(decisions)
    unresolved = total_issues - resolved_issues
    quality = max(0, 15 - unresolved * 3)

    # 5. 优化处理（10分）-- 已处理的问题占比
    if total_issues > 0:
        optimization = int(10 * resolved_issues / total_issues)
    else:
        optimization = 10

    score = completeness + specificity + risk + quality + optimization

    return QualityScore(
        score=score,
        completeness=completeness,
        specificity=specificity,
        risk=risk,
        quality=quality,
        optimization=optimization,
        breakdown={
            "filled_fields": filled_count,
            "total_fields": total_fields,
            "missing_required": len(missing_required),
            "missing_recommended": len(missing_recommended),
            "total_issues": total_issues,
            "resolved_issues": resolved_issues,
            "unresolved_issues": unresolved,
            "risk_level": assessment.overall_level,
            "jd_length": len(jd_text),
        },
    )


def suggest_field_values(job_title: str) -> list[SmartSuggestion]:
    """根据岗位名称智能推荐字段值"""
    suggestions: list[SmartSuggestion] = []
    title_lower = job_title.lower()

    # 岗位类别匹配
    role_patterns: list[tuple[str, list[dict[str, str]]]] = [
        (
            r"(ai|人工智能|大模型|llm|agent|智能)",
            [
                {"field": "required_skills", "label": "必备能力", "value": "具备 RAG / Agent 工作流设计经验\n理解 LLM 能力边界与评估方法\n能定义 AI 产品的效果指标", "reason": "AI 岗位核心能力"},
                {"field": "preferred_skills", "label": "加分能力", "value": "有模型评估或 Agent 产品上线经验\n了解 Prompt Engineering 最佳实践\n熟悉 LangChain / LlamaIndex 等框架", "reason": "AI 岗位差异化优势"},
                {"field": "selling_points", "label": "岗位亮点", "value": "参与核心 AI 产品从0到1建设\n直接对接算法团队，技术深度高\nAI 应用落地经验可复用", "reason": "AI 岗位吸引点"},
            ],
        ),
        (
            r"(产品经理|产品总监|product)",
            [
                {"field": "required_skills", "label": "必备能力", "value": "需求分析与 PRD 撰写\n跨团队协调与项目推进\n数据分析与用户洞察", "reason": "产品经理核心能力"},
                {"field": "preferred_skills", "label": "加分能力", "value": "有 B 端 SaaS 产品经验\n具备技术背景，能与研发深度沟通\n有产品 0-1 经验", "reason": "产品经理差异化"},
                {"field": "selling_points", "label": "岗位亮点", "value": "主导核心产品方向\n直接向业务负责人汇报\n产品决策权与资源支持", "reason": "产品岗吸引力"},
            ],
        ),
        (
            r"(前端|frontend|web开发|react|vue)",
            [
                {"field": "required_skills", "label": "必备能力", "value": "精通 JavaScript / TypeScript\n熟练使用 React 或 Vue 框架\n掌握 HTML5 / CSS3 / 响应式布局", "reason": "前端核心技能"},
                {"field": "preferred_skills", "label": "加分能力", "value": "有 SSR / Next.js / Nuxt.js 经验\n了解 Webpack / Vite 构建工具\n有前端性能优化经验", "reason": "前端进阶能力"},
                {"field": "selling_points", "label": "岗位亮点", "value": "前端技术栈自由度高\n参与从架构设计到上线的全流程\n团队注重技术分享与成长", "reason": "前端岗吸引点"},
            ],
        ),
        (
            r"(后端|backend|java|python|go|golang|服务端)",
            [
                {"field": "required_skills", "label": "必备能力", "value": "精通至少一门后端语言（Java/Go/Python）\n熟悉微服务架构与分布式系统\n掌握 MySQL / Redis 等存储中间件", "reason": "后端核心技能"},
                {"field": "preferred_skills", "label": "加分能力", "value": "有高并发系统设计经验\n熟悉 Kafka / RabbitMQ 等消息中间件\n了解 K8s / Docker 容器化部署", "reason": "后端进阶能力"},
                {"field": "selling_points", "label": "岗位亮点", "value": "处理亿级流量核心系统\n技术驱动型团队文化\n完善的导师制与技术成长路径", "reason": "后端岗吸引点"},
            ],
        ),
        (
            r"(数据|data|算法|algorithm|机器学习|ml|深度学习|dl)",
            [
                {"field": "required_skills", "label": "必备能力", "value": "扎实的数学与统计基础\n精通 Python 及数据科学生态（Pandas/NumPy/Scikit-learn）\n具备模型训练与评估经验", "reason": "数据/算法核心能力"},
                {"field": "preferred_skills", "label": "加分能力", "value": "有深度学习框架经验（PyTorch/TensorFlow）\n了解大模型微调与部署\n有 Kaggle 或顶会论文经历", "reason": "算法岗差异化"},
                {"field": "selling_points", "label": "岗位亮点", "value": "海量真实业务数据\n充足 GPU 算力资源\n研究成果可直接落地产生业务价值", "reason": "算法岗吸引点"},
            ],
        ),
        (
            r"(设计|design|ui|ux|交互|视觉)",
            [
                {"field": "required_skills", "label": "必备能力", "value": "精通 Figma / Sketch 等设计工具\n具备用户研究与交互设计能力\n理解设计系统与组件化思维", "reason": "设计岗核心能力"},
                {"field": "preferred_skills", "label": "加分能力", "value": "有动效设计经验\n了解前端实现原理\n有 B 端复杂产品设计经验", "reason": "设计岗差异化"},
                {"field": "selling_points", "label": "岗位亮点", "value": "设计话语权充分\n参与产品设计全流程\n团队重视设计价值与用户体验", "reason": "设计岗吸引点"},
            ],
        ),
        (
            r"(测试|qa|quality|质量)",
            [
                {"field": "required_skills", "label": "必备能力", "value": "掌握自动化测试框架（Selenium/Pytest/Jest）\n熟悉接口测试与性能测试\n理解 CI/CD 流程", "reason": "测试核心技能"},
                {"field": "preferred_skills", "label": "加分能力", "value": "有测试平台开发经验\n了解安全测试\n有代码审查与白盒测试能力", "reason": "测试进阶能力"},
                {"field": "selling_points", "label": "岗位亮点", "value": "质量保障体系完善\n测试工具链先进\n参与质量标准制定", "reason": "测试岗吸引点"},
            ],
        ),
        (
            r"(运营|operation|增长|growth)",
            [
                {"field": "required_skills", "label": "必备能力", "value": "数据分析与 SQL 查询能力\n用户增长方法论与 A/B 测试经验\n内容策划与活动运营能力", "reason": "运营核心能力"},
                {"field": "preferred_skills", "label": "加分能力", "value": "有私域运营经验\n了解 SEO/SEM\n有跨部门项目管理经验", "reason": "运营差异化"},
                {"field": "selling_points", "label": "岗位亮点", "value": "直接对增长指标负责\n数据驱动决策文化\n丰富的用户触达渠道", "reason": "运营岗吸引点"},
            ],
        ),
    ]

    for pattern, fields in role_patterns:
        if re.search(pattern, title_lower):
            for field_data in fields:
                suggestions.append(SmartSuggestion(
                    field=field_data["field"],
                    label=field_data["label"],
                    value=field_data["value"],
                    confidence=0.8,
                    reason=field_data["reason"],
                ))
            break

    # 通用建议
    if not suggestions:
        suggestions.append(SmartSuggestion(
            field="selling_points",
            label="岗位亮点",
            value="核心业务直接参与\n清晰的职业发展路径\n有竞争力的薪酬福利",
            confidence=0.5,
            reason="通用岗位亮点模板",
        ))

    return suggestions


def extract_keywords(text: str) -> list[KeywordInfo]:
    """从文本中提取关键词"""
    if not text:
        return []

    keywords: dict[str, KeywordInfo] = {}

    # 技术关键词
    tech_keywords = [
        "Python", "Java", "Go", "JavaScript", "TypeScript", "Rust", "C++",
        "React", "Vue", "Angular", "Next.js", "Node.js",
        "MySQL", "Redis", "MongoDB", "PostgreSQL", "Elasticsearch",
        "Kafka", "RabbitMQ", "Docker", "Kubernetes", "K8s",
        "AWS", "Azure", "GCP", "阿里云", "腾讯云",
        "PyTorch", "TensorFlow", "LangChain", "LlamaIndex",
        "RAG", "Agent", "LLM", "NLP", "CV", "机器学习", "深度学习",
        "微服务", "分布式", "高并发", "CI/CD",
        "Figma", "Sketch", "SQL", "NoSQL",
        "PRD", "用户研究", "数据分析", "A/B测试",
        "项目管理", "敏捷开发", "Scrum",
    ]

    for kw in tech_keywords:
        count = text.lower().count(kw.lower())
        if count > 0:
            category = "skill"
            if kw in ["PRD", "用户研究", "数据分析", "A/B测试", "项目管理", "敏捷开发", "Scrum"]:
                category = "requirement"
            keywords[kw] = KeywordInfo(keyword=kw, category=category, frequency=count)

    # 城市关键词
    cities = ["北京", "上海", "广州", "深圳", "杭州", "成都", "南京", "武汉", "西安", "苏州", "长沙", "重庆"]
    for city in cities:
        if city in text:
            keywords[city] = KeywordInfo(keyword=city, category="location", frequency=text.count(city))

    # 学历关键词
    for edu in ["博士", "硕士", "本科", "大专"]:
        if edu in text:
            keywords[edu] = KeywordInfo(keyword=edu, category="education", frequency=text.count(edu))

    # 薪资关键词
    salary_matches = re.findall(r"\d+[Kk万]", text)
    for s in salary_matches:
        if s not in keywords:
            keywords[s] = KeywordInfo(keyword=s, category="salary", frequency=1)

    return sorted(keywords.values(), key=lambda x: x.frequency, reverse=True)


def analyze_skill_gaps(job: JobInput) -> list[SkillGapItem]:
    """分析技能缺口"""
    # 典型岗位技能库
    role_skill_map: dict[str, list[tuple[str, str, str]]] = {
        "ai": [
            ("RAG", "技术", "high", "检索增强生成是 AI 应用的核心模式"),
            ("LLM", "技术", "high", "大语言模型理解是 AI 产品的基础"),
            ("Prompt Engineering", "技术", "medium", "提示词工程影响模型效果"),
            ("模型评估", "方法", "high", "效果评估是 AI 产品的关键环节"),
            ("Agent", "技术", "medium", "Agent 是 AI 应用的主流方向"),
        ],
        "product": [
            ("PRD", "方法", "high", "产品需求文档是产品经理的核心产出"),
            ("用户研究", "方法", "high", "用户洞察驱动产品决策"),
            ("数据分析", "方法", "high", "数据驱动是现代产品管理的基础"),
            ("A/B测试", "方法", "medium", "实验思维验证产品假设"),
            ("项目管理", "方法", "medium", "跨团队协作需要项目管理能力"),
        ],
        "frontend": [
            ("JavaScript", "技术", "high", "前端开发的基础语言"),
            ("TypeScript", "技术", "high", "现代前端项目的标配"),
            ("React", "技术", "high", "主流前端框架"),
            ("CSS", "技术", "high", "页面样式与响应式设计基础"),
            ("性能优化", "方法", "medium", "前端性能影响用户体验"),
        ],
        "backend": [
            ("MySQL", "技术", "high", "关系型数据库是后端基础"),
            ("Redis", "技术", "high", "缓存是高并发系统的关键"),
            ("微服务", "架构", "high", "现代后端系统主流架构"),
            ("Docker", "技术", "medium", "容器化是部署的标准实践"),
            ("消息队列", "技术", "medium", "异步解耦的关键组件"),
        ],
        "data": [
            ("Python", "技术", "high", "数据科学的主力语言"),
            ("SQL", "技术", "high", "数据查询的基础技能"),
            ("机器学习", "技术", "high", "数据科学的核心能力"),
            ("Pandas", "技术", "medium", "数据处理的标准工具"),
            ("数据可视化", "方法", "medium", "数据表达的重要能力"),
        ],
    }

    title_lower = (job.job_title or "").lower()
    all_text = f"{job.job_title} {job.responsibilities} {job.required_skills} {job.preferred_skills}".lower()

    # 确定岗位类别
    role_key = None
    if re.search(r"(ai|人工智能|大模型|llm|agent|智能)", title_lower):
        role_key = "ai"
    elif re.search(r"(产品经理|product)", title_lower):
        role_key = "product"
    elif re.search(r"(前端|frontend|react|vue)", title_lower):
        role_key = "frontend"
    elif re.search(r"(后端|backend|java|python|go|服务端)", title_lower):
        role_key = "backend"
    elif re.search(r"(数据|data|算法|机器学习)", title_lower):
        role_key = "data"

    if not role_key:
        return []

    gaps: list[SkillGapItem] = []
    for skill, category, importance, note in role_skill_map.get(role_key, []):
        in_jd = skill.lower() in all_text
        if not in_jd:
            gaps.append(SkillGapItem(
                skill=skill,
                category=category,
                in_jd=False,
                importance=importance,
                note=note,
            ))

    return sorted(gaps, key=lambda x: {"high": 0, "medium": 1, "low": 2}[x.importance])


def salary_benchmark(job_title: str, location: str, experience: str) -> SalaryBenchmark:
    """薪资基准建议"""
    # 简化版薪资基准表
    city_multiplier = {
        "北京": 1.15, "上海": 1.15, "深圳": 1.10, "杭州": 1.05,
        "广州": 0.95, "成都": 0.75, "南京": 0.85, "武汉": 0.70,
        "西安": 0.70, "苏州": 0.85, "长沙": 0.65, "重庆": 0.70,
    }

    base_ranges: list[tuple[str, tuple[int, int]]] = [
        (r"(ai|人工智能|大模型|算法)", (35, 60)),
        (r"(产品经理|product)", (25, 45)),
        (r"(前端|backend|后端|frontend)", (20, 40)),
        (r"(数据|data)", (25, 50)),
        (r"(设计|design|ui|ux)", (18, 35)),
        (r"(测试|qa)", (15, 30)),
        (r"(运营|operation)", (12, 28)),
    ]

    title_lower = (job_title or "").lower()
    base_low, base_high = 15, 30

    for pattern, (low, high) in base_ranges:
        if re.search(pattern, title_lower):
            base_low, base_high = low, high
            break

    # 经验调整
    exp_match = re.search(r"(\d+)", experience or "")
    if exp_match:
        years = int(exp_match.group(1))
        if years >= 5:
            base_low = int(base_low * 1.4)
            base_high = int(base_high * 1.4)
        elif years >= 3:
            base_low = int(base_low * 1.15)
            base_high = int(base_high * 1.15)
        elif years <= 1:
            base_low = int(base_low * 0.7)
            base_high = int(base_high * 0.7)

    # 城市调整
    mult = city_multiplier.get(location, 0.80)
    base_low = int(base_low * mult)
    base_high = int(base_high * mult)

    suggested = f"{base_low}K-{base_high}K·14薪"

    confidence = "medium"
    if location and experience and job_title:
        confidence = "high"
    elif location or experience:
        confidence = "medium"
    else:
        confidence = "low"

    return SalaryBenchmark(
        job_title=job_title or "未知岗位",
        location=location or "未指定",
        suggested_range=suggested,
        confidence=confidence,
        source="行业经验基准",
        notes="此为参考范围，实际薪资应根据公司情况、候选人能力等因素调整。",
    )


def generate_smart_tips(
    job: JobInput,
    jd_text: str,
    assessment: RiskAssessment,
    quality_score: QualityScore | None = None,
) -> list[SmartTip]:
    """生成上下文感知的智能提示"""
    tips: list[SmartTip] = []
    tip_counter = 0

    missing_required, missing_recommended, _ = inspect_completeness(job)

    # 必填字段缺失
    if missing_required:
        tip_counter += 1
        tips.append(SmartTip(
            tip_id=f"tip_{tip_counter}",
            level="danger",
            title="必填信息不完整",
            content=f"还缺少 {len(missing_required)} 个必填字段：{'、'.join(missing_required)}。这些信息是生成 JD 的前提。",
            action="请补充缺失字段后继续",
        ))

    # 推荐字段缺失
    if missing_recommended and len(missing_recommended) > 3:
        tip_counter += 1
        tips.append(SmartTip(
            tip_id=f"tip_{tip_counter}",
            level="warning",
            title="建议补充更多信息",
            content=f"有 {len(missing_recommended)} 个推荐字段未填写，补充后可显著提升 JD 质量。",
        ))

    # 风险提示
    if assessment.overall_level == "高":
        tip_counter += 1
        high_issues = [i for i in assessment.issues if i.level == "high"]
        tips.append(SmartTip(
            tip_id=f"tip_{tip_counter}",
            level="danger",
            title="存在发布阻断风险",
            content=f"检测到 {len(high_issues)} 个高风险项，必须修改后才能发布。",
        ))
    elif assessment.overall_level == "中":
        tip_counter += 1
        tips.append(SmartTip(
            tip_id=f"tip_{tip_counter}",
            level="warning",
            title="存在需要核实的内容",
            content="有中等风险项需要人工核实后再发布。",
        ))

    # 质量评分提示
    if quality_score:
        if quality_score.score < 50:
            tip_counter += 1
            tips.append(SmartTip(
                tip_id=f"tip_{tip_counter}",
                level="warning",
                title=f"JD 质量评分较低（{quality_score.score}/100）",
                content=f"完整度 {quality_score.completeness}/30，具体性 {quality_score.specificity}/25。建议补充更多细节。",
            ))
        elif quality_score.score >= 80:
            tip_counter += 1
            tips.append(SmartTip(
                tip_id=f"tip_{tip_counter}",
                level="success",
                title=f"JD 质量优秀（{quality_score.score}/100）",
                content="当前 JD 质量较高，可以进行审批发布。",
            ))

    # 技能缺口提示
    gaps = analyze_skill_gaps(job)
    high_gaps = [g for g in gaps if g.importance == "high"]
    if high_gaps:
        tip_counter += 1
        tips.append(SmartTip(
            tip_id=f"tip_{tip_counter}",
            level="info",
            title="检测到潜在技能缺口",
            content=f"同类岗位通常还要求：{'、'.join(g.skill for g in high_gaps[:3])}。确认是否需要补充。",
        ))

    # 薪资建议
    if not job.salary and job.job_title:
        benchmark = salary_benchmark(job.job_title, job.location, job.experience)
        tip_counter += 1
        tips.append(SmartTip(
            tip_id=f"tip_{tip_counter}",
            level="info",
            title="薪资参考建议",
            content=f"根据岗位和地点，建议薪资范围：{benchmark.suggested_range}（{benchmark.confidence}置信度）",
        ))

    # JD 长度检查
    if jd_text:
        if len(jd_text) < 200:
            tip_counter += 1
            tips.append(SmartTip(
                tip_id=f"tip_{tip_counter}",
                level="warning",
                title="JD 内容偏短",
                content="JD 正文较短，可能影响候选人对岗位的理解。建议补充更多细节。",
            ))
        elif len(jd_text) > 3000:
            tip_counter += 1
            tips.append(SmartTip(
                tip_id=f"tip_{tip_counter}",
                level="info",
                title="JD 内容较长",
                content="JD 正文较长，在部分平台可能影响阅读体验。建议精简非核心内容。",
            ))

    return tips


def compare_requirement_vs_jd(
    original_text: str,
    job: JobInput,
    jd_text: str,
) -> list[ComparisonItem]:
    """对比原始需求与生成的 JD"""
    items: list[ComparisonItem] = []

    field_labels = {
        "job_title": "岗位名称",
        "location": "工作地点",
        "work_mode": "工作方式",
        "experience": "经验要求",
        "education": "学历要求",
        "salary": "薪资范围",
        "department": "所属部门",
    }

    for field, label in field_labels.items():
        original_val = ""
        generated_val = getattr(job, field, "")
        match = True

        if original_text:
            if field == "job_title":
                m = re.search(r"(?:招|招聘|招募)\s*(?:一名|一个|一位)?\s*(.+?)(?:[，,。；;]|\s+负责|\s+要求)", original_text)
                if m:
                    original_val = m.group(1).strip()
            elif field == "location":
                m = re.search(r"(北京|上海|广州|深圳|杭州|成都|南京|武汉|西安|苏州|长沙|重庆|天津)", original_text)
                if m:
                    original_val = m.group(1)
            elif field == "salary":
                m = re.search(r"\d+[\-–到]\d+[Kk万](?:[·]?\d+薪)?", original_text)
                if m:
                    original_val = m.group(0)
            elif field == "experience":
                m = re.search(r"(\d+)\s*年以上", original_text)
                if m:
                    original_val = f"{m.group(1)}年以上"

        match = original_val.lower() == generated_val.lower() if original_val and generated_val else True

        items.append(ComparisonItem(
            field=field,
            label=label,
            original=original_val or "(未提取)",
            generated=generated_val or "(未填写)",
            match=match,
        ))

    return items
