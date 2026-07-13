"""应用依赖容器模块�?
负责把配置、状态存储、RAG 组件、任务执行组件和业务服务按依赖顺序装配成一个统一容器�?�?API 层和应用生命周期共享。该文件是项目运行时依赖关系最集中的位置之一�?"""

from __future__ import annotations
from pathlib import Path
from typing import Any, cast

from app.agent_platform.agents.memory import TaskMemory
from app.agent_platform.agents.planner import TaskPlanner
from app.agent_platform.agents.runtime import AgentRuntime
from app.agent_platform.agents.subagents import ContractAgent, EvidenceAgent, ReportingAgent, ReviewAgent, SubAgentRegistry, SubAgentRuntime
from app.agent_platform.agents.tools.api_contract_tools import ListApiContractsTool, ReadApiContractTool, SearchApiContractOperationsTool
from app.agent_platform.agents.tools.artifact_capability_tools import ListArtifactsTool, ReadArtifactTool
from app.agent_platform.agents.tools.analysis_tools import ExtractKeyPointsTool, ExtractRisksTool
from app.agent_platform.agents.tools.artifact_tools import DraftReportTool, FinalizeReportTool, ReviewReportTool
from app.agent_platform.agents.tools.command_tools import ShellCommandTool, RepositoryCommandTool
from app.agent_platform.agents.tools.base import AgentTool
from app.agent_platform.agents.tools.catalog_tools import LoadExtensionTool, LoadRuleTool
from app.agent_platform.agents.tools.database_tools import DescribeDatabaseTableTool, ListDatabaseTablesTool, QueryDatabaseTool
from app.agent_platform.agents.tools.defaults import build_runtime_rag_tools
from app.agent_platform.capabilities.registry import build_default_registry
from app.agent_platform.services.agent_def_manager import AgentDefManager
from app.agent_platform.services.agent_service import AgentService
from app.agent_platform.services.customization_engine import CustomizationEngine
from app.agent_platform.services.extension_catalog import ExtensionCatalog
from app.agent_platform.services.file_instruction_manager import FileInstructionManager
from app.agent_platform.services.instructions_manager import InstructionsManager
from app.agent_platform.services.auth_manager import AuthManager
from app.agent_platform.services.config_store import ConfigStore
from app.agent_platform.agents.brain.agent_loop import AgentLoop
from app.agent_platform.agents.brain.intent_recognizer import IntentRecognizer
from app.agent_platform.agents.brain.mode_router import ModeRouter
from app.agent_platform.agents.brain.step_executor import StepExecutor
from app.agent_platform.harness.safety.engine import SafetyEngine
from app.agent_platform.agents.brain.consent_store import ConsentStore
from app.agent_platform.services.intent_matcher import IntentMatcher
from app.agent_platform.services.llm_config_manager import LlmConfigManager
from app.agent_platform.services.llm_router import LlmRouter
from app.agent_platform.services.mcp_manager import McpManager
from app.agent_platform.services.plan_executor import PlanExecutor
from app.agent_platform.services.plan_generator import PlanGenerator
from app.agent_platform.services.prompt_manager import PromptManager
from app.agent_platform.services.session_manager import SessionManager
from app.agent_platform.services.skill_manager import SkillManager
from app.agent_platform.services.system_settings import (
    RuntimeConfigReader,
    SystemSettingsManager,
)
from app.agent_platform.agents.tools.repository_tools import ListRepositoryFilesTool, ReadRepositoryFileTool, SearchRepositoryTool
from app.agent_platform.agents.tools.registry import ToolRegistry
from app.agent_platform.capabilities.api_contract import build_api_contract_capability_from_provider
from app.agent_platform.capabilities.artifact import build_artifact_capability_from_provider
from app.agent_platform.capabilities.database import build_database_capability_from_provider
from app.rag_system.knowledge import build_knowledge_capability
from app.agent_platform.capabilities.repository import build_repository_capability
from app.agent_platform.capabilities.weather import WeatherCapability
from app.agent_platform.capabilities.finance import FinanceCapability
from app.agent_platform.capabilities.news import NewsCapability
from app.agent_platform.capabilities.currency import CurrencyCapability
from app.agent_platform.capabilities.geocoding import GeocodingCapability
from app.agent_platform.capabilities.sandbox_execute import LocalSandboxExecuteCapability
from app.agent_platform.capabilities.url_fetch import UrlFetchCapability
from app.agent_platform.capabilities.translation import TranslationCapability

from app.agent_platform.agents.tools.weather_tools import GetCurrentWeatherTool, GetWeatherForecastTool
from app.agent_platform.agents.tools.finance_tools import GetStockQuoteTool, GetHistoricalPricesTool
from app.agent_platform.agents.tools.news_tools import GetLatestNewsTool, SearchNewsTool
from app.agent_platform.agents.tools.currency_tools import ConvertCurrencyTool, GetExchangeRatesTool
from app.agent_platform.agents.tools.calculator_tools import CalculateTool
from app.agent_platform.agents.tools.datetime_tools import GetCurrentTimeTool, GetDateInfoTool
from app.agent_platform.agents.tools.geocoding_tools import GeocodeAddressTool, ReverseGeocodeTool
from app.agent_platform.agents.tools.url_fetch_tools import FetchWebpageTool
from app.agent_platform.agents.tools.translation_tools import TranslateTextTool, DetectLanguageTool
from app.agent_platform.agents.tools.chart_tools import GenerateChartTool
from app.agent_platform.agents.tools.web_search_tools import WebSearchTool
from app.agent_platform.agents.tools.coding_tools import ExtractCodeIssuesTool, RunCodeAnalysisTool
from app.agent_platform.agents.tools.rag_system_tools import (
    RagSystemRetrieveTool,
    RagSystemQueryTool,
    RagSystemIngestTool,
)

from app.agent_platform.core.config import Settings
from app.rag_system.container import RagContainer as RagSystemContainer
from app.rag_system.config.settings import RagSettings
from app.agent_platform.harness.hooks import EventBus
from app.agent_platform.harness.trace_hook import MemoryHook, TraceHook
from app.agent_platform.harness.guardrails import GuardrailEngine
from app.agent_platform.harness.model_router import ModelRouter
from app.agent_platform.harness.policy import PolicyEngine
from app.agent_platform.harness.sandbox import ToolSandbox
from app.agent_platform.observability.trace_recorder import TraceRecorder


class AppContainer:
    """按应用生命周期组织核心状态、RAG 组件与业务服务�?
    这个容器把配置、存储、检索、任务编排和 capability 组装为一套可复用的运行时依赖图，
    �?API 入口、后�?worker 和测试环境共享�?    """

    def __init__(self, settings: Settings, start_worker: bool | None = None) -> None:
        """初始化应用运行所需的全部核心依赖�?
        Args:
            settings: 已经解析完成的全局配置对象，决定模型、存储和功能开关行为�?            start_worker: 是否显式指定启动内嵌任务 worker；为 `None` 时回退到配置值�?        """

        self.settings = settings
        self.state = InMemoryState()
        self.persistence = SQLiteStateStore(settings)
        # 先把持久化状态恢复到内存，再初始化依赖这些状态的上层服务�?        self.persistence.load_into(self.state)
        self.trace = TraceRecorder()
        self.event_bus = EventBus()
        self.event_bus.register(TraceHook(trace=self.trace))
        self.llm = build_llm(settings)
        self.model_router = ModelRouter()
        # 底层基础能力先初始化，再按依赖关系组装上层服务�?        self.vector_store = ChromaClientFactory(settings)
        self.graph_service = GraphService(
            self.state,
            self.vector_store,
            self.trace,
            self.persistence,
            llm=self.llm,
        )
        self.retrieval = RagRetrievalService(
            settings,
            self.state,
            self.vector_store,
            self.trace,
            graph_service=self.graph_service,
        )
        self.semantic_cache = SemanticCacheService(
            settings,
            self.state,
            self.retrieval.embed_model,
            self.trace,
            self.persistence,
            runtime_config=self.runtime_config,
        )
        self.ingestion = RagIngestionService(
            settings,
            self.state,
            self.vector_store,
            self.trace,
            self.persistence,
            self.graph_service,
        )
        self.local_knowledge_capability = build_knowledge_capability(
            settings=settings,
            state=self.state,
            retrieval=self.retrieval,
            vector_store=self.vector_store,
            llm=self.llm,
            provider_name='default',
            model_router=self.model_router,
        )
        self.knowledge_capability = build_knowledge_capability(
            settings=settings,
            state=self.state,
            retrieval=self.retrieval,
            vector_store=self.vector_store,
            llm=self.llm,
            model_router=self.model_router,
            local_fallback_capability=self.local_knowledge_capability,
        )
        self.rag_facade = RagFacade(self.knowledge_capability)
        self.query_engine = RagQueryEngine(
            settings,
            self.state,
            self.retrieval,
            self.trace,
            self.persistence,
            self.semantic_cache,
            knowledge_capability=self.knowledge_capability,
            runtime_config=self.runtime_config,
        )
        self.query_orchestrator = QueryWorkflowOrchestrator(
            settings,
            self.query_engine,
            self.trace,
            self.state,
            self.persistence,
            capabilities=self.capabilities,
            event_bus=self.event_bus,
        )
        self.local_repository_capability = build_repository_capability()
        self.repository_capability = self.local_repository_capability
        self.local_api_contract_capability = build_api_contract_capability_from_provider(settings=settings, provider_name='default')
        self.api_contract_capability = self.local_api_contract_capability
        self.local_artifact_capability = build_artifact_capability_from_provider(
            settings=settings,
            state=self.state,
            persistence=self.persistence,
            provider_name='default',
        )
        self.artifact_capability = self.local_artifact_capability
        self.local_database_capability = build_database_capability_from_provider(settings=settings, provider_name='sqlite_local')
        self.database_capability = self.local_database_capability

        # ── 统一组装 capabilities dict，供 Harness �?Orchestrator 注入 ──
        self.capabilities = {
            'knowledge': self.knowledge_capability,
            'rag': self.rag_facade,
            'repository': self.repository_capability,
            'api_contract': self.api_contract_capability,
            'artifact': self.artifact_capability,
            'database': self.database_capability,
        }

        # ── 外部数据服务 Capability ────────────
        self.weather_capability = WeatherCapability(api_key=settings.weather_api_key or '')
        self.finance_capability = FinanceCapability()
        self.news_capability = NewsCapability(api_key=settings.news_api_key or '')
        self.currency_capability = CurrencyCapability()
        self.geocoding_capability = GeocodingCapability()
        self.url_fetch_capability = UrlFetchCapability()
        self.translation_capability = TranslationCapability()
        self.sandbox_execute_capability = LocalSandboxExecuteCapability(settings=settings)
        # ── 独立 RAG 系统（阶段一�?──────────────
        self.rag_system = RagSystemContainer(
            settings=RagSettings.from_app_settings(settings),
        )
        # ───────────────────────────────────────

        self.external_services: dict[str, Any] = {
            'weather': self.weather_capability,
            'finance': self.finance_capability,
            'news': self.news_capability,
            'currency': self.currency_capability,
            'geocoding': self.geocoding_capability,
            'url_fetch': self.url_fetch_capability,
            'translation': self.translation_capability,
            'sandbox_execute': self.sandbox_execute_capability,
            'rag_system': self.rag_system,
        }
        # ───────────────────────────────────────

        self.local_sandbox_engine = ToolSandbox()
        self.sandbox_engine = ToolSandbox(settings)

        # ── Agent 平台新服�?───────────────────
        self.config_store = ConfigStore(
            db_path=settings.resolved_data_dir / "app.sqlite3",
        )
        self.mcp_manager = McpManager(persistence=self.persistence)
        self.auth_manager = AuthManager(config_store=self.config_store)
        self.llm_router = LlmRouter(
            config_store=self.config_store,
            env_settings=settings,
        )
        self.llm_config_manager = LlmConfigManager(
            config_store=self.config_store,
            llm_router=self.llm_router,
        )
        self.skill_manager = SkillManager(persistence=self.persistence)
        self.agent_def_manager = AgentDefManager(persistence=self.persistence)
        self.prompt_manager = PromptManager(persistence=self.persistence)
        self.system_settings_manager = SystemSettingsManager(
            config_store=self.config_store,
        )
        self.runtime_config = RuntimeConfigReader(
            env_settings=settings,
            runtime_manager=self.system_settings_manager,
        )
        self.capability_registry = build_default_registry()
        self.intent_matcher = IntentMatcher(
            registry=self.capability_registry,
            llm=self.llm,
        )
        self.plan_generator = PlanGenerator(
            registry=self.capability_registry, llm=self.llm,
        )
        self.plan_executor = PlanExecutor()
        # ───────────────────────────────────────

        self.task_memory = TaskMemory(self.state, self.persistence)
        self.event_bus.register(MemoryHook(memory=self.task_memory))

        # SessionManager 依赖 task_memory，在 task_memory 之后初始�?        self.session_manager = SessionManager(
            state=self.state,
            persistence=self.persistence,
            task_memory=self.task_memory,
        )
        # 创建扩展清单（大模型通过 load_extension / load_rule 按需加载�?        self.extension_catalog = ExtensionCatalog(
            skill_manager=self.skill_manager,
            agent_def_manager=self.agent_def_manager,
            mcp_manager=self.mcp_manager,
        )

        # ── 定制化原语系�?───────────────────
        self.instructions_manager = InstructionsManager()
        self.file_instruction_manager = FileInstructionManager()
        self.customization_engine = CustomizationEngine(
            agents_dir=Path(".lania"),
            skill_manager=self.skill_manager,
            agent_def_manager=self.agent_def_manager,
            prompt_manager=self.prompt_manager,
            mcp_manager=self.mcp_manager,
            event_bus=self.event_bus,
            file_instruction_manager=self.file_instruction_manager,
            instructions_manager=self.instructions_manager,
            settings=settings,
        )
        # ─────────────────────────────────────

        # ── Brain 组件（意图识�?+ 模式路由 + 安全策略�?──
        self.safety_engine = SafetyEngine()
        self.consent_store = ConsentStore()
        self.step_executor = StepExecutor(
            tool_registry=self.task_tool_registry,
            harness=None,
            consent_store=self.consent_store,
            safety_engine=self.safety_engine,
        )
        self.intent_recognizer = IntentRecognizer(llm=self.llm)
        self.mode_router = ModeRouter()
        self.agent_loop = AgentLoop(
            llm=self.llm,
            step_executor=self.step_executor,
            intent_recognizer=self.intent_recognizer,
            mode_router=self.mode_router,
            tool_registry=self.task_tool_registry,
        )
        # ────────────────────────────────────────

        self.agent_service = AgentService(
            registry=self.capability_registry,
            intent_matcher=self.intent_matcher,
            session_manager=self.session_manager,
            mcp_manager=self.mcp_manager,
            plan_generator=self.plan_generator,
            plan_executor=self.plan_executor,
            task_orchestrator=None,
            query_orchestrator=None,
            repository=self.local_repository_capability,
            database=self.local_database_capability,
            llm=self.llm,
            tool_registry=self.task_tool_registry,
            skill_manager=self.skill_manager,
            agent_def_manager=self.agent_def_manager,
            catalog=self.extension_catalog,
            customization_engine=self.customization_engine,
            # Brain 组件
            intent_recognizer=self.intent_recognizer,
            mode_router=self.mode_router,
            agent_loop=self.agent_loop,
            step_executor=self.step_executor,
        )
        self.task_planner = TaskPlanner()
        self.evidence_agent = EvidenceAgent(self.task_memory, self.trace)
        self.reporting_agent = ReportingAgent(self.task_memory, self.trace)
        self.review_agent = ReviewAgent(self.task_memory, self.trace)
        self.contract_agent = ContractAgent(self.task_memory, self.trace)
        self.subagent_registry = SubAgentRegistry()
        self.subagent_registry.register(self.evidence_agent)
        self.subagent_registry.register(self.reporting_agent)
        self.subagent_registry.register(self.review_agent)
        self.subagent_registry.register(self.contract_agent)
        self.subagent_runtime = SubAgentRuntime(self.subagent_registry, self.trace)
        self.task_tool_registry = ToolRegistry()
        # 任务工具在容器启动时一次性注册，避免运行阶段出现工具集合不一致�?        # 公开 task tool surface 已只保留 `rag_*` 主路径工具名�?        for tool in (
            *build_runtime_rag_tools(),
            ListRepositoryFilesTool(),
            SearchRepositoryTool(),
            ReadRepositoryFileTool(),
            ListApiContractsTool(),
            SearchApiContractOperationsTool(),
            ReadApiContractTool(),
            ListArtifactsTool(),
            ReadArtifactTool(),
            ListDatabaseTablesTool(),
            DescribeDatabaseTableTool(),
            QueryDatabaseTool(),
            ExtractKeyPointsTool(),
            ExtractRisksTool(),
            DraftReportTool(),
            ReviewReportTool(),
            FinalizeReportTool(),
            ShellCommandTool(),
            RepositoryCommandTool(),
            # ── 外部数据服务工具 ──
            GetCurrentWeatherTool(),
            GetWeatherForecastTool(),
            GetStockQuoteTool(),
            GetHistoricalPricesTool(),
            GetLatestNewsTool(),
            SearchNewsTool(),
            ConvertCurrencyTool(),
            GetExchangeRatesTool(),
            CalculateTool(),
            GetCurrentTimeTool(),
            GetDateInfoTool(),
            GeocodeAddressTool(),
            ReverseGeocodeTool(),
            FetchWebpageTool(),
            TranslateTextTool(),
            DetectLanguageTool(),
            GenerateChartTool(),
            WebSearchTool(),
            # ── Coding Agent 工具 ──
            ExtractCodeIssuesTool(),
            RunCodeAnalysisTool(),
            # ── RAG 系统工具（独�?RAG 引擎）──
            RagSystemRetrieveTool(),
            RagSystemQueryTool(),
            RagSystemIngestTool(),
            # ── 扩展清单工具（大模型按需加载扩展内容）──
            LoadExtensionTool(self.extension_catalog),
            LoadRuleTool(self.extension_catalog),
        ):
            self.task_tool_registry.register(cast(AgentTool, tool))
        self.guardrail_engine = GuardrailEngine(self.task_tool_registry)
        self.policy_engine = PolicyEngine(settings, persistence=self.persistence)
        self.task_orchestrator = TaskWorkflowOrchestrator(
            self.task_planner,
            self.task_tool_registry,
            self.task_memory,
            self.trace,
            settings,
            self.state,
            self.retrieval,
            self.vector_store,
            self.llm,
            self.subagent_runtime,
            guardrail_engine=self.guardrail_engine,
            policy_engine=self.policy_engine,
            capabilities=self.capabilities,
            model_router=self.model_router,
            services=self.external_services,
            event_bus=self.event_bus,
        )
        # �?orchestrator 注入 AgentService
                            self.agent_service._query_orchestrator = self.query_orchestrator

                if start_worker is not None:
            should_start_worker = start_worker
        else:
            should_start_worker = settings.enable_embedded_task_worker
        if should_start_worker:
            # 开发环境可直接启用内嵌 worker，减少额外进程编排成本�?            self.task_worker.start_background()

    def shutdown(self) -> None:
        """释放容器托管的后台资源�?
        当前主要用于关闭任务 worker 与调度器，避免测试结束或服务退出时遗留后台线程�?        统一走这个出口，也方便后续补充更多需要显式释放的运行时资源�?        """

        # task_worker removed
        # task_dispatcher removed


def build_container(settings: Settings, start_worker: bool | None = None) -> AppContainer:
    """构建应用级依赖容器�?
    Args:
        settings: 已加载完成的全局配置实例�?        start_worker: 是否显式指定启动内嵌任务 worker；为 `None` 时沿用配置项�?
    Returns:
        完成依赖装配后的应用容器实例�?    """

    return AppContainer(settings, start_worker=start_worker)
