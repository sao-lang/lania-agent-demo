"""翻译工具模块�?

封装文本翻译能力�?LLM 可调用的工具函数�?
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.agent_platform.agents.tools.base import ToolExecutionError, ToolRetryPolicy
from app.agent_platform.capabilities.translation import TranslationCapability


class TranslateTextInput(BaseModel):
    """翻译文本的输入参数�?""
    text: str = Field(description='待翻译文�?)
    target_language: str = Field(description='目标语言代码，如 zh（中文）/ en（英语）/ ja（日语）/ fr（法语）')
    source_language: str = Field(default='', description='源语言代码，为空则自动检�?)


class TranslateTextOutput(BaseModel):
    """翻译结果输出�?""
    translated_text: str
    detected_source_language: str
    source_text: str


class DetectLanguageInput(BaseModel):
    """检测语言的输入参数�?""
    text: str = Field(description='待检测文�?)


class DetectLanguageOutput(BaseModel):
    """语言检测结果输出�?""
    language: str
    confidence: str


class TranslateTextTool:
    """翻译文本到目标语言�?""

    name = 'translate_text'
    version = 'v1'
    timeout_ms = 30_000
    retry_policy = ToolRetryPolicy(max_attempts=2, backoff_ms=500)
    trace_fields = ['tool_call_id', 'task_id', 'step_name', 'tool_name', 'duration_ms', 'status']
    input_model = TranslateTextInput
    output_model = TranslateTextOutput
    risk_level = 'low'
    sandbox_mode = 'inline'

    def run(self, payload: TranslateTextInput, context) -> TranslateTextOutput:
        """执行文本翻译�?""
        cap = self._get_capability(context)
        try:
            result = cap.translate(payload.text, payload.target_language, payload.source_language)
        except (ConnectionError, TimeoutError) as exc:
            raise ToolExecutionError(
                code='translation_api_error',
                message=str(exc),
                error_type='dependency_error',
                default_action='fallback',
            ) from exc
        return TranslateTextOutput(
            translated_text=result.translated_text,
            detected_source_language=result.detected_source_language,
            source_text=result.source_text,
        )

    def _get_capability(self, context) -> TranslationCapability:
        services = getattr(context, 'services', None) or {}
        cap = services.get('translation')
        if cap is not None:
            return cap
        return TranslationCapability()


class DetectLanguageTool:
    """检测文本的语言�?""

    name = 'detect_language'
    version = 'v1'
    timeout_ms = 15_000
    retry_policy = ToolRetryPolicy(max_attempts=2, backoff_ms=500)
    trace_fields = ['tool_call_id', 'task_id', 'step_name', 'tool_name', 'duration_ms', 'status']
    input_model = DetectLanguageInput
    output_model = DetectLanguageOutput
    risk_level = 'low'
    sandbox_mode = 'inline'

    def run(self, payload: DetectLanguageInput, context) -> DetectLanguageOutput:
        """执行语言检测�?""
        cap = self._get_capability(context)
        try:
            result = cap.detect_language(payload.text)
        except (ConnectionError, TimeoutError) as exc:
            raise ToolExecutionError(
                code='translation_api_error',
                message=str(exc),
                error_type='dependency_error',
                default_action='fallback',
            ) from exc
        return DetectLanguageOutput(
            language=result.get('language', 'unknown'),
            confidence=result.get('confidence', '0'),
        )

    def _get_capability(self, context) -> TranslationCapability:
        services = getattr(context, 'services', None) or {}
        cap = services.get('translation')
        if cap is not None:
            return cap
        return TranslationCapability()
