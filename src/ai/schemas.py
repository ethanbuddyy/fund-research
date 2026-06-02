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
        },
        "required": [
            "fund_rationales",
            "portfolio_thesis",
            "position_sizing_notes",
            "rebalance_triggers",
            "scenario_analysis",
        ],
    },
}
