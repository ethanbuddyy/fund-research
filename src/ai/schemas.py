"""Tool use schemas for Phase 1 and Phase 2 Claude analysis."""

PHASE1_TOOL = {
    "name": "analyze_market_context",
    "description": "输出结构化市场分析，识别主要矛盾和因子关系",
    "input_schema": {
        "type": "object",
        "properties": {
            "primary_contradiction": {
                "type": "string",
                "description": "当前市场最核心的矛盾，例如：高估值（CAPE处于历史90%分位）vs 流动性充裕（信用利差3.2%处于低位）",
            },
            "factor_interactions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "factors": {"type": "array", "items": {"type": "string"}},
                        "relationship": {
                            "type": "string",
                            "enum": ["reinforcing", "offsetting", "neutral"],
                        },
                        "net_effect": {
                            "type": "string",
                            "enum": ["bullish", "bearish", "neutral"],
                        },
                        "explanation": {"type": "string"},
                    },
                    "required": ["factors", "relationship", "net_effect", "explanation"],
                },
                "description": "关键因子之间的相互作用分析，2-4组",
            },
            "cycle_phase_assessment": {
                "type": "object",
                "properties": {
                    "confirmed_phase": {"type": "string"},
                    "phase_confidence": {
                        "type": "string",
                        "enum": ["high", "medium", "low"],
                    },
                    "dissonant_signals": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "phase_reasoning": {"type": "string"},
                },
                "required": [
                    "confirmed_phase",
                    "phase_confidence",
                    "dissonant_signals",
                    "phase_reasoning",
                ],
            },
            "regional_opportunity_map": {
                "type": "object",
                "properties": {
                    "preferred_regions": {"type": "array", "items": {"type": "string"}},
                    "avoid_regions": {"type": "array", "items": {"type": "string"}},
                    "region_reasoning": {"type": "string"},
                },
                "required": ["preferred_regions", "avoid_regions", "region_reasoning"],
            },
            "risk_factors": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "risk": {"type": "string"},
                        "severity": {
                            "type": "string",
                            "enum": ["high", "medium", "low"],
                        },
                        "timeframe": {
                            "type": "string",
                            "enum": ["near_term", "medium_term", "long_term"],
                        },
                    },
                    "required": ["risk", "severity", "timeframe"],
                },
                "description": "主要风险因素，按严重程度排序",
            },
            "allocation_bias": {
                "type": "object",
                "properties": {
                    "equity_bias": {
                        "type": "string",
                        "enum": ["overweight", "neutral", "underweight"],
                    },
                    "style_preference": {
                        "type": "string",
                        "enum": ["growth", "value", "blend", "defensive"],
                    },
                    "geographic_tilt": {"type": "string"},
                    "reasoning": {"type": "string"},
                },
                "required": [
                    "equity_bias",
                    "style_preference",
                    "geographic_tilt",
                    "reasoning",
                ],
            },
            "market_narrative": {
                "type": "string",
                "description": "200字以内的连贯市场叙事，替换现有模板化 narrative insights",
            },
        },
        "required": [
            "primary_contradiction",
            "factor_interactions",
            "cycle_phase_assessment",
            "regional_opportunity_map",
            "risk_factors",
            "allocation_bias",
            "market_narrative",
        ],
    },
}

PHASE2_TOOL = {
    "name": "build_investment_decision",
    "description": "基于市场分析和候选基金，输出具体投资决策",
    "input_schema": {
        "type": "object",
        "properties": {
            "portfolio_thesis": {
                "type": "string",
                "description": (
                    "整个组合的核心投资逻辑，150字以内。"
                    "必须包含：①当前周期定性（引用阶段一的确认周期）；"
                    "②核心/卫星配置比例背后的逻辑；③最主要的组合级风险敞口。"
                ),
            },
            "position_sizing_notes": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "仓位管理操作建议，3-4条。每条必须包含：可执行动词 + 具体触发条件 + 操作幅度。"
                    "示例：'若VIX跌破16且趋势分维持≥7，将核心仓位上限从60%提至70%'。"
                    "禁止：'保持关注'、'视情况而定'等模糊表述。"
                ),
            },
            "rebalance_triggers": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "condition": {"type": "string"},
                        "action": {"type": "string"},
                    },
                    "required": ["condition", "action"],
                },
                "description": "触发再平衡的条件和对应操作，2-3条",
            },
            "scenario_analysis": {
                "type": "object",
                "properties": {
                    "bull_case": {"type": "string"},
                    "bear_case": {"type": "string"},
                    "base_case": {"type": "string"},
                },
                "required": ["bull_case", "bear_case", "base_case"],
            },
            "fund_rationales": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "fund_code": {"type": "string"},
                        "fund_name": {"type": "string"},
                        "role": {"type": "string", "enum": ["核心", "卫星"]},
                        "cycle_fit": {
                            "type": "string",
                            "description": (
                                "为何适合当前周期，1-2句，必须引用阶段一的具体宏观指标或该基金的具体评分维度。"
                                "禁止空泛表述如'表现稳健'或'适合当前环境'。"
                            ),
                        },
                        "risk_note": {
                            "type": "string",
                            "description": (
                                "该基金当前最需关注的具体风险，必须结合费率/跟踪指数/地区集中度等基金特征，"
                                "以及当前市场主要矛盾（如高估值、流动性收紧等），禁止泛化表述。"
                            ),
                        },
                        "conviction_level": {
                            "type": "string",
                            "enum": ["high", "medium", "low"],
                        },
                    },
                    "required": [
                        "fund_code",
                        "fund_name",
                        "role",
                        "cycle_fit",
                        "risk_note",
                        "conviction_level",
                    ],
                },
            },
        },
        "required": [
            "portfolio_thesis",
            "position_sizing_notes",
            "rebalance_triggers",
            "scenario_analysis",
            "fund_rationales",
        ],
    },
}

# Phase 3：对抗式审查（adversarial review）。
# 借鉴 Anthropic 自助分析实践：在 Phase2 决策产出后，由一个"只负责挑错"的子智能体
# 默认怀疑地复核，专抓「与量化数据相矛盾 / 无依据 / 过度自信 / 遗漏风险 / 自相矛盾」。
# 实测它能再提升准确率，但显著增加 token 与延迟，故默认关闭、仅对重要输出按需启用。
PHASE3_TOOL = {
    "name": "review_investment_decision",
    "description": "以对抗视角审查投资决策，逐条挑出与量化数据矛盾或无依据的主张",
    "input_schema": {
        "type": "object",
        "properties": {
            "overall_verdict": {
                "type": "string",
                "enum": ["sound", "minor_concerns", "material_concerns"],
                "description": (
                    "总体判定：sound=未发现实质问题；minor_concerns=有可改进的措辞/小瑕疵；"
                    "material_concerns=存在与数据矛盾或无依据的实质问题，使用前应人工复核。"
                ),
            },
            "confidence": {
                "type": "string",
                "enum": ["high", "medium", "low"],
                "description": "审查员对本次审查结论本身的置信度。",
            },
            "findings": {
                "type": "array",
                "description": "逐条问题；无问题时返回空数组。按严重程度从高到低排序。",
                "items": {
                    "type": "object",
                    "properties": {
                        "claim": {
                            "type": "string",
                            "description": "被质疑的原始主张（引用决策中的具体句子或字段）。",
                        },
                        "category": {
                            "type": "string",
                            "enum": [
                                "data_contradiction",     # 与给定量化数据直接矛盾
                                "unsupported_claim",       # 缺乏数据支撑的断言
                                "overstated_conviction",   # 信心/确定性表述超出证据强度
                                "missing_risk",            # 遗漏了数据中明显的风险
                                "internal_inconsistency",  # 决策内部自相矛盾（如仓位与信号不一致）
                            ],
                        },
                        "severity": {
                            "type": "string",
                            "enum": ["high", "medium", "low"],
                        },
                        "issue": {
                            "type": "string",
                            "description": "具体指出问题所在，必须引用相关的量化数值。",
                        },
                        "suggested_fix": {
                            "type": "string",
                            "description": "应如何修正该主张（更保守的措辞 / 补充风险 / 修正数字）。",
                        },
                    },
                    "required": ["claim", "category", "severity", "issue", "suggested_fix"],
                },
            },
            "summary": {
                "type": "string",
                "description": "1-2 句话的审查总结，给使用者一句可读的结论。",
            },
        },
        "required": ["overall_verdict", "confidence", "findings", "summary"],
    },
}
